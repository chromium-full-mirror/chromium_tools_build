# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Generic runner for scripts.

The recipe will launch scripts based on the input.
"""

from recipe_engine import post_process
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb
from PB.recipe_engine import result as result_pb
from PB.recipes.build.chromium.generic_script_runner \
    import InputProperties, ScriptInvocation
from PB.recipe_modules.recipe_engine.swarming import properties as swarming_pb
from RECIPE_MODULES.build import proto_validation

PROPERTIES = InputProperties

DEPS = [
    'chromium',
    'chromium_checkout',
    'depot_tools/gclient',
    'depot_tools/git',
    'depot_tools/tryserver',
    'recipe_engine/context',
    'recipe_engine/properties',
    'recipe_engine/step',
    'recipe_engine/swarming',
]

VALIDATORS = proto_validation.Registry()


def RunSteps(api, properties):
  assert not api.tryserver.is_tryserver
  assert api.swarming.current_server == "https://chromium-swarm.appspot.com"
  errors = VALIDATORS.validate(properties)
  if errors:
    summary = ['The following errors were found with the input properties:', '']
    summary.extend(errors)
    return result_pb.RawResult(
        status=common_pb.INFRA_FAILURE, summary_markdown='\n'.join(summary))

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


def _RunScript(api, source_dir, script_invocation):
  cmd = [source_dir / script_invocation.script]
  cmd.extend(script_invocation.args)
  return api.step(
      name=script_invocation.step_name, cmd=cmd, raise_on_failure=False)


@VALIDATORS.register(InputProperties)
def _validate_input_properties(message, ctx):
  ctx.validate_repeated_field(message, 'scripts')


@VALIDATORS.register(ScriptInvocation)
def _validate_script_invocation(message, ctx):
  ctx.validate_field(message, 'step_name')
  ctx.validate_field(message, 'script')


def GenTests(api):
  yield api.test(
      'happy_path_fuzzy_diff_analyzer',
      api.properties.environ(
          swarming_pb.EnvProperties(
              SWARMING_SERVER='https://chromium-swarm.appspot.com')),
      api.properties(
          InputProperties(scripts=[
              ScriptInvocation(
                  step_name='run script',
                  script='some/script.py',
                  args=['--some-arg'],
              )
          ])),
      api.post_process(post_process.StepCommandRE, 'run script', [
          '.*some/script.py',
          '--some-arg',
      ]),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'validate_missing_scripts',
      api.properties.environ(
          swarming_pb.EnvProperties(
              SWARMING_SERVER='https://chromium-swarm.appspot.com')),
      api.properties(InputProperties(scripts=[])),
      api.post_process(
          post_process.SummaryMarkdown,
          'The following errors were found with the input properties:\n\n'
          'scripts is empty'),
      api.expect_status('INFRA_FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'validate_missing_step_name',
      api.properties.environ(
          swarming_pb.EnvProperties(
              SWARMING_SERVER='https://chromium-swarm.appspot.com')),
      api.properties(
          InputProperties(
              scripts=[ScriptInvocation(script='some/script.py',)])),
      api.post_process(
          post_process.SummaryMarkdown,
          'The following errors were found with the input properties:\n\n'
          'scripts[0].step_name is not set'),
      api.expect_status('INFRA_FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'validate_missing_script',
      api.properties.environ(
          swarming_pb.EnvProperties(
              SWARMING_SERVER='https://chromium-swarm.appspot.com')),
      api.properties(
          InputProperties(scripts=[ScriptInvocation(step_name='run script',)])),
      api.post_process(
          post_process.SummaryMarkdown,
          'The following errors were found with the input properties:\n\n'
          'scripts[0].script is not set'),
      api.expect_status('INFRA_FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'multiple_scripts_with_failure',
      api.properties.environ(
          swarming_pb.EnvProperties(
              SWARMING_SERVER='https://chromium-swarm.appspot.com')),
      api.properties(
          InputProperties(scripts=[
              ScriptInvocation(
                  step_name='step_name_failure', script='some/script.py'),
              ScriptInvocation(
                  step_name='step_name_success', script='some/script.py')
          ])),
      api.step_data('step_name_failure', retcode=1),
      api.post_process(post_process.StepFailure, 'step_name_failure'),
      api.post_process(post_process.StepSuccess, 'step_name_success'),
      api.expect_status('FAILURE'),
      api.post_process(post_process.SummaryMarkdown,
                       'Step(\'step_name_failure\') (retcode: 1)'),
      api.post_process(post_process.DropExpectation),
  )
