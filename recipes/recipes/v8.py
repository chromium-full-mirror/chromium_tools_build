# Copyright 2013 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import json

from recipe_engine import recipe_test_api
from recipe_engine.post_process import (Filter, DoesNotRun, DoesNotRunRE,
                                        DropExpectation, MustRun, StepException,
                                        StepFailure, SummaryMarkdownRE)
from recipe_engine.recipe_api import Property

from PB.recipe_modules.recipe_engine.led import properties as led_properties_pb

DEPS = [
    'archive',
    'chromium',
    'chromium_swarming',
    'depot_tools/gclient',
    'recipe_engine/buildbucket',
    'recipe_engine/context',
    'recipe_engine/json',
    'recipe_engine/path',
    'recipe_engine/platform',
    'recipe_engine/properties',
    'recipe_engine/raw_io',
    'recipe_engine/scheduler',
    'recipe_engine/step',
    'recipe_engine/swarming',
    'recipe_engine/url',
    'recipe_engine/time',
    'test_utils',
    'depot_tools/tryserver',
    'v8',
    'v8_tests',
]

PROPERTIES = {
    # Additional configurations to enable binary size tracking. The mapping
    # consists of "binary" and "category".
    'binary_size_tracking': Property(default=None, kind=dict),
    # Deprecated.
    'build_config': Property(default=None, kind=str),
    # Switch to clobber build dir before runhooks.
    'clobber': Property(default=False, kind=bool),
    # Switch to clobber build dir before bot_update.
    'clobber_all': Property(default=False, kind=bool),
    # Additional configurations set for archiving builds to GS buckets for
    # clusterfuzz. The mapping consists of "name", "bucket" and optional
    # "bitness".
    'clusterfuzz_archive': Property(default=None, kind=dict),
    # Optional coverage setting. Set to "llvm" to use.
    'coverage': Property(default=None, kind=str),
    # Mapping of custom dependencies to sync (dependency name as in DEPS
    # file -> deps url).
    'custom_deps': Property(default=None, kind=dict),
    # Optional list of default targets. If not specified the implicit "all"
    # target will be built.
    'default_targets': Property(default=None, kind=list),
    # Switch to enable/disable swarming.
    'enable_swarming': Property(default=None, kind=bool),
    # Mapping of additional gclient variables to set (map name -> value).
    'gclient_vars': Property(default=None, kind=dict),
    # Optional path to a different MB config. The path must be relative to the
    # V8 checkout and using forward slashes.
    'mb_config_path': Property(default=None, kind=str),
    # One of intel|arm|mips.
    'target_arch': Property(default=None, kind=str),
    # One of android|fuchsia|linux|mac|win.
    'target_platform': Property(default=None, kind=str),
    # Weather to track and upload build-dependencies stats.
    'track_build_dependencies': Property(default=None, kind=bool),
    # List of tester names to trigger.
    'triggers': Property(default=None, kind=list),
    # Weather to trigger the internal trigger proxy.
    'triggers_proxy': Property(default=False, kind=bool),
}


