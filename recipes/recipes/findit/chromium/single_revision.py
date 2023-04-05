# Copyright 2019 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine.post_process import (DoesNotRun, DropExpectation, MustRun,
                                        StepCommandContains, StatusSuccess)

from PB.recipes.build.findit.chromium.single_revision import InputProperties
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb

from RECIPE_MODULES.build import chromium
from RECIPE_MODULES.build.chromium_tests import steps
from RECIPE_MODULES.build import chromium_tests_builder_config as ctbc

DEPS = [
    'chromium',
    'chromium_swarming',
    'chromium_tests',
    'chromium_tests_builder_config',
    'depot_tools/tryserver',
    'filter',
    'findit',
    'goma',
    'isolate',
    'recipe_engine/buildbucket',
    'recipe_engine/json',
    'recipe_engine/properties',
    'recipe_engine/swarming',
    'test_utils',
]

PROPERTIES = InputProperties


def RunSteps(api, properties):
  if properties.isolate_targets:
    for target in properties.isolate_targets:
      args = [
          '--isolated-script-test-repeat=%d' % properties.test_repeat_count
      ] if properties.test_repeat_count else []
      api.isolate.run_isolated("Run isolated test '%s'" % target.hash,
                               target.hash, args)
    return

  # 0. Validate properties.
  assert (
      properties.target_builder and properties.target_builder.group and
      properties.target_builder.builder), 'Target builder property is required'

  # 1. Configure the builder.
  builder_id, builder_config, compile_kwargs = _configure_builder(
      api, properties.target_builder)

  # 2. Check out the code.
  bot_update_step, build_config = api.chromium_tests.prepare_checkout(
      builder_config, set_output_commit=False, report_cache_state=False)

  # 3. Configure swarming
  api.chromium_swarming.configure_swarming('chromium', precommit=False)

  # 4. Determine what to build and what to test.
  compile_targets, test_objects = _compute_targets_and_tests(
      api, builder_config, build_config, builder_id, properties.tests,
      properties.compile_targets, properties.skip_analyze)

  # 5. Build what's needed.

  # Since these builders run on different platforms, and require different Goma
  # settings depending on the platform, set the Goma ATS flag based on the OS.
  api.goma.configure_enable_ats()

  if compile_targets:
    compile_result, _ = api.chromium_tests.compile_specific_targets(
        builder_id,
        builder_config,
        bot_update_step,
        build_config,
        compile_targets,
        tests=test_objects,
        **compile_kwargs)
    if compile_result.status != common_pb.SUCCESS:
      return compile_result

  # 6. Run the tests.
  _run_tests(api, builder_config, test_objects, properties.tests,
             properties.test_repeat_count)


def _configure_builder(api, target_tester):
  target_builder_id = chromium.BuilderId.create_for_group(
      target_tester.group, target_tester.builder)
  builder_config = api.findit.get_builder_config(target_builder_id)
  api.chromium_tests.configure_build(builder_config)

  # If there is a problem with goma, rather than default to compiling locally
  # only, fail. This is important because findit relies on fast compile for
  # timely production of actionable changes, and local compilation alone is
  # unlikely to help findit find a culprit in time for automatic revert.
  # Better to fail the analysis and let the sheriffs try to find a culprit
  # manually.
  api.chromium.apply_config('goma_failfast')

  compile_kwargs = {
      'override_execution_mode': ctbc.COMPILE_AND_TEST,
  }
  return builder_config.builder_ids[0], builder_config, compile_kwargs


