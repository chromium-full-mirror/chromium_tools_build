# Copyright 2014 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from contextlib import contextmanager
from recipe_engine.post_process import DropExpectation, MustRun

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import builder_group, reclient
from RECIPE_MODULES.depot_tools import bot_update, depot_tools, gclient
from RECIPE_MODULES.recipe_engine import (
    buildbucket,
    cas,
    context,
    file,
    legacy_annotation,
    path,
    platform,
    properties,
    step,
    swarming,
)


@dataclass
class DEPS(RecipeScriptApi):
  bot_update: bot_update.API
  buildbucket: buildbucket.API
  builder_group: builder_group.API
  cas: cas.API
  context: context.API
  depot_tools: depot_tools.API
  file: file.API
  gclient: gclient.API
  legacy_annotation: legacy_annotation.API
  path: path.API
  platform: platform.API
  properties: properties.API
  reclient: reclient.API
  step: step.API
  swarming: swarming.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  bot_update: bot_update.TEST_API
  buildbucket: buildbucket.TEST_API
  builder_group: builder_group.TEST_API
  context: context.TEST_API
  file: file.TEST_API
  gclient: gclient.TEST_API
  legacy_annotation: legacy_annotation.TEST_API
  path: path.TEST_API
  platform: platform.TEST_API
  properties: properties.TEST_API
  reclient: reclient.TEST_API
  step: step.TEST_API
  swarming: swarming.TEST_API

# Maps from triggering builder to triggered builder for swarming.
swarming_dimensions = {
    'linux_64-newlib-arm_qemu-pnacl-dbg': {
        'builder': 'odroid_32-newlib-arm_hw-pnacl-dbg',
        'pool': 'luci.flex.ci',
    },
    'linux_64-newlib-arm_qemu-pnacl-opt': {
        'builder': 'odroid_32-newlib-arm_hw-pnacl-opt',
        'pool': 'luci.flex.ci',
    },
    'linux_64-newlib-arm_qemu-pnacl-buildonly-spec': {
        'builder': 'odroid_32-newlib-arm_hw-pnacl-spec',
        'pool': 'luci.flex.ci',
    },
    'nacl-arm_opt': {
        'builder': 'nacl-arm_hw_opt',
        'pool': 'luci.flex.try',
    },
    'nacl-arm_perf': {
        'builder': 'nacl-arm_hw_perf',
        'pool': 'luci.flex.try',
    },
}

swarming_collection_step_name = 'collect hardware tests'


class FileInfo:

  def __init__(self, input_path, output_dir, is_dir):
    self.input_path = input_path
    self.output_dir = output_dir
    self.is_dir = is_dir


def CheckoutSteps(api: DEPS):
  api.gclient.set_config('nacl')
  update_result = api.bot_update.ensure_checkout()
  api.gclient.runhooks()
  return update_result


def ExecBuildSteps(api: DEPS, source_dir, env):
  with api.context(cwd=source_dir, env=env):
    with api.depot_tools.on_path():
      cmd = [
          'vpython3', '-u',
          source_dir.joinpath('buildbot', 'buildbot_selector.py')
      ]
      api.legacy_annotation('annotated steps', cmd)


def AnnotatedStepsSteps(api: DEPS, got_revision, source_dir,
                        compiled_sources_path):
  use_reclient = api.reclient.instance
  # Default environment; required by all builders.
  env = {
      'BETWEEN_BUILDERS': str(compiled_sources_path),
      'BOT_TYPE': 'builder_bot',
      'BUILDBOT_MASTERNAME': api.builder_group.for_current,
      'BUILDBOT_BUILDERNAME': api.buildbucket.builder_name,
      'BUILDBOT_REVISION': api.buildbucket.gitiles_commit.id,
      'BUILDBOT_BUILDNUMBER': api.buildbucket.build.number,
      'BUILDBOT_GOT_REVISION': got_revision,
      'BUILDBOT_SLAVE_TYPE': api.properties['slavetype'],
      'PYTHONPATH': str(api.repo_resource('scripts')),
  }
  if use_reclient:
    env.update({'USE_RECLIENT': '1'})
    with api.reclient.process(
        'compile', '', source_dir, deps_cache_by_step=False):
      ExecBuildSteps(api, source_dir, env)
  else:
    ExecBuildSteps(api, source_dir, env)


