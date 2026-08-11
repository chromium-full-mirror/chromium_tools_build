# Copyright 2020 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process

from RECIPE_MODULES.build import chromium_tests_builder_config as ctbc

DEPS = [
    'chromium',
    'chromium_swarming',
    'chromium_tests',
    'chromium_tests_builder_config',
    'depot_tools/bot_update',
    'depot_tools/tryserver',
    'recipe_engine/buildbucket',
    'recipe_engine/commit_position',
    'recipe_engine/file',
    'recipe_engine/json',
    'recipe_engine/path',
    'recipe_engine/platform',
    'recipe_engine/properties',
    'recipe_engine/raw_io',
    'recipe_engine/runtime',
    'recipe_engine/step',
    'recipe_engine/swarming',
    'test_utils',
]


def RunSteps(api):
  api.chromium_swarming.set_default_dimension('os', 'Linux')
  builder_id, builder_config = (
      api.chromium_tests_builder_config.lookup_builder())
  if api.tryserver.is_tryserver:
    with api.chromium.chromium_layout():
      return api.chromium_tests.trybot_steps(builder_id, builder_config)
  else:
    with api.chromium.chromium_layout():
      build_result, _ = api.chromium_tests.main_waterfall_steps(
          builder_id, builder_config)
      return build_result


