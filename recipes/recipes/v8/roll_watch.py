# Copyright 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from PB.go.chromium.org.luci.buildbucket.proto.builds_service import (
        BuildPredicate)
from PB.go.chromium.org.luci.buildbucket.proto.build import Build
from PB.go.chromium.org.luci.buildbucket.proto.builder_common import BuilderID
from PB.go.chromium.org.luci.buildbucket.proto.common import (
    FAILURE, INFRA_FAILURE, SCHEDULED, STARTED, SUCCESS,
    GerritChange, StringPair,
)
from PB.go.chromium.org.luci.buildbucket.proto.step import Step
from PB.recipe_engine.result import RawResult

from recipe_engine.recipe_api import Property
from recipe_engine.post_process import (DropExpectation, StepSuccess,
                                        StepFailure)


DEPS = [
    'depot_tools/depot_tools',
    'depot_tools/gerrit',
    'depot_tools/git',
    'depot_tools/gsutil',

    'recipe_engine/buildbucket',
    'recipe_engine/context',
    'recipe_engine/file',
    'recipe_engine/json',
    'recipe_engine/path',
    'recipe_engine/properties',
    'recipe_engine/raw_io',
    'recipe_engine/step',
]


PROPERTIES = {
    'watched_rollers': Property(
            help='Roller descriptors',
            default=[],
            kind=list
    )
}


def RunSteps(api, watched_rollers):
  with api.depot_tools.on_path():
    for roller in watched_rollers:
      with api.step.nest("Roller: '{}'".format(roller['name'])):
        process_roller(api, roller)
  if any(roller.get('has_failure', False) for roller in watched_rollers):
    return RawResult(status=FAILURE)


def process_roller(api, roller):
  open_cls = find_open_CLs(api, roller)
  for cl in open_cls:
    if verify_subject(roller, cl):
      process_cl(api, roller, cl)


def find_open_CLs(api, roller):
  return api.gerrit.get_changes(
      'https://{}'.format(roller['review-host']),
      query_params=[
          ('project', roller['project'] ),
          ('owner', roller['account']),
          ('status', 'open'),
          ('-hashtag', 'rw_reported'),
      ] + additional_criteria(roller),
      o_params = ['LABELS', 'CURRENT_REVISION', 'DOWNLOAD_COMMANDS'],
      limit=20,
      step_test_data=api.gerrit.test_api.get_empty_changes_response_data,
      name='Find open CLs',
  )


def verify_subject(roller, cl):
  return ('subject' not in roller) or (roller['subject'] == cl['subject'])


def additional_criteria(roller):
  return [term.split(':') for term in roller.get('criteria',[])]


def process_cl(api, roller, cl):
  with api.step.nest('Checking CL {}'.format(cl['_number'])) as parent_step:
    present_cl_link(roller, cl, parent_step.presentation)
    builds = find_all_builds(api, roller, cl)
    cl['builds'] = builds
    blocking_builds = find_cq_blocking_builds(cl)
    if any(has_failed(build) for build in blocking_builds):
      run_failure_recovery(api, roller, cl)
    else:
      api.step('No CQ failures on this CL yet...', [])


def find_cq_blocking_builds(cl):
  return [build for build in cl['builds'] if is_cq_build(build)]


def is_cq_build(build):
  return any(tag.key == 'cq_experimental' and tag.value == 'false'
          for tag in build.tags)


def find_all_builds(api, roller, cl):
  return api.buildbucket.search(
      BuildPredicate(
          gerrit_changes=[{
              'host' : roller['review-host'],
              'change' : cl['_number'],
              'patchset' : last_patch(cl)['_number'],
              'project': roller['project'],
          }],
          include_experimental=True,
      ),
      limit=100,
      fields=['tags.*,steps.*'],
      report_build = False,
  )

def present_cl_link(roller, cl, presentation):
  cl_path = '{}/+/{}'.format(cl['project'], cl['_number'])
  presentation.links[cl_path] = 'https://{}/c/{}'.format(
          roller['review-host'],
          cl_path,
  )


def last_patch(cl):
  return list(cl['revisions'].values())[0]


def has_failed(build):
  return build.status in [FAILURE, INFRA_FAILURE]


def run_failure_recovery(api, roller, cl):
  default_recovery = ['mark_as_reported','just_fail']
  for recovery_fn_name in roller.get('failure_recovery', default_recovery):
    recovery_fn = find_recovery_fn(recovery_fn_name)
    recovery_fn(api, roller, cl)


