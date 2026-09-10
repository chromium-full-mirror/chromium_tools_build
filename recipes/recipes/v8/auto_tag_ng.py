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

import json
import re


from PB.go.chromium.org.luci.buildbucket.proto.common import FAILURE
from recipe_engine.post_process import (
  DropExpectation,
  DoesNotRunRE,
  MustRun,
  StepFailure,
)
from recipe_engine.recipe_api import Property
from PB.recipe_engine.result import RawResult

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import builder_group, v8
from RECIPE_MODULES.depot_tools import (
  gclient,
  gerrit,
  git,
  gitiles,
)
from RECIPE_MODULES.recipe_engine import (
  buildbucket,
  context,
  file,
  json as json_module,
  properties,
  raw_io,
  runtime,
  service_account,
  step,
)


@dataclass
class DEPS(RecipeScriptApi):
  buildbucket: buildbucket.API
  builder_group: builder_group.API
  context: context.API
  file: file.API
  gclient: gclient.API
  gerrit: gerrit.API
  git: git.API
  gitiles: gitiles.API
  json: json_module.API
  properties: properties.API
  raw_io: raw_io.API
  runtime: runtime.API
  service_account: service_account.API
  step: step.API
  v8: v8.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  gerrit: gerrit.TEST_API
  git: git.TEST_API
  gitiles: gitiles.TEST_API
  json: json_module.TEST_API
  properties: properties.TEST_API
  raw_io: raw_io.TEST_API
  runtime: runtime.TEST_API
  step: step.TEST_API
  v8: v8.TEST_API


CHROMIUM_BRANCH_RE = re.compile(r'\w+\s+refs/heads/chromium/(\w+)')
CHROMIUM_BRANCH_REF_RE = re.compile(r'^refs/branch-heads/(\d+)$')
RELEASE_BRANCH_REF_RE = re.compile(r'^refs/branch-heads/\d+\.\d+$')
MAX_COMMIT_WAIT_RETRIES = 5
REMOTE_REPO_URL = 'https://chromium.googlesource.com/v8/v8.git'
CHROMIUM_REPO_URL = 'https://chromium.googlesource.com/chromium/src'
MILESTONES_FILE = 'infra/config/milestones.json'

# TODO(sergiyb): Replace with api.service_account.default().get_email() when
# https://crbug.com/846923 is resolved.
PUSH_ACCOUNT = (
  'v8-ci-autoroll-builder@chops-service-accounts.iam.gserviceaccount.com'
)


class BuildResults:
  def __init__(self):
    self.success = True
    self.performed_actions = []


def RunSteps(api: DEPS):
  api.gclient.set_config('v8')
  update_result = api.v8.checkout(with_branch_heads=True)
  source_dir = update_result.source_root.path

  with api.context(
    cwd=source_dir, env_prefixes={'PATH': [api.v8.depot_tools_path(source_dir)]}
  ):
    api.v8.git_output('fetch', 'origin', '--prune')

    build_results = BuildResults()
    milestone_versions = milestone_version_mapping(api)

    for chromium_version in fetch_unmanaged_chromium_versions(
      api, milestone_versions
    ):
      update_chromium_branch(api, source_dir, chromium_version, build_results)

    for v8_version, chromium_version in milestone_versions:
      update_v8_branch(
        api, source_dir, v8_version, chromium_version, build_results
      )

    result = api.step('Summary', cmd=None)
    result.presentation.step_text = "\n".join(
      build_results.performed_actions or ["-none-"]
    )
    if not build_results.success:
      return RawResult(status=FAILURE)


