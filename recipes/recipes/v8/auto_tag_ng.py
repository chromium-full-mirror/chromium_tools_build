# Copyright 2015 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""
This recipe checks if a version update on branch <B> is necessary, where
'version' refers to the contents of the v8 version file (part of the v8
sources).

The recipe will:
- Commit a v8 version change to <B> with an incremented patch level if the
  latest two commits point to the same version.
- Make sure that the actual HEAD of <B> is tagged with its v8 version (as
  specified in the v8 version file at HEAD).
- Update a ref called <B>-lkgr to point to the latest commit that has a unique,
  incremented version and that is tagged with that version.
"""

import re


from PB.go.chromium.org.luci.buildbucket.proto.common import FAILURE
from recipe_engine.post_process import (
    DropExpectation, DoesNotRunRE, MustRun, StepFailure)
from recipe_engine.recipe_api import Property
from PB.recipe_engine.result import RawResult

DEPS = [
    'builder_group',
    'depot_tools/gclient',
    'depot_tools/gerrit',
    'depot_tools/git',
    'recipe_engine/buildbucket',
    'recipe_engine/context',
    'recipe_engine/json',
    'recipe_engine/file',
    'recipe_engine/path',
    'recipe_engine/properties',
    'recipe_engine/raw_io',
    'recipe_engine/runtime',
    'recipe_engine/service_account',
    'recipe_engine/step',
    'v8',
]

RELEASE_BRANCH_REF_RE = re.compile(r'^refs/branch-heads/\d+\.\d+$')
MAX_COMMIT_WAIT_RETRIES = 5
MAX_NUMBER_OF_TRACKED_BRANCHES = 10
REMOTE_REPO_URL = 'https://chromium.googlesource.com/v8/v8.git'

# TODO(sergiyb): Replace with api.service_account.default().get_email() when
# https://crbug.com/846923 is resolved.
PUSH_ACCOUNT = (
    'v8-ci-autoroll-builder@chops-service-accounts.iam.gserviceaccount.com')

PROPERTIES = {
    'tracked_branches_count': Property(
            kind=int,
            help='Number of maximum branches that we want to track.',
            default=MAX_NUMBER_OF_TRACKED_BRANCHES)
}

class BuildResults:
  def __init__(self):
    self.success = True
    self.performed_actions = []


def RunSteps(api, tracked_branches_count):
  api.gclient.set_config('v8')
  api.v8.checkout(with_branch_heads=True)

  with api.context(
      cwd=api.path['checkout'],
      env_prefixes={'PATH': [api.v8.depot_tools_path]}):
    api.v8.git_output('fetch', 'origin', '--prune')
    branches = api.v8.latest_branches()
    assert len(branches) >= tracked_branches_count, "Too few branches"

    build_results = BuildResults()
    for chromium_milestone in branches[:tracked_branches_count]:
      chromium_milestone_str = api.v8.version_num2str(chromium_milestone)
      check_branch(api, chromium_milestone_str, build_results)

    result = api.step('Summary', cmd=None)
    result.presentation.step_text = "\n".join(build_results.performed_actions
        or ["-none-"])
    if not build_results.success:
      return RawResult(status=FAILURE)


def check_branch(api, branch_version, build_results):
  with api.step.nest('Checking branch %s' % branch_version):
    branch_ref = 'branch-heads/%s' % branch_version
    api.v8.git_output('checkout', branch_ref)
    version_at_head = api.v8.read_version_from_ref("HEAD", branch_ref)
    proof_of_version_change = api.v8.git_output(
        'show',
        api.v8.VERSION_FILE,
        ok_ret='any',
        name='Proof of version change')
    if proof_of_version_change:
      verify_version_tag(api, version_at_head, build_results)
      has_pgo_tag = verify_pgo_tag(api, version_at_head)

      # TODO(crbug.com/1382471): We can remove this again if there are no back-
      # merges to LTS branches before M112 anymore.
      before_cutoff = (
          (int(version_at_head.major), int(version_at_head.minor)) < (11, 2))

      if has_pgo_tag or before_cutoff:
        # We only update the lkgr if pgo profiles are available. If they are not
        # generated yet, we update the lkgr in the next run of auto-tag ng.
        verify_lkgr(api, branch_version, version_at_head, build_results)

    else:
      maybe_increment_version(api, branch_ref, version_at_head, build_results)


def verify_version_tag(api, version_at_branch_head, build_results):
  with api.step.nest('Verify version tag'):
    commit_at_tag = get_commit_at_tag(api, version_at_branch_head)
    commit_at_head = api.v8.git_output(
        'show', '--format=%H', '--no-patch', 'HEAD', name='Commit at HEAD')
    assert commit_at_head, 'Expected a checkout, but no head revision found.'
    if commit_at_head != commit_at_tag:
      # Tag latest version.
      if api.properties.get('dry_run') or api.runtime.is_experimental:
        api.step('Dry-run tag %s' % version_at_branch_head, cmd=None)
      else:
        api.git('tag', str(version_at_branch_head), 'HEAD')
        api.git('push', REMOTE_REPO_URL, str(version_at_branch_head))
      build_results.performed_actions.append(
            "Tagged %s" % version_at_branch_head)


def verify_pgo_tag(api, version_at_branch_head):
  with api.step.nest('Verify pgo tag'):
    return bool(get_commit_at_tag(api, f'{version_at_branch_head}-pgo'))


def get_commit_at_tag(api, tag):
  return api.v8.git_output(
      'show',
      '--format=%H',
      '--no-patch',
      f'refs/tags/{tag}',
      name=f'Commit at {tag}',
      ok_ret='any')


def verify_lkgr(api, branch_version, version_at_branch_head,
        build_results):
  with api.step.nest('Verify LKGR'):
    lkgr_ref = 'refs/heads/%s-lkgr' % branch_version
    current_lkgr = get_commit_for_ref(api, lkgr_ref)
    branch_head = get_commit_for_ref(api,
                                     'refs/tags/%s' % version_at_branch_head)
    api.step('LKGR commit %s' % current_lkgr, [])
    api.step('HEAD commit %s' % branch_head, [])
    if branch_head != current_lkgr:
      set_lkgr(api, branch_head, lkgr_ref, build_results)
    else:
      api.step('There is no new lkgr.', [])


def set_lkgr(api, branch_head, lkgr_ref, build_results):
  if api.properties.get('dry_run') or api.runtime.is_experimental:
    api.step('Dry-run lkgr update %s' % branch_head, cmd=None)
  else:
    push_ref(api, REMOTE_REPO_URL, lkgr_ref, branch_head)
  build_results.performed_actions.append("Ref updated %s" % lkgr_ref)


def get_commit_for_ref(api, ref):
  result = api.v8.git_output(
      'ls-remote',
      REMOTE_REPO_URL,
      ref,
      # Need str() to turn unicode into ascii in production.
      name=str('git ls-remote %s' % ref.replace('/', '_')),
  )
  if result:
    # Extract hash if available. Otherwise keep empty string.
    result = result.split()[0]
  return result


def push_ref(api, repo, ref, hsh):
  api.git('push', repo, '+%s:%s' % (hsh, ref))


def maybe_increment_version(api, ref, latest_version, build_results):
  with api.step.nest('Increment version from %s' % latest_version):
    commits = api.gerrit.get_changes(
        'https://chromium-review.googlesource.com',
        query_params=[
            ('project', 'v8/v8'),
            ('owner', PUSH_ACCOUNT),
            ('status', 'open'),
        ],
        limit=20,
        step_test_data=api.gerrit.test_api.get_empty_changes_response_data,
    )
    if [c for c in commits if c['subject'] == subject(latest_version)]:
      step_result = api.step('Stale version change CL found!', cmd=None)
      step_result.presentation.status = 'FAILURE'
      build_results.success = False
    else:
      new_version = latest_version.with_incremented_patch()
      api.v8.update_version_cl(
        ref, new_version, push_account=PUSH_ACCOUNT, bot_commit=True)
      build_results.performed_actions.append("Version updated %s" % new_version)


def subject(latest_version):
  return 'Version %s' % latest_version


def GenTests(api):

  def stdout(step_name, text):
    return api.override_step_data(
        step_name, api.raw_io.stream_output_text(text, stream='stdout'))

  def tracked_branches_count(branches):
    return api.properties(tracked_branches_count=branches)

  def test(name, *test_data):
    return api.test(
        name,
        # If the test case specifies tracked_branches_count, it will override
        # this
        tracked_branches_count(1),
        *test_data)

  def version_file(patch_level, description, prefix=''):
    return api.v8.version_file(patch_level, description, prefix=prefix, major=11)

  yield test(
      'branches-to-update-version-for',
      tracked_branches_count(2),
      stdout('last branches', 'branch-heads/11.1\nbranch-heads/11.2'),
      version_file(3, 'branch-heads/11.2', prefix="Checking branch 11.2."),
      version_file(
          4,
          'latest',
          prefix="Checking branch 11.2.Increment version from 11.4.3.3."),
      version_file(2, 'branch-heads/11.1', prefix="Checking branch 11.1."),
      version_file(
          3,
          'latest',
          prefix="Checking branch 11.1.Increment version from 11.4.3.2."),
  )

  yield test(
      'dry-run-branches-to-update-version-for',
      tracked_branches_count(2),
      api.runtime(is_experimental=True),
      stdout('last branches', 'branch-heads/11.1\nbranch-heads/11.2'),
      version_file(3, 'branch-heads/11.2', prefix="Checking branch 11.2."),
      version_file(
          4,
          'latest',
          prefix="Checking branch 11.2.Increment version from 11.4.3.3."),
      version_file(2, 'branch-heads/11.1', prefix="Checking branch 11.1."),
      version_file(
          3,
          'latest',
          prefix="Checking branch 11.1.Increment version from 11.4.3.2."),
  )

  yield test(
      'branche-with-stale-version-update',
      stdout('last branches', 'branch-heads/11.2'),
      version_file(3, 'branch-heads/11.2', prefix="Checking branch 11.2."),
      api.override_step_data(
          'Checking branch 11.2.'
          'Increment version from 11.4.3.3.gerrit changes',
          api.json.output([{
              '_number': '123',
              'subject': 'Version 11.4.3.3'
          }])),
      api.post_process(
          StepFailure, 'Checking branch 11.2.'
          'Increment version from 11.4.3.3.'
          'Stale version change CL found!'),
      api.expect_status('FAILURE'),
  )

  yield test(
      'branch-with-updated-version-but-no-tag',
      stdout('last branches', 'branch-heads/11.3'),
      version_file(3, 'branch-heads/11.3', prefix="Checking branch 11.3."),
      stdout('Checking branch 11.3.Proof of version change',
             'dummy proof of version change'),
      stdout('Checking branch 11.3.Verify version tag.Commit at HEAD', '123'),
      api.post_process(
          MustRun, 'Checking branch 11.3.Verify version tag.git push'),
      api.post_process(DropExpectation),
  )

  yield test(
      'branch-with-correct-tags',
      stdout('last branches', 'branch-heads/11.3'),
      version_file(3, 'branch-heads/11.3', prefix="Checking branch 11.3."),
      stdout('Checking branch 11.3.Proof of version change',
             'dummy proof of version change'),
      stdout('Checking branch 11.3.Verify version tag.Commit at 11.4.3.3', '123'),
      stdout('Checking branch 11.3.Verify pgo tag.Commit at 11.4.3.3-pgo', '123'),
      stdout('Checking branch 11.3.Verify version tag.Commit at HEAD', '123'),
      stdout(
          'Checking branch 11.3.Verify LKGR.'
          'git ls-remote refs_heads_11.3-lkgr', '112233'),
      stdout(
          'Checking branch 11.3.Verify LKGR.'
          'git ls-remote refs_tags_11.4.3.3', '112233'),
      api.post_process(DoesNotRunRE, 'Checking branch 11.3.'
                       'Verify version tag.git tag'),
      api.post_process(
          MustRun, 'Checking branch 11.3.Verify LKGR.'
          'There is no new lkgr.'),
  )

  yield test(
      'lkgr-branch',
      stdout('last branches', 'branch-heads/11.3'),
      version_file(3, 'branch-heads/11.3', prefix="Checking branch 11.3."),
      stdout('Checking branch 11.3.Proof of version change',
             'dummy proof of version change'),
      stdout('Checking branch 11.3.Verify version tag.Commit at 11.4.3.3', '123'),
      stdout('Checking branch 11.3.Verify pgo tag.Commit at 11.4.3.3-pgo', '123'),
      stdout('Checking branch 11.3.Verify version tag.Commit at HEAD', '123'),
      stdout(
          'Checking branch 11.3.Verify LKGR.'
          'git ls-remote refs_heads_11.3-lkgr', 'faceb00c'),
      stdout(
          'Checking branch 11.3.Verify LKGR.'
          'git ls-remote refs_tags_11.4.3.3', '404'),
      api.post_process(MustRun, 'Checking branch 11.3.Verify LKGR.git push'),
      api.post_process(DropExpectation),
  )

  yield test(
      'no-pgo-profiles',
      stdout('last branches', 'branch-heads/11.3'),
      version_file(3, 'branch-heads/11.3', prefix="Checking branch 11.3."),
      stdout('Checking branch 11.3.Proof of version change',
             'dummy proof of version change'),
      stdout('Checking branch 11.3.Verify version tag.Commit at 11.4.3.3', '123'),
      stdout('Checking branch 11.3.Verify version tag.Commit at HEAD', '123'),
      api.post_process(
          DoesNotRunRE,
          '.*Verify LKGR.*',
      ),
      api.post_process(DropExpectation),
  )

  yield test(
      'dry-run-branch-no-tag-no-lkgr',
      api.runtime(is_experimental=True),
      stdout('last branches', 'branch-heads/11.3'),
      stdout('Checking branch 11.3.Proof of version change',
             'dummy proof of version change'),
      stdout('Checking branch 11.3.Verify version tag.Commit at HEAD', '123'),
      stdout('Checking branch 11.3.Verify pgo tag.Commit at 11.4.3.3-pgo', '123'),
      version_file(3, 'branch-heads/11.3', prefix="Checking branch 11.3."),
      stdout(
          'Checking branch 11.3.Verify LKGR.'
          'git ls-remote refs_heads_11.3-lkgr', '3e1a'),
      stdout(
          'Checking branch 11.3.Verify LKGR.'
          'git ls-remote refs_tags_11.4.3.3', '404'),
      api.post_process(
          MustRun, 'Checking branch 11.3.Verify LKGR.'
          'Dry-run lkgr update 404'),
  )