def find_recovery_fn(name):
  return {
      'just_fail': just_fail,
      'just_pass': just_pass,
      'mark_as_reported': mark_as_reported,
      'try_update_screenshots': try_update_screenshots,
      'apply_screenshot_patches': apply_screenshot_patches,
  }[name]


def tag_cl(api, roller, cl, tag_name, step_name):
  api.gerrit.call_raw_api('https://{}'.format(roller['review-host']),
              '/changes/{}/hashtags'.format(cl['_number']),
              method='POST',
              body={"add": [tag_name]},
              accept_statuses=[200, 201],
              name=step_name)


#Generic recovery functions {


def just_fail(api, roller, cl, title=None, message=None):
  title = title or "Roller '{}' failed".format(roller['name'])
  failure_step = api.step(title, cmd=None)
  if message:
    failure_step.presentation.step_text = message
  failure_step.presentation.status = api.step.FAILURE
  present_cl_link(roller, cl, failure_step.presentation)
  roller['has_failure'] = True


def just_pass(api, roller, cl):
  pass


def mark_as_reported(api, roller, cl):
  tag_cl(api, roller, cl, "rw_reported", 'Mark as reported')


#} Generic recovery functions


#Devtools screenshots recovery functions {


def try_update_screenshots(api, roller, cl):
  if screenshot_builders_triggered(roller, cl):
    apply_screenshot_patches(api, roller, cl)
  else:
    trigger_screenshot_builders(api, roller, cl)


def screenshot_builders_triggered(roller, cl):
  return roller['screenshot_builders_triggered_tag'] in cl['hashtags']


def apply_screenshot_patches(api, roller, cl):
  with api.step.nest('Apply screenshot patches'):
    if is_unable_to_apply(api, roller, cl):
      return
    work_dir = api.path.mkdtemp()
    with api.context(cwd=work_dir):
      prepare_local_checkout(api, cl)
      patches_found = [
          apply_patch_from_screenshot_builder(api, roller, builder, cl)
          for builder in roller['screenshot_builders']
      ]
      outcome_title = None
      outcome_message = 'No screenshot patches available'
      if any(patches_found):
        git_output(api, 'commit', '-am', 'update screenshots')
        git_output(api, 'cl', 'upload',
                '-f', '--bypass-hooks', '--cq-dry-run')
        tag_cl(api, roller, cl, roller['screenshots_applied_tag'],
            'Mark CL as patched with new screenshots')
        outcome_title = 'CL needs review'
        outcome_message = 'Please review screenshot patch!'
      just_fail(api, roller, cl,
              title=outcome_title,
              message=outcome_message)


def is_unable_to_apply(api, roller, cl):
  screenshot_builds = [build for build in cl['builds']
      if build.builder.builder in roller['screenshot_builders']]
  if not(screenshot_builds):
    api.step('No screenshot builds found', [])
    return True
  if any(is_in_progress(build) for build in screenshot_builds):
    api.step('Builders still in progress...', [])
    return True
  if any(has_failed(build) for build in screenshot_builds):
    just_fail(api, roller, cl,
        message='Unable to apply due to failed builder!')
    return True
  return False


def is_in_progress(build):
  return build.status in [STARTED, SCHEDULED]


def prepare_local_checkout(api, cl):
  with api.step.nest('Prepare local checkout'):
    fetch_info = last_patch(cl)['fetch']['http']
    git_output(api, 'clone', fetch_info['url'], '.')
    git_output(api, 'fetch', fetch_info['url'], fetch_info['ref'])
    git_output(api, 'checkout', '-b', 'work-branch', 'FETCH_HEAD')
    git_output(api, 'cl', 'issue', cl['_number'])


def apply_patch_from_screenshot_builder(api, roller, builder, cl):
  with api.step.nest('Apply screenshot patch from {}'.format(builder)):
    patch_dir = api.path.mkdtemp()
    gs_path = [ 'screenshots',
            builder,
            str(cl['_number']),
            str(last_patch(cl)['_number']),
            'screenshot.patch']
    gs_location = '/'.join(gs_path)
    patch_platform = builder.split('_')[-2]
    local_path = patch_dir.join(patch_platform + '.patch')
    api.gsutil.download(
            roller.get('gs_bucket', 'devtools-internal-screenshots'),
            gs_location,
            local_path,
            name='Download patch from {}'.format(builder))
    if api.file.read_text('read patch for {}'.format(patch_platform),
            local_path):
      git_output(api, 'apply', local_path)
      return True
    api.step('Empty patch', [])
    return False

