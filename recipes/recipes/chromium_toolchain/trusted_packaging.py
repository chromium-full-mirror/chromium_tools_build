# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process

from PB.recipes.build.chromium_toolchain.trusted_packaging import InputProperties
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb2

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.recipe_engine import (
    buildbucket,
    cipd,
    context,
    json,
    platform,
    properties,
    step,
)


@dataclass
class DEPS(RecipeScriptApi):
  buildbucket: buildbucket.API
  cipd: cipd.API
  context: context.API
  json: json.API
  platform: platform.API
  properties: properties.API
  step: step.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  buildbucket: buildbucket.TEST_API
  platform: platform.TEST_API
  properties: properties.TEST_API

PROPERTIES = InputProperties


def RunSteps(api: DEPS, properties):
  if properties.toolchain == InputProperties.UNKNOWN:
    raise api.step.StepFailure(
        "toolchain property must be set to CLANG or RUST")

  if properties.toolchain == InputProperties.CLANG:
    toolchain_name = 'clang'
  else:
    toolchain_name = 'rust'

  # Run TBI packaging
  with api.step.nest(f"package {toolchain_name}"):
    tbi_client = api.cipd.ensure_tool(
        "infra_internal/tools/security/lexan_tbi_client/${platform}", "latest")

    args = [
        tbi_client,
        "--result_file",
        api.json.output(name="summary"),
    ]

    if properties.toolchain == InputProperties.CLANG:
      args.append("--clang")
    if properties.config_path:
      args.extend(["--config", properties.config_path])

    if properties.trusted_build_instance:
      args.extend(["--instance", properties.trusted_build_instance])
    if properties.trusted_build_instance_pool:
      args.extend(["--pool", properties.trusted_build_instance_pool])
    if properties.trusted_build_instance_env:
      args.extend(["--env", properties.trusted_build_instance_env])
    if properties.trusted_build_instance_project:
      args.extend(["--project", properties.trusted_build_instance_project])

    if api.buildbucket.build.id:
      args.extend(["--build_prefix", f"bb-{api.buildbucket.build.id}"])

    # TODO(dlf): Support rust

    # If we are in a CI build (not a try job), override the source commit.
    # For try jobs, lexan_tbi_client automatically detects Gerrit env vars.
    changes = api.buildbucket.build.input.gerrit_changes
    if not changes:
      gitiles_commit = api.buildbucket.gitiles_commit
      if gitiles_commit and gitiles_commit.id:
        args.extend(["-chromium_src_commit", gitiles_commit.id])

    env = {}
    if changes:
      change = changes[0]
      env = {
          'GERRIT_HOST': str(change.host),
          'GERRIT_PROJECT': str(change.project),
          'GERRIT_CHANGE_ID': str(change.change),
          'GERRIT_PATCHSET_ID': str(change.patchset),
      }

    # Run the TBI client.
    with api.context(env=env):
      res = api.step('request build', args)
      if res.exc_result.retcode == 0:
        res.presentation.step_text = 'TBI finished successfully'