def RunSteps(api, binary_size_tracking, build_config, clobber, clobber_all,
             clusterfuzz_archive, coverage, custom_deps, default_targets,
             enable_swarming, gclient_vars, mb_config_path, target_arch,
             target_platform, track_build_dependencies, triggers,
             triggers_proxy):
  link_to_parent(api)
  v8 = api.v8
  api.v8_tests.read_cl_footer_flags()
  api.v8_tests.load_static_test_configs()
  bot_config = v8.get_bot_config(
      binary_size_tracking,
      clusterfuzz_archive,
      coverage,
      enable_swarming,
      target_arch,
      target_platform,
      track_build_dependencies,
      triggers,
      triggers_proxy,
  )
  v8.apply_bot_config(bot_config)
  v8.set_gclient_custom_vars(gclient_vars)
  v8.set_gclient_custom_deps(custom_deps)
  v8.set_chromium_configs(clobber, default_targets)

  additional_trigger_properties = {}
  test_spec = api.v8_tests.TEST_SPEC()

  # Read tests from V8-side test specs.
  tests = api.v8_tests.extra_tests_from_properties()

  if v8.is_pure_swarming_tester:
    source_dir = None
    build_dir = None
    with api.step.nest('initialization'):
      # This is to install golang swarming client via CIPD.
      with api.swarming.on_path():
        pass

      # Simulate a v8 update on slim swarming testers. The revision
      # property is mandatory.
      info_step = api.step('simulated revision info', cmd=None)
      info_step.presentation.properties['got_revision'] = (
          api.buildbucket.gitiles_commit.id)
      info_step.presentation.properties['got_revision_cp'] = (
          api.properties.get('parent_got_revision_cp'))

      api.v8_tests.set_up_swarming()
  else:
    with api.step.nest('initialization'):
      update_step = v8.checkout(clobber=clobber_all)
      source_dir = update_step.source_root.path
      build_dir = api.v8.build_dir(source_dir)

      api.v8_tests.set_up_swarming()
      v8.runhooks(source_dir, build_dir)

      # Dynamically load more test specifications from all discovered test
      # roots.
      test_roots = v8.get_test_roots(source_dir)
      for test_root in test_roots:
        api.v8_tests.update_test_configs(v8.load_dynamic_test_configs(test_root))
        test_spec.update(v8.read_test_spec(test_root, v8.builderset))
        # Tests from dynamic test roots have precedence.
        tests = v8.dedupe_tests(v8.extra_tests_from_test_spec(test_spec), tests)

    if v8.should_build:
      with api.step.nest('build'):
        compile_failure = v8.compile(source_dir, build_dir, test_spec)
        if compile_failure:
          return compile_failure

    v8.maybe_create_clusterfuzz_archive(source_dir, build_dir, update_step)

  if v8.should_test and tests:
    with v8.maybe_clang_coverage(source_dir, build_dir):
      test_results = api.v8_tests.runtests(source_dir, tests)
    v8.maybe_bisect(source_dir, build_dir, test_results, test_spec)

    if not api.tryserver.is_tryserver and test_results.is_negative:
      # Let the overall build fail for failures and flakes.
      raise api.step.StepFailure('Failures or flakes in build.')

    if api.tryserver.is_tryserver and test_results.has_failures:
      # Let tryjobs fail for failures only.
      raise api.step.StepFailure('Failures in tryjob.')

  v8.maybe_trigger(test_spec=test_spec, **additional_trigger_properties)

  if api.v8.should_collect_post_compile_metrics:
    with api.step.nest('measurements'):
      api.v8.collect_post_compile_metrics(source_dir, build_dir)

  return v8.recipe_result


def link_to_parent(api):
  value = api.properties.get('parent_build')
  if value :
    step_result = api.step('triggered by', [])
    step_result.presentation.links['parent build'] = value


