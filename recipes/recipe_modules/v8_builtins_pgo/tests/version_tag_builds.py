# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine.recipe_api import Property

from recipe_engine.post_process import (
  DoesNotRun,
  DoesNotRunRE,
  DropExpectation,
  LogContains,
  MustRun,
  StepCommandContains,
)

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import chromium_swarming, test_utils, v8_builtins_pgo
from RECIPE_MODULES.recipe_engine import (
  buildbucket,
  json,
  properties,
  step,
  time,
)


@dataclass
class DEPS(RecipeScriptApi):
  buildbucket: buildbucket.API
  chromium_swarming: chromium_swarming.API
  json: json.API
  properties: properties.API
  step: step.API
  test_utils: test_utils.API
  time: time.API
  v8_builtins_pgo: v8_builtins_pgo.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  buildbucket: buildbucket.TEST_API
  chromium_swarming: chromium_swarming.TEST_API
  properties: properties.TEST_API
  test_utils: test_utils.TEST_API
  time: time.TEST_API
  v8_builtins_pgo: v8_builtins_pgo.TEST_API


PROPERTIES = {
  'compilators': Property(kind=list, default=None),
  'max_parallel_versions': Property(kind=int, default=None),
}


def RunSteps(api: DEPS, compilators, max_parallel_versions):
  return api.v8_builtins_pgo.run(
    compilators=compilators,
    version_number_cutoff=(1, 1),
    max_parallel_versions=max_parallel_versions,
  )