def fetch_unmanaged_chromium_versions(api: DEPS, milestone_versions):
  """Returns supported chromium branches for the active milestones without
  the milestone branches themselves, which are managed automatically by this
  script.

  Supported branches are: mini branches and recent canary branches that
  are either currently active or used for the dev channel.

  Branches are ordered latest first.
  """
  output = api.v8.git_output('ls-remote', 'origin', 'refs/heads/chromium/*')
  unmanaged_versions = []
  managed_versions = set(str(version) for _, version in milestone_versions)
  for index, line in enumerate(list(reversed(output.split('\n')))[:300]):
    match = CHROMIUM_BRANCH_RE.fullmatch(line)
    assert match
    chromium_version = match.group(1)

    # Check if this is a mini branch.
    if '_' in chromium_version:
      # Check if this mini branch is active.
      base_version = chromium_version.split('_')[0]
      if base_version in managed_versions:
        unmanaged_versions.append(chromium_version)

    # The dev channel must be one of the latest canary branches.
    elif index < 20 and chromium_version not in managed_versions:
      unmanaged_versions.append(chromium_version)
  logs = api.step.active_result.presentation.logs
  logs['chromium versions'] = unmanaged_versions
  return unmanaged_versions


def milestone_version_mapping(api: DEPS):
  """Returns a list of tuples (V8 version, Chromium version) for all active
  Chromium milestones.

  The V8 version is of the form used in V8 refs:
  refs/branch-heads/X, e.g. X="11.8".

  The Chromium version is of the form used in Chromium refs:
  refs/branch-heads/X, e.g. X="5814".

  The Chromium verison of the tuple matches the milestone of the V8 version.

  The Chromium version may be None. On branch-cut day, the V8 branch is
  prepared before the Chromium branch and the Chromium version doesn't exist
  yet. It will be updated as soon as it is provided by the active milestone.
  """
  milestones = json.loads(
    api.gitiles.download_file(
      CHROMIUM_REPO_URL, MILESTONES_FILE, step_name='fetch milestones'
    )
  )

  def milestone2version(milestone):
    return api.v8.version_num2str(int(milestone))

  def ref2version(config):
    ref = config['ref']
    match = CHROMIUM_BRANCH_REF_RE.match(ref)
    assert match, f'Chromium branch ref {ref} did not match.'
    return match.group(1)

  result = [
    (milestone2version(milestone), ref2version(config))
    for milestone, config in sorted(milestones.items(), reverse=True)
  ]

  # If needed, prepend the version with the latest branch on the V8 side even
  # if it isn't active yet. Like that, most of the auto-tag work will be
  # processed on the branch before Chromium's branch cut.
  latest_version = milestone2version(api.v8.latest_branches()[0])
  if float(latest_version) > float(result[0][0]):
    result = [(latest_version, None)] + result

  return result


def update_v8_branch(
  api: DEPS, source_dir, branch_version, chromium_version, build_results
):
  with api.step.nest(f'Checking V8 branch {branch_version}'):
    branch_ref = f'branch-heads/{branch_version}'
    api.v8.git_output('checkout', branch_ref)
    version_at_head = api.v8.read_version_from_ref(
      source_dir, "HEAD", branch_ref
    )
    if current_branch_has_version_change(api):
      verify_version_tag(api, version_at_head, build_results)
      has_pgo_tag = verify_pgo_tag(api, version_at_head)

      # TODO(crbug.com/1382471): We can remove this again if there are no back-
      # merges to LTS branches before M112 anymore.
      before_cutoff = (
        int(version_at_head.major),
        int(version_at_head.minor),
      ) < (11, 2)

      if has_pgo_tag or before_cutoff:
        # We only update the lkgr and chromium ref if pgo profiles are
        # available. If they are not generated yet, we update the refs in the
        # next run of auto-tag ng.
        verify_floating_refs(
          api, version_at_head, branch_version, chromium_version, build_results
        )

    else:
      new_version = version_at_head.with_incremented_patch()
      maybe_increment_version(
        api, source_dir, branch_ref, version_at_head, new_version, build_results
      )


