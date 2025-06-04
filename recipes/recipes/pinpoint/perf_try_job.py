# Copyright 2025 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Trigger Pinpoint try jobs as performance tests"""

import json

from recipe_engine import post_process
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb2
from PB.recipes.build.pinpoint import perf_try_job

DEPS = [
    'chromium',
    'chromium_tests_builder_config',
    'depot_tools/tryserver',
    'recipe_engine/buildbucket',
    'recipe_engine/cv',
    'recipe_engine/json',
    'recipe_engine/properties',
    'recipe_engine/raw_io',
    'recipe_engine/service_account',
    'recipe_engine/step',
]

PROPERTIES = perf_try_job.InputProperties


def _generate_try_job_name(api, properties):
  benchmark = properties.benchmark
  story = properties.story
  project = api.tryserver.gerrit_change_repo_project
  change = api.tryserver.gerrit_change.change
  patchset = api.tryserver.gerrit_change.patchset

  return f'CQ Perf on {benchmark}/{story} at {project}:{change}/{patchset}'


# convert a list of key-value pairs in tags into a json string.
# e.g. [{'key':'k','value':'v'},{'key':'kk','value':'vv'}]
#        => '{"k":"v", "kk":"vv"}'
def _generate_tag_string(tags):
  tags_dict = {}
  for tag in tags:
    tags_dict[tag.key] = tag.value
  return json.dumps(tags_dict)


def _generate_access_token(api):
  return api.service_account.default().get_access_token()


def RunSteps(api, properties):
  api.tryserver.require_is_tryserver()
  with api.chromium.chromium_layout():
    cmd = ['vpython3', api.resource('pinpoint_try_job_invoker.py')]

    cmd.extend(['--name', _generate_try_job_name(api, properties)])
    cmd.extend(['--base_git_hash', api.buildbucket.gitiles_commit.id or 'HEAD'])
    cmd.extend(['--patch', api.tryserver.gerrit_change_review_url])
    cmd.extend(['--configuration', properties.configuration])
    cmd.extend(['--benchmark', properties.benchmark])
    cmd.extend(['--story', properties.story])
    cmd.extend(['--user', api.tryserver.gerrit_change_owner['name']])
    cmd.extend(['--tags', _generate_tag_string(properties.tags)])
    token = _generate_access_token(api)
    cmd.extend(['--token', api.raw_io.input_text(token)])
    if properties.use_staging:
      cmd.extend(['--use_staging'])

    step_result = api.step(
        f'Trigger Performance Test {properties.benchmark}',
        cmd,
        stdout=api.json.output())
    if step_result.stdout:
      url = step_result.stdout.get('response', {}).get('url')
      api.step(f'Try job triggered: {url}', None)


def GenTests(api):
  mock_gerrit_change = [
      common_pb2.GerritChange(
          host='chromium-review.googlesource.com',
          project='fake-project',
          change=666666,
          patchset=16,
      )
  ]
  mock_properties = {
      'configuration':
          'fake-bot-configuration',
      'benchmark':
          'fake-benchmark',
      'story':
          'fake-story',
      'tags': [{
          'key': 'origin',
          'value': 'CQ'
      }, {
          'key': 'other',
          'value': 'data'
      }],
  }
  mock_properties_staging = dict(mock_properties)
  mock_properties_staging['use_staging'] = True
  mock_try_build = {
      'builder_group': 'fake-try-group',
      'builder': 'fake-perf',
      'tags': api.buildbucket.tags(cq_attempt_key='fake-cq-attempt-key'),
      'gerrit_changes': mock_gerrit_change,
      'revision': 'fake-hash'
  }
  mock_gerrit_fetch_result = [{
      'branch': 'fake-branch',
      'revisions': {},
      'owner': {
          'name': 'fake-user',
      },
  }]
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
      '--user',
      'fake-user',
      '--tags',
      '{"origin": "CQ", "other": "data"}',
      '--token',
      '.*',  # any string for oauth token
  ]
  expected_cmd_staging = list(expected_cmd)
  expected_cmd_staging.append('--use_staging')

  yield api.test(
      'trigger_with_hash_staging',
      api.chromium.try_build(**mock_try_build),
      api.properties(**mock_properties_staging),
      api.override_step_data('gerrit fetch current CL info',
                             api.json.output(mock_gerrit_fetch_result)),
      api.post_process(post_process.MustRun,
                       'Trigger Performance Test fake-benchmark'),
      api.post_process(
          post_process.StepCommandRE,
          'Trigger Performance Test fake-benchmark',
          expected_cmd_staging,
      ),
      api.post_process(post_process.DropExpectation),
  )
  yield api.test(
      'trigger_with_hash',
      api.chromium.try_build(**mock_try_build),
      api.properties(**mock_properties),
      api.override_step_data('gerrit fetch current CL info',
                             api.json.output(mock_gerrit_fetch_result)),
      api.post_process(post_process.MustRun,
                       'Trigger Performance Test fake-benchmark'),
      api.post_process(
          post_process.StepCommandRE,
          'Trigger Performance Test fake-benchmark',
          expected_cmd,
      ),
      api.post_process(post_process.DropExpectation),
  )
  yield api.test(
      'trigger_with_hash_throw',
      api.chromium.try_build(**mock_try_build),
      api.properties(**mock_properties),
      api.step_data('Trigger Performance Test fake-benchmark', retcode=1),
      api.post_process(post_process.MustRun,
                       'Trigger Performance Test fake-benchmark'),
      api.post_process(post_process.DoesNotRun,
                       'Try job triggered: fake-pinpoint-link'),
      api.post_process(post_process.DropExpectation),
      status='FAILURE',
  )
  yield api.test(
      'trigger_with_hash_return_0',
      api.chromium.try_build(**mock_try_build),
      api.properties(**mock_properties),
      api.step_data(
          'Trigger Performance Test fake-benchmark',
          stdout=api.json.output({'response': {
              'url': 'fake-pinpoint-link'
          }})),
      api.post_process(post_process.MustRun,
                       'Trigger Performance Test fake-benchmark'),
      api.post_process(post_process.MustRun,
                       'Try job triggered: fake-pinpoint-link'),
      api.post_process(post_process.DropExpectation),
  )