def UploadFilesToCAS(api: DEPS, files):
  """Pushes files up to the RBE-CAS."""
  with api.step.nest('Upload isolates'):
    isolate_dir = api.path.mkdtemp('isolate_directory')
    for file_info in files:
      output_dir = isolate_dir / file_info.output_dir
      if file_info.is_dir:
        api.file.copytree("Copying tree: {}".format(file_info.input_path),
                          file_info.input_path, output_dir)
  return api.cas.archive('archive files', isolate_dir, isolate_dir)


def ParseSwarmingResults(api: DEPS, builder_name, results):
  """Called after swarming.collect() to produce proper step results."""
  success = True
  message = 'Starting execution on {}'.format(builder_name)
  step = api.step(message, cmd=None)
  step.presentation.logs['output'] = []
  for result in results:
    if result.state not in (api.swarming.TaskState.COMPLETED,
                            api.swarming.TaskState.TIMED_OUT):
      api.step.active_result.presentation.status = 'EXCEPTION'
      success = False
      result.analyze()
    if result.state == api.swarming.TaskState.TIMED_OUT or not result.success:
      step.presentation.status = 'EXCEPTION'
      success = False
    if result.output is not None:
      for line in result.output.splitlines():
        if line.startswith('@@@BUILD_STEP'):
          # Cut out the '@@@BUILD_STEP' start and the '@@@' end.
          step = api.step(line[14:-3], cmd=None)
          step.presentation.logs['output'] = []
        elif line == '@@@STEP_FAILURE@@@':
          step.presentation.status = 'FAILURE'
          success = False
        else:
          step.presentation.logs['output'].append(line)

  if not success:
    fail_text = 'hardware test failure on {}'.format(builder_name)
    raise api.step.StepFailure(fail_text)


def TriggerHardwareTests(api: DEPS, got_revision, source_dir,
                         compiled_sources_path, dimensions):
  """Triggers tests on ARM hardware bots with precompiled sources."""
  # Isolate required files
  isolated_files = [
      FileInfo(source_dir, 'native_client', True),
      FileInfo(compiled_sources_path, 'native_client/between_builders', True),
      FileInfo(api.path.start_dir / 'third_party', 'third_party', True),
      FileInfo(api.path.start_dir / 'testing', 'testing', True),
      # The ARM bots need the linux_arm toolchain.
      FileInfo(
          source_dir.joinpath('toolchain', 'linux_x86'),
          'native_client/toolchain/linux_arm', True),
  ]
  isolated_digest = UploadFilesToCAS(api, isolated_files)

  environment_vars = {
      'BUILDBOT_MASTERNAME': api.builder_group.for_current,
      'BUILDBOT_BUILDERNAME': dimensions['builder'],
      'BUILDBOT_SLAVE_TYPE': api.properties['slavetype'],
      'PYTHONPATH': str(api.repo_resource('scripts')),
      'BOT_TYPE': 'arm_hw_bot',
  }

  # Generate the swarming request
  request = api.swarming.task_request().with_name(dimensions['builder'])
  request = (
      request.with_slice(
          0,
          request[0].with_command([
              'python3', 'buildbot/buildbot_selector.py'
          ]).with_relative_cwd('native_client').with_dimensions(
              **dimensions).with_env_vars(**environment_vars)
          .with_cas_input_root(isolated_digest).with_expiration_secs(
              60 * 120).with_execution_timeout_secs(60 * 60),
      ))

  metadata = api.swarming.trigger('Trigger hardware tests', requests=[request])

  # Collect the result of the task.
  output_directory = api.path.mkdtemp('swarming-output')
  results = api.swarming.collect(
      swarming_collection_step_name,
      metadata,
      output_dir=output_directory,
      timeout='180m')
  ParseSwarmingResults(api, dimensions['builder'], results)


def RunSteps(api: DEPS):
  update_result = CheckoutSteps(api)
  got_revision = update_result.properties['got_revision']
  source_dir = update_result.source_root.path
  compiled_sources_path = api.path.mkdtemp('between_builders')
  AnnotatedStepsSteps(api, got_revision, source_dir, compiled_sources_path)
  if api.buildbucket.builder_name in swarming_dimensions:
    TriggerHardwareTests(api, got_revision, source_dir, compiled_sources_path,
                         swarming_dimensions[api.buildbucket.builder_name])


