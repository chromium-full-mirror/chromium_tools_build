# Copyright 2022 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
'''A script to verify the test suite support on actual builders.

You could run a build locally:
> recipes.py run flaky_reproducer:examples/builder_verifier \
>   task_id=xxx test_name=xxx

or using led:
> led get-builder luci.chromium.flaky-reproducer:runner \
>   | led edit -r flaky_reproducer:examples/builder_verifier \
>   | led edit -p 'task_id="xxx"' -p 'test_name="xxx"' \
>   | led launch
'''

from recipe_engine.recipe_api import Property
from RECIPE_MODULES.build.flaky_reproducer.libs import (
    ReproducingStep, TestBinaryWithBatchMixin, TestBinaryWithParallelMixin)

DEPS = [
    'flaky_reproducer',
    'recipe_engine/buildbucket',
    'recipe_engine/file',
    'recipe_engine/json',
    'recipe_engine/luci_analysis',
    'recipe_engine/properties',
    'recipe_engine/raw_io',
    'recipe_engine/resultdb',
    'recipe_engine/step',
    'recipe_engine/swarming',
]

PROPERTIES = {
    'task_id': Property(default=None, kind=str),
    'test_name': Property(default=None, kind=str),
    'config': Property(default='auto', kind=str),
    'verify_on_builders': Property(default=None, kind=list),
}


def RunSteps(api, config, task_id, test_name, verify_on_builders):
  api.flaky_reproducer.set_config(config)
  api.flaky_reproducer.apply_config('verify_on_every_builders')
  result_summary = api.flaky_reproducer.get_test_result_summary(task_id)
  test_binary = api.flaky_reproducer.get_test_binary(task_id)

  test_sample = result_summary.get_all(test_name)[0]
  verify_builders = api.flaky_reproducer.find_related_builders(
      task_id, test_name, verify_on_builders)
  verify_builders[test_binary.builder or 'Task Sample'] = task_id

  results = {builder: {} for builder in verify_builders}

  # Check result summary support
  with api.step.nest('check result summary support'):
    for builder, builder_task_id in verify_builders.items():
      try:
        with api.step.nest(builder):
          builder_result_summary = api.flaky_reproducer.get_test_result_summary(
              builder_task_id)
          if test_name not in builder_result_summary:
            raise api.step.StepFailure(
                'Cannot find test {0} in test result for task {1}.'.format(
                    test_name, task_id))
          builder_test_sample = builder_result_summary.get_all(test_name)[0]
          results[builder].update({
              'result_summary': {
                  'result':
                      True,
                  'link':
                      api.flaky_reproducer._swarming_task_url(builder_task_id),
              },
              'batch_strategy':
                  isinstance(test_binary, TestBinaryWithBatchMixin)
                  and builder_test_sample.batch_id is not None,
              'parallel_strategy':
                  isinstance(test_binary, TestBinaryWithParallelMixin)
                  and builder_test_sample.thread_id is not None
                  and builder_test_sample.start_time is not None
                  and builder_test_sample.duration is not None,
          })
      except Exception:
        results[builder]['result_summary'] = {
            'result': False,
            'link': api.flaky_reproducer._swarming_task_url(builder_task_id),
        }

  # repeat
  with api.step.nest('check test binary support'):
    verify_test_binary = (
        test_binary  # go/pyformat-break
        .with_tests([test_name, test_name])  #
        .with_repeat(2)  #
    )
    if isinstance(test_binary, TestBinaryWithParallelMixin):
      verify_test_binary = verify_test_binary.with_parallel_jobs(2)
    reproducing_step = ReproducingStep(verify_test_binary, 'builder_check')
    builder_results = api.flaky_reproducer.verify_reproducing_step_on_builders(
        verify_builders, test_sample, reproducing_step, retries=1)
    for builder, result in builder_results.items():
      results[builder]['binary_repeat'] = {
          'result': bool(result.reproduced_runs),
          'link': api.flaky_reproducer._swarming_task_url(result.task_id),
      }
      if isinstance(test_binary, TestBinaryWithParallelMixin):
        results[builder]['binary_parallel'] = {
            'result': bool(result.reproduced_runs),
            'link': api.flaky_reproducer._swarming_task_url(result.task_id),
        }

    # batch
    if isinstance(test_binary, TestBinaryWithBatchMixin):
      verify_test_binary = (
          test_binary  # go/pyformat-break
          .with_tests([test_name, test_name])  #
          .with_single_batch()  #
      )
      reproducing_step = ReproducingStep(verify_test_binary, 'builder_check')
      builder_results = api.flaky_reproducer.verify_reproducing_step_on_builders(
          verify_builders, test_sample, reproducing_step, retries=1)
      for builder, result in builder_results.items():
        results[builder]['binary_batch'] = {
            'result': bool(result.reproduced_runs),
            'link': api.flaky_reproducer._swarming_task_url(result.task_id),
        }

  # to html
  with api.step.nest('summarize_results') as presentation:
    presentation.logs['results.json'] = api.json.dumps(results)
    artifacts_ret = api.resultdb.upload_invocation_artifacts({
        'results.html': {
            'content_type': 'text/html',
            'contents': _result_json_to_html(results),
        }
    })
    for a in artifacts_ret.artifacts:
      presentation.step_summary_text = (
          '[results.html](https://luci-milo.appspot.com/ui/artifact/raw/{0})'
          .format(a.name))


