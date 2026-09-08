# Copyright 2025 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Trigger Pinpoint try jobs as performance tests"""

import json

from recipe_engine import post_process
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb2
from PB.recipes.build.pinpoint import perf_try_job
from PB.recipe_engine import result as result_pb2

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import chromium, chromium_checkout, chromium_tests_builder_config
from RECIPE_MODULES.depot_tools import gclient, gerrit, tryserver
from RECIPE_MODULES.recipe_engine import (
    buildbucket,
    cv,
    file,
    json as json_module,
    properties,
    raw_io,
    service_account,
    step,
)


@dataclass
class DEPS(RecipeScriptApi):
  buildbucket: buildbucket.API
  chromium: chromium.API
  chromium_checkout: chromium_checkout.API
  chromium_tests_builder_config: chromium_tests_builder_config.API
  cv: cv.API
  file: file.API
  gclient: gclient.API
  gerrit: gerrit.API
  json: json_module.API
  properties: properties.API
  raw_io: raw_io.API
  service_account: service_account.API
  step: step.API
  tryserver: tryserver.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  buildbucket: buildbucket.TEST_API
  chromium: chromium.TEST_API
  file: file.TEST_API
  gerrit: gerrit.TEST_API
  json: json_module.TEST_API
  properties: properties.TEST_API
  raw_io: raw_io.TEST_API
  step: step.TEST_API
  tryserver: tryserver.TEST_API

PROPERTIES = perf_try_job.InputProperties
_CQ_TRIGGER_THRESHOLD = 3
_STATIC_MAP_FILE_NAME = 'sp3_static_map.json'


def _generate_try_job_name(api: DEPS, properties):
  benchmark = properties.benchmark
  story = properties.story
  project = api.tryserver.gerrit_change_repo_project
  change = api.tryserver.gerrit_change.change
  patchset = api.tryserver.gerrit_change.patchset

  return f'CQ Perf on {benchmark}/{story} at {project}:{change}/{patchset}'


# convert a list of key-value pairs in tags into a json string.
# e.g. [{'key':'k','value':'v'},{'key':'kk','value':'vv'}]
#        => '{"k":"v", "kk":"vv"}'
def _generate_tag_string(api: DEPS, tags):
  # The default tags to indicate the job source.
  tags_dict = {
      'origin': 'CQ',
      'change': str(api.tryserver.gerrit_change.change),
      'patchset': str(api.tryserver.gerrit_patchset_number)
  }
  for tag in tags:
    tags_dict[tag.key] = tag.value
  return json.dumps(tags_dict)


def _generate_access_token(api: DEPS):
  return api.service_account.default().get_access_token()


def _get_current_change_from_gerrit(api: DEPS):
  change_id = str(api.tryserver.gerrit_change.change)
  patchset_id = api.tryserver.gerrit_patchset_number
  changes = api.gerrit.get_changes(
      'https://%s' % api.tryserver.gerrit_change.host,
      query_params=[('change', change_id)],
      o_params=[
          'DOWNLOAD_COMMANDS',
          'DETAILED_ACCOUNTS',
          'ALL_FILES',
          'CURRENT_REVISION',
      ],
      name=f'loads current patchset ({patchset_id}) from change {change_id}',
      limit=1)
  if len(changes) == 0:
    return None

  return changes[0]


def _get_user(api: DEPS, gerrit_change):
  user = gerrit_change.get('owner', {}).get('email')
  if not user:
    change = api.tryserver.gerrit_change_number
    patchset = api.tryserver.gerrit_patchset_number
    user = f'Change {change}/{patchset}'
  return user


def _ask_gemini(api: DEPS):
  ask_gemini = api.properties.get('ask_gemini')
  # Remove API KEY after switching to keyless authentication.
  gemini_api_key = api.properties.get('gemini_api_key')
  if ask_gemini and gemini_api_key:
    cmd = [
        'vpython3',
        api.resource('ask_gemini.py'),
        '--gemini_api_key',
        gemini_api_key,
    ]
    api.step('Use Gemini to analyze changes', cmd, infra_step=True)
    return
  api.step.empty('Skip Gemini!')