def _compute_targets_and_tests(api, builder_config, targets_config, builder_id,
                               requested_tests, compile_targets, skip_analyze):
  if requested_tests:
    # Figure out which test steps to run.
    requested_tests_to_run = [
        test for test in targets_config.all_tests
        if test.canonical_name in requested_tests
    ]

    # Figure out the test targets to be compiled.
    requested_test_targets = []
    for test in requested_tests_to_run:
      requested_test_targets.extend(test.compile_targets())
    requested_test_targets = sorted(set(requested_test_targets))

    if skip_analyze:
      # All test targets requested
      return requested_test_targets, requested_tests_to_run

    # '' is src/ relative to src/
    changed_files = api.tryserver.get_files_affected_by_patch('')

    affected_test_targets, actual_compile_targets = (
        api.filter.analyze(
            changed_files,
            test_targets=tuple(requested_test_targets),
            additional_compile_targets=tuple(compile_targets),
            builder_id=builder_id))

    actual_tests_to_run = []
    for test in requested_tests_to_run:
      targets = test.compile_targets()
      if targets and not any(t in affected_test_targets for t in targets):
        # Skip tests whose targets are not affected by the change.
        # NB: Non-compiled tests i.e. checkperms are not filtered out.
        continue
      actual_tests_to_run.append(test)
    # Targets filtered by analyze
    return actual_compile_targets, actual_tests_to_run

  # No tests, only compile targets
  default_compile_targets = targets_config.compile_targets
  # If no targets were specifically requested, compile every target in the spec.
  if not compile_targets:
    return default_compile_targets, []
  # Filter out targets that do not exist in this revision. i.e. By calling
  # `ninja query`.
  existing_targets = api.findit.existing_targets(
      compile_targets, builder_id=builder_id)
  return existing_targets, []


def _run_tests(api, builder_config, test_objects, requested_tests,
               test_repeat_count):
  # Default to 20 repeats.
  test_repeat_count = test_repeat_count or 20
  # test_objects are instances of the classes under chromium_tests/steps
  # whereas requested_tests is the dictionary passed as a property mapping a
  # test step to the test names to run.
  for test_obj in test_objects:
    test_filter = tuple(requested_tests[test_obj.canonical_name].names)
    # ScriptTests do not support test_options property
    if not isinstance(test_obj, steps.ScriptTest):
      test_obj.test_options = steps.TestOptions.create(
          test_filter=test_filter,
          repeat_count=test_repeat_count,
          retry_limit=0 if test_repeat_count else None,
          run_disabled=bool(test_repeat_count))

  # Run the tests.
  with api.chromium_tests.wrap_chromium_tests(builder_config, test_objects):
    return api.test_utils.run_tests(test_objects, '')