def GenTests(api):
  fake_group = 'fake-group'
  fake_builder = 'fake-builder'
  fake_tester = 'fake-tester'
  fake_try_builder = 'fake-try-builder'
  fake_test = 'fake_test'
  webgl_fake_test = 'webgl_fake_test'
  fake_targets_spec = (fake_group, {
      fake_tester: {
          'isolated_scripts': [{
              'name': fake_test,
              'swarming': {},
          }],
      }
  })
  fake_swarm_hashes = {
      fake_test: 'eeeeeeeeeeeeeeeeeeeeeeeeeeeeee/size',
      webgl_fake_test: 'gggggggggggggggggggggggggg/size'
  }
  fake_command_lines_digest = (
      'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855/0')
  fake_command_lines = {
      fake_test: [
          './%s' % fake_test, '--fake-flag', '--fake-log-file',
          '$ISOLATED_OUTDIR/fake.log'
      ],
      webgl_fake_test: [
          './%s' % webgl_fake_test, '--fake-flag', '--fake-log-file',
          '$ISOLATED_OUTDIR/fake.log'
      ],
  }
  fake_rts_command_lines_digest = (
      'rts-e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855/0')
  fake_rts_command_lines = {
      fake_test:
          fake_command_lines[fake_test] +
          ['--test-launcher-filter-file=gen/rts/fake_test.filter'],
  }
  fake_rts_complement_command_lines_digest = (
      'rts_complement-e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855/0'
  )
  fake_rts_complement_command_lines = {
      fake_test:
          fake_command_lines[fake_test] +
          ['--test-launcher-filter-file=gen/rts/fake_test_inverted.filter'],
  }

  def is_subsequence(containing, contained):
    result = False
    for i in range(len(containing) - len(contained) + 1):
      if containing[i:i + len(contained)] == contained:
        result = True
        break
    return result

  yield api.test(
      'combined_builder_tester',
      api.chromium_tests_builder_config.ci_build(
          builder_group=fake_group,
          builder=fake_tester,
          builder_db=ctbc.BuilderDatabase.create({
              fake_group: {
                  fake_tester:
                      ctbc.BuilderSpec.create(
                          chromium_config='chromium',
                          gclient_config='chromium',
                      ),
              },
          }),
      ),
      api.properties(swarm_hashes=fake_swarm_hashes),
      api.chromium_tests.read_targets_spec(*fake_targets_spec),
      api.step_data('find command lines', api.json.output(fake_command_lines)),
      api.post_process(
          api.swarming.check_triggered_request,
          'test_pre_run.[trigger] %s' % fake_test, lambda check, req: check(req[
              0].env_vars['ISOLATED_OUTDIR'] == '${ISOLATED_OUTDIR}'), lambda
          check, req: check(req[0].relative_cwd == 'out/bb71-fake-tester'),
          lambda check, req: check(
              is_subsequence(req[0].command, fake_command_lines[fake_test]))),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'combined_builder_tester_use_swarming',
      api.chromium_tests_builder_config.ci_build(
          builder_group=fake_group,
          builder=fake_tester,
          builder_db=ctbc.BuilderDatabase.create({
              fake_group: {
                  fake_tester:
                      ctbc.BuilderSpec.create(
                          chromium_config='chromium',
                          gclient_config='chromium',
                      ),
              },
          }),
      ),
      api.properties(swarm_hashes=fake_swarm_hashes),
      api.chromium_tests.read_targets_spec(*fake_targets_spec),
      api.step_data('find command lines', api.json.output(fake_command_lines)),
      api.post_check(
          api.swarming.check_triggered_request,
          'test_pre_run.[trigger] %s' % fake_test, lambda check, req: check(req[
              0].relative_cwd == 'out/bb71-fake-tester'),
          lambda check, req: check(
              is_subsequence(req[0].command, fake_command_lines[fake_test])),
          lambda check, req: check(req[0].env_vars['ISOLATED_OUTDIR'] ==
                                   '${ISOLATED_OUTDIR}')),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'combined_builder_tester_use_swarming_go_in_trigger_script',
      api.chromium_tests_builder_config.ci_build(
          builder_group=fake_group,
          builder=fake_tester,
          builder_db=ctbc.BuilderDatabase.create({
              fake_group: {
                  fake_tester:
                      ctbc.BuilderSpec.create(
                          chromium_config='chromium',
                          gclient_config='chromium',
                      ),
              },
          }),
      ),
      api.properties(swarm_hashes=fake_swarm_hashes),
      api.chromium_tests.read_targets_spec(*fake_targets_spec),
      api.step_data('find command lines', api.json.output(fake_command_lines)),
      api.post_process(post_process.DropExpectation),
  )

  fake_builder_db = ctbc.BuilderDatabase.create({
      fake_group: {
          fake_builder:
              ctbc.BuilderSpec.create(
                  chromium_config='chromium', gclient_config='chromium'),
          fake_tester:
              ctbc.BuilderSpec.create(
                  execution_mode=ctbc.TEST,
                  parent_buildername=fake_builder,
                  chromium_config='chromium',
                  gclient_config='chromium'),
      }
  })

  yield api.test(
      'build_only_builder_sets_command_line_hash_and_cwd_in_trigger',
      api.chromium_tests_builder_config.ci_build(
          builder_group=fake_group,
          builder=fake_builder,
          builder_db=fake_builder_db,
      ),
      api.properties(swarm_hashes=fake_swarm_hashes),
      api.chromium_tests.read_targets_spec(*fake_targets_spec),
      api.step_data('find command lines', api.json.output(fake_command_lines)),
      api.step_data('archive command lines to RBE-CAS',
                    api.raw_io.output_text(fake_command_lines_digest)),
      api.post_process(post_process.LogContains, 'trigger', 'input',
                       [fake_command_lines_digest]),
      api.post_process(post_process.LogContains, 'trigger', 'input',
                       ['swarming_command_lines_cwd', 'out/ceb4-fake-builder']),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'test_only_builder_gets_command_lines_hash_and_cwd_from_trigger',
      api.chromium_tests_builder_config.ci_build(
          builder_group=fake_group,
          builder=fake_tester,
          builder_db=fake_builder_db,
      ),
      api.properties(
          swarm_hashes=fake_swarm_hashes,
          swarming_command_lines_digest=fake_command_lines_digest,
          swarming_command_lines_cwd='out/Release_x64'),
      api.chromium_tests.read_targets_spec(*fake_targets_spec),
      api.step_data('read command lines',
                    api.file.read_json(fake_command_lines)),
      api.post_process(
          api.swarming.check_triggered_request,
          'test_pre_run.[trigger] fake_test',
          lambda check, req: check(req[0].relative_cwd == 'out/Release_x64'),
          lambda check, req: check(
              is_subsequence(req[0].command, fake_command_lines[fake_test]))),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'test_only_builder_with_no_isolates',
      api.chromium_tests_builder_config.ci_build(
          builder_group=fake_group,
          builder=fake_tester,
          parent_buildername=fake_builder,
          builder_db=fake_builder_db,
      ),
      api.chromium_tests.read_targets_spec(
          fake_group, {fake_tester: {
              'gtest_tests': [{
                  'name': fake_test,
              }],
          }}),
      api.expect_status('FAILURE'),
      api.post_process(
          post_process.SummaryMarkdown,
          'Only isolated and/or Skylab tests are allowed on child testers.'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'trybot_with_test_failure',
      api.chromium_tests_builder_config.try_build(
          builder_group=fake_group,
          builder=fake_try_builder,
          builder_db=fake_builder_db,
          try_db=ctbc.TryDatabase.create({
              fake_group: {
                  fake_try_builder:
                      ctbc.TrySpec.create(mirrors=[
                          ctbc.TryMirror.create(
                              builder_group=fake_group,
                              buildername=fake_builder,
                              tester=fake_tester,
                          )
                      ])
              }
          }),
      ),
      api.properties(
          config='Release',
          swarm_hashes=fake_swarm_hashes,
      ),
      api.chromium_tests.read_targets_spec(*fake_targets_spec),
      api.override_step_data(
          'read filter exclusion spec',
          api.file.read_json({
              'base': {
                  'exclusions': ['f.*'],
              },
              'chromium': {
                  'exclusions': [],
              }
          })),
      api.chromium_tests.gen_swarming_and_rdb_results(
          fake_test, 'with patch', failures=['Test.One']),
      api.chromium_tests.gen_swarming_and_rdb_results(
          fake_test, 'retry shards with patch', failures=['Test.One']),
      api.chromium_tests.gen_swarming_and_rdb_results(fake_test,
                                                      'without patch'),
      api.post_process(post_process.MustRun, 'find command lines (with patch)'),
      api.post_process(post_process.MustRun,
                       'find command lines (without patch)'),
      api.post_process(post_process.DropExpectation),
      api.expect_status('FAILURE'),
  )

  yield api.test(
      "ci_bot_with_experimental_test",
      api.chromium_tests_builder_config.ci_build(
          builder_group=fake_group,
          builder=fake_tester,
          builder_db=ctbc.BuilderDatabase.create({
              fake_group: {
                  fake_tester:
                      ctbc.BuilderSpec.create(
                          chromium_config='chromium',
                          gclient_config='chromium',
                      ),
              },
          }),
      ),
      api.properties(swarm_hashes=fake_swarm_hashes),
      api.chromium_tests.read_targets_spec(
          fake_group, {
              fake_tester: {
                  'isolated_scripts': [{
                      'name': fake_test,
                      'swarming': {},
                      'experiment_percentage': 100
                  }],
              }
          }),
      api.step_data('find command lines', api.json.output(fake_command_lines)),
      api.post_process(
          api.swarming.check_triggered_request,
          'test_pre_run.[trigger] %s (experimental)' % fake_test, lambda check,
          req: check(req[0].relative_cwd == 'out/bb71-fake-tester'),
          lambda check, req: check(
              is_subsequence(req[0].command, fake_command_lines[fake_test]))),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      "ci_bot_with_rdb_enabled_swarming_test",
      api.chromium_tests_builder_config.ci_build(
          builder_group=fake_group,
          builder=fake_tester,
          builder_db=ctbc.BuilderDatabase.create({
              fake_group: {
                  fake_tester:
                      ctbc.BuilderSpec.create(
                          chromium_config='chromium',
                          gclient_config='chromium',
                      ),
              },
          }),
      ),
      api.properties(swarm_hashes=fake_swarm_hashes),
      api.chromium_tests.read_targets_spec(
          fake_group, {
              fake_tester: {
                  'isolated_scripts': [{
                      'name': fake_test,
                      'resultdb': {
                          'enable': True,
                          'has_native_resultdb_integration': True,
                          'test_location_base': '//test/location',
                      },
                      'swarming': {
                          'dimensions': {
                              'id': 'botid',
                              'device_type': 'phone',
                              'device_os': 'android',
                              'gpu': 'nv',
                              'os': 'Linux',
                          },
                      },
                      'test_id_prefix': 'ninja://:fake_test/',
                  }],
              }
          }),
      api.step_data('find command lines', api.json.output(fake_command_lines)),
      api.post_process(
          api.swarming.check_triggered_request,
          'test_pre_run.[trigger] %s on (nv) GPU on Linux' % fake_test, lambda
          check, req: check(req[0].relative_cwd == 'out/bb71-fake-tester'),
          lambda check, req: check(
              is_subsequence(req[0].command, [
                  'rdb',
                  'stream',
                  '-test-id-prefix',
                  'ninja://:fake_test/',
                  '-var',
                  'bucket:ci',
                  '-var',
                  'builder:fake-tester',
                  '-var',
                  'device_os:android',
                  '-var',
                  'device_type:phone',
                  '-var',
                  'gpu:nv',
                  '-var',
                  'os:Linux',
                  '-var',
                  'test_suite:fake_test',
                  '-test-location-base',
                  '//test/location',
                  '-tag',
                  'step_name:%s on (nv) GPU on Linux' % fake_test,
                  '-tag',
                  'target_platform:linux',
                  '-coerce-negative-duration',
                  '-location-tags-file',
                  '../../testing/location_tags.json',
                  '-exonerate-unexpected-pass',
                  '-inherit-sources',
                  '-baseline-id',
                  'ci:fake-tester',
                  '--',
              ] + fake_command_lines[fake_test]))),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      "ci_bot_with_default_resultdb_swarming_gtest",
      api.chromium_tests_builder_config.ci_build(
          builder_group=fake_group,
          builder=fake_tester,
          builder_db=ctbc.BuilderDatabase.create({
              fake_group: {
                  fake_tester:
                      ctbc.BuilderSpec.create(
                          chromium_config='chromium',
                          gclient_config='chromium',
                      ),
              },
          }),
      ),
      api.properties(swarm_hashes=fake_swarm_hashes),
      api.chromium_tests.read_targets_spec(
          fake_group, {
              fake_tester: {
                  'gtest_tests': [{
                      'name': fake_test,
                      'swarming': {
                          'dimensions': {
                              'os': 'Ubuntu-16.04',
                          },
                      },
                      'test_id_prefix': 'ninja://:fake_test/',
                  }],
              }
          }),
      api.step_data('find command lines', api.json.output(fake_command_lines)),
      api.post_process(
          api.swarming.check_triggered_request,
          'test_pre_run.[trigger] %s on Ubuntu-16.04' % fake_test, lambda check,
          req: check(req[0].relative_cwd == 'out/bb71-fake-tester'),
          lambda check, req: check(
              is_subsequence(req[0].command, [
                  'rdb',
                  'stream',
                  '-test-id-prefix',
                  'ninja://:fake_test/',
                  '-var',
                  'bucket:ci',
                  '-var',
                  'builder:fake-tester',
                  '-var',
                  'os:Ubuntu-16.04',
                  '-var',
                  'test_suite:fake_test',
                  '-tag',
                  'step_name:%s on Ubuntu-16.04' % fake_test,
                  '-tag',
                  'target_platform:linux',
                  '-coerce-negative-duration',
                  '-location-tags-file',
                  '../../testing/location_tags.json',
                  '-exonerate-unexpected-pass',
                  '-inherit-sources',
                  '-baseline-id',
                  'ci:fake-tester',
                  '--',
                  'result_adapter',
                  'gtest',
                  '-result-file',
                  '${ISOLATED_OUTDIR}/output.json',
                  '-artifact-directory',
                  '${ISOLATED_OUTDIR}',
                  '--',
              ] + fake_command_lines[fake_test]))),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'rts_active_skipping_builder',
      api.chromium_tests_builder_config.ci_build(
          builder_group=fake_group,
          builder=fake_builder,
          builder_db=fake_builder_db,
          experiments=['chromium_rts.filter_file_analysis'],
      ),
      api.properties(swarm_hashes=fake_swarm_hashes),
      api.chromium_tests.read_targets_spec(*fake_targets_spec),
      api.path.exists(
          api.path.cache_dir / 'builder' / 'src' / 'out' / 'ceb4-fake-builder' /
          'gen' / 'rts' / 'fake_test.filter',
          api.path.cache_dir / 'builder' / 'src' / 'out' / 'ceb4-fake-builder' /
          'gen' / 'rts' / 'fake_test_inverted.filter',
          api.path.cache_dir / 'builder' / 'src' / 'out' / 'ceb4-fake-builder' /
          'fake_test.isolate',
      ),
      api.override_step_data(
          'add RTS filter files to isolates.Read [CACHE]/builder/src/out/ceb4-fake-builder/fake_test.isolate',
          api.file.read_json({'variables': {
              'files': []
          }})),
      api.step_data('find command lines', api.json.output(fake_command_lines)),
      api.step_data('archive command lines to RBE-CAS',
                    api.raw_io.output_text(fake_command_lines_digest)),
      api.step_data('archive rts command lines to RBE-CAS',
                    api.raw_io.output_text(fake_rts_command_lines_digest)),
      api.step_data(
          'archive rts_complement command lines to RBE-CAS',
          api.raw_io.output_text(fake_rts_complement_command_lines_digest)),
      api.post_process(post_process.LogContains, 'trigger', 'input',
                       [fake_command_lines_digest]),
      api.post_process(post_process.LogContains, 'trigger', 'input',
                       [fake_rts_command_lines_digest]),
      api.post_process(post_process.LogContains, 'trigger', 'input', [
          'swarming_command_lines_variant_digests',
          fake_rts_command_lines_digest
      ]),
      api.post_process(post_process.LogContains, 'trigger', 'input', [
          'swarming_command_lines_variant_digests',
          fake_rts_complement_command_lines_digest
      ]),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'rts_active_skipping_tester',
      api.chromium_tests_builder_config.ci_build(
          builder_group=fake_group,
          builder=fake_tester,
          builder_db=fake_builder_db,
      ),
      api.properties(
          swarm_hashes=fake_swarm_hashes,
          swarming_command_lines_digest=fake_command_lines_digest,
          swarming_command_lines_variant_digests={
              'rts': fake_rts_command_lines_digest,
              'rts_complement': fake_rts_complement_command_lines_digest,
          },
          swarming_command_lines_cwd='out/Release_x64'),
      api.chromium_tests.read_targets_spec(*fake_targets_spec),
      api.step_data('read command lines',
                    api.file.read_json(fake_command_lines)),
      api.step_data('read rts command lines',
                    api.file.read_json(fake_rts_command_lines)),
      api.step_data('read rts_complement command lines',
                    api.file.read_json(fake_rts_complement_command_lines)),
      api.post_process(post_process.MustRun, 'download rts command lines'),
      api.post_process(post_process.MustRun, 'read rts command lines'),
      api.post_process(post_process.MustRun,
                       'download rts_complement command lines'),
      api.post_process(post_process.MustRun,
                       'read rts_complement command lines'),
      api.post_process(post_process.DropExpectation),
  )