def _should_run_perf_on_cq(file_names, static_map, benchmark, story):
  for file in file_names:
    affected_benchmakrs = static_map.get(file, {})
    if affected_benchmakrs:
      affected_story_count = affected_benchmakrs.get(benchmark,
                                                     {}).get(story, 0)
      if affected_story_count >= _CQ_TRIGGER_THRESHOLD:
        return True
  return False


def _load_static_map(api: DEPS):
  mock_data = api.json.dumps({
      'abc': {
          'fake-benchmark': {
              'fake-story': 3
          },
          'fake-benchmark-less': {
              'fake-story-less': 1
          }
      }
  })
  static_map_path = api.resource(_STATIC_MAP_FILE_NAME)
  static_map_content = api.file.read_text(
      'Read JSON File', static_map_path, test_data=mock_data)
  data = api.json.loads(static_map_content)

  return data


def _generate_invoker_cmd(api: DEPS, properties, gerrit_change):
  cmd = ['vpython3', api.resource('pinpoint_try_job_invoker.py')]

  cmd.extend(['--name', _generate_try_job_name(api, properties)])
  cmd.extend(['--base_git_hash', api.buildbucket.gitiles_commit.id or 'HEAD'])
  cmd.extend(['--patch', api.tryserver.gerrit_change_review_url])
  cmd.extend(['--configuration', properties.configuration])
  cmd.extend(['--benchmark', properties.benchmark])
  cmd.extend(['--story', properties.story])
  if properties.attempts_count:
    cmd.extend(['--attempts_count', str(properties.attempts_count)])
  cmd.extend(['--user', _get_user(api, gerrit_change)])
  cmd.extend(['--tags', _generate_tag_string(api, properties.tags)])
  token = _generate_access_token(api)
  cmd.extend(['--token', api.raw_io.input_text(token)])
  if properties.use_staging:
    cmd.extend(['--use_staging'])

  return cmd


def _generate_poller_cmd(api: DEPS, properties, job_id):
  cmd = ['vpython3', api.resource('pinpoint_try_job_poller.py')]

  cmd.extend(['--job_id', job_id])
  token = _generate_access_token(api)
  cmd.extend(['--token', api.raw_io.input_text(token)])
  if properties.use_staging:
    cmd.extend(['--use_staging'])

  return cmd


def _generate_cabe_analysis_cmd(api: DEPS, job_id):
  cmd = ['vpython3', api.resource('cabe_analysis_getter.py')]

  cmd.extend(['--job_id', job_id])
  token = _generate_access_token(api)
  cmd.extend(['--token', api.raw_io.input_text(token)])

  return cmd


