# Copyright 2022 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import ast

from recipe_engine import recipe_test_api

from . import builders
from . import testing


class V8TestApi(recipe_test_api.RecipeTestApi):
  @staticmethod
  def TOP_TESTS():
    return [
      {
        'name': 'mjsunit/Cool.Test',
        'flags': ['-f'],
        'command': 'd8 -f mjsunit/Cool.Test',
        'duration': 61.0028,
        'max_rss': 2254857831,
        'max_vms': 12254857831,
        'variant': 'default',
      },
      {
        'name': 'mjsunit/Cool.Test2',
        'flags': ['-f', '-g'],
        'command': 'd8 -f mjsunit/Cool.Test2',
        'duration': 0.1012,
        'max_rss': 2254857830,
        'max_vms': 12254857830,
        'variant': 'default',
      },
    ]

  def output_json(
    self,
    has_failures=False,
    flakes=False,
    unmarked_slow_test=False,
    empty_run=False,
  ):
    top_tests = V8TestApi.TOP_TESTS()
    if empty_run:
      return self.m.json.output(
        {
          'results': [],
          'slowest_tests': [],
          'tags': [],
          'test_total': 0,
        }
      )
    if unmarked_slow_test:
      top_tests += [
        {
          'name': 'mjsunit/slow',
          'flags': [],
          'command': 'd8 -f mjsunit/slow',
          'duration': 123.0,
          'max_rss': 12254857830,
          'max_vms': 17,
          'marked_slow': False,
          'marked_heavy': False,
          'variant': 'stress',
        }
      ]
    if not has_failures:
      return self.m.json.output(
        {
          'results': [],
          'max_rss_tests': top_tests,
          'max_vms_tests': top_tests,
          'slowest_tests': top_tests,
          'tags': [],
          'test_total': 1,
        }
      )
    if flakes:
      return self.m.json.output(
        {
          'results': [
            {
              'flags': [],
              'result': 'FAIL',
              'expected': ['PASS', 'SLOW'],
              'duration': 3,
              'variant': 'default',
              'random_seed': 123,
              'run': 1,
              'stdout': 'Some output.',
              'stderr': 'Some errput.',
              'crash_type': 'Some crash type.',
              'crash_state': 'Some crash state.',
              'name': 'suite-name/dir/test-name',
              'command': 'd8 test.js',
              'exit_code': 1,
            },
            {
              'flags': [],
              'result': 'PASS',
              'expected': ['PASS', 'SLOW'],
              'duration': 10,
              'variant': 'default',
              'random_seed': 123,
              'run': 2,
              'stdout': 'Some output.',
              'stderr': '',
              'crash_type': 'Some crash type.',
              'crash_state': 'Some crash state.',
              'name': 'suite-name/dir/test-name',
              'command': 'd8 test.js',
              'exit_code': 1,
            },
            {
              'flags': [],
              'result': 'FAIL',
              'expected': ['PASS', 'SLOW'],
              'duration': 1.5,
              'variant': 'default',
              'random_seed': 123,
              'run': 1,
              'stdout': 'Some output.',
              'stderr': 'Some errput.',
              'crash_type': '',
              'crash_state': '',
              'name': 'suite-name/dir/test-name2',
              'command': 'd8 test.js',
              'exit_code': 1,
            },
            {
              'flags': [],
              'result': 'PASS',
              'expected': ['PASS', 'SLOW'],
              'duration': 10,
              'variant': 'default',
              'random_seed': 123,
              'run': 2,
              'stdout': 'Some output.',
              'stderr': '',
              'name': 'suite-name/dir/test-name2',
              'command': 'd8 test.js',
              'exit_code': 1,
            },
          ],
          'max_rss_tests': top_tests,
          'max_vms_tests': top_tests,
          'slowest_tests': top_tests,
          'tags': [],
          'test_total': 4,
        }
      )

    # Add enough failures to exceed the maximum number of shown failures
    # (test-name9 will be cut off).
    results = []
    for i in range(0, 10):
      results.append(
        {
          'flags': ['--opt42'],
          'result': 'FAIL',
          'expected': ['PASS', 'SLOW'],
          'duration': 61.0028,
          'variant': 'default',
          'random_seed': 123,
          'run': 1,
          'stdout': 'Some output.',
          'stderr': 'Some errput.',
          'crash_type': 'Some crash type.',
          'crash_state': 'Some crash state.',
          'name': 'suite-name/dir/test-name%d' % i,
          'command': 'out/theMode/d8 --opt42 test/suite-name/dir/test-name.js',
          'exit_code': 1,
        }
      )
      results.append(
        {
          'flags': ['--other'],
          'result': 'FAIL',
          'duration': 3599.9999,
          'variant': 'default',
          'random_seed': 123,
          'run': 1,
          'stdout': 'Some output.',
          'stderr': 'Some errput.',
          'crash_type': 'Some crash type.',
          'crash_state': 'Some crash state.',
          'name': 'suite-name/dir/test-name%d' % i,
          'command': 'out/theMode/d8 --other test/suite-name/dir/test-name.js',
          'exit_code': 1,
        }
      )
      results.append(
        {
          'flags': ['--other'],
          'result': 'CRASH',
          'duration': 0.1111,
          'variant': 'default',
          'random_seed': 123,
          'run': 1,
          'stdout': 'Some output\nwith\nmore\nlines.',
          'stderr': 'Some errput.',
          'crash_type': 'Some crash type.',
          'crash_state': 'Some crash state.',
          'name': 'other-suite/dir/other-test-very-very-very-long-name%d' % i,
          'command': (
            'out/theMode/d8 --other test/other-suite/dir/other-test-very-very-'
            'very-long-name.js'
          ),
          'exit_code': 1,
        }
      )

    return self.m.json.output(
      {
        'results': results,
        'slowest_tests': top_tests,
        'tags': [],
        'test_total': 10,
      }
    )

  def one_failure(self):
    return self.m.json.output(
      {
        'results': [
          {
            'flags': [],
            'framework_name': 'standard_runner',
            'result': 'FAIL',
            'expected': ['PASS', 'SLOW'],
            'duration': 5,
            'variant': 'default',
            'random_seed': 123,
            'run': 1,
            'stdout': 'Some output.',
            'stderr': 'Some errput.',
            'crash_type': 'Some crash type.',
            'crash_state': 'Some crash state.',
            'name': 'suite-name/dir/test-name',
            'command': 'd8 test.js',
            'target_name': 'd8',
            'exit_code': 1,
          },
        ],
        'slowest_tests': V8TestApi.TOP_TESTS(),
        'tags': [],
        'test_total': 1,
      }
    )

  def flakes(self, count, num_fuzz=False, crash_type='Some crash type.'):
    if num_fuzz:
      framework_name = 'num_fuzzer'
      variant = None
      variant_flags = ['--flag1', '--flag2']
    else:
      framework_name = 'standard_runner'
      variant = 'stress'
      variant_flags = None

    default_result = {
      'flags': [],
      'result': 'FAIL',
      'expected': ['PASS', 'SLOW'],
      'duration': 3,
      'variant': variant,
      'variant_flags': variant_flags,
      'random_seed': 123,
      'run': 1,
      'stdout': 'Some output.',
      'stderr': 'Some errput.',
      'crash_type': crash_type,
      'crash_state': 'Some crash state.',
      'name': 'suite-name/dir/test-name',
      'command': 'd8 test.js',
      'exit_code': 1,
      'shard_id': 1,
      'shard_count': 2,
      'framework_name': framework_name,
    }

    results = []
    for index in range(count):
      name = f'suite-name/dir/test-name-{index}'
      results.append(dict(default_result, name=name))
      results.append(
        dict(
          default_result,
          name=name,
          result='PASS',
          stderr='',
          exit_code=0,
          duration=10,
        )
      )

    return self.m.json.output(
      {
        'results': results,
        'slowest_tests': V8TestApi.TOP_TESTS(),
        'tags': [],
        'test_total': len(results),
      }
    )

  def one_flake(self, num_fuzz=False):
    return self.flakes(1, num_fuzz=num_fuzz)

  def infra_failure(self):
    return self.m.json.output(
      {
        'results': [],
        'slowest_tests': V8TestApi.TOP_TESTS(),
        'tags': ['UNRELIABLE_RESULTS'],
        'test_total': 1,
      }
    ) + self.m.json.output([['Collect warning', '']], name='warnings')

  def failures_example(self, variant1='default', variant2='default'):
    flags = {'default': '', 'stress': ' --stress-opt'}
    return self.m.json.output(
      {
        'results': [
          {
            'flags': [],
            'result': 'FAIL',
            'expected': ['PASS', 'SLOW'],
            'duration': 3,
            'variant': variant1,
            'random_seed': 123,
            'run': 1,
            'stdout': 'Some output.',
            'stderr': 'Some errput.',
            'crash_type': 'Some crash type.',
            'crash_state': 'Some crash state.',
            'name': 'suite-name/dir/slow',
            'command': 'd8%s test.js' % flags[variant1],
            'shard_id': 0,
            'shard_count': 1,
            'target_name': 'd8',
            'exit_code': 1,
          },
          {
            'flags': [],
            'result': 'FAIL',
            'expected': ['PASS', 'SLOW'],
            'duration': 1.5,
            'variant': variant2,
            'random_seed': 123,
            'run': 1,
            'stdout': 'Some output.',
            'stderr': 'Some errput.',
            'crash_type': 'Some crash type.',
            'crash_state': 'Some crash state.',
            'name': 'suite-name/dir/fast',
            'command': 'd8%s test.js' % flags[variant2],
            'shard_id': 0,
            'shard_count': 1,
            'target_name': 'd8',
            'exit_code': 1,
          },
        ],
        'slowest_tests': V8TestApi.TOP_TESTS(),
        'tags': [],
        'test_total': 2,
      }
    )

  def example_parent_test_spec_properties(self, buildername, spec):
    """Properties dict containing an example parent_test_spec.

    Args:
      buildername: Name of the builder that should get this property.
      spec: The raw test spec pyl text.
    Returns: A dict with a parent_test_spec key set to a packed test spec.
    """
    return builders.TestSpec.from_python_literal(
      {buildername: ast.literal_eval(spec)},
      [buildername],
    ).as_properties_dict(buildername)

  def _make_dummy_swarm_hashes(self, test_names):
    """Makes dummy isolate hashes for all tests of a bot.

    Either an explicit isolate target must be defined or the naming
    convention "test name == isolate target name" will be used.
    """

    def gen_isolate_targets(test_name):
      config = testing.TEST_CONFIGS.get(test_name, {})
      if config.get('isolated_target'):
        yield config['isolated_target']
      else:
        for test in config.get('tests', []):
          yield test

    return dict(
      (target, '[dummy hash for %s]/123' % target)
      for test_name in test_names
      for target in gen_isolate_targets(test_name)
    )

  def fail(self, step_name, variant1='default', variant2='default'):
    return self.override_step_data(
      step_name, self.failures_example(variant1=variant1, variant2=variant2)
    )