def trigger_screenshot_builders(api, roller, cl):
  if any(is_in_progress(build) for build in cl['builds']):
    api.step('Builders still in progress...', [])
    return
  blocking_builds = find_cq_blocking_builds(cl)
  if any(has_mixed_failures(build, roller, api) for build in blocking_builds):
    api.step('Some failures do not refer to screenshots...', [])
    tag_cl(api, roller, cl, roller['screenshots_unavailable_tag'],
        'Tag CL for no screenshots patch available')
    just_fail(api, roller, cl, 'Failure cause is other than screenshots')
    return
  api.buildbucket.schedule([
          api.buildbucket.schedule_request(
              project=roller['project'].split('/')[1],
              bucket='try',
              builder=builder_name,
              gerrit_changes=[GerritChange(
                  host=roller['review-host'],
                  project=cl['project'],
                  change=cl['_number'],
                  patchset=list(cl['revisions'].values())[0]['_number'],
              )],
          ) for builder_name in roller['screenshot_builders']
      ],
      step_name='Trigger screenshots builders',
  )
  tag_cl(api, roller, cl, roller['screenshot_builders_triggered_tag'],
          'Tag CL for later screenshots retrieval')


def has_mixed_failures(build, roller, api):
  allowed_failure_steps = roller['allowed_failure_steps']
  return any(
      step.name not in allowed_failure_steps
      for step in build.steps
      if step.status == FAILURE
  )


def git_output(api, *args, **kwargs):
  """Convenience wrapper."""
  step_result = api.git(*args, stdout=api.raw_io.output_text(), **kwargs)
  output = step_result.stdout
  step_result.presentation.logs['stdout'] = output.splitlines()
  return output.strip()


#} Devtools screenshots recovery functions