def RunSteps(api: DEPS, properties):
  api.tryserver.require_is_tryserver()
  with api.chromium.chromium_layout():
    gerrit_change = _get_current_change_from_gerrit(api)
    if gerrit_change is None:
      return result_pb2.RawResult(
          status=common_pb2.FAILURE,
          summary_markdown=('Failed to fetch gerrit changes.'))

    revs = gerrit_change.get('revisions', {})
    files = {}
    for _, detail in revs.items():
      files = detail.get('files', {}).keys()
      break
    if len(files) == 0:
      return result_pb2.RawResult(
          status=common_pb2.SUCCESS,
          summary_markdown=('Gerrit result shows no file is changed.'))

    # 0. Experimenting with Gemini to predict and log performance regressions.
    _ask_gemini(api)

    # 1. Use static map to decide whether we need to trigger the try job.
    static_map = _load_static_map(api)
    if not _should_run_perf_on_cq(
        file_names=files,
        static_map=static_map,
        benchmark=properties.benchmark,
        story=properties.story):
      return result_pb2.RawResult(
          status=common_pb2.SUCCESS,
          summary_markdown=(
              'No benchmark is likely to be affected by the files in the CL.'))

    # 2. Trigger a Pinpoint pairwise try job
    #    Expect the invoke command to return:
    #    {
    #      request_url: the url sent to pinpoint.
    #      params: the params used to create the url.
    #      ressponse: the pinpoint response json with 'jobId' and 'jobUrl'.
    #    }
    cmd = _generate_invoker_cmd(api, properties, gerrit_change)

    invoke_cmd_result = api.step(
        f'Trigger Performance Test {properties.benchmark}',
        cmd,
        stdout=api.json.output())

    if not invoke_cmd_result.stdout:
      return result_pb2.RawResult(
          status=common_pb2.FAILURE,
          summary_markdown=('Failed to trigger Pinpoint try job.'))

    # 3. Print the Pinpoint try job url.
    job_id = invoke_cmd_result.stdout.get('response', {}).get('jobId')
    url = invoke_cmd_result.stdout.get('response', {}).get('jobUrl')
    report_step_result = api.step('Try job triggered.', None)
    report_step_result.presentation.links['Pinpoint job'] = url

    # 4. Trigger poller script to wait for Pinpoint try job to finish,
    #    Expect the poller to return:
    #    {
    #      job_id: the pinpoint job id
    #      status: the job status, which can be one of:
    #       [Queued, Running, Completed, Failed, Cancelled]
    #      error: the error message
    #    }
    #    Error field will present when Pinpoint cannot finish as expected,
    #    e.g., service is down, or it takes expected long.
    poller_cmd = _generate_poller_cmd(api, properties, job_id)
    poller_cmd_result = api.step(
        f'Waiting Pinpoint job {job_id} to finish. (Timeout max 2hr)',
        poller_cmd,
        timeout=7300,  # the script itself should timeout in 7200 (2 hr)
        stdout=api.json.output())
    if not poller_cmd_result.stdout:
      return result_pb2.RawResult(
          status=common_pb2.FAILURE,
          summary_markdown=('Failed to get Pinpoint job status.'))
    status = poller_cmd_result.stdout.get('status', 'Unknown')
    if poller_cmd_result.stdout.get('status') != 'Completed':
      return result_pb2.RawResult(
          status=common_pb2.FAILURE,
          summary_markdown=(f'Pinpoint job did not finish. Status: {status}'))
    api.step('Pinpoint try job finished.', None)

    # 5. Send get analysis request to CABE by the job id.
    cabe_cmd = _generate_cabe_analysis_cmd(api, job_id)
    cabe_cmd_result = api.step(
        'Getting CABE analysis results.',
        cabe_cmd,
        timeout=300,
        stdout=api.json.output())
    if cabe_cmd_result.stdout is None:
      return result_pb2.RawResult(
          status=common_pb2.FAILURE,
          summary_markdown=('Failed to get CABE analysis results.'))
    api.step('CABE analysis finished.', None)
    regressions = cabe_cmd_result.stdout.get('regressions', {})
    if len(regressions):
      msg = f'Regression detected: {regressions}'
      api.step('Regression detected.', None)
      api.step.active_result.presentation.status = api.step.FAILURE
      api.step.active_result.presentation.step_text = msg
      return result_pb2.RawResult(
          status=common_pb2.FAILURE,
          summary_markdown=(f'Regression detected: {regressions}'))
    msg = 'No regressions found.'
    api.step(msg, None)
    return result_pb2.RawResult(
        status=common_pb2.SUCCESS, summary_markdown=(msg))