def GenTests(api: TEST_DEPS):
  all_tracks = {'x86', 'x64', 'x86-rl', 'x64-rl'}
  pgo_api = api.v8_builtins_pgo

  def test(name, *args, **kwargs):
    return api.test(
      name,
      api.buildbucket.ci_build(bucket='ci-hp', revision=None),
      *args,
      **kwargs,
    )

  def main_scenario(name, *args, max_parallel_versions=2, **kwargs):
    return test(
      name,
      api.properties(
        max_parallel_versions=max_parallel_versions,
        version_number_cutoff=(1, 1),
      ),
      api.time.seed(1700000000),
      api.time.step(10),
      pgo_api.mock_git_tags(
        [
          ('1234', 'refs/tags/0.1.10.1'),
          ('2234', 'refs/tags/1.1.1'),
          ('2234', 'refs/tags/1.1.1-pgo'),
          ('ab34', 'refs/tags/1.1.1.1'),
          ('ab34', 'refs/tags/1.1.1.1-pgo'),
          ('abde', 'refs/tags/1.1.1.2'),
          ('f034', 'refs/tags/1.1.1.3'),
          ('cd12', 'refs/tags/1.1.1.4'),
          ('43ff', 'refs/tags/1.1.2'),
        ]
      ),
      *args,
      **kwargs,
    )

  yield main_scenario(
    'basic',
    *pgo_api.mock_compilation(['1.1.2.0', '1.1.1.4'], all_tracks),
    *pgo_api.mock_profiles(['1.1.2.0', '1.1.1.4'], all_tracks),
  )

  yield test(
    'no_new_versions',
    pgo_api.mock_git_tags(
      [
        ('2234', 'refs/tags/1.1.1'),
        ('2234', 'refs/tags/1.1.1-pgo'),
      ]
    ),
    api.post_process(
      DoesNotRunRE,
      'augment isolates.*',
      'trigger profilers.*',
      'collect profiles.*',
      'upload to gs.*',
      'assign pgo tags.*',
    ),
    api.post_process(DropExpectation),
  )

  failing_track = 'x86'
  passing_tracks = all_tracks - {failing_track}
  yield main_scenario(
    'one_track_compilation_failure',
    *pgo_api.mock_compilation(['1.1.2.0'], all_tracks),
    *pgo_api.mock_profiles(['1.1.2.0'], all_tracks),
    *pgo_api.mock_compilation(['1.1.1.4'], passing_tracks),
    *pgo_api.mock_profiles(['1.1.1.4'], passing_tracks),
    *pgo_api.mock_compilation(
      ['1.1.1.4'], [failing_track], compilator_properties={}
    ),
    api.post_process(
      DoesNotRun,
      f'augment isolates.1.1.1.4 {failing_track}',
      f'trigger profilers.1.1.1.4 {failing_track}',
      f'collect profiles.1.1.1.4 {failing_track}',
      f'upload to gs.gsutil upload 1.1.1.4 {failing_track}',
      'upload to gs.gsutil upload metadata 1.1.1.4',
      'assign pgo tags.gerrit create_gerrit_tag (v8/v8 1.1.1.4-pgo)',
    ),
    api.post_process(DropExpectation),
    status='FAILURE',
  )

  failing_track = 'x86'
  passing_tracks = all_tracks - {failing_track}
  yield main_scenario(
    'one_track_profiling_failure',
    *pgo_api.mock_compilation(['1.1.2.0', '1.1.1.4'], all_tracks),
    *pgo_api.mock_profiles(['1.1.2.0'], all_tracks),
    *pgo_api.mock_profiles(['1.1.1.4'], passing_tracks),
    api.step_data(
      f'collect profiles.1.1.1.4 {failing_track}.pgo profile 1.1.1.4 x86 '
      'on Ubuntu 31.41',
      api.chromium_swarming.summary(
        api.test_utils.canned_gtest_output(True),
        {'shards': [{'state': 'FAILURE'}]},
      ),
    ),
    api.post_process(
      DoesNotRun,
      f'upload to gs.gsutil upload 1.1.1.4 {failing_track}',
      'upload to gs.gsutil upload metadata 1.1.1.4',
      'assign pgo tags.gerrit create_gerrit_tag (v8/v8 1.1.1.4-pgo)',
    ),
    api.post_process(DropExpectation),
    status='FAILURE',
  )

  yield main_scenario(
    'full_version_failure',
    *pgo_api.mock_compilation(['1.1.2.0'], all_tracks),
    *pgo_api.mock_profiles(['1.1.2.0'], all_tracks),
    *pgo_api.mock_compilation(
      ['1.1.1.4'], all_tracks, compilator_properties={}
    ),
    api.post_process(
      DoesNotRun,
      'augment isolates.1.1.1.4 x86',
      'trigger profilers.1.1.1.4 x86',
      'collect profiles.1.1.1.4 x86',
      'upload to gs.gsutil upload 1.1.1.4 x86',
      'augment isolates.1.1.1.4 x64',
      'trigger profilers.1.1.1.4 x64',
      'collect profiles.1.1.1.4 x64',
      'upload to gs.gsutil upload 1.1.1.4 x64',
      'upload to gs.gsutil upload metadata 1.1.1.4',
      'assign pgo tags.gerrit create_gerrit_tag (v8/v8 1.1.1.4-pgo)',
    ),
    api.post_process(
      StepCommandContains,
      'report exceptions.gsutil upload blocked-versions.txt',
      [
        '\n'.join(
          [
            '# If you manually add a version to this list, use the format:',
            '# <major>.<minor>.<build>.<patch> {"reason": "<Link to CL or bug>"}',
            '#',
            '# Example:',
            '# 12.8.5.0 {"reason": "crbug.com/40245627"}',
            '1.1.1.4 {"failures": [{"build": "https://cr-buildbucket.appspot.com/build/8945511751514863184", "time": 1700000010}]}',
          ]
        )
      ],
    ),
    api.post_process(DropExpectation),
    status='FAILURE',
  )

  yield test(
    'version_number_cutoff',
    api.properties(max_parallel_versions=100, version_number_cutoff=(1, 1, 1)),
    pgo_api.mock_git_tags(
      [
        ('1234', 'refs/tags/0.1.10.1'),
        ('1234', 'refs/tags/1.0.3.1'),
        ('2234', 'refs/tags/1.1.1'),
        ('ab34', 'refs/tags/1.1.1.1'),
        ('abde', 'refs/tags/1.1.1.2'),
        ('f034', 'refs/tags/1.1.1.3'),
        ('cd12', 'refs/tags/1.1.1.4'),
      ]
    ),
    api.post_process(
      MustRun,
      'trigger compilators.1.1.1.4 x86',
      'trigger compilators.1.1.1.4 x64',
      'trigger compilators.1.1.1.3 x86',
      'trigger compilators.1.1.1.3 x64',
      'trigger compilators.1.1.1.2 x86',
      'trigger compilators.1.1.1.2 x64',
      'trigger compilators.1.1.1.1 x86',
      'trigger compilators.1.1.1.1 x64',
      'trigger compilators.1.1.1.0 x86',
      'trigger compilators.1.1.1.0 x64',
    ),
    api.post_process(
      DoesNotRun,
      'trigger compilators.1.0.3.1 x86',
      'trigger compilators.1.0.3.1 x64',
      'trigger compilators.0.1.1.10 x86',
      'trigger compilators.0.1.1.10 x64',
    ),
    api.post_process(DropExpectation),
    # The test fails due to missing mock data for following steps.
    status='FAILURE',
  )

  mock_block_file = '\n'.join(
    [
      '# A multiline comment which is ignored',
      '# by the parser.',
      pgo_api.blocked_version_line('1.1.1.2'),
      pgo_api.blocked_version_line('1.1.1.4'),
      '1.1.1.5 {"reason": "crbug.com/12345"}',
      '',
    ]
  )
  yield test(
    'version_blocklist',
    api.properties(max_parallel_versions=100, version_number_cutoff=(1, 1, 1)),
    pgo_api.mock_git_tags(
      [
        ('ab34', 'refs/tags/1.1.1.1'),
        ('abde', 'refs/tags/1.1.1.2'),
      ]
    ),
    *pgo_api.mock_block_file(mock_block_file, report_exceptions=True),
    api.post_process(
      MustRun,
      'trigger compilators.1.1.1.1 x86',
      'trigger compilators.1.1.1.1 x64',
    ),
    api.post_process(
      DoesNotRun,
      'trigger compilators.1.1.1.2 x86',
      'trigger compilators.1.1.1.2 x64',
    ),
    api.post_process(DropExpectation),
    # The test fails due to missing mock data for following steps.
    status='FAILURE',
  )

  yield main_scenario(
    'tampered_profile',
    *pgo_api.mock_compilation(['1.1.2.0', '1.1.1.4'], ['x86', 'x64']),
    *pgo_api.mock_profiles(['1.1.2.0', '1.1.1.4'], ['x86']),
    *pgo_api.mock_profiles(['1.1.2.0'], ['x64']),
    *pgo_api.mock_profiles(['1.1.1.4'], ['x64'], content='invalid-content'),
    api.expect_exception('AssertionError'),
    api.post_process(DropExpectation),
  )

  retry_versions = [
    {'version': '1.1.1.2', 'failed_hours_ago': [0], 'retry': True},
    {'version': '1.1.1.3', 'failed_hours_ago': [0, 0], 'retry': False},
    {'version': '1.1.1.4', 'failed_hours_ago': [7, 7], 'retry': True},
    {'version': '1.1.1.5', 'failed_hours_ago': [7, 7, 1], 'retry': False},
    {'version': '1.1.1.6', 'failed_hours_ago': [25, 24, 19], 'retry': True},
    {'version': '1.1.1.7', 'failed_hours_ago': [360], 'retry': False},
  ]
  profiled_versions = [rv['version'] for rv in retry_versions if rv['retry']]
  skipped_versions = [rv['version'] for rv in retry_versions if not rv['retry']]
  download_blockfile = '\n'.join(
    [
      '# A multiline comment which is ignored',
      '# by the parser.',
      *[
        pgo_api.blocked_version_line(
          rv['version'], failures_hours_ago=rv['failed_hours_ago']
        )
        for rv in retry_versions
      ],
      '',
    ]
  )
  yield main_scenario(
    'retry_timing',
    pgo_api.mock_git_tags(
      [('abde', f'refs/tags/{rv["version"]}') for rv in retry_versions]
    ),
    *pgo_api.mock_block_file(download_blockfile),
    *pgo_api.mock_compilation(profiled_versions, all_tracks),
    *pgo_api.mock_profiles(profiled_versions, all_tracks),
    api.post_process(
      MustRun,
      *[f'trigger compilators.{v} x86' for v in profiled_versions],
    ),
    api.post_process(
      DoesNotRun,
      *[f'trigger compilators.{v} x86' for v in skipped_versions],
    ),
    api.post_process(DropExpectation),
    max_parallel_versions=len(retry_versions),
  )

  download_blockfile = '12.5.4.1 {[@;invalid json'
  yield main_scenario(
    'version_blockfile_invalid_justification',
    pgo_api.mock_git_tags([('abde', 'refs/tags/12.5.4.1')]),
    *pgo_api.mock_block_file(download_blockfile),
    api.post_process(DoesNotRun, 'trigger compilators.12.5.4.1 x86'),
    api.post_process(
      LogContains,
      'init trackers for candidate versions.gsutil cat',
      'blocked versions',
      [
        '"reason": "Justification cannot be parsed"',
      ],
    ),
    api.post_process(DropExpectation),
  )