def GenTests(api):
  yield (api.v8.test(
      'client.v8.branches',
      'V8 Foobar',
      'branch_sync_failure',
      git_ref='refs/branch-heads/4.3',
      status='INFRA_FAILURE',
  ) + api.step_data('initialization.bot_update', retcode=1))

  # Minimal bot config for a builder. Used to simulate test data for
  # triggered testers.
  linux_bot_config = {
    'testing': {
      'platform': 'linux',
    },
  }

  # Minimal v8-side test spec for simulating most recipe features.
  test_spec = json.dumps({
    "tests": [
      {"name": "v8testing"},
      {"name": "test262", "test_args": ["--extra-flags=--flag"]},
    ],
  }, indent=2)

  # Simulate a tryjob for setting up different swarming default tags.
  yield (
    api.v8.test(
        'tryserver.v8',
        'v8_foobar_rel_ng_triggered',
        'triggered_by_cq',
        parent_buildername='v8_foobar_rel_ng',
        parent_bot_config=linux_bot_config,
        parent_test_spec=test_spec,
        blamelist=['dude@chromium.org'],
    )
  )

  # Test usage of test filters. They're used when the buildbucket
  # job gets a property 'testfilter', which is expected to be a json list of
  # test-filter strings.
  yield (
    api.v8.test(
        'tryserver.v8',
        'v8_foobar_rel_ng_triggered',
        'test_filter',
        parent_buildername='v8_foobar_rel_ng',
        parent_bot_config=linux_bot_config,
        parent_test_spec=test_spec,
        testfilter=['mjsunit/regression/*', 'intl/foo', 'intl/bar'],
        extra_flags='--trace_gc --turbo_stats',
    )
  )

  # Test extra properties on a builder bot to ensure it triggers the tester
  # with the right properties.
  yield (
    api.v8.test(
        'tryserver.v8',
        'v8_foobar_rel_ng',
        'test_filter_builder',
        triggers=['v8_foobar_rel_ng_triggered'],
        testfilter=['mjsunit/regression/*', 'intl/foo', 'intl/bar'],
        extra_flags='--trace_gc --turbo_stats',
    ) +
    api.v8.test_spec_in_checkout(
        'v8_foobar_rel_ng', test_spec, 'v8_foobar_rel_ng_triggered') +
    api.post_process(Filter('trigger'))
  )

  # Test using extra flags with a bot that already uses some extra flags as
  # positional argument.
  yield (
    api.v8.test(
        'tryserver.v8',
        'v8_foobar_rel_ng_triggered',
        'positional_extra_flags',
        parent_buildername='v8_foobar_rel_ng',
        parent_bot_config=linux_bot_config,
        parent_test_spec=test_spec,
        extra_flags=['--trace_gc', '--turbo_stats'],
    )
  )

  yield (api.v8.test(
      'tryserver.v8',
      'v8_foobar_rel_ng_triggered',
      'failures',
      parent_buildername='v8_foobar_rel_ng',
      parent_bot_config=linux_bot_config,
      parent_test_spec=test_spec,
      disable_auto_bisect=True,
      status='FAILURE',
  ) + api.override_step_data('Check',
                             api.v8_tests.output_json(has_failures=True)))

  yield (
    api.v8.test(
        'tryserver.v8',
        'v8_foobar_rel_ng_triggered',
        'flakes',
        parent_buildername='v8_foobar_rel_ng',
        parent_bot_config=linux_bot_config,
        parent_test_spec=test_spec,
    ) +
    api.override_step_data(
        'Check', api.v8_tests.output_json(has_failures=True, flakes=True))
  )

  def TestFailures(flakes):
    flakes_suffix = "_flakes" if flakes else ""
    return (api.v8.test(
        'client.v8',
        'V8 Foobar',
        'test_failures%s' % flakes_suffix,
        status='FAILURE',
    ) + api.v8.test_spec_in_checkout('V8 Foobar', test_spec) +
            api.override_step_data(
                'Check',
                api.v8_tests.output_json(has_failures=True, flakes=flakes)) +
            api.post_process(Filter().include_re(r'.*Check.*')))

  yield TestFailures(flakes=False)
  yield TestFailures(flakes=True)

  yield (api.v8.test(
      'client.v8',
      'V8 Foobar',
      'compile_failure',
      status='FAILURE',
  ) + api.step_data('build.compile', retcode=1) +
         api.post_process(DropExpectation))

  yield (
      api.v8.test(
          'client.v8',
          'V8 Foobar',
          'compile_bisect_failure',
          status='FAILURE',
      ) + api.v8.test_spec_in_checkout('V8 Foobar', test_spec) +
      api.override_step_data(
          'Check', api.v8_tests.output_json(has_failures=True, flakes=False)) +
      api.step_data('Bisect a2.compile', retcode=1) +
      api.post_process(DropExpectation))

  yield (api.v8.test(
      'client.v8',
      'V8 Foobar',
      'swarming_collect_failure',
      parent_buildername='V8 Foobar - builder',
      parent_bot_config=linux_bot_config,
      parent_test_spec=test_spec,
      status='FAILURE',
  ) + api.step_data(
      'Check',
      api.json.output([['warning', 'danger']], name='warnings'),
      retcode=1,
  ))

  yield (
    api.v8.test(
        'client.v8',
        'V8 Foobar',
        'empty_json',
        status='INFRA_FAILURE',
    ) +
    api.v8.test_spec_in_checkout('V8 Foobar', test_spec) +
    api.override_step_data('Check', api.json.output([])) +
    api.expect_exception('AssertionError') +
    api.post_process(DropExpectation)
  )

  yield (api.v8.test(
      'client.v8',
      'V8 Foobar',
      'one_failure',
      status='FAILURE',
  ) + api.v8.test_spec_in_checkout('V8 Foobar', test_spec) +
         api.override_step_data('Check', api.v8_tests.one_failure()))

  yield (api.v8.test(
      'client.v8',
      'V8 Foobar',
      'one_failure_build_env_not_supported',
      parent_buildername='V8 Foobar - builder',
      parent_bot_config=linux_bot_config,
      parent_test_spec=test_spec,
      status='FAILURE',
  ) + api.override_step_data('Check', api.v8_tests.one_failure()) +
         api.properties(parent_gn_args=None))

  yield (api.v8.test(
      'client.v8',
      'V8 Foobar',
      'infra_failure',
      status='FAILURE',
  ) + api.v8.test_spec_in_checkout('V8 Foobar', test_spec) +
         api.override_step_data('Check', api.v8_tests.infra_failure()) +
         api.post_process(StepException, 'Check') +
         api.post_process(SummaryMarkdownRE, 'Failures or flakes in build.') +
         api.post_process(DropExpectation))

  # Test flako command line with interesting data.
  win_bot_config = {
    'testing': {
      'platform': 'win',
    },
  }
  flake_test_spec = """
    {
      "swarming_dimensions": {
        "os": "Windows-42",
        "cpu": "x86-64",
      },
      "tests": [
        {"name": "test262", "test_args": ["--extra-flags=--flag"]},
      ],
    }
  """.strip()
  yield (api.v8.test(
      'client.v8',
      'V8 Foobar',
      'flako',
      parent_buildername='V8 Foobar - builder',
      parent_bot_config=win_bot_config,
      parent_test_spec=flake_test_spec,
      status='FAILURE',
  ) + api.override_step_data('Test262', api.v8_tests.one_flake()) +
         api.post_process(Filter('Test262 (flakes)')))

  # Test flako command line for number fuzzer.
  yield (api.v8.test(
      'client.v8',
      'V8 Foobar',
      'flako_numfuzz',
      parent_buildername='V8 Foobar - builder',
      parent_bot_config=win_bot_config,
      parent_test_spec=flake_test_spec,
      status='FAILURE',
  ) + api.override_step_data('Test262', api.v8_tests.one_flake(num_fuzz=True)) +
         api.post_process(Filter('Test262 (flakes)')))

  yield (
    api.v8.test(
        'client.v8',
        'V8 Foobar',
        'generic_swarming_task',
        parent_buildername='V8 Foobar - builder',
        parent_bot_config=linux_bot_config,
        parent_test_spec='{"tests": [{"name": "jsfunfuzz"}]}',
    )
  )

  step_test_data = api.m.chromium_swarming.canned_summary_output(
      api.m.test_utils.canned_gtest_output(passing=False), failure=True)
  yield (api.v8.test(
      'client.v8',
      'V8 Foobar',
      'fuzz_archive',
      parent_buildername='V8 Foobar - builder',
      parent_bot_config=linux_bot_config,
      parent_test_spec='{"tests": [{"name": "jsfunfuzz"}]}',
      status='FAILURE',
  ) + api.override_step_data('Fuzz on Ubuntu-16.04', step_test_data))

  yield (api.v8.test(
      'client.v8',
      'V8 Foobar',
      'gcmole',
      parent_buildername='V8 Foobar - builder',
      parent_bot_config=linux_bot_config,
      parent_test_spec=json.dumps(
          {
              'tests': [
                  {
                      'name': 'gcmole_v2',
                      'variant': 'arm64'
                  },
                  {
                      'name': 'gcmole_v3',
                      'variant': 'x64',
                      'shards': 2
                  },
                  {
                      'name': 'gcmole_v2',
                      'variant': 'ia32',
                      'suffix': 'test',
                      'test_args': ['--test-run']
                  },
                  {
                      'name': 'gcmole_v3',
                      'variant': 'ia32',
                      'suffix': 'test2',
                      'test_args': ['--test-run']
                  },
              ]
          },
          indent=2),
  ))

  yield (
    api.v8.test(
        'client.v8',
        'V8 Foobar',
        'initializers',
        parent_buildername='V8 Foobar - builder',
        parent_bot_config=linux_bot_config,
        parent_test_spec='{"tests": [{"name": "v8initializers"}]}',
    )
  )

  yield (api.v8.test(
      'client.v8',
      'V8 Foobar',
      'bytecode_baseline',
      parent_buildername='V8 Foobar - builder',
      parent_bot_config=linux_bot_config,
      parent_test_spec='{"tests": [{"name": "check-bytecode-baseline"}]}',
  ) + api.post_process(MustRun, 'Bytecode-Baseline on Ubuntu-16.04') +
         api.post_process(DropExpectation))

  yield (api.v8.test(
      'client.v8',
      'V8 Foobar',
      'fuchsia-unittests',
      parent_buildername='V8 Foobar - builder',
      parent_bot_config=linux_bot_config,
      parent_test_spec='{"tests": [{"name": "fuchsia-unittests"}]}',
  ) + api.post_process(MustRun, 'Unittests on Ubuntu-16.04') +
         api.post_process(Filter().include_re(r'.*Unittests.*')
  ))

  yield (api.v8.test(
      'client.v8',
      'V8 Foobar',
      'perf',
      parent_buildername='V8 Foobar - builder',
      parent_bot_config=linux_bot_config,
      parent_test_spec='{"tests": [{"name": "perf_integration"}, '
      '{"name": "jetstream3"}]}',
  ))

  # Bisect over range a1, a2, a3. Assume a2 is the culprit. Steps:
  # Bisect a0 -> no failures.
  # Bisect a2 -> failures.
  # Bisect a1 -> no failures.
  # Report culprit a2.
  yield (api.v8.test(
      'client.v8',
      'V8 Foobar',
      'bisect',
      enable_swarming=False,
      status='FAILURE',
  ) + api.v8.test_spec_in_checkout('V8 Foobar', test_spec) +
         api.v8_tests.fail('Check') + api.v8_tests.fail('Bisect a2.Retry') +
         api.time.step(120))

  # The same as above, but overriding changes.
  yield (api.v8.test(
      'client.v8',
      'V8 Foobar',
      'bisect_override_triggers',
      enable_swarming=False,
      status='FAILURE',
  ) + api.v8.test_spec_in_checkout('V8 Foobar', test_spec) +
         api.properties(override_triggers=['a1', 'a2', 'a3']) +
         api.v8_tests.fail('Check') + api.v8_tests.fail('Bisect a2.Retry') +
         api.time.step(120))

  # Disable bisection, because the failing test is too long compared to the
  # overall test time.
  yield (api.v8.test(
      'client.v8',
      'V8 Foobar',
      'bisect_tests_too_long',
      enable_swarming=False,
      status='FAILURE',
  ) + api.v8.test_spec_in_checkout('V8 Foobar', test_spec) +
         api.v8_tests.fail('Check') + api.time.step(7))

  # Bisect over range a1, a2, a3. Assume a2 is the culprit.
  # Same as above with a swarming builder_tester.
  yield (api.v8.test(
      'client.v8',
      'V8 Foobar',
      'bisect_swarming',
      status='FAILURE',
  ) + api.v8.test_spec_in_checkout('V8 Foobar', test_spec) +
         api.v8_tests.fail('Check') + api.v8_tests.fail('Bisect a2.Retry') +
         api.time.step(120) +
         api.post_process(MustRun, 'Bisect a0.isolate tests'))

  # Bisect over range a1, a2, a3. Assume a3 is the culprit. This is a tester
  # and the build for a2 is not available. Steps:
  # Bisect a0 -> no failures.
  # Bisect a1 -> no failures.
  # Report a2 and a3 as possible culprits.
  yield (api.v8.test(
      'client.v8',
      'V8 Foobar',
      'bisect_tester_swarming',
      parent_buildername='V8 Foobar - builder',
      parent_bot_config=linux_bot_config,
      parent_test_spec=test_spec,
      status='FAILURE',
  ) + api.v8_tests.fail('Check') + api.time.step(120))

  # Disable bisection due to a recurring failure. Steps:
  # Bisect a0 -> failures.
  yield (api.v8.test(
      'client.v8',
      'V8 Foobar',
      'bisect_recurring_failure',
      enable_swarming=False,
      status='FAILURE',
  ) + api.v8.test_spec_in_checkout('V8 Foobar', test_spec) +
         api.v8_tests.fail('Check') + api.v8_tests.fail('Bisect a0.Retry') +
         api.time.step(120))

  # Disable bisection due to less than two changes.
  yield (
      api.v8.test(
          'client.v8',
          'V8 Foobar',
          'bisect_one_change',
          enable_swarming=False,
          status='FAILURE',
      ) + api.v8.test_spec_in_checkout('V8 Foobar', test_spec) +
      api.v8_tests.fail('Check') +
      api.scheduler(triggers=[api.v8.example_scheduler_buildbucket_trigger()]) +
      api.override_step_data(
          'Bisect.Get change range',
          api.v8.example_bisection_range_one_change(),
      ) + api.time.step(120))

  # Explicitly highlight slow tests not marked as slow.
  yield (
    api.v8.test(
        'tryserver.v8',
        'v8_foobar_rel_ng_triggered',
        'slow_tests',
        parent_buildername='v8_foobar_rel_ng',
        parent_bot_config=linux_bot_config,
        parent_test_spec=test_spec,
        requester='commit-bot@chromium.org',
        blamelist=['dude@chromium.org'],
    ) +
    api.override_step_data(
        'Check', api.v8_tests.output_json(unmarked_slow_test=True)
    )
  )

  # Raise exception when zero tests are run on all steps.
  yield (api.v8.test(
      'tryserver.v8',
      'v8_foobar_rel_ng_triggered',
      'empty_run',
      testfilter=['intl/foo'],
      parent_buildername='v8_foobar_rel_ng',
      parent_bot_config=linux_bot_config,
      parent_test_spec=test_spec,
      requester='commit-bot@chromium.org',
      blamelist=['dude@chromium.org'],
      status='FAILURE',
  ) + api.override_step_data('Check', api.v8_tests.output_json(empty_run=True))
         + api.post_process(SummaryMarkdownRE, 'No tests were run') +
         api.post_process(DropExpectation))

  # Test tryjob with named cache.
  yield (
    api.v8.test(
        'tryserver.v8',
        'v8_foobar_rel_ng',
        'with_cache',
        blamelist=['dude@chromium.org'],
        requester='commit-bot@chromium.org',
        triggers=['v8_foobar_rel_ng_triggered'],
    )
  )

  # Test using build_id (replaces buildnumber in LUCI world).
  yield (
    api.v8.test(
        'tryserver.v8',
        'v8_foobar_rel_ng',
        'with_build_id',
        build_id='buildbucket/cr-buildbucket.appspot.com/1234567890',
        triggers=['v8_foobar_rel_ng_triggered'],
    )
  )

  # Test reading a pyl test-spec from the V8 repository. The additional test
  # targets should be isolated and the tests should be executed.
  test_spec = """
    {
      "swarming_dimensions": {
        "pool": "noodle",
        "gpu": "quantum",
      },
      "swarming_task_attrs": {
        "priority": 25,
        "hard_timeout": 7200,
        "expiration": 7200,
      },
      "tests": [
        {
          "name": "mjsunit",
          "variant": "sweet",
          "shards": 2,
        },
        {
          "name": "mjsunit",
          "variant": "sour",
          "suffix": "everything",
          "test_args": ["--extra-flags", "--flag1 --flag2"],
          # This tests that the default pool dimension above is overridden.
          "swarming_dimensions": {"pool": "override"},
          # This tests that the default priority above is overridden.
          "swarming_task_attrs": {"priority": 100},
        },
      ],
    }
  """.strip()
  yield (
    api.v8.test(
        'client.v8',
        'V8 Foobar',
        'with_test_spec',
    ) +
    api.v8.test_spec_in_checkout('V8 Foobar', test_spec) +
    api.post_process(
        Filter()
            .include('initialization.read test spec (v8)')
            .include('build.isolate tests')
            .include_re(r'.*Mjsunit.*')
    )
  )

  # As above but on a builder. The additional test targets should be isolated
  # and the tests should be passed as properties to the triggered testers in
  # the trigger step.
  yield (
    api.v8.test(
        'client.v8',
        'V8 Foobar builder',
        'with_test_spec',
        triggers=['V8 Foobar'],
    ) +
    api.v8.test_spec_in_checkout(
        'V8 Foobar builder', test_spec, 'V8 Foobar') +
    api.post_process(Filter(
        'initialization.read test spec (v8)',
        'build.generate_build_files',
        'build.isolate tests',
        'trigger',
    ))
  )

  # As above but on a tester. The additional tests passed as property from the
  # builder should be executed.
  yield (
    api.v8.test(
        'client.v8',
        'V8 Foobar',
        'tester_with_test_spec',
        parent_bot_config=linux_bot_config,
        parent_buildername='V8 Foobar builder',
        parent_test_spec=test_spec,
    ) +
    api.post_process(Filter().include_re(r'.*Mjsunit.*'))
  )

  # Test that the cpu dimension is reset when triggering Android bots.
  android_test_spec = """
    {
      "swarming_dimensions": {
        "os": "Android",
      },
      "tests": [
        {
          "name": "mjsunit",
        },
      ],
    }
  """.strip()
  yield (
    api.v8.test(
        'client.v8',
        'Android bot',
        parent_test_spec=android_test_spec,
        swarm_hashes={'mjsunit': 'hash/123'},
    ) +
    api.v8.check_not_in_any_arg('trigger tests.[trigger] Mjsunit on Android',
                                'cpu') +
    api.post_process(DropExpectation)
  )

  # Test that we use mac-arm64 cpython when using mac-arm64 devices.
  mac_arm64_test_spec = """
    {
      "swarming_dimensions": {
        "pool": "chromium.tests",
        "cpu": "arm64",
        "os": "Mac-11",
      },
      "tests": [
        {
          "name": "d8testing",
        },
      ],
    }
  """.strip()
  yield (
    api.v8.test(
        'client.v8',
        'mac_arm64',
        parent_buildername='V8 Foobar - builder',
        parent_test_spec=mac_arm64_test_spec,
        swarm_hashes={'d8_default': 'hash/123'},
    ) +
    api.post_process(Filter('trigger tests.[trigger] Check - d8 on Mac-11'))
  )

  # Test reading pyl test configs and build configs from a separate checkout.
  extra_test_config = """
    {
      'foounit': {
        'name': 'Foounit',
        'tests': ['foounit'],
      },
    }
  """.strip()
  extra_test_spec = """
    {
      "tests": [
        {
          "name": "foounit",
        },
      ],
    }
  """.strip()
  yield (api.v8.test(
      'somewhere.v8',
      'V8 Foobar',
      'with_test_config',
      enable_swarming=False,
  ) + api.v8.example_test_roots('test_checkout') + api.path.exists(
      api.path.cache_dir.joinpath('builder', 'v8', 'custom_deps',
                                  'test_checkout', 'infra', 'testing',
                                  'config.pyl'),
      api.path.cache_dir.joinpath('builder', 'v8', 'custom_deps',
                                  'test_checkout', 'infra', 'testing',
                                  'builders.pyl'),
  ) + api.override_step_data(
      'initialization.read test config (test_checkout)',
      api.v8.example_test_config(extra_test_config),
  ) + api.override_step_data(
      'initialization.read test spec (test_checkout)',
      api.v8.example_test_spec('V8 Foobar', extra_test_spec),
  ) + api.post_process(DoesNotRun, 'build.isolate tests') + api.post_process(
      Filter().include('initialization.read test config (test_checkout)')
      .include('initialization.read test spec (test_checkout)').include_re(
          r'.*Foounit.*')))

  # Test using custom_deps and gclient_vars property.
  yield (api.v8.test(
      'client.v8',
      'V8 Foobar - builder',
      'custom_properties',
      custom_deps={'v8/foo': 'bar'},
      gclient_vars={'download_gcmole': 'True'}) + api.v8.check_in_param(
          'initialization.bot_update', '--spec-path', '\'custom_vars\': '
          '{\'download_gcmole\': \'True\'') +
         api.v8.check_in_param('initialization.bot_update', '--spec-path',
                               '\'custom_deps\': {\'v8/foo\': \'bar\'}') +
         api.post_process(DropExpectation))

  # Test using source side properties.
  yield (
      api.v8.test('client.v8', 'V8 Foobar - builder', 'src_side_properties',
                  target_arch='arm',
                  target_platform='fuchsia',
                  triggers=['V8 Foobar'],
                  triggers_proxy=True,
      ) +
      api.v8.check_in_param(
          'initialization.bot_update',
          '--spec-path', 'target_cpu = [\'arm\', \'arm64\']') +
      api.v8.check_in_param(
          'initialization.bot_update',
          '--spec-path', 'target_os = [\'fuchsia\']') +
      api.v8.check_in_any_arg('build.generate_build_files', 'build') +
      api.v8.check_in_any_arg('build.compile', 'build') +
      api.post_process(Filter('trigger'))
  )

  # Test led run.
  led_properties = {
      '$recipe_engine/led':
          led_properties_pb.InputProperties(
              led_run_id='fake-run-id',
          ),
  }
  yield (
      api.v8.test('client.v8', 'V8 Foobar - builder', 'led',
                  triggers=['V8 Foobar'],
      ) +
      api.properties(**led_properties) +
      api.post_process(Filter().include_re(r'trigger.*'))
  )

  # As above but for tryserver.
  yield (
      api.v8.test('tryserver.v8', 'v8_foobar_rel_ng', 'led',
                  triggers=['v8_foobar_rel_ng_triggered'],
      ) +
      api.properties(**led_properties) +
      api.post_process(DropExpectation)
  )

  # Test mac builder.
  yield (
      api.v8.test('client.v8', 'V8 Foobar - builder', 'mac') +
      api.platform('mac', 64) +
      api.post_process(MustRun, 'initialization.ensure_installed') +
      api.post_process(DropExpectation)
  )

  # Test ios configs.
  yield (
      api.v8.test('client.v8', 'V8 Foobar - builder', 'ios',
                  target_platform='ios',
      ) +
      api.platform('mac', 64) +
      api.v8.check_in_param(
          'initialization.bot_update',
          '--spec-path', 'target_os = [\'ios\']') +
      api.v8.check_in_any_arg('build.install xcode', 'ios') +
      api.post_process(DropExpectation)
  )

  # Cover test config entries with specific isolate targets.
  yield (api.v8.test(
      'client.v8',
      'V8 Foobar',
      'specific_isolated_file',
  ) + api.v8.test_spec_in_checkout(
      'V8 Foobar', '{"tests": [{"name": "numfuzz", "suffix": "sfx"}]}') +
         api.post_process(
             Filter('build.isolate tests',
                    'trigger tests.[trigger] Num Fuzz - sfx on Ubuntu-16.04')))

  # Test using clobber_all property.
  yield (
    api.v8.test(
        'client.v8',
        'V8 Foobar',
        'clobber_all',
        clobber_all=True
    ) +
    api.v8.check_in_any_arg('initialization.bot_update', '--clobber') +
    api.post_process(DropExpectation)
  )

  def check_gs_url_equals(check, steps, expected):
    check('gsutil upload' in steps)
    check(expected == steps['gsutil upload'].cmd[-1])

  # Test configurations for clusterfuzz builders.
  yield (api.v8.test(
      'client.v8',
      'V8 Foobar',
      'clusterfuzz',
      clobber=True,
      clusterfuzz_archive={
          'name': 'd8_bar',
          'bucket': 'v8_clusterfoo',
          'bitness': 64,
      },
      default_targets=['v8_foobar'],
  ) + api.post_process(
      check_gs_url_equals, 'gs://v8_clusterfoo/linux64-release/'
      'd8_bar-linux64-release-v8-component-50110.zip') +
         api.post_process(MustRun, 'initialization.clobber') + api.post_process(
             Filter(
                 'build.compile',
                 'create staging_dir',
                 'filter archive_root',
                 'zipping',
                 'gsutil upload',
             )))

  # Test configurations for perf builders.
  yield (api.v8.test(
      'client.v8.perf',
      'V8 Foobar perf',
      'perf',
  ) + api.post_process(Filter('build.compile')))

  # Test configurations for post-compilation build measurements.
  yield (
    api.v8.test(
        'client.v8',
        'V8 Foobar',
        'measurements',
        binary_size_tracking={
          'binary': 'd8',
          'category': 'foo64',
        },
        track_build_dependencies=True,
    ) +
    api.post_process(Filter(
        'measurements.track build dependencies (fyi)',
        'measurements.Check binary size',
        'measurements.perf dashboard post',
        'measurements.perf dashboard post (2)',
    ))
  )
  yield (api.v8.test(
      'client.v8',
      'V8 Foobar',
      'no measurements',
      binary_size_tracking={
          'binary': 'd8',
          'category': 'foo64',
      },
      track_build_dependencies=True,
  ) + api.buildbucket.ci_build(
      project='v8',
      git_repo='https://chromium.googlesource.com/v8/v8',
      bucket='ci.br',
      builder='V8 Foobar',
      git_ref='refs/heads/br',
      build_number=571,
      revision='deadbeef' * 5,
      tags=api.buildbucket.tags(
          user_agent='luci-scheduler',
          buildset='commit/gitiles/chromium.googlesource.com/v8/v8/+/'
          'deadbeefdeadbeefdeadbeefdeadbeefdeadbeef')
    ) + api.post_process(DoesNotRunRE, 'measurements.perf.*'
    ) + api.post_process(DropExpectation
    ))

  # Test overall failure on upload failures.
  yield (api.v8.test(
      'client.v8',
      'V8 Foobar',
      'measurements_upload_failure',
      track_build_dependencies=True,
      status='INFRA_FAILURE',
  ) + api.override_step_data('measurements.perf dashboard post',
                             api.json.output({'status_code': 403})) +
         api.post_process(DropExpectation))

  # Test windows-specific build steps.
  yield (api.v8.test(
      'client.v8',
      'V8 Foobar',
      'windows',
  ) + api.platform('win', 64) + api.post_process(DropExpectation))
