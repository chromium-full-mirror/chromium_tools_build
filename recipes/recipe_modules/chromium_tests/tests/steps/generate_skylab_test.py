# Copyright 2020 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb2

from RECIPE_MODULES.build import chromium_tests_builder_config as ctbc

from recipe_engine import post_process

DEPS = [
    'chromium_tests',
    'chromium_tests_builder_config',
    'skylab',
    'test_utils',
    'depot_tools/tryserver',
    'recipe_engine/file',
    'recipe_engine/path',
    'recipe_engine/properties',
    'recipe_engine/json',
    'recipe_engine/raw_io',
    'siso',
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

  UNIFIED_RUNTIME_DEPS_NAME = 'unified_runtime_deps'

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
                  test_args='',
                  benchmark='',
                  isolate_content=GOOD_ISOLATE_TEXT,
                  isolate_file_exists=True,
                  is_ci_build=True,
                  target_name=TAST_TARGET,
                  should_read_isolate=True,
                  ci_only_tests=False,
                  tester='',
                  shards=1,
                  cros_test_tags=None,
                  cros_test_max_in_shards=None,
                  has_retry_without_patch=False):
    builders = {
        builder_group: {
            builder:
                ctbc.BuilderSpec.create(
                    chromium_config='chromium',
                    chromium_apply_config=['shared_build_dir'],
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
          chromium_apply_config=['shared_build_dir'],
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
        'benchmark': benchmark,
        'args': [test_args],
        'test': target_name,
        'resultdb': {
            'enable': True,
        },
        'timeout_sec': 7200,
        'shards': shards,
    }

    if cros_test_tags:
      test_spec['cros_test_tags'] = cros_test_tags
    if cros_test_max_in_shards:
      test_spec['cros_test_max_in_shards'] = cros_test_max_in_shards

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

    build_dir = source_dir / 'out' / 'shared-Release'
    mock_paths.append(build_dir / 'chrome')
    mock_paths.append(build_dir / f'bin/run_{target_name}')

    mock_paths.append(build_dir / f'bin/{target_name}.filter')
    if isolate_file_exists:
      mock_paths.append(build_dir / f'{target_name}.isolate')
    steps += api.path.exists(*mock_paths)
    if isolate_file_exists and should_read_isolate:
      steps += api.step_data(
          'prepare skylab tests.'
          'collect runtime deps for %s.read isolate file' % target_name,
          api.file.read_text(isolate_content))
      if has_retry_without_patch:
        steps += api.step_data(
            'prepare skylab tests (2).'
            'collect runtime deps for %s.read isolate file' % target_name,
            api.file.read_text(isolate_content))
    return steps

  def _check_test_args(check, step_odict, step, argument, value):
    cmd = step_odict[step].cmd
    check(
        any(cmd[i:i + 3] == ['--test-arg', argument, value]
            for i in range(len(cmd) - 2)))

  def _check_link_equals(check, step_odict, step, link_name, link_value):
    check(f'link {link_name} for step {step} is {link_value}',
          (link_name, link_value) in step_odict[step].links.items())

  yield api.test(
      'basic',
      api.properties(orchestrator={'builder_name': 'orchestrator'}),
      boilerplate('chrome-test-builds'),
      api.skylab.mock_wait_on_suites(
          'basic_EVE_TOT', 1, runner_builds=[(901, common_pb2.FAILURE)]),
      api.post_process(post_process.StepCommandContains, 'compile',
                       ['lacros_fyi_tast_tests']),
      api.post_process(
          post_process.LogContains,
          ('prepare skylab tests.upload skylab runtime deps for %s.'
           'Generic Archiving Steps.Write file list to copy') %
          UNIFIED_RUNTIME_DEPS_NAME,
          'tmp_tmp_2',
          ['out/shared-Release/chrome'],
      ),
      api.post_process(
          post_process.LogContains,
          ('prepare skylab tests.upload skylab runtime deps for %s.'
           'Generic Archiving Steps.Write file list to copy (2)') %
          UNIFIED_RUNTIME_DEPS_NAME,
          'tmp_tmp_5',
          ['metadata.json'],
      ),
      api.post_process(
          post_process.MustRun,
          ('prepare skylab tests.upload skylab runtime deps for %s.'
           'write metadata.json') % UNIFIED_RUNTIME_DEPS_NAME,
      ),
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
              f'{UNIFIED_RUNTIME_DEPS_NAME}/skylab_runtime_deps.tar.zst'
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
          'https://tests.chromeos.goog/p/chromeos/logs/unified/'
          'invocations/build-8702874632118866193?treeQuery=cros-test',
      ),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'some shards had failed before run tests',
      boilerplate(
          'chrome-test-builds',
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
      boilerplate('chrome-test-builds', shards=3),
      api.skylab.mock_wait_on_suites(
          'basic_EVE_TOT',
          3,
          runner_builds=[(901, common_pb2.SUCCESS), (902, common_pb2.SUCCESS),
                         (903, common_pb2.INFRA_FAILURE)]),
      api.override_step_data(
          'basic_EVE_TOT (retry shards).read_ctp_response',
          api.m.json.output({
              'some test 2': {
                  'url': 'http://runner-link/904',
                  'shard': 2,
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
      'some shards infra failure and retry succeeded tfc',
      boilerplate(
          'chrome-test-builds',
          cros_test_tags=['group:mainline', 'dep:lacros'],
          cros_test_max_in_shards=20),
      api.skylab.mock_wait_on_suites(
          'basic_EVE_TOT',
          3,
          runner_builds=[(901, common_pb2.SUCCESS), (902, common_pb2.SUCCESS),
                         (903, common_pb2.INFRA_FAILURE)]),
      api.override_step_data(
          'basic_EVE_TOT (retry shards).read_ctp_response',
          api.m.json.output({
              'some test 2': {
                  'url': 'http://runner-link/904',
                  'shard': 2,
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
                  failing_tests=['Test.Four'],
              ))),
      api.override_step_data(
          'basic_EVE_TOT results (2)',
          stdout=api.raw_io.output_text(
              api.test_utils.rdb_results(
                  'basic_EVE_TOT', passing_tests=['Test.Two', 'Test.Four']))),
      # Already passing tests are excluded.
      api.post_process(
          post_process.StepCommandContains,
          'test_pre_run (retry shards).basic_EVE_TOT (retry shards).schedule', [
              '--cros-test-names-exclude',
              'Test.One',
          ]),
      api.post_process(
          post_process.StepCommandContains,
          'test_pre_run (retry shards).basic_EVE_TOT (retry shards).schedule', [
              '--cros-test-names-exclude',
              'Test.Three',
          ]),
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
          shards=1),
      api.skylab.mock_wait_on_suites(
          'basic_EVE_TOT', 1, runner_builds=[(901, common_pb2.INFRA_FAILURE)]),
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
          'Write file list to copy'.format(target=UNIFIED_RUNTIME_DEPS_NAME),
          'tmp_tmp_2',
          [f'out/shared-Release/bin/run_{GTEST_TARGET}'],
      ),
      api.post_process(
          post_process.MustRun,
          ('prepare skylab tests.upload skylab runtime deps for %s.'
           'Generic Archiving Steps.'
           'Copy folder testing/buildbot/filters') % UNIFIED_RUNTIME_DEPS_NAME,
      ),
      api.post_process(post_process.StepCommandContains, 'compile',
                       [GTEST_TARGET]),
      api.post_process(_check_test_args, 'test_pre_run.basic_EVE_TOT.schedule',
                       'exe_rel_path',
                       f'out/shared-Release/bin/run_{GTEST_TARGET}'),
      api.post_process(
          post_process.StepCommandContains,
          'test_pre_run.basic_EVE_TOT.schedule', [
              '--lacros-gcs-path', 'gs://chrome-test-builds/lacros/'
              f'8945511751514863184_with_patch/{UNIFIED_RUNTIME_DEPS_NAME}/'
              'skylab_runtime_deps.tar.zst'
          ]),
      api.post_process(post_process.StepFailure, 'basic_EVE_TOT'),
      api.post_process(
          post_process.SummaryMarkdown,
          '1 Test Suite(s) failed.\n\n**basic_EVE_TOT** '
          'failed because of:\n\n- Test.One'),
      api.post_process(
          _check_link_equals, 'basic_EVE_TOT.shard: #0', 'debug log',
          'https://tests.chromeos.goog/p/chromeos/logs/unified/'
          'invocations/build-8702874632118866193?treeQuery=cros-test'),
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
      boilerplate('chrome-test-builds'),
      api.skylab.mock_wait_on_suites('basic_EVE_TOT', 1),
      api.post_process(post_process.StepCommandContains, 'compile',
                       ['lacros_fyi_tast_tests']),
      api.post_process(post_process.StepException, 'basic_EVE_TOT'),
      api.post_process(post_process.StepTextContains, 'basic_EVE_TOT',
                       ['Test did not run or failed to report to ResultDB.']),
      api.post_process(post_process.DropExpectation),
      api.expect_status('FAILURE'),
  )

  # CrOS lab has outage and no response from the buildbucket call.
  yield api.test(
      'Skylab outage',
      boilerplate('chrome-test-builds'),
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
      boilerplate('', should_read_isolate=False),
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
          'chrome-test-builds', is_ci_build=False, target_name=GTEST_TARGET),
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
      'retry_without_patch tfc',
      boilerplate(
          'chrome-test-builds',
          is_ci_build=False,
          cros_test_tags=['group:mainline', 'dep:lacros'],
          cros_test_max_in_shards=20,
          has_retry_without_patch=True),
      api.skylab.mock_wait_on_suites(
          'basic_EVE_TOT (with patch)',
          3,
          runner_builds=[(901, common_pb2.SUCCESS), (902, common_pb2.SUCCESS),
                         (903, common_pb2.FAILURE)]),
      api.skylab.mock_wait_on_suites(
          'basic_EVE_TOT (retry shards with patch)',
          1,
          runner_builds=[(909, common_pb2.FAILURE)]),
      api.override_step_data(
          'basic_EVE_TOT results',
          stdout=api.raw_io.output_text(
              api.test_utils.rdb_results(
                  'basic_EVE_TOT',
                  passing_tests=['Test.One', 'Test.Three'],
                  failing_tests=['Test.Two'],
              ))),
      api.override_step_data(
          'basic_EVE_TOT results (2)',
          stdout=api.raw_io.output_text(
              api.test_utils.rdb_results(
                  'basic_EVE_TOT', failing_tests=['Test.Two']))),
      # Retry without patch.
      api.post_process(post_process.MustRun, 'test_pre_run (without patch)'),
      api.skylab.mock_wait_on_suites('basic_EVE_TOT (without patch)', 1),
      api.post_process(post_process.DropExpectation),
      # Already failing tests leads to builder success.
      api.override_step_data(
          'basic_EVE_TOT results (3)',
          stdout=api.raw_io.output_text(
              api.test_utils.rdb_results(
                  'basic_EVE_TOT', failing_tests=['Test.Two']))),
      api.expect_status('SUCCESS'),
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
          'Write file list to copy'.format(target=UNIFIED_RUNTIME_DEPS_NAME),
      ),
      api.post_process(
          post_process.MustRun,
          ('prepare skylab tests.upload skylab runtime deps for %s.'
           'Generic Archiving Steps.'
           'Copy folder testing/buildbot/filters') % UNIFIED_RUNTIME_DEPS_NAME,
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
                      'out/shared-Release/bin/run_vaapi_unittest',
                  'lacros_gcs_path':
                      'gs://chrome-test-builds/lacros/8945511751514863184_with_patch/vaapi_unittest',
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
                      'benchmark': "",
                      'args': [],
                      'test': GTEST_TARGET,
                      'resultdb': {
                          'enable': True,
                      },
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
                  'basic_EVE_TOT', passing_tests=['Test.One']))),
      api.post_process(
          post_process.DoesNotRun,
          'prepare skylab tests.'
          'upload skylab runtime deps for {target}.'
          'Generic Archiving Steps.'
          'Copy file out/shared-Release/bin/run_{target}'.format(
              target=GTEST_TARGET),
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
      api.step_data('collect skylab results.wait', times_out_after=7201 + 600),
      api.override_step_data(
          'basic_EVE_TOT results',
          stdout=api.raw_io.output_text(
              api.test_utils.rdb_results(
                  'basic_EVE_TOT', passing_tests=['Test.One']))),
      api.post_process(post_process.StepException, 'collect skylab results'),
      api.post_process(post_process.MustRun, 'basic_EVE_TOT.read_ctp_response'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'prepare skylab tests with siso fs flush',
      boilerplate(
          'chrome-test-builds',
          test_args='--test-launcher-filter-file=../../testing/buildbot/filter',
          target_name=GTEST_TARGET),
      api.siso.properties(
          configs=["remote-link"], output_local_strategy="minimum"),
      api.skylab.mock_wait_on_suites('basic_EVE_TOT', 1),
      api.override_step_data(
          'basic_EVE_TOT results',
          stdout=api.raw_io.output_text(
              api.test_utils.rdb_results(
                  'basic_EVE_TOT', passing_tests=['Test.One']))),
      api.post_process(
          post_process.MustRun,
          f'prepare skylab tests.collect runtime deps for {GTEST_TARGET}.fetch RBE artifacts from CAS'
      ),
      api.post_process(post_process.DropExpectation),
  )

  # This isn't testing ci_only specifically, just covering the skylab-specific
  # bits of code that are executed for disabled tests
  yield api.test(
      'disabled-test',
      boilerplate(
          'chrome-test-builds',
          is_ci_build=False,
          isolate_file_exists=False,
          ci_only_tests=True),
      api.post_process(
          post_process.StepCommandEmpty,
          'basic_EVE_TOT (with patch)',
      ),
      api.post_process(post_process.DropExpectation),
  )