def GenTests(api):

  def _StepCommandNotContains(check, step_odict, step, arg):
    check('Step %s does not contain %s' % (step, arg),
          arg not in step_odict[step].cmd)

  def _common(api,
              test_repeat_count=20,
              target_builder_group='chromium.findit',
              target_builder='fake-builder',
              tests=None,
              compile_targets=None,
              spec=None,
              skip_analyze=False):
    """Create test properties and other data for tests."""
    tests = tests or {}
    compile_targets = compile_targets or []
    _default_spec = 'chromium.findit', {
        'fake-builder': {
            'isolated_scripts': [{
                'isolate_name': 'blink_web_tests',
                'name': 'blink_web_tests',
                'swarming': {
                    'can_use_on_swarming_builders': True,
                    'dimension_sets': [{
                        'os': 'Linux',
                    },],
                    'shards': 1,
                },
            }],
            'gtest_tests': [{
                'test': 'base_unittests',
                'swarming': {
                    'can_use_on_swarming_builders': True,
                    'dimension_sets': [{
                        'os': 'Linux',
                    },],
                },
            }],
        }
    }
    _default_builders = ctbc.BuilderDatabase.create({
        'chromium.findit': {
            'fake-tester':
                ctbc.BuilderSpec.create(
                    parent_buildername='fake-builder',
                    chromium_config='chromium',
                    chromium_apply_config=['mb'],
                    gclient_config='chromium',
                    chromium_config_kwargs={
                        'BUILD_CONFIG': 'Release',
                        'TARGET_BITS': 64,
                    },
                    execution_mode=ctbc.TEST,
                    simulation_platform='linux',
                ),
            'fake-builder':
                ctbc.BuilderSpec.create(
                    chromium_config='chromium',
                    chromium_apply_config=['mb'],
                    gclient_config='chromium',
                    chromium_config_kwargs={
                        'BUILD_CONFIG': 'Release',
                        'TARGET_BITS': 64,
                    },
                    simulation_platform='linux',
                ),
        },
    })

    props_proto = InputProperties()
    props_proto.skip_analyze = skip_analyze
    props_proto.test_repeat_count = test_repeat_count
    props_proto.target_builder.group = target_builder_group
    props_proto.target_builder.builder = target_builder
    for t in compile_targets:
      props_proto.compile_targets.append(t)
    for k, v in tests.items():
      new_message = props_proto.tests[k]
      for t in v:
        new_message.names.append(t)

    t = sum([
        api.chromium.ci_build(
            builder_group=target_builder_group,
            builder=target_builder,
        ),
        api.properties(props_proto),
        api.chromium_tests.read_targets_spec(*(spec or _default_spec)),
        api.chromium_tests_builder_config.databases(_default_builders),
    ], api.empty_test_data())
    return t

  yield api.test(
      'all_targets',
      _common(api),
      api.post_process(MustRun, 'compile'),
      api.post_process(StepCommandContains, 'compile', ['blink_web_tests']),
      api.post_process(StepCommandContains, 'compile', ['base_unittests']),
      api.post_process(StatusSuccess),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'specific_target',
      _common(api, compile_targets=['base_unittests', 'missing_target']),
      api.override_step_data('check_targets',
                             api.json.output({
                                 'found': ['base_unittests']
                             })),
      api.post_process(MustRun, 'compile'),
      api.post_process(StepCommandContains, 'compile', ['base_unittests']),
      api.post_process(_StepCommandNotContains, 'compile', 'missing_target'),
      api.post_process(StatusSuccess),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'compile_failure',
      _common(api),
      api.step_data('compile', retcode=1),
      api.expect_status('FAILURE'),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'with_tests',
      _common(
          api,
          skip_analyze=True,
          tests={
              'blink_web_tests': [
                  'fast/Test/One.html', 'fast/Test/Two.html', 'dummy/Three.js'
              ],
              'base_unittests': []
          }),
      api.post_process(MustRun, 'compile'),
      api.post_process(MustRun, 'test_pre_run.[trigger] blink_web_tests'),
      api.post_check(
          api.swarming.check_triggered_request,
          'test_pre_run.[trigger] blink_web_tests', lambda check, req: check(
              '--gtest_filter='
              'fast/Test/One.html:fast/Test/Two.html:dummy/Three.js' in req[
                  0].command)),
      api.post_process(MustRun, 'test_pre_run.[trigger] base_unittests'),
      api.post_process(DoesNotRun, 'analyze'),
      api.post_process(StatusSuccess),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'with_tests_and_analyze',
      _common(
          api,
          target_builder='fake-tester',
          tests={
              'blink_web_tests': [
                  'fast/Test/One.html', 'fast/Test/Two.html', 'dummy/Three.js'
              ],
              'base_unittests': [],
          }),
      api.filter.analyze_output(
          status='Found dependency',
          test_targets=['base_unittests'],
          compile_targets=['base_unittests'],
      ),
      api.post_process(MustRun, 'compile'),
      api.post_process(DoesNotRun, 'test_pre_run.[trigger] blink_web_tests'),
      api.post_process(MustRun, 'test_pre_run.[trigger] base_unittests'),
      api.post_process(MustRun, 'analyze'),
      api.post_process(StatusSuccess),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'compile_skipped',
      _common(
          api,
          tests={'checkperms': []},
          spec=('chromium.findit', {
              'fake-builder': {
                  'scripts': [{
                      'name': 'checkperms',
                      'script': 'checkperms.py'
                  }]
              }
          }),
      ),
      api.post_process(DoesNotRun, 'compile'),
      api.post_process(MustRun, 'checkperms'),
      api.post_process(StatusSuccess),
      api.post_process(DropExpectation),
  )

  isolate_properties = InputProperties()
  isolate_properties.isolate_targets.add(server='server1', hash='hash1')
  isolate_properties.isolate_targets.add(server='server2', hash='hash2')
  yield api.test('isolate_targets', api.properties(isolate_properties))