def GenTests(api: TEST_DEPS):
  mock_gerrit_change = [
      common_pb2.GerritChange(
          host='chromium-review.googlesource.com',
          project='fake-project',
          change=666666,
          patchset=16,
      )
  ]
  mock_properties = {
      'configuration': 'fake-bot-configuration',
      'benchmark': 'fake-benchmark',
      'story': 'fake-story',
      'attempts_count': 64,
      'tags': [{
          'key': 'other',
          'value': 'data'
      }],
  }
  mock_properties_staging = dict(mock_properties)
  mock_properties_staging['use_staging'] = True
  mock_properties_no_attempt = dict(mock_properties)
  mock_properties_no_attempt.pop('attempts_count')
  mock_properties_less_impact = dict(mock_properties)
  mock_properties_less_impact['benchmark'] = 'fake-benchmark-less'
  mock_properties_less_impact['story'] = 'fake-story-less'
  mock_properties_gemini = dict(mock_properties_less_impact)
  mock_properties_gemini['ask_gemini'] = True
  mock_properties_gemini['gemini_api_key'] = 'fake_api_key'
  mock_try_build = {
      'builder_group': 'fake-try-group',
      'builder': 'fake-perf',
      'tags': api.buildbucket.tags(cq_attempt_key='fake-cq-attempt-key'),
      'gerrit_changes': mock_gerrit_change,
      'revision': 'fake-hash'
  }
  # expected cmd based on the mocks above
  expected_cmd = [
      'vpython3',
      '.*pinpoint_try_job_invoker.py',
      '--name',
      'CQ Perf on fake-benchmark/fake-story at fake-project:666666/16',
      '--base_git_hash',
      'fake-hash',
      '--patch',
      'https://chromium-review.googlesource.com/c/666666/16',
      '--configuration',
      'fake-bot-configuration',
      '--benchmark',
      'fake-benchmark',
      '--story',
      'fake-story',
      '--attempts_count',
      '64',
      '--user',
      'chr@mium.org',
      '--tags',
      '{"origin": "CQ", "change": "666666", "patchset": "16", "other": "data"}',
      '--token',
      '.*',  # any string for oauth token
  ]
  expected_cmd_no_user = list(expected_cmd)
  expected_cmd_no_user[expected_cmd_no_user.index(
      'chr@mium.org')] = 'Change 666666/16'
  expected_cmd_no_attempt = list(expected_cmd)
  expected_cmd_no_attempt.remove('--attempts_count')
  expected_cmd_no_attempt.remove('64')
  expected_cmd_staging = list(expected_cmd)
  expected_cmd_staging.append('--use_staging')
  expected_poll_cmd = [
      'vpython3',
      '.*pinpoint_try_job_poller.py',
      '--job_id',
      '13579',
      '--token',
      '.*',  # any string for oauth token
  ]
  expected_poll_cmd_staging = list(expected_poll_cmd)
  expected_poll_cmd_staging.append('--use_staging')
  mock_gerrit_cl_info = [{
      'owner': {
          'email': 'chr@mium.org'
      },
      'revisions': {
          'haha1010': {
              'files': {
                  'abc': {},
                  'qwe.txt': {},
              }
          }
      }
  }]
  mock_gerrit_cl_info_no_email = [{
      'owner': {
          '_account_id': 1234
      },
      'revisions': {
          'haha1010': {
              'files': {
                  'abc': {},
                  'qwe.txt': {},
              }
          }
      }
  }]
  mock_gerrit_cl_info_no_file = [{
      'owner': {
          'email': 'chr@mium.org'
      },
      'revisions': {
          'haha1010': {}
      }
  }]
  ###################################################################
  yield api.test(
      'trigger_complete_pass',
      api.chromium.try_build(**mock_try_build),
      api.properties(**mock_properties),
      api.override_step_data(
          'gerrit loads current patchset (16) from change 666666',
          api.json.output(mock_gerrit_cl_info)),
      api.step_data(
          'Trigger Performance Test fake-benchmark',
          stdout=api.json.output(
              {'response': {
                  'jobId': '13579',
                  'jobUrl': 'fake-pinpoint-link'
              }})),
      api.step_data(
          'Waiting Pinpoint job 13579 to finish. (Timeout max 2hr)',
          stdout=api.json.output({'status': 'Completed'})),
      api.step_data(
          'Getting CABE analysis results.',
          stdout=api.json.output({})),  # no regression
      api.post_process(post_process.MustRun,
                       'Trigger Performance Test fake-benchmark'),
      api.post_process(
          post_process.StepCommandRE,
          'Trigger Performance Test fake-benchmark',
          expected_cmd,
      ),
      api.post_process(post_process.MustRun, 'Try job triggered.'),
      api.post_process(post_process.LinkEquals, 'Try job triggered.',
                       'Pinpoint job', 'fake-pinpoint-link'),
      api.post_process(
          post_process.MustRun,
          'Waiting Pinpoint job 13579 to finish. (Timeout max 2hr)'),
      api.post_process(
          post_process.StepCommandRE,
          'Waiting Pinpoint job 13579 to finish. (Timeout max 2hr)',
          expected_poll_cmd,
      ),
      api.post_process(post_process.MustRun, 'Pinpoint try job finished.'),
      api.post_process(post_process.MustRun, 'Getting CABE analysis results.'),
      api.post_process(post_process.MustRun, 'CABE analysis finished.'),
      api.post_process(post_process.DropExpectation),
  )
  yield api.test(
      'trigger_complete_regressions',
      api.chromium.try_build(**mock_try_build),
      api.properties(**mock_properties),
      api.override_step_data(
          'gerrit loads current patchset (16) from change 666666',
          api.json.output(mock_gerrit_cl_info)),
      api.step_data(
          'Trigger Performance Test fake-benchmark',
          stdout=api.json.output(
              {'response': {
                  'jobId': '13579',
                  'jobUrl': 'fake-pinpoint-link'
              }})),
      api.step_data(
          'Waiting Pinpoint job 13579 to finish. (Timeout max 2hr)',
          stdout=api.json.output({'status': 'Completed'})),
      # everything else same as the pass case, except for the cabe result.
      api.step_data(
          'Getting CABE analysis results.',
          stdout=api.json.output({
              'benchmark': 'fake-benchmark',
              'regressions': {
                  'workload-1': {
                      'p-value': 0.248
                  }
              }
          })),
      api.post_process(post_process.MustRun, 'CABE analysis finished.'),
      api.post_process(post_process.DropExpectation),
      status='FAILURE',
  )

  yield api.test(
      'trigger_complete_job_failed',
      api.chromium.try_build(**mock_try_build),
      api.properties(**mock_properties),
      api.override_step_data(
          'gerrit loads current patchset (16) from change 666666',
          api.json.output(mock_gerrit_cl_info)),
      api.step_data(
          'Trigger Performance Test fake-benchmark',
          stdout=api.json.output(
              {'response': {
                  'jobId': '13579',
                  'jobUrl': 'fake-pinpoint-link'
              }})),
      # poller return non-completed status
      api.step_data(
          'Waiting Pinpoint job 13579 to finish. (Timeout max 2hr)',
          stdout=api.json.output({'status': 'Failed'})),
      api.post_process(post_process.MustRun, 'Try job triggered.'),
      api.post_process(post_process.DoesNotRun, 'Pinpoint try job finished.'),
      api.post_process(post_process.DropExpectation),
      status='FAILURE',
  )
  yield api.test(
      'trigger_complete_pass_staging',
      api.chromium.try_build(**mock_try_build),
      api.properties(**mock_properties_staging),  # with use_staging
      api.override_step_data(
          'gerrit loads current patchset (16) from change 666666',
          api.json.output(mock_gerrit_cl_info)),
      api.step_data(
          'Trigger Performance Test fake-benchmark',
          stdout=api.json.output(
              {'response': {
                  'jobId': '13579',
                  'jobUrl': 'fake-pinpoint-link'
              }})),
      api.step_data(
          'Waiting Pinpoint job 13579 to finish. (Timeout max 2hr)',
          stdout=api.json.output({'status': 'Completed'})),
      api.step_data(
          'Getting CABE analysis results.', stdout=api.json.output({})),
      api.post_process(post_process.MustRun,
                       'Trigger Performance Test fake-benchmark'),
      api.post_process(
          post_process.StepCommandRE,
          'Trigger Performance Test fake-benchmark',
          expected_cmd_staging,  # with --use_staging
      ),
      api.post_process(
          post_process.MustRun,
          'Waiting Pinpoint job 13579 to finish. (Timeout max 2hr)'),
      api.post_process(
          post_process.StepCommandRE,
          'Waiting Pinpoint job 13579 to finish. (Timeout max 2hr)',
          expected_poll_cmd_staging,  # with --use_staging
      ),
      api.post_process(post_process.MustRun, 'CABE analysis finished.'),
      api.post_process(post_process.DropExpectation),
  )
  yield api.test(
      'trigger_complete_no_user',
      api.chromium.try_build(**mock_try_build),
      api.properties(**mock_properties),
      api.override_step_data(
          'gerrit loads current patchset (16) from change 666666',
          api.json.output(mock_gerrit_cl_info_no_email)),
      api.step_data(
          'Trigger Performance Test fake-benchmark',
          stdout=api.json.output(
              {'response': {
                  'jobId': '13579',
                  'jobUrl': 'fake-pinpoint-link'
              }})),
      api.step_data(
          'Waiting Pinpoint job 13579 to finish. (Timeout max 2hr)',
          stdout=api.json.output({'status': 'Completed'})),
      api.step_data(
          'Getting CABE analysis results.', stdout=api.json.output({})),
      api.post_process(post_process.MustRun,
                       'Trigger Performance Test fake-benchmark'),
      # no valid email returned by get gerrit data, and thus a make-up user
      # will be used.
      api.post_process(
          post_process.StepCommandRE,
          'Trigger Performance Test fake-benchmark',
          expected_cmd_no_user,
      ),
      api.post_process(post_process.MustRun, 'CABE analysis finished.'),
      api.post_process(post_process.DropExpectation),
  )
  yield api.test(
      'trigger_complete_no_attempt',
      api.chromium.try_build(**mock_try_build),
      api.properties(**mock_properties_no_attempt),
      api.override_step_data(
          'gerrit loads current patchset (16) from change 666666',
          api.json.output(mock_gerrit_cl_info)),
      api.step_data(
          'Trigger Performance Test fake-benchmark',
          stdout=api.json.output(
              {'response': {
                  'jobId': '13579',
                  'jobUrl': 'fake-pinpoint-link'
              }})),
      api.step_data(
          'Waiting Pinpoint job 13579 to finish. (Timeout max 2hr)',
          stdout=api.json.output({'status': 'Completed'})),
      api.step_data(
          'Getting CABE analysis results.', stdout=api.json.output({})),
      api.post_process(post_process.MustRun,
                       'Trigger Performance Test fake-benchmark'),
      # no attempts_count is given.
      api.post_process(
          post_process.StepCommandRE,
          'Trigger Performance Test fake-benchmark',
          expected_cmd_no_attempt,
      ),
      api.post_process(post_process.MustRun, 'CABE analysis finished.'),
      api.post_process(post_process.DropExpectation),
  )
  yield api.test(
      'trigger_invoker_throws',
      api.chromium.try_build(**mock_try_build),
      api.properties(**mock_properties),
      api.override_step_data(
          'gerrit loads current patchset (16) from change 666666',
          api.json.output(mock_gerrit_cl_info)),
      # The invoking returns 1. The remaining steps will be skipped.
      api.step_data('Trigger Performance Test fake-benchmark', retcode=1),
      api.post_process(post_process.MustRun,
                       'Trigger Performance Test fake-benchmark'),
      api.post_process(post_process.DoesNotRun, 'Try job triggered.'),
      api.post_process(post_process.DropExpectation),
      status='FAILURE',
  )
  yield api.test(
      'trigger_invoker_return_empty_stdout',
      api.chromium.try_build(**mock_try_build),
      api.properties(**mock_properties),
      api.override_step_data(
          'gerrit loads current patchset (16) from change 666666',
          api.json.output(mock_gerrit_cl_info)),
      # step 'Trigger Performance Test xxxx' is not mocked, a.k.a., no stdout.
      api.post_process(post_process.MustRun,
                       'Trigger Performance Test fake-benchmark'),
      api.post_process(post_process.DoesNotRun, 'Try job triggered.'),
      api.post_process(post_process.DropExpectation),
      status='FAILURE',
  )
  yield api.test(
      'trigger_gerrit_returns_nothing',
      api.chromium.try_build(**mock_try_build),
      api.properties(**mock_properties),
      # empty list is returned from get gerrit.
      api.override_step_data(
          'gerrit loads current patchset (16) from change 666666',
          api.json.output([])),
      api.post_process(post_process.DoesNotRun,
                       'Trigger Performance Test fake-benchmark'),
      api.post_process(post_process.DropExpectation),
      status='FAILURE',
  )
  yield api.test(
      'trigger_not_triggered_with_less_impact',
      api.chromium.try_build(**mock_try_build),
      api.properties(**mock_properties_less_impact),
      api.override_step_data(
          'gerrit loads current patchset (16) from change 666666',
          api.json.output(mock_gerrit_cl_info)),
      # the file has less impact than the threshold
      api.post_process(post_process.DoesNotRun,
                       'Trigger Performance Test fake-benchmark'),
      api.post_process(post_process.DropExpectation),
      status='SUCCESS',
  )
  yield api.test(
      'trigger_not_triggered_with_no_file_list',
      api.chromium.try_build(**mock_try_build),
      api.properties(**mock_properties),
      api.override_step_data(
          'gerrit loads current patchset (16) from change 666666',
          api.json.output(mock_gerrit_cl_info_no_file)),
      # gerrit returns empty file list
      api.post_process(post_process.DoesNotRun,
                       'Trigger Performance Test fake-benchmark'),
      api.post_process(post_process.DropExpectation),
      status='SUCCESS',
  )
  yield api.test(
      'trigger_not_triggered_with_no_static_map',
      api.chromium.try_build(**mock_try_build),
      api.properties(**mock_properties),
      api.override_step_data(
          'gerrit loads current patchset (16) from change 666666',
          api.json.output(mock_gerrit_cl_info_no_file)),
      # gerrit returns empty file list
      api.post_process(post_process.DoesNotRun,
                       'Trigger Performance Test fake-benchmark'),
      api.post_process(post_process.DropExpectation),
      status='SUCCESS',
  )
  yield api.test(
      'trigger_triggered_poller_throws',
      api.chromium.try_build(**mock_try_build),
      api.properties(**mock_properties),
      api.override_step_data(
          'gerrit loads current patchset (16) from change 666666',
          api.json.output(mock_gerrit_cl_info)),
      api.step_data(
          'Trigger Performance Test fake-benchmark',
          stdout=api.json.output(
              {'response': {
                  'jobId': '13579',
                  'jobUrl': 'fake-pinpoint-link'
              }})),
      api.step_data(
          'Waiting Pinpoint job 13579 to finish. (Timeout max 2hr)', retcode=1),
      api.post_process(
          post_process.MustRun,
          'Waiting Pinpoint job 13579 to finish. (Timeout max 2hr)'),
      api.post_process(post_process.DoesNotRun, 'Pinpoint try job finished.'),
      api.post_process(post_process.DropExpectation),
      status='FAILURE',
  )
  yield api.test(
      'trigger_triggered_poller_return_empty_stdout',
      api.chromium.try_build(**mock_try_build),
      api.properties(**mock_properties),
      api.override_step_data(
          'gerrit loads current patchset (16) from change 666666',
          api.json.output(mock_gerrit_cl_info)),
      api.step_data(
          'Trigger Performance Test fake-benchmark',
          stdout=api.json.output(
              {'response': {
                  'jobId': '13579',
                  'jobUrl': 'fake-pinpoint-link'
              }})),
      # step 'Waiting Pinpoint job xxxx' is not mocked, a.k.a., no stdout.
      api.post_process(
          post_process.MustRun,
          'Waiting Pinpoint job 13579 to finish. (Timeout max 2hr)'),
      api.post_process(post_process.DoesNotRun, 'Pinpoint try job finished.'),
      api.post_process(post_process.DropExpectation),
      status='FAILURE',
  )
  yield api.test(
      'trigger_triggered_poller_incomplete',
      api.chromium.try_build(**mock_try_build),
      api.properties(**mock_properties),
      api.override_step_data(
          'gerrit loads current patchset (16) from change 666666',
          api.json.output(mock_gerrit_cl_info)),
      api.step_data(
          'Trigger Performance Test fake-benchmark',
          stdout=api.json.output(
              {'response': {
                  'jobId': '13579',
                  'jobUrl': 'fake-pinpoint-link'
              }})),
      api.step_data(
          'Waiting Pinpoint job 13579 to finish. (Timeout max 2hr)',
          stdout=api.json.output({'status': 'Running'})),
      api.post_process(
          post_process.MustRun,
          'Waiting Pinpoint job 13579 to finish. (Timeout max 2hr)'),
      api.post_process(post_process.DoesNotRun, 'Pinpoint try job finished.'),
      api.post_process(post_process.DropExpectation),
      status='FAILURE',
  )
  yield api.test(
      'trigger_cabe_throws',
      api.chromium.try_build(**mock_try_build),
      api.properties(**mock_properties),
      api.override_step_data(
          'gerrit loads current patchset (16) from change 666666',
          api.json.output(mock_gerrit_cl_info)),
      api.step_data(
          'Trigger Performance Test fake-benchmark',
          stdout=api.json.output(
              {'response': {
                  'jobId': '13579',
                  'jobUrl': 'fake-pinpoint-link'
              }})),
      api.step_data(
          'Waiting Pinpoint job 13579 to finish. (Timeout max 2hr)',
          stdout=api.json.output({'status': 'Completed'})),
      api.step_data('Getting CABE analysis results.', retcode=1),
      api.post_process(post_process.MustRun, 'Getting CABE analysis results.'),
      api.post_process(post_process.DoesNotRun, 'CABE analysis finished.'),
      api.post_process(post_process.DropExpectation),
      status='FAILURE',
  )
  yield api.test(
      'trigger_cabe_returns_empty',
      api.chromium.try_build(**mock_try_build),
      api.properties(**mock_properties),
      api.override_step_data(
          'gerrit loads current patchset (16) from change 666666',
          api.json.output(mock_gerrit_cl_info)),
      api.step_data(
          'Trigger Performance Test fake-benchmark',
          stdout=api.json.output(
              {'response': {
                  'jobId': '13579',
                  'jobUrl': 'fake-pinpoint-link'
              }})),
      api.step_data(
          'Waiting Pinpoint job 13579 to finish. (Timeout max 2hr)',
          stdout=api.json.output({'status': 'Completed'})),
      # step 'Getting CABE analysis results' is not mocked, a.k.a., no stdout.
      api.post_process(post_process.MustRun, 'Getting CABE analysis results.'),
      api.post_process(post_process.DoesNotRun, 'CABE analysis finished.'),
      api.post_process(post_process.DropExpectation),
      status='FAILURE',
  )
  yield api.test(
      'run_ask_gemini',
      api.chromium.try_build(**mock_try_build),
      api.properties(**mock_properties_gemini),
      api.override_step_data(
          'Use Gemini to analyze changes', stdout=api.raw_io.output('foo')),
      api.override_step_data(
          'gerrit loads current patchset (16) from change 666666',
          api.json.output(mock_gerrit_cl_info)),
      # the file has less impact than the threshold
      api.post_process(post_process.DoesNotRun,
                       'Trigger Performance Test fake-benchmark'),
      api.post_process(post_process.DropExpectation),
      status='SUCCESS',
  )
