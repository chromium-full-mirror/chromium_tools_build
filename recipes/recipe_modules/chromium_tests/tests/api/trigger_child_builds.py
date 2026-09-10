# Copyright 2019 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import collections
import re

from recipe_engine import post_process, recipe_api

from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb
from PB.go.chromium.org.luci.swarming.proto.api_v2 import (
  swarming as swarming_pb,
)
from PB.recipe_modules.recipe_engine.led import properties as led_properties_pb

from RECIPE_MODULES.build import chromium_tests_builder_config as ctbc

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import (
  chromium,
  chromium_tests,
  chromium_tests_builder_config,
)
from RECIPE_MODULES.recipe_engine import properties, step


@dataclass
class DEPS(RecipeScriptApi):
  chromium: chromium.API
  chromium_tests: chromium_tests.API
  chromium_tests_builder_config: chromium_tests_builder_config.API
  properties: properties.API
  step: step.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  chromium: chromium.TEST_API
  chromium_tests_builder_config: chromium_tests_builder_config.TEST_API
  properties: properties.TEST_API


PROPERTIES = {
  'commit': recipe_api.Property(default=None),
  'set_output_commit': recipe_api.Property(default=True),
}


def RunSteps(api: DEPS, commit, set_output_commit):
  # Create a nested step so that setup steps can be easily filtered out
  with api.step.nest('setup steps'):
    builder_id = api.chromium.get_builder_id()
    builder_id, builder_config = (
      api.chromium_tests_builder_config.lookup_builder()
    )
    api.chromium_tests.configure_build(builder_config)
    update_step, _, _ = api.chromium_tests.prepare_checkout(
      builder_config, set_output_commit=set_output_commit
    )
    if commit is not None:
      commit = common_pb.GitilesCommit(**commit)

  api.chromium_tests.trigger_child_builds(
    builder_id, update_step, builder_config, commit=commit
  )


