# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Generic runner for scripts.

The recipe will launch scripts based on the input.
"""

from recipe_engine import post_process
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb
from PB.recipe_engine import result as result_pb
from PB.recipes.build.chromium.generic_script_runner import (
  InputProperties,
  ScriptInvocation,
)
from PB.recipe_modules.recipe_engine.swarming import properties as swarming_pb
from RECIPE_MODULES.build import proto_validation

PROPERTIES = InputProperties

from dataclasses import dataclass  # noqa: E402

from recipe_engine.recipe_api import RecipeScriptApi  # noqa: E402
from recipe_engine.recipe_test_api import RecipeTestApi  # noqa: E402

from RECIPE_MODULES.build import (  # noqa: E402
  chromium,
  chromium_checkout,
  chromium_tests,
  chromium_tests_builder_config,
)
from RECIPE_MODULES.depot_tools import gclient  # noqa: E402
from RECIPE_MODULES.recipe_engine import (  # noqa: E402
  context,
  properties,
  step,
  swarming,
)


@dataclass
class DEPS(RecipeScriptApi):
  chromium: chromium.API
  chromium_checkout: chromium_checkout.API
  chromium_tests: chromium_tests.API
  chromium_tests_builder_config: chromium_tests_builder_config.API
  context: context.API
  gclient: gclient.API
  properties: properties.API
  step: step.API
  swarming: swarming.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  chromium: chromium.TEST_API
  chromium_tests_builder_config: chromium_tests_builder_config.TEST_API
  properties: properties.TEST_API
  step: step.TEST_API


VALIDATORS = proto_validation.Registry()


def RunSteps(api: DEPS, properties):
  assert api.swarming.current_server in (
    'https://chromium-swarm.appspot.com',
    'https://chrome-swarming.appspot.com',
  )
  errors = VALIDATORS.validate(properties)
  if errors:
    summary = ['The following errors were found with the input properties:', '']
    summary.extend(errors)
    return result_pb.RawResult(
      status=common_pb.INFRA_FAILURE, summary_markdown='\n'.join(summary)
    )

  if '$build/chromium_tests_builder_config' in api.properties:
    _, builder_config = api.chromium_tests_builder_config.lookup_builder()
    api.chromium_tests.configure_build(builder_config)
  else:
    api.gclient.set_config('chromium_skip_wpr_archives_download')
  update_result = api.chromium_checkout.ensure_checkout()
  api.gclient.runhooks()

  source_dir = update_result.source_root.path
  results = []
  for script_invocation in properties.scripts:
    with api.context(cwd=source_dir):
      results.append(_RunScript(api, source_dir, script_invocation))

  for result in results:
    api.step.raise_on_failure(result)


def _RunScript(api: DEPS, source_dir, script_invocation):
  cmd = [source_dir / script_invocation.script]
  cmd.extend(script_invocation.args)
  return api.step(
    name=script_invocation.step_name, cmd=cmd, raise_on_failure=False
  )


@VALIDATORS.register(InputProperties)
def _validate_input_properties(message, ctx):
  ctx.validate_repeated_field(message, 'scripts')


@VALIDATORS.register(ScriptInvocation)
def _validate_script_invocation(message, ctx):
  ctx.validate_field(message, 'step_name')
  ctx.validate_field(message, 'script')


def GenTests(api: TEST_DEPS):
  yield api.test(
    'happy_path_fuzzy_diff_analyzer',
    api.properties.environ(
      swarming_pb.EnvProperties(
        SWARMING_SERVER='https://chromium-swarm.appspot.com'
      )
    ),
    api.properties(
      InputProperties(
        scripts=[
          ScriptInvocation(
            step_name='run script',
            script='some/script.py',
            args=['--some-arg'],
          )
        ]
      )
    ),
    api.post_process(
      post_process.StepCommandRE,
      'run script',
      [
        '.*some/script.py',
        '--some-arg',
      ],
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'happy_path_try',
    api.chromium.try_build(
      builder_group='fake-group',
      builder='fake-builder',
    ),
    api.chromium_tests_builder_config.properties(
      api.chromium_tests_builder_config.properties_assembler_for_ci_builder(
        builder_group='fake-group',
        builder='fake-builder',
      ).assemble()
    ),
    api.properties.environ(
      swarming_pb.EnvProperties(
        SWARMING_SERVER='https://chromium-swarm.appspot.com'
      )
    ),
    api.properties(
      InputProperties(
        scripts=[
          ScriptInvocation(
            step_name='run script',
            script='some/script.py',
            args=['--some-arg'],
          )
        ]
      )
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'validate_missing_scripts',
    api.properties.environ(
      swarming_pb.EnvProperties(
        SWARMING_SERVER='https://chromium-swarm.appspot.com'
      )
    ),
    api.properties(InputProperties(scripts=[])),
    api.post_process(
      post_process.SummaryMarkdown,
      'The following errors were found with the input properties:\n\n'
      'scripts is empty',
    ),
    api.expect_status('INFRA_FAILURE'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'validate_missing_step_name',
    api.properties.environ(
      swarming_pb.EnvProperties(
        SWARMING_SERVER='https://chromium-swarm.appspot.com'
      )
    ),
    api.properties(
      InputProperties(
        scripts=[
          ScriptInvocation(
            script='some/script.py',
          )
        ]
      )
    ),
    api.post_process(
      post_process.SummaryMarkdown,
      'The following errors were found with the input properties:\n\n'
      'scripts[0].step_name is not set',
    ),
    api.expect_status('INFRA_FAILURE'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'validate_missing_script',
    api.properties.environ(
      swarming_pb.EnvProperties(
        SWARMING_SERVER='https://chromium-swarm.appspot.com'
      )
    ),
    api.properties(
      InputProperties(
        scripts=[
          ScriptInvocation(
            step_name='run script',
          )
        ]
      )
    ),
    api.post_process(
      post_process.SummaryMarkdown,
      'The following errors were found with the input properties:\n\n'
      'scripts[0].script is not set',
    ),
    api.expect_status('INFRA_FAILURE'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'multiple_scripts_with_failure',
    api.properties.environ(
      swarming_pb.EnvProperties(
        SWARMING_SERVER='https://chromium-swarm.appspot.com'
      )
    ),
    api.properties(
      InputProperties(
        scripts=[
          ScriptInvocation(
            step_name='step_name_failure', script='some/script.py'
          ),
          ScriptInvocation(
            step_name='step_name_success', script='some/script.py'
          ),
        ]
      )
    ),
    api.step_data('step_name_failure', retcode=1),
    api.post_process(post_process.StepFailure, 'step_name_failure'),
    api.post_process(post_process.StepSuccess, 'step_name_success'),
    api.expect_status('FAILURE'),
    api.post_process(
      post_process.SummaryMarkdown, 'Step(\'step_name_failure\') (retcode: 1)'
    ),
    api.post_process(post_process.DropExpectation),
  )