def GenTests(api: TEST_DEPS):

  def gen_props(toolchain, **kwargs):
    return api.properties(toolchain=toolchain, **kwargs)

  yield api.test(
      'clang_success',
      api.platform.name('linux'),
      api.buildbucket.ci_build(
          project='chromium',
          bucket='ci',
          builder='trusted-packaging-linux-clang',
          git_ref='refs/heads/main',
          revision='a' * 40,
      ),
      gen_props(InputProperties.CLANG),
      api.post_process(post_process.StatusSuccess),
      api.post_process(post_process.MustRun, 'package clang.request build'),
      api.post_process(post_process.StepCommandContains,
                       'package clang.request build', ['--clang']),
      api.post_process(post_process.StepCommandContains,
                       'package clang.request build', [
                           '-chromium_src_commit',
                           'a' * 40,
                       ]),
      api.post_process(post_process.StepCommandDoesNotContain,
                       'package clang.request build', ['-chromium_src_ref']),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'rust_success',
      api.platform.name('linux'),
      api.buildbucket.ci_build(
          project='chromium',
          bucket='ci',
          builder='trusted-packaging-linux-rust',
          git_ref='refs/branch-heads/5400',
          revision='b' * 40,
      ),
      gen_props(InputProperties.RUST),
      api.post_process(post_process.StatusSuccess),
      api.post_process(post_process.MustRun, 'package rust.request build'),
      api.post_process(post_process.StepCommandContains,
                       'package rust.request build', [
                           '-chromium_src_commit',
                           'b' * 40,
                       ]),
      api.post_process(post_process.StepCommandDoesNotContain,
                       'package rust.request build', ['-chromium_src_ref']),
      api.post_process(post_process.StepCommandDoesNotContain,
                       'package rust.request build', ['--rust']),
      api.post_process(post_process.StepCommandDoesNotContain,
                       'package rust.request build', ['--clang']),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'no_gitiles_commit',
      api.platform.name('linux'),
      api.buildbucket.ci_build(
          project='chromium',
          bucket='ci',
          builder='trusted-packaging-linux-clang',
          git_ref='',
          revision='',
      ),
      gen_props(InputProperties.CLANG),
      api.post_process(post_process.StatusSuccess),
      api.post_process(post_process.MustRun, 'package clang.request build'),
      api.post_process(post_process.StepCommandDoesNotContain,
                       'package clang.request build', [
                           '-chromium_src_commit',
                           '-chromium_src_ref',
                       ]),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'try_job_success',
      api.platform.name('linux'),
      api.buildbucket.try_build(
          project='chromium',
          bucket='try',
          builder='trusted-packaging-linux-clang',
          git_repo='https://chromium.googlesource.com/chromium/src',
          change_number=123456,
          patch_set=16,
      ),
      gen_props(InputProperties.CLANG),
      api.post_process(post_process.StatusSuccess),
      api.post_process(post_process.MustRun, 'package clang.request build'),
      api.post_process(post_process.StepEnvContains,
                       'package clang.request build', {
                           'GERRIT_CHANGE_ID': '123456',
                           'GERRIT_PATCHSET_ID': '16',
                       }),
      api.post_process(post_process.StepCommandDoesNotContain,
                       'package clang.request build', ['-chromium_src_commit']),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'unknown_toolchain',
      api.platform.name('linux'),
      api.buildbucket.ci_build(
          project='chromium',
          bucket='ci',
          builder='trusted-packaging-linux-clang',
      ),
      gen_props(InputProperties.UNKNOWN),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'clang_with_tbi_properties',
      api.platform.name('linux'),
      api.buildbucket.ci_build(
          project='chromium',
          bucket='ci',
          builder='trusted-packaging-linux-clang',
          git_ref='refs/heads/main',
          revision='a' * 40,
      ),
      gen_props(
          InputProperties.CLANG,
          trusted_build_instance='fake-instance',
          trusted_build_instance_pool='fake-pool',
          trusted_build_instance_env='fake-env',
          trusted_build_instance_project='fake-project',
          config_path='fake/config/path/linux.textproto'),
      api.post_process(post_process.StatusSuccess),
      api.post_process(post_process.MustRun, 'package clang.request build'),
      api.post_process(post_process.StepCommandContains,
                       'package clang.request build', ['--clang']),
      api.post_process(post_process.StepCommandContains,
                       'package clang.request build', [
                           '--instance', 'fake-instance', '--pool', 'fake-pool',
                           '--env', 'fake-env', '--project', 'fake-project'
                       ]),
      api.post_process(post_process.StepCommandContains,
                       'package clang.request build', [
                           '--build_prefix',
                           'bb-8945511751514863184',
                       ]),
      api.post_process(post_process.StepCommandContains,
                       'package clang.request build', [
                           '-chromium_src_commit',
                           'a' * 40,
                       ]),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'clang_default_build_prefix',
      api.platform.name('linux'),
      api.buildbucket.ci_build(
          project='chromium',
          bucket='ci',
          builder='trusted-packaging-linux-clang',
          git_ref='refs/heads/main',
          revision='a' * 40,
          build_id=123456789,
      ),
      gen_props(InputProperties.CLANG),
      api.post_process(post_process.StatusSuccess),
      api.post_process(post_process.MustRun, 'package clang.request build'),
      api.post_process(post_process.StepCommandContains,
                       'package clang.request build', [
                           '--build_prefix',
                           'bb-123456789',
                       ]),
      api.post_process(post_process.DropExpectation),
  )