def update_chromium_branch(
  api: DEPS, source_dir, chromium_version, build_results
):
  with api.step.nest(f'Checking Chromium branch {chromium_version}'):
    branch_ref = f'remotes/origin/chromium/{chromium_version}'
    api.v8.git_output('checkout', branch_ref)
    version_at_head = api.v8.read_version_from_ref(
      source_dir, "HEAD", branch_ref
    )
    if current_branch_has_version_change(api):
      verify_version_tag(api, version_at_head, build_results)
    else:
      new_version = version_at_head.with_increment_for_chromium(
        chromium_version
      )
      maybe_increment_version(
        api, source_dir, branch_ref, version_at_head, new_version, build_results
      )


def current_branch_has_version_change(api: DEPS):
  return api.v8.git_output(
    'show', api.v8.VERSION_FILE, ok_ret='any', name='Proof of version change'
  )


def verify_version_tag(api: DEPS, version_at_branch_head, build_results):
  with api.step.nest('Verify version tag'):
    commit_at_tag = get_commit_at_tag(api, version_at_branch_head)
    commit_at_head = api.v8.git_output(
      'show', '--format=%H', '--no-patch', 'HEAD', name='Commit at HEAD'
    )
    assert commit_at_head, 'Expected a checkout, but no head revision found.'
    if commit_at_head != commit_at_tag:
      # Tag latest version.
      if api.v8.dry_run:
        api.step('Dry-run tag %s' % version_at_branch_head, cmd=None)
      else:
        api.git('tag', str(version_at_branch_head), 'HEAD')
        api.git('push', REMOTE_REPO_URL, str(version_at_branch_head))
      build_results.performed_actions.append(
        "Tagged %s" % version_at_branch_head
      )


def verify_pgo_tag(api: DEPS, version_at_branch_head):
  with api.step.nest('Verify pgo tag'):
    return bool(get_commit_at_tag(api, f'{version_at_branch_head}-pgo'))


def get_commit_at_tag(api: DEPS, tag):
  return api.v8.git_output(
    'show',
    '--format=%H',
    '--no-patch',
    f'refs/tags/{tag}',
    name=f'Commit at {tag}',
    ok_ret='any',
  )


def verify_floating_refs(
  api: DEPS, version_at_head, branch_version, chromium_version, build_results
):
  """Update the two floating refs pointing to a valid tip of the release branch.

  The two refs are:
  refs/heads/<branch_version>-lkgr
  refs/heads/chromium/<chromium_version>

  Both refs are kept in sync. On branch-cut day, the V8 branch is prepared
  before the Chromium branch and the Chromium version doesn't exist yet.
  It will be updated as soon as it is provided by the active milestone.
  """
  branch_head = get_commit_for_ref(api, f'refs/tags/{version_at_head}')
  lkgr_ref = f'refs/heads/{branch_version}-lkgr'
  verify_ref(api, 'LKGR', lkgr_ref, branch_head, build_results)
  if chromium_version:
    chromium_ref = f'refs/heads/chromium/{chromium_version}'
    verify_ref(api, 'Chromium', chromium_ref, branch_head, build_results)


def verify_ref(api: DEPS, name, ref, branch_head, build_results):
  with api.step.nest(f'Verify {name}'):
    current_commit = get_commit_for_ref(api, ref)
    api.step(f'{name} commit {current_commit}', [])
    api.step(f'HEAD commit {branch_head}', [])
    if branch_head != current_commit:
      set_ref(api, branch_head, ref, build_results)
    else:
      api.step(f'There is no new {name} ref.', [])


def set_ref(api: DEPS, branch_head, ref, build_results):
  if api.v8.dry_run:
    api.step('Dry-run ref update %s' % branch_head, cmd=None)
  else:
    push_ref(api, REMOTE_REPO_URL, ref, branch_head)
  build_results.performed_actions.append(f'Updated {ref}')


def get_commit_for_ref(api: DEPS, ref):
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


def push_ref(api: DEPS, repo, ref, hsh):
  api.git('push', repo, '+%s:%s' % (hsh, ref))


