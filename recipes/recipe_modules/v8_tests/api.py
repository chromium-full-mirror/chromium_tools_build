# Copyright 2022 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import argparse
from collections import defaultdict
import contextlib
import random
import re

from recipe_engine import recipe_api
from RECIPE_MODULES.build.chromium_tests.resultdb import ResultDB

from .builders import TestSpec
from . import builders as v8_builders
from . import formatting
from . import testing


# With too many letters, labels are to big and stretch the UI.
MAX_LABEL_SIZE = 35

# Make sure that a step is not flooded with log lines.
MAX_FAILURE_LOGS = 10

TEST_RUNNER_PARSER = argparse.ArgumentParser()
TEST_RUNNER_PARSER.add_argument('--extra-flags')

V8_RECIPE_FLAGS = 'V8-Recipe-Flags'


class V8TestsApi(recipe_api.RecipeApi):
  EMPTY_TEST_SPEC = v8_builders.EmptyTestSpec
  TEST_CONFIGS = testing.TEST_CONFIGS
  TEST_SPEC = v8_builders.TestSpec

  def __init__(self, *args, **kwargs):
    super().__init__(*args, **kwargs)
    self.gn_args = None
    self.isolated_tests = None
    self.test_configs = {}
    self.rerun_failures_count = 2
    self.enable_swarming = True
    self.v8_recipe_flags = []
    self._resultdb = None

    # If tests are run, this value will be set to their total duration.
    self.test_duration_sec = 0

  def initialize(self):
    self.gn_args = self.m.properties.get('parent_gn_args')
    self.isolated_tests = self.m.isolate.isolated_tests

  def read_cl_footer_flags(self):
    if self.m.tryserver.is_gerrit_issue:
      footers = self.m.tryserver.get_footers() or {}
      flags = footers.get(V8_RECIPE_FLAGS, [])
      self.v8_recipe_flags = flags

  def is_flag_set(self, flag_name):
    return flag_name in self.v8_recipe_flags  # pragma: no cover

  @property
  def resultdb(self):
    if not self._resultdb:
      self._resultdb = ResultDB.create()
    return self._resultdb

  def update_test_configs(self, test_configs):
    """Update test configs without mutating previous copy."""
    self.test_configs = dict(self.test_configs)
    self.test_configs.update(test_configs)

  def load_static_test_configs(self):
    """Set predefined test configs from build repository."""
    self.update_test_configs(self.TEST_CONFIGS)

  def testing_random_seed(self):
    """Return a random seed suitable for v8 testing.

    If there are isolate hashes, build a random seed based on the hashes.
    Otherwise use the system's PRNG. This uses a deterministic seed for
    recipe simulation.
    """
    r = random.Random()
    if self.isolated_tests:
      r.seed("".join(self.isolated_tests))
    elif self._test_data.enabled:
      r.seed(12345)

    seed = 0
    while not seed:
      # Avoid 0 because v8 switches off usage of random seeds when
      # passing 0 and creates a new one.
      seed = r.randint(-2147483648, 2147483647)
    return seed

  def set_up_swarming(self):
    self.m.chromium_swarming.set_default_dimension('pool', 'chromium.tests')
    self.m.chromium_swarming.set_default_dimension('os', 'Ubuntu-16.04')
    self.m.chromium_swarming.default_hard_timeout = 45 * 60

    self.m.chromium_swarming.default_idempotent = True
    self.m.chromium_swarming.task_output_stdout = 'all'

    if self.m.builder_group.for_current == 'tryserver.v8':
      self.m.chromium_swarming.add_default_tag('purpose:pre-commit')
      self.m.chromium_swarming.default_priority = 30

      changes = self.m.buildbucket.build.input.gerrit_changes
      assert len(changes) <= 1
      if changes and changes[0].project:
        self.m.chromium_swarming.add_default_tag(
          f'patch_project:{changes[0].project}'
        )
    else:
      if self.m.builder_group.for_current in [
        'client.v8',
        'client.v8.branches',
        'client.v8.ports',
      ]:
        self.m.chromium_swarming.default_priority = 25
      else:
        # This should be lower than the CQ.
        self.m.chromium_swarming.default_priority = 35
      self.m.chromium_swarming.add_default_tag('purpose:post-commit')
      self.m.chromium_swarming.add_default_tag('purpose:CI')

  def isolate_targets_from_tests(self, test_spec=None):
    """Returns the isolated targets associated with a list of tests from
    a test spec.

    Args:
      test_spec: Optional TestSpec object as returned by read_test_spec().
    """
    test_spec = test_spec or self.EMPTY_TEST_SPEC
    if not self.enable_swarming:
      return []
    targets = []
    for test in test_spec.get_all_test_names():
      config = self.test_configs.get(test) or {}

      # Tests either define an explicit isolate target or use the test
      # names for convenience.
      if config.get('isolated_target'):
        targets.append(config['isolated_target'])
      elif config.get('tests'):
        targets.extend(config['tests'])
    return targets

  @property
  def relative_path_to_d8(self):
    return self.m.path.join('out', 'build', 'd8')

  def extra_tests_from_properties(self, properties=None):
    """Returns runnable testing.BaseTest objects for each extra test specified
    by parent_test_spec property.
    """
    properties = properties or self.m.properties
    return [
      self.create_test(test)
      for test in self.TEST_SPEC.from_properties_dict(properties)
    ]

  def create_test(self, test):
    """Wrapper that allows to shortcut common tests with their names.

    Returns: A runnable test instance.
    """
    return testing.create_test(test, self.m)

  @contextlib.contextmanager
  def maybe_nest(self, condition, parent_step_name):
    if not condition:
      yield
    else:
      with self.m.step.nest(parent_step_name):
        yield

  def runtests(self, source_dir, tests):
    if self.extra_flags:
      result = self.m.step('Customized run with extra flags', cmd=None)
      result.presentation.step_text += ' '.join(self.extra_flags)
      assert all(re.match(r'[\w\-]*', x) for x in self.extra_flags), (
        'no special characters allowed in extra flags'
      )

    start_time_sec = self.m.time.time()

    # Apply test filter.
    tests = [t for t in tests if t.apply_filter()]

    swarming_tests = [t for t in tests if t.uses_swarming]
    local_tests = [t for t in tests if not t.uses_swarming]

    # There are no mixed tests in V8.
    assert not local_tests or not swarming_tests

    test_group = testing.TestGroup(
      self.m, swarming_tests if swarming_tests else local_tests
    )

    with self.maybe_nest(swarming_tests, 'trigger tests'):
      test_group.pre_run()

    test_group.mid_run()

    test_group.run(source_dir)

    test_group.raise_on_failure()

    test_group.raise_on_empty()

    self.test_duration_sec = self.m.time.time() - start_time_sec
    return test_group.test_results

  def update_slowest(self, output, presentation):
    presentation.logs['slowest tests'] = formatting.top_tests(
      output['slowest_tests'],
      formatting.duration,
      'Duration',
      'marked_slow',
    )

  def update_max_rss(self, output, presentation):
    if not output.get('max_rss_tests') or not output['max_rss_tests'][0].get(
      'max_rss'
    ):
      return
    presentation.logs['largest tests (rss)'] = formatting.top_tests(
      output['max_rss_tests'],
      formatting.rss,
      'Maximum RSS',
      'marked_heavy',
    )

  def update_max_vms(self, output, presentation):
    if not output.get('max_vms_tests') or not output['max_vms_tests'][0].get(
      'max_vms'
    ):
      return
    presentation.logs['largest tests (vms)'] = formatting.top_tests(
      output['max_vms_tests'],
      formatting.vms,
      'Maximum VMS',
      'marked_heavy',
    )

  def ui_test_label(self, full_test_name):
    # Use test base name as UI label (without suite and directory names).
    label = full_test_name.split('/')[-1]
    # Truncate the label if it is still too long.
    if len(label) > MAX_LABEL_SIZE:
      label = label[: MAX_LABEL_SIZE - 3] + '...'
    return label

  def _get_failure_logs(self, output, failure_factory):
    if not output['results']:
      return {}, [], {}, []

    unique_results = defaultdict(list)
    for result in output['results']:
      label = self.ui_test_label(result['name'])
      # Group tests with the same label (usually the same test that ran under
      # different configurations).
      unique_results[label].append(result)

    failure_log = {}
    flake_log = {}
    failures = []
    flakes = []
    for label in sorted(unique_results)[:MAX_FAILURE_LOGS]:
      failure_lines = []
      flake_lines = []

      # Group results by command. The same command might have run multiple
      # times to detect flakes.
      results_per_command = defaultdict(list)
      for result in unique_results[label]:
        results_per_command[result['command']].append(result)

      for command in results_per_command:
        results = results_per_command[command]
        # Determine flakiness.
        failure = failure_factory(results)
        if failure.is_flaky:
          # This is a flake.
          flakes.append(failure)
          flake_lines += failure.log_lines()
        else:
          # This is a failure.
          failures.append(failure)
          failure_lines += failure.log_lines()

      if failure_lines:
        failure_log[label] = failure_lines
      if flake_lines:
        flake_log[label] = flake_lines

    return failure_log, failures, flake_log, flakes

  def _update_failure_presentation(self, log, failures, presentation):
    for label in sorted(log):
      presentation.logs[label] = log[label]

    if failures:
      # Number of failures.
      presentation.step_text += f'failures: {len(failures)}<br/>'

  @property
  def extra_flags(self):
    extra_flags = self.m.properties.get('extra_flags', '')
    if isinstance(extra_flags, str):
      extra_flags = extra_flags.split()
    assert isinstance(extra_flags, (list, tuple))
    return list(extra_flags)

  def _with_extra_flags(self, args):
    """Returns: the arguments with additional extra flags inserted.

    Extends a possibly existing extra flags option.
    """
    if not self.extra_flags:
      return args

    options, args = TEST_RUNNER_PARSER.parse_known_args(args)

    if options.extra_flags:
      new_flags = [options.extra_flags] + self.extra_flags
    else:
      new_flags = self.extra_flags

    args.extend(['--extra-flags', ' '.join(new_flags)])
    return args

  @property
  def test_filter(self):
    return [
      f for f in self.m.properties.get('testfilter', []) if f != 'defaulttests'
    ]

  def _applied_test_filter(self, test):
    """Returns: the list of test filters that match a test configuration."""
    # V8 test filters always include the full suite name, followed
    # by more specific paths and possibly ending with a glob, e.g.:
    # 'mjsunit/regression/prefix*'.
    return [
      f
      for f in self.test_filter
      for t in test.get('suite_mapping', test['tests'])
      if f.startswith(t)
    ]

  def _setup_test_runner(self, test, applied_test_filter, test_step_config):
    env = {}
    full_args = [
      '--progress=verbose',
      '--outdir',
      self.m.path.join('out', 'build'),
    ]

    # Add optional non-standard root directory for test suites.
    if test.get('test_root'):
      full_args += ['--test-root', test['test_root']]

    # On reruns, there's a fixed random seed set in the test configuration.
    if '--random-seed' not in test.get('test_args', []) and test.get(
      'use_random_seed', True
    ):
      full_args.append(f'--random-seed={self.testing_random_seed()}')

    # Either run tests as specified by the filter (trybots only) or as
    # specified by the test configuration.
    if applied_test_filter:
      full_args += applied_test_filter
    else:
      full_args += list(test.get('tests', []))

    # Add test-specific test arguments.
    full_args += test.get('test_args', [])

    # Add builder-specific test arguments.
    full_args += self.c.testing.test_args

    # Add builder-, test- and step-specific variants.
    full_args += testing.test_args_from_variants(
      test.get('variants'),
      test_step_config.variants,
    )

    # Add step-specific test arguments.
    full_args += test_step_config.test_args

    full_args = self._with_extra_flags(full_args)

    full_args += [
      f'--rerun-failures-count={self.rerun_failures_count}',
    ]

    return full_args, env