def _result_json_to_html(results):
  '''Generate a simple html table of verify results.'''
  html = ['<!doctype html>', '<table><thead><tr>', '<th>builder</th>']
  headers = []
  for _, result in results.items():
    for key in result:
      if key not in headers:
        headers.append(key)
        html.append('<th>{0}</th>'.format(key))
  html.append('</tr></thead><tbody>')
  for builder, result in sorted(results.items()):
    html.append('<tr><td>{0}</td>'.format(builder))
    for key in headers:
      if isinstance(result.get(key), dict):
        html.append('<td><a href="{0}" target="_blank">{1}</a></td>'.format(
            result[key]['link'], 'O' if result[key]['result'] else 'X'))
      else:
        html.append('<td>{0}</td>'.format('O' if result.get(key) else 'X'))
    html.append('</tr>')
  html.append('</tbody></table>')
  return ''.join(html)


from recipe_engine import post_process
from PB.go.chromium.org.luci.resultdb.proto.v1 import (
    common as common_pb2,  # go/pyformat-break
    invocation as invocation_pb2,  #
    resultdb as resultdb_pb2,  #
    test_result as test_result_pb2,  #
    recorder,  #
    artifact,  #
)
from PB.go.chromium.org.luci.analysis.proto.v1 import (
    common as analysis_common_pb2,  # go/pyformat-break
    test_history,  #
    test_verdict,  #
)


def GenTests(api):
  resultdb_invocation = api.resultdb.Invocation(
      proto=invocation_pb2.Invocation(
          state=invocation_pb2.Invocation.FINALIZED,
          realm='chromium:ci',
      ),
      test_results=[
          test_result_pb2.TestResult(
              test_id='ninja://base:base_unittests/MockUnitTests.PassTest',
              name='test_name_1',
              expected=True,
              status=test_result_pb2.PASS,
              variant=common_pb2.Variant(
                  **{
                      'def': {
                          'builder': 'Linux Tests',
                          'test_suite': 'base_unittests',
                      }
                  }),
              tags=[
                  common_pb2.StringPair(
                      key="test_name", value="MockUnitTests.PassTest"),
              ],
          ),
      ],
  )
  query_test_history_res = test_history.QueryTestHistoryResponse(verdicts=[
      test_verdict.TestVerdict(
          test_id='ninja://base:base_unittests/MockUnitTests.PassTest',
          variant_hash='dummy_hash_1',
          invocation_id='build-1234',
          status=test_verdict.TestVerdictStatus.UNEXPECTED,
      ),
  ])
  gtest_empty_result = api.json.loads(
      api.flaky_reproducer.get_test_data('gtest_good_output.json'))
  gtest_empty_result['per_iteration_data'] = []

  yield api.test(
      'base',
      api.properties(
          task_id='54321fffffabc123',
          test_name='MockUnitTests.PassTest',
          config='manual'),
      api.step_data(
          'get_test_result_summary.download swarming outputs',
          api.raw_io.output_dir({
              'output.json':
                  api.flaky_reproducer.get_test_data('gtest_good_output.json'),
          })),
      api.step_data(
          'get_test_binary from 54321fffffabc123',
          api.json.output_stream(
              api.json.loads(
                  api.flaky_reproducer.get_test_data(
                      'gtest_task_request.json')))),
      api.resultdb.query(
          {
              'task-example.swarmingserver.appspot.com-54321fffffabc123':
                  resultdb_invocation,
          },
          step_name='find_related_builders.rdb query'),
      api.luci_analysis.query_variants(
          test_history.QueryVariantsResponse(variants=[
              test_history.QueryVariantsResponse.VariantInfo(
                  variant_hash='dummy_hash_1',
                  variant=analysis_common_pb2.Variant(
                      **{"def": {
                          'builder': 'Win10 Tests x64'
                      }}),
              ),
          ]),
          test_id='ninja://base:base_unittests/MockUnitTests.PassTest',
          parent_step_name='find_related_builders',
      ),
      api.luci_analysis.query_test_history(
          query_test_history_res,
          test_id='ninja://base:base_unittests/MockUnitTests.PassTest',
          parent_step_name='find_related_builders',
      ),
      api.resultdb.query_test_results(
          resultdb_pb2.QueryTestResultsResponse(test_results=[
              dict(
                  test_id='ninja://base:base_unittests/MockUnitTests.PassTest',
                  name=('invocations/task-example.swarmingserver.appspot.com'
                        '-54321fffffabc001/result-1'),
                  variant=common_pb2.Variant(
                      **{'def': {
                          'builder': 'Win10 Tests x64',
                      }}),
              )
          ]),
          step_name='find_related_builders.query_test_results',
      ),
      api.step_data(
          'check result summary support.Win11 Tests x64'
          '.get_test_result_summary.download swarming outputs',
          api.raw_io.output_dir({
              'output.json':
                  api.flaky_reproducer.get_test_data('gtest_good_output.json'),
          })),
      api.step_data(
          'check result summary support.Win10 Tests x64'
          '.get_test_result_summary.download swarming outputs',
          api.raw_io.output_dir({
              'output.json': api.json.dumps(gtest_empty_result).encode('utf8'),
          })),
      api.resultdb.upload_invocation_artifacts(
          recorder.BatchCreateArtifactsResponse(artifacts=[
              artifact.Artifact(
                  artifact_id='results.html',
                  content_type='text/html',
                  contents=b'foobar'),
          ]), 'summarize_results.upload_invocation_artifacts'),
      api.post_check(lambda check, steps: check('results.html' in steps[
          'summarize_results'].step_summary_text)),
      api.post_process(post_process.DropExpectation),
  )
