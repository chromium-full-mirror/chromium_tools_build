# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process, turboci
from PB.recipe_engine import result as result_pb2
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import chromium, chromium_turboci
from RECIPE_MODULES.recipe_engine import buildbucket, properties


@dataclass
class DEPS(RecipeScriptApi):
  buildbucket: buildbucket.API
  chromium: chromium.API
  chromium_turboci: chromium_turboci.API
  properties: properties.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  buildbucket: buildbucket.TEST_API


def RunSteps(api: DEPS):
  api.chromium.set_config('chromium')
  # exercise ensure_check_id
  api.chromium_turboci.ensure_check_id('foo', 'prefix')
  api.chromium_turboci.ensure_check_id('', 'prefix')

  # create nodes to satisfy dependencies
  turboci.write_nodes(
    turboci.reason('setup'),
    turboci.check(
      'source', kind='CHECK_KIND_SOURCE', state='CHECK_STATE_FINAL'
    ),
  )

  # exercise create_build_check
  api.chromium_turboci.create_build_check('build', 'source')
  api.chromium_turboci.create_build_check(
    'build2', 'source', create_analyze_check=True
  )

  # exercise prepare_analyze_check
  callback = api.chromium_turboci.prepare_analyze_check(
    'analyze',
    test_targets=['test'],
    additional_compile_targets=['compile'],
    analyze_config_names=['config'],
    additional_exclusions={'file': 'reason'},
  )
  callback('reason', ['compile'], ['test'])

  # exercise update_build_check_compile_targets
  api.chromium_turboci.update_build_check_compile_targets(
    'build', ['target'], 'reason'
  )

  # exercise set_build_check_planned
  api.chromium_turboci.set_build_check_planned('build', 'reason')

  # exercise finalize_build_check
  api.chromium_turboci.finalize_build_check('build', 'reason')

  api.chromium_turboci.create_build_check('build_final', 'source')
  api.chromium_turboci.set_build_check_planned('build_final', 'reason')
  api.chromium_turboci.finalize_build_check(
    'build_final',
    'reason',
    raw_result=result_pb2.RawResult(
      status=common_pb.SUCCESS, summary_markdown='markdown'
    ),
  )

  # exercise get_test_check_id
  api.chromium_turboci.get_test_check_id('test')

  # exercise create_test_check
  api.chromium_turboci.create_test_check('test')

  # exercise set_test_check_planned
  api.chromium_turboci.set_test_check_planned('test')

  # exercise finalize_test_check
  api.chromium_turboci.finalize_test_check('test', True, 'display')


def GenTests(api: TEST_DEPS):
  yield api.test(
    'basic',
    api.buildbucket.ci_build(
      project='chromium',
      bucket='ci',
      builder='builder',
    ),
    api.post_process(post_process.StatusSuccess),
    api.post_process(post_process.DropExpectation),
  )