def GenTests(api: TEST_DEPS):
  git_repo = (
      'https://chromium.googlesource.com/native_client/src/native_client.git')

  triggering_builder_name = 'linux_64-newlib-arm_qemu-pnacl-dbg'
  yield api.test(
      'linux_triggering_arm',
      api.platform('linux', 64),
      api.builder_group.for_current('client.nacl'),
      api.buildbucket.ci_build(
          builder=triggering_builder_name,
          git_repo=git_repo,
          revision='a' * 40,
          build_number=1234,
      ),
      api.properties(slavetype='BuilderTester'),
  )

  yield api.test(
      'linux_triggering_failed',
      api.platform('linux', 64),
      api.builder_group.for_current('client.nacl'),
      api.buildbucket.ci_build(
          builder=triggering_builder_name,
          git_repo=git_repo,
          revision='a' * 40,
          build_number=1234,
      ),
      api.properties(slavetype='BuilderTester'),
      api.step_data('annotated steps', api.legacy_annotation.failure_step),
      api.expect_status('FAILURE'),
  )

  failed_output = '@@@BUILD_STEP fake_step@@@\nfake_output\n@@@STEP_FAILURE@@@'
  failed_result = api.swarming.task_result(
      id='0',
      name=swarming_dimensions[triggering_builder_name]['builder'],
      state=api.swarming.TaskState.COMPLETED,
      output=failed_output,
      failure=True)
  yield api.test(
      'linux_triggering_arm_with_collect_COMPLETED_and_failed',
      api.platform('linux', 64),
      api.builder_group.for_current('client.nacl'),
      api.buildbucket.ci_build(
          builder=triggering_builder_name,
          git_repo=git_repo,
          revision='a' * 40,
          build_number=1234,
      ),
      api.properties(slavetype='BuilderTester'),
      api.override_step_data(swarming_collection_step_name,
                             api.swarming.collect([failed_result])),
      api.expect_status('FAILURE'),
  )

  timeout_result = api.swarming.task_result(
      id='0',
      name=swarming_dimensions[triggering_builder_name]['builder'],
      state=api.swarming.TaskState.TIMED_OUT)
  yield api.test(
      'linux_triggering_arm_with_collect_TIMED_OUT',
      api.platform('linux', 64),
      api.builder_group.for_current('client.nacl'),
      api.buildbucket.ci_build(
          builder=triggering_builder_name,
          git_repo=git_repo,
          revision='a' * 40,
          build_number=1234,
      ),
      api.properties(slavetype='BuilderTester'),
      api.override_step_data(swarming_collection_step_name,
                             api.swarming.collect([timeout_result])),
      api.expect_status('FAILURE'),
  )

  died_result = api.swarming.task_result(
      id='0',
      name=swarming_dimensions[triggering_builder_name]['builder'],
      state=api.swarming.TaskState.BOT_DIED)
  yield api.test(
      'linux_triggering_arm_with_collect_BOT_DIED',
      api.platform('linux', 64),
      api.builder_group.for_current('client.nacl'),
      api.buildbucket.ci_build(
          builder=triggering_builder_name,
          git_repo=git_repo,
          revision='a' * 40,
          build_number=1234,
      ),
      api.properties(slavetype='BuilderTester'),
      api.override_step_data(swarming_collection_step_name,
                             api.swarming.collect([died_result])),
      api.expect_status('INFRA_FAILURE'),
  )

  yield api.test(
      'reclient_linux',
      api.platform('linux', 64),
      api.builder_group.for_current('client.nacl'),
      api.buildbucket.ci_build(
          builder='nacl-precise_64-newlib-x86_64-pnacl',
          git_repo=git_repo,
          revision='a' * 40,
          build_number=1234,
      ),
      api.properties(slavetype='BuilderTester'),
      api.properties(
          **{'$build/reclient': {
              'instance': 'fake-reclient-instance',
          }},),
      api.post_process(MustRun, 'preprocess for reclient'),
      api.post_process(DropExpectation),
  )