def GenTests(api: TEST_DEPS):

  def StepStdinRE(check, step_odict, step, regex):
    check(
      'stdin for %s contained %s' % (step, regex),
      re.match(regex, step_odict[step].stdin),
    )

  def builder_with_tester_to_trigger(bucket='ci', **kwargs):
    return api.chromium_tests_builder_config.ci_build(
      bucket=bucket,
      builder_group='fake-group',
      builder='fake-builder',
      builder_db=ctbc.BuilderDatabase.create(
        {
          'fake-group': {
            'fake-builder': ctbc.BuilderSpec.create(
              chromium_config='chromium',
              gclient_config='chromium',
            ),
            'fake-tester': ctbc.BuilderSpec.create(
              execution_mode=ctbc.TEST,
              chromium_config='chromium',
              gclient_config='chromium',
              parent_buildername='fake-builder',
            ),
          }
        }
      ),
      **kwargs,
    )

  yield api.test(
    'scheduler',
    builder_with_tester_to_trigger(),
    api.post_check(StepStdinRE, 'trigger', '.*"ref": "refs/heads/main".*'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'scheduler-with-no-commit',
    builder_with_tester_to_trigger(),
    api.properties(set_output_commit=False),
    api.post_check(post_process.MustRun, 'no commit for trigger'),
    api.expect_status('INFRA_FAILURE'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'scheduler-with-commit-provided',
    builder_with_tester_to_trigger(),
    api.properties(
      commit=common_pb.GitilesCommit(
        host='fake-host',
        project='fake-project',
        ref='fake-ref',
        id='fake-revision',
      )
    ),
    api.post_check(StepStdinRE, 'trigger', '.*"ref": "fake-ref".*'),
    api.post_process(post_process.DropExpectation),
  )

  led_trigger_prefix = 'trigger.chromium/ci/fake-tester'
  yield api.test(
    'led',
    builder_with_tester_to_trigger(),
    api.properties(
      **{
        '$recipe_engine/led': led_properties_pb.InputProperties(
          led_run_id='fake-run-id',
          rbe_cas_input=swarming_pb.CASReference(
            cas_instance=('projects/example/instances/default_instance'),
            digest=swarming_pb.Digest(
              hash='examplehash',
              size_bytes=71,
            ),
          ),
        ),
      }
    ),
    api.post_process(post_process.StepSuccess, led_trigger_prefix),
    api.post_process(
      post_process.StepSuccess, led_trigger_prefix + '.led get-builder'
    ),
    api.post_process(
      post_process.StepSuccess, led_trigger_prefix + '.led edit-gitiles-commit'
    ),
    api.post_process(
      post_process.StepSuccess, led_trigger_prefix + '.led edit'
    ),
    api.post_process(
      post_process.StepSuccess, led_trigger_prefix + '.led edit (2)'
    ),
    api.post_process(
      post_process.StepSuccess, led_trigger_prefix + '.led launch'
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'led-real-build',
    builder_with_tester_to_trigger(bucket='ci.shadow'),
    api.properties(
      **{
        '$recipe_engine/led': led_properties_pb.InputProperties(
          led_run_id='fake-run-id',
          rbe_cas_input=swarming_pb.CASReference(
            cas_instance=('projects/example/instances/default_instance'),
            digest=swarming_pb.Digest(
              hash='examplehash',
              size_bytes=71,
            ),
          ),
          shadowed_bucket='ci',
        ),
      }
    ),
    api.post_process(post_process.StepSuccess, led_trigger_prefix),
    api.post_process(
      post_process.StepSuccess, led_trigger_prefix + '.led get-builder'
    ),
    api.post_process(
      post_process.StepSuccess, led_trigger_prefix + '.led edit-gitiles-commit'
    ),
    api.post_process(
      post_process.StepSuccess, led_trigger_prefix + '.led edit'
    ),
    api.post_process(
      post_process.StepSuccess, led_trigger_prefix + '.led edit (2)'
    ),
    api.post_process(
      post_process.StepSuccess, led_trigger_prefix + '.led launch'
    ),
    api.post_process(post_process.DropExpectation),
  )

  # If someone uses led to trigger a CI builder with a CL applied, the CL should
  # be applied on the triggered tester as well
  yield api.test(
    'led-with-cl',
    api.chromium.try_build(
      bucket='ci.shadow',
      builder_group='fake-group',
      builder='fake-builder',
    ),
    api.chromium_tests_builder_config.properties(
      api.chromium_tests_builder_config.properties_assembler_for_ci_builder(
        builder_group='fake-group',
        builder='fake-builder',
        builder_spec=ctbc.BuilderSpec.create(
          gclient_config='chromium',
          chromium_config='chromium',
        ),
      )
      .with_tester(
        builder_group='fake-group',
        builder='fake-tester',
      )
      .assemble()
    ),
    api.properties(
      **{
        '$recipe_engine/led': led_properties_pb.InputProperties(
          led_run_id='fake-run-id',
          rbe_cas_input=swarming_pb.CASReference(
            cas_instance=('projects/example/instances/default_instance'),
            digest=swarming_pb.Digest(
              hash='examplehash',
              size_bytes=71,
            ),
          ),
          shadowed_bucket='ci',
        ),
      }
    ),
    api.post_process(post_process.StepSuccess, led_trigger_prefix),
    api.post_process(
      post_process.StepSuccess, led_trigger_prefix + '.led get-builder'
    ),
    api.post_process(
      post_process.StepSuccess, led_trigger_prefix + '.led edit-gitiles-commit'
    ),
    api.post_process(
      post_process.StepSuccess, led_trigger_prefix + '.led edit-gerrit-cl'
    ),
    api.post_process(
      post_process.StepSuccess, led_trigger_prefix + '.led edit'
    ),
    api.post_process(
      post_process.StepSuccess, led_trigger_prefix + '.led edit (2)'
    ),
    api.post_process(
      post_process.StepSuccess, led_trigger_prefix + '.led launch'
    ),
    api.post_process(post_process.DropExpectation),
  )
