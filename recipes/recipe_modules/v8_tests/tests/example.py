# Copyright 2022 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import json

from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb
from PB.recipe_engine import result as result_pb2
from recipe_engine.post_process import DropExpectation, LogContains, StepFailure

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import v8_tests
from RECIPE_MODULES.recipe_engine import (
  buildbucket,
  json as json_module,
  path,
  properties,
  runtime,
  swarming,
)


@dataclass
class DEPS(RecipeScriptApi):
  buildbucket: buildbucket.API
  json: json_module.API
  path: path.API
  properties: properties.API
  runtime: runtime.API
  swarming: swarming.API
  v8_tests: v8_tests.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  buildbucket: buildbucket.TEST_API
  json: json_module.TEST_API
  properties: properties.TEST_API
  swarming: swarming.TEST_API
  v8_tests: v8_tests.TEST_API


def RunSteps(api: DEPS):
  api.v8_tests.set_config('v8')
  api.v8_tests.enable_swarming = api.properties.get('enable_swarming', True)
  api.v8_tests.set_up_swarming()
  api.v8_tests.read_cl_footer_flags()
  api.v8_tests.load_static_test_configs()
  source_dir = api.path.cache_dir / 'builder' / 'src'

  tests = api.v8_tests.extra_tests_from_properties()
  result = api.v8_tests.runtests(source_dir, tests)
  status = common_pb.FAILURE if result.is_negative else common_pb.SUCCESS
  return result_pb2.RawResult(status=status)


def GenTests(api: TEST_DEPS):
  # Minimal v8-side test spec for simulating most recipe features.
  test_spec = json.dumps(
    {
      "tests": [
        {"name": "v8testing"},
        {"name": "test262", "test_args": ["--extra-flags=--flag"]},
      ],
    },
    indent=2,
  )
  parent_test_spec = api.v8_tests.example_parent_test_spec_properties(
    'v8_foobar_rel_ng_triggered', test_spec
  )
  buider_spec = parent_test_spec.get('parent_test_spec', {})
  swarm_hashes = api.v8_tests._make_dummy_swarm_hashes(
    test[0] for test in buider_spec.get('tests', [])
  )

  yield api.test(
    'basic',
    api.buildbucket.try_build(project='v8'),
    api.properties(swarm_hashes=swarm_hashes, **parent_test_spec),
    api.post_process(
      api.swarming.check_triggered_request,
      'trigger tests.[trigger] Check on Ubuntu-16.04',
      lambda check, req: check(
        ('pool', 'chromium.tests') in req[0].dimensions.items()
      ),
      lambda check, req: check(
        ('os', 'Ubuntu-16.04') in req[0].dimensions.items()
      ),
      lambda check, req: check('project:v8' in req.tags),
    ),
    api.post_process(
      LogContains,
      'trigger tests.[trigger] Test262 on Ubuntu-16.04',
      'json.input',
      ['"--extra-flags=--flag",'],
    ),
    api.post_process(DropExpectation),
  )

  yield api.test(
    'cl_with_resultdb_footer',
    api.buildbucket.try_build(),
    api.properties(swarm_hashes=swarm_hashes, **parent_test_spec),
    api.step_data(
      'parse description', api.json.output({'V8-Recipe-Flags': ['resultdb']})
    ),
    api.post_process(
      LogContains,
      'parse description',
      'json.output',
      [
        '"V8-Recipe-Flags":',
        '"resultdb"',
      ],
    ),
    api.post_process(DropExpectation),
  )

  yield api.test(
    'local_test_failure',
    api.buildbucket.try_build(),
    api.properties(enable_swarming=False, **parent_test_spec),
    api.step_data('Check', retcode=1),
    api.post_process(StepFailure, 'Check'),
    api.post_process(DropExpectation),
  )

  yield api.test(
    'local_other_failure',
    api.buildbucket.try_build(),
    api.properties(enable_swarming=False, **parent_test_spec),
    api.step_data('Check', retcode=2),
    api.post_process(StepFailure, 'Check'),
    api.post_process(DropExpectation),
    status='FAILURE',
  )

  yield api.test(
    'exonerated_flake',
    api.buildbucket.try_build(),
    api.properties(swarm_hashes=swarm_hashes, **parent_test_spec),
    api.step_data(
      'Check',
      api.v8_tests.flakes(
        2, crash_type='_Magically_exonerated_crash_type_for_testing_'
      ),
    ),
    api.post_process(DropExpectation),
    status='SUCCESS',
  )