def GenTests(api):
  default_roller = {
          'name' : 'roller',
          'subject': 'Update dependencies',
          'review-host': 'review.googlesource.com',
          'project': 'v8/v8',
          'account': 'autoroll@service-accounts.com',
  }
  screenshot_roller = {
      'name': 'experiment',
      'subject': 'Break something',
      "review-host": "chrome-internal-review.googlesource.com",
      "project": "devtools/devtools-internal",
      "account": "liviurau@google.com",
      "criteria": ["-hashtag:screenshots_applied"],
      "failure_recovery": ["try_update_screenshots"],
      "screenshot_builders": ["devtools_screenshot_linux_rel"],
      "screenshot_builders_triggered_tag": "screenshot_builders_triggered",
      "screenshots_applied_tag": "screenshots_applied",
      "screenshots_unavailable_tag": "screenshots_unavailable",
      "allowed_failure_steps": "Interactions",
  }


  def roller(roller, **kwargs):
    config = dict(roller)
    config.update(**kwargs)
    return api.properties(watched_rollers=[config])


  def find_fake_cls(roller, **kwargs):
    cl_data = {
        '_number': 123,
        'subject': roller['subject'],
        'project': 'project1',
        'revisions': {
            'last': {
                '_number': 1,
                'fetch': {
                    'http': {
                        'url': '$git-url',
                        'ref': '$ref',
                    }
                }
            },

        },
        'hashtags': []
    }
    cl_data.update(**kwargs)
    return api.override_step_data("Roller: '{}'."
            'gerrit Find open CLs'.format(roller['name']),
            api.json.output([cl_data]))


  def find_fake_builds(roller, *builds):
    return api.buildbucket.simulated_search_results(list(builds),
        step_name = "Roller: '{}'.Checking CL 123."
                "buildbucket.search".format(roller['name']))


  def build(build_id, status, builder_name=None, experimental=False,
          steps=None):
    exp_tag = StringPair(key='cq_experimental',
            value='true' if experimental else 'false')
    return Build(id=build_id, status=status,
            builder=BuilderID(builder=builder_name),
            tags=[exp_tag], steps=steps)


  yield api.test(
      "no-cls",
      roller(default_roller),
  )

  yield api.test(
      "roller-with-stale-cls",
      roller(default_roller),
      find_fake_cls(default_roller),
  )

  yield api.test(
      "roll-failing-in-cq-default",
      roller(default_roller),
      find_fake_cls(default_roller),
      find_fake_builds(
          default_roller,
          build(1, SUCCESS),
          build(2, FAILURE),
      ),
      api.expect_status('FAILURE'),
  )

  yield api.test(
      "roll-failing-in-cq-just-pass",
      roller(
          default_roller,
          criteria=["hashtags:sometag"],
          failure_recovery=["just_pass"]),
      find_fake_cls(default_roller),
      find_fake_builds(
          default_roller,
          build(1, SUCCESS),
          build(2, FAILURE),
      ),
  )

  yield api.test(
      "sc-trigger-builders",
      roller(screenshot_roller),
      find_fake_cls(screenshot_roller),
      find_fake_builds(
          screenshot_roller,
          build(1, SUCCESS),
          build(2, FAILURE),
      ),
  )

  yield api.test(
      "sc-no-trigger-builders-in-progress",
      roller(screenshot_roller),
      find_fake_cls(screenshot_roller),
      find_fake_builds(
          screenshot_roller,
          build(1, STARTED, experimental=True),
          build(2, FAILURE),
      ),
      api.post_process(
          StepSuccess, "Roller: 'experiment'.Checking CL 123."
          "Builders still in progress..."),
      api.post_process(DropExpectation),
  )

  yield api.test(
      "sc-no-trigger-wrong-step",
      roller(screenshot_roller),
      find_fake_cls(screenshot_roller),
      find_fake_builds(
          screenshot_roller,
          build(1, FAILURE, steps=[Step(name='Interactions', status=FAILURE)]),
          build(2, FAILURE, steps=[Step(name='Test', status=FAILURE)]),
      ),
      api.post_process(
          StepSuccess, "Roller: 'experiment'.Checking CL 123."
          "Some failures do not refer to screenshots..."),
      api.post_process(
          StepSuccess, "Roller: 'experiment'.Checking CL 123."
          "gerrit Tag CL for no screenshots patch available"),
      api.post_process(DropExpectation),
      api.expect_status('FAILURE'),
  )

  yield api.test(
      "sc-update-screenshots",
      roller(screenshot_roller),
      find_fake_cls(
          screenshot_roller, hashtags=["screenshot_builders_triggered"]),
      find_fake_builds(
          screenshot_roller,
          build(1, FAILURE),
          build(2, SUCCESS, builder_name="devtools_screenshot_linux_rel"),
      ),
      api.override_step_data(
          "Roller: 'experiment'.Checking CL 123.Apply screenshot patches."
          "Apply screenshot patch from devtools_screenshot_linux_rel."
          "read patch for linux",
          api.file.read_text('patch contents'),
      ),
      api.expect_status('FAILURE'),
  )

  yield api.test(
      "sc-no-screenshot-builders",
      roller(screenshot_roller),
      find_fake_cls(
          screenshot_roller, hashtags=["screenshot_builders_triggered"]),
      find_fake_builds(
          screenshot_roller,
          build(1, FAILURE),
      ),
      api.post_process(
          StepSuccess, "Roller: 'experiment'.Checking CL 123."
          "Apply screenshot patches.No screenshot builds found"),
      api.post_process(DropExpectation),
  )

  yield api.test(
      "sc-no-screenshot-patches",
      roller(screenshot_roller),
      find_fake_cls(
          screenshot_roller, hashtags=["screenshot_builders_triggered"]),
      find_fake_builds(
          screenshot_roller, build(1, FAILURE),
          build(2, SUCCESS, builder_name="devtools_screenshot_linux_rel")),
      api.post_process(
          StepSuccess, "Roller: 'experiment'.Checking CL 123."
          "Apply screenshot patches.Apply screenshot patch from "
          "devtools_screenshot_linux_rel.Empty patch"),
      api.post_process(
          StepFailure, "Roller: 'experiment'.Checking CL 123."
          "Apply screenshot patches.Roller 'experiment' failed"),
      api.post_process(DropExpectation),
      api.expect_status('FAILURE'),
  )

  yield api.test(
      "sc-no-update-builders-in-progress",
      roller(screenshot_roller),
      find_fake_cls(
          screenshot_roller, hashtags=["screenshot_builders_triggered"]),
      find_fake_builds(
          screenshot_roller, build(1, FAILURE),
          build(
              2,
              STARTED,
              experimental=True,
              builder_name="devtools_screenshot_linux_rel")),
      api.post_process(
          StepSuccess, "Roller: 'experiment'.Checking CL 123."
          "Apply screenshot patches.Builders still in progress..."),
      api.post_process(DropExpectation),
  )

  yield api.test(
      "sc-no-update-builders-failed",
      roller(screenshot_roller),
      find_fake_cls(
          screenshot_roller, hashtags=["screenshot_builders_triggered"]),
      find_fake_builds(
          screenshot_roller, build(1, FAILURE),
          build(2, FAILURE, builder_name="devtools_screenshot_linux_rel")),
      api.post_process(
          StepFailure, "Roller: 'experiment'.Checking CL 123."
          "Apply screenshot patches."
          "Roller 'experiment' failed"),
      api.post_process(DropExpectation),
      api.expect_status('FAILURE'),
  )
