# Copyright 2020 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb2

from RECIPE_MODULES.build import chromium_tests_builder_config as ctbc

from recipe_engine import post_process

DEPS = [
    'chromium',
    'chromium_tests',
    'chromium_tests_builder_config',
    'filter',
    'skylab',
    'test_utils',
    'depot_tools/tryserver',
    'recipe_engine/buildbucket',
    'recipe_engine/file',
    'recipe_engine/path',
    'recipe_engine/json',
    'recipe_engine/raw_io',
    'recipe_engine/step',
]


def RunSteps(api):
  builder_id, builder_config = (
      api.chromium_tests_builder_config.lookup_builder())
  if api.tryserver.is_tryserver:
    return api.chromium_tests.trybot_steps(builder_id, builder_config)
  return api.chromium_tests.main_waterfall_steps(builder_id, builder_config)



def GenTests(api):

  TAST_TARGET = 'lacros_fyi_tast_tests'
  GTEST_TARGET = 'vaapi_unittest'

  GOOD_ISOLATE_TEXT = """
    {'variables': {'command': ['bin/run_lacros_smoke_tast_tests',
                            '--logs-dir=${ISOLATED_OUTDIR}'],
                'files': ['../../.vpython',
                          'bin/run_vaapi_unittest',
                          'resources.pak',
                          'resources.pak.info',
                          './chrome',
                          '../../testing/buildbot/filters',
                          'gen/third_party',
                          '../../testing/buildbot/filters',
                          'bin/lacros_fyi_tast_tests.filter'
                            ]}}
  """

  EMPTY_FILE_LIST = """
    {'variables': {'command': ['bin/run_lacros_smoke_tast_tests',
                            '--logs-dir=${ISOLATED_OUTDIR}'],
                'files': []}}
  """

  BAD_ISOLATE_TEXT = """
    not isolate file at all
  """

  def boilerplate(skylab_gcs,
                  build_id=8945511751514863184,
                  builder_group='chromium.chromiumos',
                  builder='lacros-amd64-generic-rel',
                  tast_expr='',
                  test_args='',
                  benchmark='',
                  isolate_content=GOOD_ISOLATE_TEXT,
                  isolate_file_exists=True,
                  is_ci_build=True,
                  target_name=TAST_TARGET,
                  should_read_isolate=True,
                  ci_only_tests=True):
    builder_db = ctbc.BuilderDatabase.create({
        builder_group: {
            builder:
                ctbc.BuilderSpec.create(
                    chromium_config='chromium',
                    gclient_config='chromium',
                    skylab_gs_bucket=skylab_gcs,
                    skylab_gs_extra='lacros',
                ),
        }
    })
    if is_ci_build:
      build_gen = api.chromium_tests_builder_config.ci_build(
          build_id=build_id,
          builder_group=builder_group,
          builder=builder,
          parent_buildername='Linux Builder',
          builder_db=builder_db,
      )
    else:
      build_gen = api.chromium_tests_builder_config.try_build(
          build_id=build_id,
          builder_group=builder_group,
          builder=builder,
          builder_db=builder_db,
          try_db=None,
      )

    steps = sum([
        build_gen,
        api.chromium_tests.read_targets_spec(
            builder_group, {
                builder: {
                    'skylab_tests': [{
                        'cros_board': 'eve',
                        'cros_img': 'eve-release/R89-13631.0.0',
                        'test_id_prefix': 'ninja://basic_EVE_TOT/',
                        'ci_only': ci_only_tests,
                        'name': 'basic_EVE_TOT',
                        'tast_expr': tast_expr,
                        'benchmark': benchmark,
                        'args': [test_args],
                        'swarming': {},
                        'test': target_name,
                        'resultdb': {
                            'enable': True,
                        },
                        'description': 'This is a description.',
                        'timeout_sec': 7200
                    }],
                }
            }),
    ], api.empty_test_data())
    # Mock the file/folder for recipe training.
    mock_paths = [
        api.path['start_dir'].join('squashfs', 'squashfs-tools', 'mksquashfs')
    ]
    # testing/buildbot/filters should be a folder.
    mock_paths.append(api.path['checkout'].join('testing', 'buildbot',
                                                'filters', 'foo'))
    mock_paths.append(api.path['checkout'].join('out', 'Release', 'chrome'))
    mock_paths.append(api.path['checkout'].join('out', 'Release', 'bin',
                                                'run_%s' % target_name))

    mock_paths.append(api.path['checkout'].join('out', 'Release', 'bin',
                                                '%s.filter' % target_name))
    if isolate_file_exists:
      mock_paths.append(api.path['checkout'].join('out', 'Release',
                                                  '%s.isolate' % target_name))
    steps += api.path.exists(*mock_paths)
    if isolate_file_exists and should_read_isolate:
      steps += api.step_data(
          'prepare skylab tests.'
          'collect runtime deps for %s.read isolate file' % target_name,
          api.file.read_text(isolate_content))
    return steps

  def _check_test_args(check, step_odict, step, argument):
    cmd = step_odict[step].cmd
    check(argument in cmd[cmd.index('-test-args') + 1])

  yield api.test(
      'basic for tast',
      boilerplate(
          'chrome-test-builds', tast_expr='("group:mainline" && "dep:lacros")'),
      api.skylab.mock_wait_on_suites(
          'find test runner build',
          1,
          runner_builds=[(901, common_pb2.FAILURE),
                         (902, common_pb2.INFRA_FAILURE),
                         (903, common_pb2.SUCCESS)]),
      api.post_process(post_process.StepCommandContains, 'compile', ['chrome']),
      api.post_process(
          post_process.StepCommandContains,
          'test_pre_run.schedule skylab tests.basic_EVE_TOT.schedule',
          ["-max-retries", "3"]),
      api.post_process(
          post_process.MustRun,
          ('prepare skylab tests.upload skylab runtime deps for %s.'
           'Generic Archiving Steps.'
           'Copy file out/Release/chrome') % TAST_TARGET,
      ),
      api.post_process(
          post_process.MustRun,
          ('prepare skylab tests.upload skylab runtime deps for %s.'
           'Generic Archiving Steps.'
           'Copy file metadata.json') % TAST_TARGET,
      ),
      api.post_process(
          post_process.MustRun,
          ('prepare skylab tests.upload skylab runtime deps for %s.'
           'write metadata.json') % TAST_TARGET,
      ),
      api.post_process(
          _check_test_args,
          'test_pre_run.schedule skylab tests.basic_EVE_TOT.schedule',
          'tast_expr_file=out/Release/bin/%s.filter' % TAST_TARGET),
      api.override_step_data(
          'basic_EVE_TOT results',
          stdout=api.raw_io.output_text(
              api.test_utils.rdb_results(
                  'basic_EVE_TOT',
                  failing_tests=['Test.Two'],
                  flaky_failing_tests=['Test.One']))),
      api.post_process(post_process.StepFailure,
                       'basic_EVE_TOT.shard: #0.attempt: #1'),
      api.post_process(post_process.StepException,
                       'basic_EVE_TOT.shard: #0.attempt: #2'),
      api.post_process(post_process.StepSuccess,
                       'basic_EVE_TOT.shard: #0.attempt: #3'),
      # The attempts within a shard are determined by the build status
      api.post_process(post_process.StepSuccess, 'basic_EVE_TOT.shard: #0'),
      # The overal test suite status is based on the test results (or failed shard)
      api.post_process(post_process.StepFailure, 'basic_EVE_TOT'),
      api.post_process(
          post_process.StepTextEquals, 'basic_EVE_TOT.shard: #0',
          'Test had failed runs. Check "Test Results" tab for '
          'the deterministic results.'),
      # Only Test.Two should appear in the build summary, because Test.One
      # has a green run.
      api.post_process(
          post_process.ResultReason,
          '1 Test Suite(s) failed.\n\n**basic_EVE_TOT** '
          'failed because of:\n\n- Test.Two'),
      api.post_process(post_process.StatusFailure),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'basic for gtest',
      boilerplate(
          'chrome-test-builds',
          test_args='--test-launcher-filter-file=../../testing/buildbot/filter',
          target_name=GTEST_TARGET),
      api.skylab.mock_wait_on_suites(
          'find test runner build',
          1,
          runner_builds=[(902, common_pb2.SUCCESS)]),
      api.override_step_data(
          'basic_EVE_TOT results',
          stdout=api.raw_io.output_text(
              api.test_utils.rdb_results(
                  'basic_EVE_TOT',
                  failing_tests=['Test.One'],
                  skipped_tests=['Test.One']))),
      api.post_process(
          post_process.MustRun,
          'prepare skylab tests.'
          'upload skylab runtime deps for {target}.'
          'Generic Archiving Steps.'
          'Copy file out/Release/bin/run_{target}'.format(target=GTEST_TARGET),
      ),
      api.post_process(
          post_process.MustRun,
          ('prepare skylab tests.upload skylab runtime deps for %s.'
           'Generic Archiving Steps.'
           'Copy folder testing/buildbot/filters') % GTEST_TARGET,
      ),
      api.post_process(post_process.StepCommandContains, 'compile',
                       [GTEST_TARGET]),
      api.post_process(
          post_process.StepCommandContains,
          'test_pre_run.schedule skylab tests.basic_EVE_TOT.schedule', [
              '-lacros-path',
              'gs://chrome-test-builds/lacros/8945511751514863184_with_patch/%s'
              % GTEST_TARGET
          ]),
      api.post_process(
          _check_test_args,
          'test_pre_run.schedule skylab tests.basic_EVE_TOT.schedule',
          'exe_rel_path=out/Release/bin/run_%s' % GTEST_TARGET),
      api.post_process(post_process.StepFailure, 'basic_EVE_TOT'),
      api.post_process(
          post_process.ResultReason,
          '1 Test Suite(s) failed.\n\n**basic_EVE_TOT** '
          'failed because of:\n\n- Test.One'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'test from fyi builder',
      boilerplate('chrome-test-builds', builder='lacros-amd64-generic-fyi'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'RDB returned empty test_results',
      boilerplate(
          'chrome-test-builds', tast_expr='("group:mainline" && "dep:lacros")'),
      api.skylab.mock_wait_on_suites('find test runner build', 1),
      api.post_process(post_process.StepCommandContains, 'compile', ['chrome']),
      api.post_process(post_process.StepException, 'basic_EVE_TOT'),
      api.post_process(post_process.StepTextContains, 'basic_EVE_TOT',
                       ['Test did not run or failed to report to ResultDB.']),
      api.post_process(post_process.DropExpectation),
  )

  # CrOS lab has outage and no response from the buildbucket call.
  yield api.test(
      'Skylab outage',
      boilerplate(
          'chrome-test-builds', tast_expr='("group:mainline" && "dep:lacros")'),
      api.post_process(post_process.StepException, 'basic_EVE_TOT'),
      api.post_process(post_process.ResultReason,
                       '1 Test Suite(s) failed.\n\n**basic_EVE_TOT** failed.'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'not scheduled for absent skylab gcs',
      boilerplate('', tast_expr=TAST_TARGET, should_read_isolate=False),
      api.post_process(
          post_process.StepTextContains, 'basic_EVE_TOT',
          ['Test was not scheduled because of absent lacros_gcs_path.']),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'failed to find isolate file',
      boilerplate(
          'chrome-test-builds',
          tast_expr='("group:mainline" && "dep:lacros")',
          isolate_file_exists=False),
      api.post_process(
          post_process.StepFailure,
          'prepare skylab tests.collect runtime deps for %s' % TAST_TARGET),
      api.post_process(post_process.ResultReason,
                       'Failed to find the %s.isolate.' % TAST_TARGET),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'failed to parse isolate file',
      boilerplate(
          'chrome-test-builds',
          tast_expr='("group:mainline" && "dep:lacros")',
          isolate_content=BAD_ISOLATE_TEXT),
      api.post_process(
          post_process.StepFailure,
          'prepare skylab tests.collect runtime deps for %s' % TAST_TARGET),
      api.post_process(post_process.ResultReason,
                       'Failed to parse the %s.isolate' % TAST_TARGET),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'target has no deps',
      boilerplate(
          'chrome-test-builds',
          tast_expr='("group:mainline" && "dep:lacros")',
          isolate_content=EMPTY_FILE_LIST),
      api.post_process(
          post_process.StepFailure,
          'prepare skylab tests.collect runtime deps for %s' % TAST_TARGET),
      api.post_process(post_process.ResultReason,
                       'No dependencies attached to target %s.' % TAST_TARGET),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'test_suite_with_decription_on_ci_builder',
      boilerplate(
          'chrome-test-builds', tast_expr='("group:mainline" && "dep:lacros")'),
      api.skylab.mock_wait_on_suites('find test runner build', 1),
      api.post_process(post_process.StepTextContains, 'basic_EVE_TOT',
                       ['This is a description.']),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'ci_only_test_on_ci_builder',
      boilerplate(
          'chrome-test-builds', tast_expr='("group:mainline" && "dep:lacros")'),
      api.skylab.mock_wait_on_suites('find test runner build', 1),
      api.post_process(post_process.StepTextContains, 'basic_EVE_TOT', [
          'This test will not be run on try builders',
      ]),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'ci_only_test_on_trybot',
      boilerplate(
          'chrome-test-builds',
          tast_expr='dummy_tast',
          is_ci_build=False,
          should_read_isolate=False),
      api.post_process(post_process.StepTextContains, 'ci_only tests',
                       ['* basic_EVE_TOT']),
      api.post_process(
          post_process.DoesNotRun,
          'basic_EVE_TOT (with patch)',
      ),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'ci_only_test_on_trybot_bypass',
      boilerplate(
          'chrome-test-builds', tast_expr='dummy_tast', is_ci_build=False),
      api.step_data('parse description',
                    api.json.output({'Include-Ci-Only-Tests': ['true']})),
      api.post_process(
          post_process.MustRun,
          'basic_EVE_TOT (with patch)',
      ),
      api.post_process(post_process.StepTextContains,
                       'basic_EVE_TOT (with patch)',
                       [('This test is being run due to the'
                         ' Include-Ci-Only-Tests gerrit footer')]),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'basic for telemetry test',
      boilerplate(
          'chrome-test-builds', benchmark='speedometer2', target_name='chrome'),
      api.skylab.mock_wait_on_suites(
          'find test runner build',
          1,
          runner_builds=[(902, common_pb2.SUCCESS)]),
      api.override_step_data(
          'basic_EVE_TOT results',
          stdout=api.raw_io.output_text(
              api.test_utils.rdb_results(
                  'basic_EVE_TOT',
                  failing_tests=['Test.One'],
                  skipped_tests=['Test.One']))),
      api.post_process(
          post_process.StepCommandContains,
          'test_pre_run.schedule skylab tests.basic_EVE_TOT.schedule',
          ['chromium_Telemetry']),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'retry_without_patch',
      boilerplate(
          'chrome-test-builds',
          is_ci_build=False,
          target_name=GTEST_TARGET,
          ci_only_tests=False),
      api.step_data(
          'prepare skylab tests (2).'
          'collect runtime deps for %s.read isolate file' % GTEST_TARGET,
          api.file.read_text(GOOD_ISOLATE_TEXT)),
      api.skylab.mock_wait_on_suites(
          'find test runner build',
          1,
          runner_builds=[(902, common_pb2.SUCCESS)]),
      api.override_step_data(
          'basic_EVE_TOT results',
          stdout=api.raw_io.output_text(
              api.test_utils.rdb_results(
                  'basic_EVE_TOT',
                  failing_tests=['Test.One'],
                  skipped_tests=['Test.One']))),
      api.post_process(post_process.MustRun, 'test_pre_run (without patch)'),
      api.post_process(post_process.DropExpectation),
  )
