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
    'recipe_engine/properties',
    'recipe_engine/json',
    'recipe_engine/raw_io',
    'recipe_engine/step',
]


def RunSteps(api):
  builder_id, builder_config = (
      api.chromium_tests_builder_config.lookup_builder())
  if api.tryserver.is_tryserver:
    return api.chromium_tests.trybot_steps(builder_id, builder_config)
  build_result, _ = api.chromium_tests.main_waterfall_steps(
      builder_id, builder_config)
  return build_result


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
                  experiment_percentage=None,
                  ci_only_tests=True,
                  tester='',
                  shards=1,
                  retries=0):
    builders = {
        builder_group: {
            builder:
                ctbc.BuilderSpec.create(
                    chromium_config='chromium',
                    gclient_config='chromium',
                    skylab_gs_bucket=skylab_gcs,
                    skylab_gs_extra='lacros',
                ),
        }
    }
    if tester:
      builders[builder_group][tester] = ctbc.BuilderSpec.create(
          execution_mode=ctbc.TEST,
          chromium_config='chromium',
          gclient_config='chromium',
          skylab_gs_bucket=skylab_gcs,
          skylab_gs_extra='lacros',
          parent_buildername=builder,
      )
    builder_db = ctbc.BuilderDatabase.create(builders)
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

    test_spec = {
        'cros_board': 'eve',
        'cros_img': 'eve-release/R89-13631.0.0',
        'test_id_prefix': 'ninja://basic_EVE_TOT/',
        'ci_only': ci_only_tests,
        'name': 'basic_EVE_TOT',
        'tast_expr': tast_expr,
        'benchmark': benchmark,
        'args': [test_args],
        'test': target_name,
        'resultdb': {
            'enable': True,
        },
        'description': 'This is a description.',
        'timeout_sec': 7200,
        'shards': shards,
        'retries': retries,
    }
    if experiment_percentage is not None:
      test_spec['experiment_percentage'] = experiment_percentage

    steps = sum([
        build_gen,
        api.chromium_tests.read_targets_spec(
            builder_group,
            {
                tester or builder: {
                    'skylab_tests': [test_spec],
                },
            },
        ),
    ], api.empty_test_data())
    # Mock the file/folder for recipe training.
    mock_paths = [
        api.path.start_dir.joinpath('squashfs', 'squashfs-tools', 'mksquashfs')
    ]
    source_dir = api.path.cache_dir / 'builder/src'
    # testing/buildbot/filters should be a folder.
    mock_paths.append(source_dir / 'testing/buildbot/filters/foo')
    mock_paths.append(source_dir / 'out/Release/chrome')
    mock_paths.append(source_dir / f'out/Release/bin/run_{target_name}')

    mock_paths.append(source_dir / f'out/Release/bin/{target_name}.filter')
    if isolate_file_exists:
      mock_paths.append(source_dir / f'out/Release/{target_name}.isolate')
    steps += api.path.exists(*mock_paths)
    if isolate_file_exists and should_read_isolate:
      steps += api.step_data(
          'prepare skylab tests.'
          'collect runtime deps for %s.read isolate file' % target_name,
          api.file.read_text(isolate_content))
    return steps

  def _check_test_args(check, step_odict, step, argument):
    cmd = step_odict[step].cmd
    check(argument in cmd[cmd.index('--test-args') + 1])

  def _check_link_equals(check, step_odict, step, link_name, link_value):
    check(f'link {link_name} for step {step} is {link_value}',
          (link_name, link_value) in step_odict[step].links.items())

  yield api.test(
      'basic for tast',
      boilerplate(
          'chrome-test-builds',
          tast_expr='("group:mainline" && "dep:lacros")',
          retries=1),
      api.skylab.mock_wait_on_suites(
          'basic_EVE_TOT', 1, runner_builds=[(901, common_pb2.FAILURE)]),
      api.post_process(post_process.StepCommandContains, 'compile', ['chrome']),
      api.post_process(
          post_process.LogContains,
          ('prepare skylab tests.upload skylab runtime deps for %s.'
           'Generic Archiving Steps.Write file list to copy') % TAST_TARGET,
          'tmp_tmp_2',
          ['out/Release/chrome'],
      ),
      api.post_process(
          post_process.LogContains,
          ('prepare skylab tests.upload skylab runtime deps for %s.'
           'Generic Archiving Steps.Write file list to copy (2)') % TAST_TARGET,
          'tmp_tmp_5',
          ['metadata.json'],
      ),
      api.post_process(
          post_process.MustRun,
          ('prepare skylab tests.upload skylab runtime deps for %s.'
           'write metadata.json') % TAST_TARGET,
      ),
      api.post_process(_check_test_args, 'test_pre_run.basic_EVE_TOT.schedule',
                       'tast_expr_file=out/Release/bin/%s.filter' %
                       TAST_TARGET),
      api.override_step_data(
          'basic_EVE_TOT results',
          stdout=api.raw_io.output_text(
              api.test_utils.rdb_results(
                  'basic_EVE_TOT',
                  failing_tests=['Test.Two'],
                  flaky_failing_tests=['Test.One']))),
      api.post_process(
          post_process.StepCommandContains,
          'test_pre_run.basic_EVE_TOT.schedule', [
              '--lacros-gcs-path',
              'gs://chrome-test-builds/lacros/8945511751514863184_with_patch/'
              f'{TAST_TARGET}/skylab_runtime_deps.tar.zst'
          ]),
      api.post_process(post_process.StepFailure, 'basic_EVE_TOT.shard: #0'),
      api.post_process(post_process.StepFailure, 'basic_EVE_TOT'),
      # Only Test.Two should appear in the build summary, because Test.One
      # has a green run.
      api.post_process(
          post_process.SummaryMarkdown,
          '1 Test Suite(s) failed.\n\n**basic_EVE_TOT** '
          'failed because of:\n\n- Test.Two'),
      api.post_process(
          _check_link_equals,
          'basic_EVE_TOT.shard: #0',
          'debug log',
          'https://cros-test-analytics.appspot.com/p/chromeos/file/view/'
          'chromeos-test-logs/test-runner/prod/2023-12-07/abcd/'
          '?test=&file=autoserv_test%2Ftast%2Fdebug%2Ftast.DEBUG',
      ),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'some shards had failed before run tests',
      boilerplate(
          'chrome-test-builds',
          tast_expr='("group:mainline" && "dep:lacros")',
          shards=2),
      api.skylab.mock_wait_on_suites(
          'basic_EVE_TOT',
          2,
          runner_builds=[(901, common_pb2.FAILURE), (902, common_pb2.SUCCESS)]),
      api.override_step_data(
          'basic_EVE_TOT results',
          stdout=api.raw_io.output_text(
              api.test_utils.rdb_results(
                  'basic_EVE_TOT',
                  passing_tests=['Test.Two'],
                  failing_tests=['Test.One']))),
      api.post_process(post_process.StepFailure, 'basic_EVE_TOT.shard: #0'),
      api.post_process(post_process.StepSuccess, 'basic_EVE_TOT.shard: #1'),
      api.post_process(post_process.StepFailure, 'basic_EVE_TOT'),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'some shards infra failure and retry succeeded',
      boilerplate(
          'chrome-test-builds',
          tast_expr='("group:mainline" && "dep:lacros")',
          shards=3,
          retries=1),
      api.skylab.mock_wait_on_suites(
          'basic_EVE_TOT',
          3,
          runner_builds=[(901, common_pb2.SUCCESS), (902, common_pb2.SUCCESS),
                         (903, common_pb2.INFRA_FAILURE)]),
      api.override_step_data(
          'test_pre_run (retry shards).basic_EVE_TOT (retry shards).schedule',
          api.m.json.output({
              'ctp_build_id': '889901',
          })),
      api.override_step_data(
          'basic_EVE_TOT (retry shards).read_ctp_response',
          api.m.json.output({
              '2': {
                  'url': 'http://runner-link/904',
                  'log_url': 'https://runner-log-link',
                  'status': common_pb2.Status.Name(common_pb2.SUCCESS),
              }
          })),
      api.override_step_data(
          'basic_EVE_TOT results',
          stdout=api.raw_io.output_text(
              api.test_utils.rdb_results(
                  'basic_EVE_TOT',
                  passing_tests=['Test.One', 'Test.Three'],
              ))),
      api.override_step_data(
          'basic_EVE_TOT results (2)',
          stdout=api.raw_io.output_text(
              api.test_utils.rdb_results(
                  'basic_EVE_TOT', passing_tests=['Test.Two']))),
      # Only retried shard 2.
      api.post_process(
          post_process.StepCommandContains,
          'test_pre_run (retry shards).basic_EVE_TOT (retry shards).schedule',
          ['--shard-indexes', '2']),
      # Only retry the infra failed test runner build 903 and pass.
      api.post_process(post_process.StepSuccess, 'basic_EVE_TOT.shard: #0'),
      api.post_process(post_process.StepSuccess, 'basic_EVE_TOT.shard: #1'),
      api.post_process(post_process.StepException, 'basic_EVE_TOT.shard: #2'),
      api.post_process(post_process.StepSuccess,
                       'basic_EVE_TOT (retry shards).shard: #2'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'infra failure not recovered by retry',
      boilerplate(
          'chrome-test-builds',
          tast_expr='("group:mainline" && "dep:lacros")',
          shards=1),
      api.skylab.mock_wait_on_suites(
          'basic_EVE_TOT', 1, runner_builds=[(901, common_pb2.INFRA_FAILURE)]),
      api.override_step_data(
          'test_pre_run (retry shards).basic_EVE_TOT (retry shards).schedule',
          api.m.json.output({
              'ctp_build_id': '889901',
          })),
      api.skylab.mock_wait_on_suites(
          'basic_EVE_TOT (retry shards)',
          1,
          runner_builds=[(904, common_pb2.INFRA_FAILURE)]),
      api.post_process(
          post_process.StepCommandContains, 'test_pre_run (retry shards).'
          'basic_EVE_TOT (retry shards).schedule', ['--shard-indexes', '0']),
      api.post_process(post_process.DoesNotRun, 'find test runner build (3)'),
      api.expect_status('INFRA_FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'basic for gtest',
      boilerplate(
          'chrome-test-builds',
          test_args='--test-launcher-filter-file=../../testing/buildbot/filter',
          target_name=GTEST_TARGET),
      api.skylab.mock_wait_on_suites('basic_EVE_TOT', 1),
      api.override_step_data(
          'basic_EVE_TOT results',
          stdout=api.raw_io.output_text(
              api.test_utils.rdb_results(
                  'basic_EVE_TOT',
                  failing_tests=['Test.One'],
                  skipped_tests=['Test.One']))),
      api.post_process(
          post_process.LogContains,
          'prepare skylab tests.'
          'upload skylab runtime deps for {target}.'
          'Generic Archiving Steps.'
          'Write file list to copy'.format(target=GTEST_TARGET),
          'tmp_tmp_2',
          [f'out/Release/bin/run_{GTEST_TARGET}'],
      ),
      api.post_process(
          post_process.MustRun,
          ('prepare skylab tests.upload skylab runtime deps for %s.'
           'Generic Archiving Steps.'
           'Copy folder testing/buildbot/filters') % GTEST_TARGET,
      ),
      api.post_process(post_process.StepCommandContains, 'compile',
                       [GTEST_TARGET]),
      api.post_process(_check_test_args, 'test_pre_run.basic_EVE_TOT.schedule',
                       'exe_rel_path=out/Release/bin/run_%s' % GTEST_TARGET),
      api.post_process(
          post_process.StepCommandContains,
          'test_pre_run.basic_EVE_TOT.schedule', [
              '--lacros-gcs-path', 'gs://chrome-test-builds/lacros/'
              f'8945511751514863184_with_patch/{GTEST_TARGET}/'
              'skylab_runtime_deps.tar.zst'
          ]),
      api.post_process(post_process.StepFailure, 'basic_EVE_TOT'),
      api.post_process(
          post_process.SummaryMarkdown,
          '1 Test Suite(s) failed.\n\n**basic_EVE_TOT** '
          'failed because of:\n\n- Test.One'),
      api.post_process(
          _check_link_equals, 'basic_EVE_TOT.shard: #0', 'debug log',
          'https://cros-test-analytics.appspot.com/p/chromeos/file/view/'
          'chromeos-test-logs/test-runner/prod/2023-12-07/abcd/'
          '?test=&file=autoserv_test%2Fchromium%2Fdebug%2Fchromium.DEBUG'),
      api.post_process(post_process.DropExpectation),
      api.expect_status('FAILURE'),
  )

  yield api.test(
      'test from fyi builder',
      boilerplate('chrome-test-builds', builder='lacros-amd64-generic-fyi'),
      api.skylab.mock_wait_on_suites('basic_EVE_TOT', 1),
      api.override_step_data(
          'basic_EVE_TOT results',
          stdout=api.raw_io.output_text(
              api.test_utils.rdb_results(
                  'basic_EVE_TOT', passing_tests=['Test.One']))),
      api.post_process(post_process.StepCommandContains,
                       'test_pre_run.basic_EVE_TOT.schedule',
                       ['--qs-account', 'lacros_fyi']),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'RDB returned empty test_results',
      boilerplate(
          'chrome-test-builds', tast_expr='("group:mainline" && "dep:lacros")'),
      api.skylab.mock_wait_on_suites('basic_EVE_TOT', 1),
      api.post_process(post_process.StepCommandContains, 'compile', ['chrome']),
      api.post_process(post_process.StepException, 'basic_EVE_TOT'),
      api.post_process(post_process.StepTextContains, 'basic_EVE_TOT',
                       ['Test did not run or failed to report to ResultDB.']),
      api.post_process(post_process.DropExpectation),
      api.expect_status('FAILURE'),
  )

  # CrOS lab has outage and no response from the buildbucket call.
  yield api.test(
      'Skylab outage',
      boilerplate(
          'chrome-test-builds', tast_expr='("group:mainline" && "dep:lacros")'),
      api.override_step_data('basic_EVE_TOT.read_ctp_response',
                             api.m.json.output({})),
      api.override_step_data('basic_EVE_TOT (retry shards).read_ctp_response',
                             api.m.json.output({})),
      api.post_process(post_process.StepException, 'basic_EVE_TOT'),
      api.post_process(
          post_process.SummaryMarkdown,
          '1 Test Suite(s) failed.\n\n**basic_EVE_TOT** '
          'did not complete, likely due to an infra bug.'),
      api.post_process(post_process.DropExpectation),
      api.expect_status('INFRA_FAILURE'),
  )

  yield api.test(
      'not scheduled for absent skylab gcs',
      boilerplate('', tast_expr=TAST_TARGET, should_read_isolate=False),
      api.post_process(
          post_process.SummaryMarkdown,
          'Test was not scheduled because of absent lacros_gcs_path.'),
      api.post_process(post_process.DropExpectation),
      api.expect_status('INFRA_FAILURE'),
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
      api.post_process(post_process.SummaryMarkdown,
                       'Failed to find the %s.isolate.' % TAST_TARGET),
      api.post_process(post_process.DropExpectation),
      api.expect_status('FAILURE'),
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
      api.post_process(post_process.SummaryMarkdown,
                       'Failed to parse the %s.isolate' % TAST_TARGET),
      api.post_process(post_process.DropExpectation),
      api.expect_status('FAILURE'),
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
      api.post_process(post_process.SummaryMarkdown,
                       'No dependencies attached to target %s.' % TAST_TARGET),
      api.post_process(post_process.DropExpectation),
      api.expect_status('FAILURE'),
  )

  yield api.test(
      'test_suite_with_decription_on_ci_builder',
      boilerplate(
          'chrome-test-builds', tast_expr='("group:mainline" && "dep:lacros")'),
      api.skylab.mock_wait_on_suites('basic_EVE_TOT', 1),
      api.post_process(post_process.StepTextContains, 'basic_EVE_TOT',
                       ['This is a description.']),
      api.post_process(post_process.DropExpectation),
      api.expect_status('FAILURE'),
  )

  yield api.test(
      'ci_only_test_on_ci_builder',
      boilerplate(
          'chrome-test-builds', tast_expr='("group:mainline" && "dep:lacros")'),
      api.skylab.mock_wait_on_suites('basic_EVE_TOT', 1),
      api.post_process(post_process.StepTextContains, 'basic_EVE_TOT', [
          'This test will not be run on try builders',
      ]),
      api.post_process(post_process.DropExpectation),
      api.expect_status('FAILURE'),
  )

  yield api.test(
      'ci_only_test_on_trybot',
      boilerplate(
          'chrome-test-builds',
          tast_expr='dummy_tast',
          is_ci_build=False,
          isolate_file_exists=False),
      api.post_process(
          post_process.StepCommandEmpty,
          'basic_EVE_TOT (with patch)',
      ),
      api.post_process(
          post_process.StepTextContains,
          'basic_EVE_TOT (with patch)',
          [("This test is not being run because it is marked 'ci_only'. "
            "Use 'Include-Ci-Only-Tests: true' to override.")],
      ),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'ci_only_test_on_trybot_bypass',
      boilerplate(
          'chrome-test-builds', tast_expr='dummy_tast', is_ci_build=False),
      api.step_data('parse description',
                    api.json.output({'Include-Ci-Only-Tests': ['true']})),
      api.skylab.mock_wait_on_suites('basic_EVE_TOT (with patch)', 1),
      api.override_step_data(
          'basic_EVE_TOT results',
          stdout=api.raw_io.output_text(
              api.test_utils.rdb_results(
                  'basic_EVE_TOT', passing_tests=['Test.One']))),
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
      api.skylab.mock_wait_on_suites('basic_EVE_TOT', 1),
      api.override_step_data(
          'basic_EVE_TOT results',
          stdout=api.raw_io.output_text(
              api.test_utils.rdb_results(
                  'basic_EVE_TOT',
                  failing_tests=['Test.One'],
                  skipped_tests=['Test.One']))),
      api.post_process(post_process.StepCommandContains,
                       'test_pre_run.basic_EVE_TOT.schedule',
                       ['chromium_Telemetry']),
      api.post_process(post_process.DropExpectation),
      api.expect_status('FAILURE'),
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
      api.skylab.mock_wait_on_suites('basic_EVE_TOT (with patch)', 1),
      api.override_step_data(
          'basic_EVE_TOT results',
          stdout=api.raw_io.output_text(
              api.test_utils.rdb_results(
                  'basic_EVE_TOT',
                  failing_tests=['Test.One'],
                  skipped_tests=['Test.One']))),
      # Retry without patch.
      api.post_process(post_process.MustRun, 'test_pre_run (without patch)'),
      api.skylab.mock_wait_on_suites('basic_EVE_TOT (without patch)', 1),
      api.post_process(post_process.DropExpectation),
      api.expect_status('FAILURE'),
  )

  yield api.test(
      'experimental test experiment on',
      boilerplate(
          'chrome-test-builds',
          tast_expr='dummy_tast',
          experiment_percentage='100'),
      api.post_process(
          post_process.MustRun,
          'basic_EVE_TOT (experimental)',
      ),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'experimental test experiment off',
      boilerplate(
          'chrome-test-builds',
          tast_expr='dummy_tast',
          experiment_percentage='0',
          isolate_file_exists=False),
      api.post_process(
          post_process.StepCommandEmpty,
          'basic_EVE_TOT (experimental)',
      ),
      api.post_process(
          post_process.StepTextContains,
          'basic_EVE_TOT (experimental)',
          ['This test was not selected for its experiment in this build'],
      ),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'trigger tester',
      boilerplate(
          'chrome-test-builds',
          test_args='--test-launcher-filter-file=../../testing/buildbot/filter',
          target_name=GTEST_TARGET,
          tester='chrome-tests'),
      api.post_process(
          post_process.MustRun,
          'prepare skylab tests.'
          'upload skylab runtime deps for {target}.'
          'Generic Archiving Steps.'
          'Write file list to copy'.format(target=GTEST_TARGET),
      ),
      api.post_process(
          post_process.MustRun,
          ('prepare skylab tests.upload skylab runtime deps for %s.'
           'Generic Archiving Steps.'
           'Copy folder testing/buildbot/filters') % GTEST_TARGET,
      ),
      api.post_process(post_process.StepCommandContains, 'compile',
                       [GTEST_TARGET]),
      api.post_process(post_process.DoesNotRun,
                       'test_pre_run.basic_EVE_TOT.schedule'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'tester',
      api.chromium_tests_builder_config.ci_build(
          builder_group='chromium.chromiumos',
          builder='chrome-tests',
          builder_db=ctbc.BuilderDatabase.create({
              'chromium.chromiumos': {
                  'chrome-tests':
                      ctbc.BuilderSpec.create(
                          execution_mode=ctbc.TEST,
                          chromium_config='chromium',
                          gclient_config='chromium',
                          skylab_gs_bucket='chrome-test-builds',
                          skylab_gs_extra='lacros',
                          parent_buildername='chrome-builder',
                      ),
                  'chrome-builder':
                      ctbc.BuilderSpec.create(
                          chromium_config='chromium',
                          gclient_config='chromium',
                          skylab_gs_bucket='chrome-test-builds',
                          skylab_gs_extra='lacros',
                      ),
              },
          }),
      ),
      api.properties(
          skylab_trigger_properties={
              'vaapi_unittest': {
                  'exe_rel_path':
                      'out/Release/bin/run_vaapi_unittest',
                  'lacros_gcs_path':
                      'gs://chrome-test-builds/lacros/8945511751514863184_with_patch/vaapi_unittest',
                  'tast_expr_file':
                      None
              }
          }),
      api.chromium_tests.read_targets_spec(
          'chromium.chromiumos', {
              'chrome-tests': {
                  'skylab_tests': [{
                      'cros_board': 'eve',
                      'cros_img': 'eve-release/R89-13631.0.0',
                      'test_id_prefix': 'ninja://basic_EVE_TOT/',
                      'ci_only': True,
                      'name': 'basic_EVE_TOT',
                      'tast_expr': "",
                      'benchmark': "",
                      'args': [],
                      'test': GTEST_TARGET,
                      'resultdb': {
                          'enable': True,
                      },
                      'description': 'This is a description.',
                      'timeout_sec': 7200,
                  }],
              },
          }),
      api.skylab.mock_wait_on_suites(
          'basic_EVE_TOT', 1, runner_builds=[(902, common_pb2.SUCCESS)]),
      api.override_step_data(
          'basic_EVE_TOT results',
          stdout=api.raw_io.output_text(
              api.test_utils.rdb_results(
                  'basic_EVE_TOT', successful_tests=['Test.One']))),
      api.post_process(
          post_process.DoesNotRun,
          'prepare skylab tests.'
          'upload skylab runtime deps for {target}.'
          'Generic Archiving Steps.'
          'Copy file out/Release/bin/run_{target}'.format(target=GTEST_TARGET),
      ),
      api.post_process(
          post_process.DoesNotRun,
          ('prepare skylab tests.upload skylab runtime deps for %s.'
           'Generic Archiving Steps.'
           'Copy folder testing/buildbot/filters') % GTEST_TARGET,
      ),
      api.post_process(post_process.DoesNotRun, 'compile'),
      api.post_process(post_process.MustRun,
                       'test_pre_run.basic_EVE_TOT.schedule'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'collect_results_even_timeout',
      boilerplate(
          'chrome-test-builds',
          test_args='--test-launcher-filter-file=../../testing/buildbot/filter',
          target_name=GTEST_TARGET),
      api.skylab.mock_wait_on_suites('basic_EVE_TOT', 1),
      api.step_data('collect skylab results.wait', times_out_after=7201),
      api.override_step_data(
          'basic_EVE_TOT results',
          stdout=api.raw_io.output_text(
              api.test_utils.rdb_results(
                  'basic_EVE_TOT', passing_tests=['Test.One']))),
      api.post_process(post_process.StepException, 'collect skylab results'),
      api.post_process(post_process.MustRun, 'basic_EVE_TOT.read_ctp_response'),
      api.post_process(post_process.DropExpectation),
  )