def maybe_increment_version(
  api: DEPS, source_dir, ref, latest_version, new_version, build_results
):
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

    def is_version_change(commit):
      return commit['subject'] == subject(latest_version) or commit[
        'subject'
      ] == subject(new_version)

    if any(is_version_change(c) for c in commits):
      step_result = api.step('Stale version change CL found!', cmd=None)
      step_result.presentation.status = 'FAILURE'
      build_results.success = False
    else:
      api.v8.update_version_cl(
        source_dir, ref, new_version, push_account=PUSH_ACCOUNT, force_land=True
      )
      build_results.performed_actions.append("Version updated %s" % new_version)


def subject(latest_version):
  return 'Version %s' % latest_version


def GenTests(api: TEST_DEPS):

  def stdout(step_name, text):
    return api.override_step_data(
      step_name, api.raw_io.stream_output_text(text, stream='stdout')
    )

  def chromium_versions(*versions):

    def line(version):
      return f'deadbeef\trefs/heads/chromium/{version}'

    return stdout('git ls-remote', '\n'.join(line(v) for v in versions))

  def milestones(*numbers):
    def ref_config(number):
      return {'ref': f'refs/branch-heads/{5000 + number}'}

    config = dict((str(number), ref_config(number)) for number in numbers)
    return api.override_step_data(
      'fetch milestones', api.gitiles.make_encoded_file(api.json.dumps(config))
    )

  def tracked_branches_count(branches):
    return api.properties(tracked_branches_count=branches)

  def test(name, *test_data, **kwargs):
    return api.test(
      name,
      # If the test case specifies tracked_branches_count, it will override
      # this
      tracked_branches_count(1),
      *test_data,
      **kwargs,
    )

  def version_file(patch_level, description, prefix='', build_level=3):
    return api.v8.version_file(
      patch_level, description, prefix=prefix, major=11, build=build_level
    )

  yield test(
    'branches-to-update-version-for',
    tracked_branches_count(2),
    milestones(111, 112),
    chromium_versions(5111),
    version_file(3, 'branch-heads/11.2', prefix="Checking V8 branch 11.2."),
    version_file(
      4,
      'latest',
      prefix="Checking V8 branch 11.2.Increment version from 11.4.3.3.",
    ),
    version_file(2, 'branch-heads/11.1', prefix="Checking V8 branch 11.1."),
    version_file(
      3,
      'latest',
      prefix="Checking V8 branch 11.1.Increment version from 11.4.3.2.",
    ),
    status='SUCCESS',
  )

  yield test(
    'dry-run-branches-to-update-version-for',
    tracked_branches_count(2),
    api.runtime(is_experimental=True),
    milestones(111, 112),
    chromium_versions(5111),
    version_file(3, 'branch-heads/11.2', prefix="Checking V8 branch 11.2."),
    version_file(
      4,
      'latest',
      prefix="Checking V8 branch 11.2.Increment version from 11.4.3.3.",
    ),
    version_file(2, 'branch-heads/11.1', prefix="Checking V8 branch 11.1."),
    version_file(
      3,
      'latest',
      prefix="Checking V8 branch 11.1.Increment version from 11.4.3.2.",
    ),
    status='SUCCESS',
  )

  yield test(
    'branche-with-stale-version-update',
    milestones(112),
    chromium_versions(5112),
    version_file(3, 'branch-heads/11.2', prefix="Checking V8 branch 11.2."),
    api.override_step_data(
      'Checking V8 branch 11.2.Increment version from 11.4.3.3.gerrit changes',
      api.json.output([{'_number': '123', 'subject': 'Version 11.4.3.3'}]),
    ),
    api.post_process(
      StepFailure,
      'Checking V8 branch 11.2.'
      'Increment version from 11.4.3.3.'
      'Stale version change CL found!',
    ),
    status='FAILURE',
  )

  yield test(
    'branch-with-updated-version-but-no-tag',
    milestones(113),
    chromium_versions(5113),
    version_file(3, 'branch-heads/11.3', prefix="Checking V8 branch 11.3."),
    stdout(
      'Checking V8 branch 11.3.Proof of version change',
      'dummy proof of version change',
    ),
    stdout('Checking V8 branch 11.3.Verify version tag.Commit at HEAD', '123'),
    api.post_process(
      MustRun, 'Checking V8 branch 11.3.Verify version tag.git push'
    ),
    api.post_process(DropExpectation),
    status='SUCCESS',
  )

  yield test(
    'branch-with-correct-tags',
    milestones(113),
    chromium_versions(5113),
    version_file(3, 'branch-heads/11.3', prefix="Checking V8 branch 11.3."),
    stdout(
      'Checking V8 branch 11.3.Proof of version change',
      'dummy proof of version change',
    ),
    stdout(
      'Checking V8 branch 11.3.Verify version tag.Commit at 11.4.3.3', '123'
    ),
    stdout(
      'Checking V8 branch 11.3.Verify pgo tag.Commit at 11.4.3.3-pgo', '123'
    ),
    stdout('Checking V8 branch 11.3.Verify version tag.Commit at HEAD', '123'),
    stdout(
      'Checking V8 branch 11.3.Verify LKGR.git ls-remote refs_heads_11.3-lkgr',
      '112233',
    ),
    stdout(
      'Checking V8 branch 11.3.git ls-remote refs_tags_11.4.3.3', '112233'
    ),
    api.post_process(
      DoesNotRunRE, 'Checking V8 branch 11.3.Verify version tag.git tag'
    ),
    api.post_process(
      MustRun, 'Checking V8 branch 11.3.Verify LKGR.There is no new LKGR ref.'
    ),
    status='SUCCESS',
  )

  yield test(
    'lkgr-branch',
    milestones(113),
    chromium_versions(5113),
    version_file(3, 'branch-heads/11.3', prefix="Checking V8 branch 11.3."),
    stdout(
      'Checking V8 branch 11.3.Proof of version change',
      'dummy proof of version change',
    ),
    stdout(
      'Checking V8 branch 11.3.Verify version tag.Commit at 11.4.3.3', '123'
    ),
    stdout(
      'Checking V8 branch 11.3.Verify pgo tag.Commit at 11.4.3.3-pgo', '123'
    ),
    stdout('Checking V8 branch 11.3.Verify version tag.Commit at HEAD', '123'),
    stdout(
      'Checking V8 branch 11.3.Verify LKGR.git ls-remote refs_heads_11.3-lkgr',
      'faceb00c',
    ),
    stdout(
      'Checking V8 branch 11.3.Verify Chromium.'
      'git ls-remote refs_heads_chromium_5113',
      'deadbeef',
    ),
    stdout('Checking V8 branch 11.3.git ls-remote refs_tags_11.4.3.3', '404'),
    api.post_process(MustRun, 'Checking V8 branch 11.3.Verify LKGR.git push'),
    api.post_process(
      MustRun, 'Checking V8 branch 11.3.Verify Chromium.git push'
    ),
    api.post_process(DropExpectation),
    status='SUCCESS',
  )

  yield test(
    'branch-on-branch-cut-day',
    # Active milestone is 111, but on branch-cut day 112 is prepared on
    # the V8 side.
    milestones(111),
    chromium_versions(5111),
    stdout('last branches', 'branch-heads/11.1\nbranch-heads/11.2'),
    # Simulate processing 112 with all data except the Chromium ref,
    # which doesn't exist yet.
    version_file(2, 'branch-heads/11.2', prefix="Checking V8 branch 11.2."),
    stdout(
      'Checking V8 branch 11.2.Proof of version change',
      'dummy proof of version change',
    ),
    stdout(
      'Checking V8 branch 11.2.Verify version tag.Commit at 11.4.3.2', '123'
    ),
    stdout(
      'Checking V8 branch 11.2.Verify pgo tag.Commit at 11.4.3.2-pgo', '123'
    ),
    stdout('Checking V8 branch 11.2.Verify version tag.Commit at HEAD', '123'),
    version_file(1, 'branch-heads/11.1', prefix="Checking V8 branch 11.1."),
    api.post_process(MustRun, 'Checking V8 branch 11.2.Verify LKGR'),
    api.post_process(DoesNotRunRE, 'Checking V8 branch 11.2.Verify Chromium.*'),
    # Simulate processing 111, where all data including Chromium ref exists.
    stdout(
      'Checking V8 branch 11.1.Proof of version change',
      'dummy proof of version change',
    ),
    stdout(
      'Checking V8 branch 11.1.Verify version tag.Commit at 11.4.3.1', '121'
    ),
    stdout(
      'Checking V8 branch 11.1.Verify pgo tag.Commit at 11.4.3.1-pgo', '121'
    ),
    stdout('Checking V8 branch 11.1.Verify version tag.Commit at HEAD', '121'),
    api.post_process(MustRun, 'Checking V8 branch 11.1.Verify LKGR'),
    api.post_process(MustRun, 'Checking V8 branch 11.1.Verify Chromium'),
    api.post_process(DropExpectation),
    status='SUCCESS',
  )

  yield test(
    'no-pgo-profiles',
    milestones(113),
    chromium_versions(5113),
    version_file(3, 'branch-heads/11.3', prefix="Checking V8 branch 11.3."),
    stdout(
      'Checking V8 branch 11.3.Proof of version change',
      'dummy proof of version change',
    ),
    stdout(
      'Checking V8 branch 11.3.Verify version tag.Commit at 11.4.3.3', '123'
    ),
    stdout('Checking V8 branch 11.3.Verify version tag.Commit at HEAD', '123'),
    api.post_process(
      DoesNotRunRE,
      '.*Verify LKGR.*',
    ),
    api.post_process(
      DoesNotRunRE,
      '.*Verify Chromium.*',
    ),
    api.post_process(DropExpectation),
    status='SUCCESS',
  )

  yield test(
    'dry-run-branch-no-tag-no-lkgr',
    api.runtime(is_experimental=True),
    milestones(113),
    chromium_versions(5113),
    stdout(
      'Checking V8 branch 11.3.Proof of version change',
      'dummy proof of version change',
    ),
    stdout('Checking V8 branch 11.3.Verify version tag.Commit at HEAD', '123'),
    stdout(
      'Checking V8 branch 11.3.Verify pgo tag.Commit at 11.4.3.3-pgo', '123'
    ),
    version_file(3, 'branch-heads/11.3', prefix="Checking V8 branch 11.3."),
    stdout(
      'Checking V8 branch 11.3.Verify LKGR.git ls-remote refs_heads_11.3-lkgr',
      '3e1a',
    ),
    stdout(
      'Checking V8 branch 11.3.Verify Chromium.'
      'git ls-remote refs_heads_chromium_5113',
      '3e1a',
    ),
    stdout('Checking V8 branch 11.3.git ls-remote refs_tags_11.4.3.3', '404'),
    api.post_process(
      MustRun, 'Checking V8 branch 11.3.Verify LKGR.Dry-run ref update 404'
    ),
    api.post_process(
      MustRun, 'Checking V8 branch 11.3.Verify Chromium.Dry-run ref update 404'
    ),
    status='SUCCESS',
  )

  yield test(
    'chromium-branches-to-update-version-for',
    tracked_branches_count(1),
    milestones(112),
    chromium_versions("5112", "5112_42", "5113", "5114"),
    # Data for mini branch.
    version_file(
      3,
      'remotes/origin/chromium/5112_42',
      prefix="Checking Chromium branch 5112_42.",
    ),
    version_file(
      3,
      'latest',
      prefix="Checking Chromium branch 5112_42.Increment version from 11.4.3.3.",
    ),
    # Data for canary/dev branch.
    version_file(
      3,
      'remotes/origin/chromium/5113',
      prefix="Checking Chromium branch 5113.",
    ),
    version_file(
      3,
      'latest',
      prefix="Checking Chromium branch 5113.Increment version from 11.4.3.3.",
    ),
    # Like above, but with an existing version change on the branch.
    version_file(
      0,
      'remotes/origin/chromium/5114',
      prefix="Checking Chromium branch 5114.",
      build_level=511403,
    ),
    version_file(
      0,
      'latest',
      prefix="Checking Chromium branch 5114.Increment version from 11.4.511403.",
      build_level=511403,
    ),
    # Dummy data for the V8 branch check, since there's always one V8 branch.
    version_file(2, 'branch-heads/11.2', prefix="Checking V8 branch 11.2."),
    version_file(
      3,
      'latest',
      prefix="Checking V8 branch 11.2.Increment version from 11.4.3.2.",
    ),
    status='SUCCESS',
  )

  yield test(
    'chromium-branch-with-updated-version-but-no-tag',
    tracked_branches_count(1),
    milestones(112),
    chromium_versions("5112", "5112_42", "5113"),
    version_file(
      3,
      'remotes/origin/chromium/5112_42',
      prefix="Checking Chromium branch 5112_42.",
    ),
    stdout(
      'Checking Chromium branch 5112_42.Proof of version change',
      'dummy proof of version change',
    ),
    stdout(
      'Checking Chromium branch 5112_42.Verify version tag.Commit at HEAD',
      '123',
    ),
    version_file(
      3, 'remotes/origin/chromium/5113', prefix="Checking Chromium branch 5113."
    ),
    stdout(
      'Checking Chromium branch 5113.Proof of version change',
      'dummy proof of version change',
    ),
    stdout(
      'Checking Chromium branch 5113.Verify version tag.Commit at HEAD', '123'
    ),
    # Dummy data for the V8 branch check, since there's always one V8 branch.
    version_file(2, 'branch-heads/11.2', prefix="Checking V8 branch 11.2."),
    version_file(
      3,
      'latest',
      prefix="Checking V8 branch 11.2.Increment version from 11.4.3.2.",
    ),
    api.post_process(
      MustRun, 'Checking Chromium branch 5113.Verify version tag.git push'
    ),
    api.post_process(DropExpectation),
    status='SUCCESS',
  )

  yield test(
    'chromium-branch-with-correct-tags',
    tracked_branches_count(1),
    milestones(112),
    chromium_versions("5112", "5112_42", "5113"),
    version_file(
      3,
      'remotes/origin/chromium/5112_42',
      prefix="Checking Chromium branch 5112_42.",
    ),
    stdout(
      'Checking Chromium branch 5112_42.Proof of version change',
      'dummy proof of version change',
    ),
    stdout(
      'Checking Chromium branch 5112_42.Verify version tag.Commit at 11.4.3.3',
      '123',
    ),
    stdout(
      'Checking Chromium branch 5112_42.Verify version tag.Commit at HEAD',
      '123',
    ),
    version_file(
      3, 'remotes/origin/chromium/5113', prefix="Checking Chromium branch 5113."
    ),
    stdout(
      'Checking Chromium branch 5113.Proof of version change',
      'dummy proof of version change',
    ),
    stdout(
      'Checking Chromium branch 5113.Verify version tag.Commit at 11.4.3.3',
      '123',
    ),
    stdout(
      'Checking Chromium branch 5113.Verify version tag.Commit at HEAD', '123'
    ),
    # Dummy data for the V8 branch check, since there's always one V8 branch.
    version_file(2, 'branch-heads/11.2', prefix="Checking V8 branch 11.2."),
    version_file(
      3,
      'latest',
      prefix="Checking V8 branch 11.2.Increment version from 11.4.3.2.",
    ),
    api.post_process(
      DoesNotRunRE, 'Checking Chromium branch 5113.Verify version tag.git push'
    ),
    api.post_process(DropExpectation),
    status='SUCCESS',
  )
