# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import v8
from RECIPE_MODULES.recipe_engine import file, step


@dataclass
class DEPS(RecipeScriptApi):
  file: file.API
  step: step.API
  v8: v8.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  file: file.TEST_API
  v8: v8.TEST_API


GS_URL = 'gs://chromium-v8/coverage'
PUB_URL = 'https://storage.googleapis.com/chromium-v8/coverage'


def RunSteps(api: DEPS):
  api.v8.apply_bot_config(
    api.v8.get_bot_config(
      binary_size_tracking=None,
      clusterfuzz_archive=None,
      coverage='llvm',
      enable_swarming=False,
      target_arch='intel',
      target_platform='linux',
      track_build_dependencies=False,
      triggers=[],
      triggers_proxy=False,
    )
  )
  update_result = api.v8.checkout()
  source_dir = update_result.source_root.path
  build_dir = api.v8.build_dir(source_dir)
  with api.v8.maybe_clang_coverage(source_dir, build_dir):
    api.step('run tests', cmd=['run-some-tests.py'])
  return api.v8.recipe_result


def StepLinkEquals(check, step_odict, step, link_name, link_value):
  check(
    f'link {link_name} for step {step} is {link_value}',
    (link_name, link_value) in step_odict[step].links.items(),
  )


def GenTests(api: TEST_DEPS):
  def step_command_contains_multiple(step, *parts):
    checks = [
      api.post_process(post_process.StepCommandContains, step, part)
      for part in parts
    ]
    return sum(checks, api.empty_test_data())

  def test(
    builder_group, builder_name, target_cpu, is_debug, gs_link, public_link
  ):
    return (
      api.v8.test(builder_group, builder_name)
      + api.step_data(
        'Code coverage.read build config',
        api.file.read_json(
          {'DEBUG_defined': is_debug, 'target_cpu': target_cpu}
        ),
      )
      + api.post_process(
        post_process.StepEnvContains,
        'run tests',
        {'LLVM_PROFILE_FILE': '[CLEANUP]/profraw/default-%9m.profraw'},
      )
      + api.post_process(
        post_process.StepCommandContains,
        'Code coverage.List profraw files',
        '[CLEANUP]/profraw',
      )
      + api.override_step_data(
        'Code coverage.List profraw files',
        api.file.listdir(['default-0.profraw', 'default-1.profraw']),
      )
      + api.post_process(
        post_process.StepCommandRE,
        'Code coverage.Merge profiles',
        [
          '.+/llvm-profdata',
          'merge',
          '-o',
          '.+/total.profdata',
          '--sparse',
          '.+/default-0.profraw',
          '.+/default-1.profraw',
        ],
      )
      + step_command_contains_multiple(
        'Code coverage.Create report',
        '-compilation-dir=[CACHE]/builder/v8/out/build',
        '-output-dir=[CLEANUP]/report',
        '-instr-profile=[CLEANUP]/profdata/total.profdata',
        '[CACHE]/builder/v8/out/build/d8',
      )
      + api.post_process(
        post_process.StepCommandContains,
        'Code coverage.gsutil coverage report',
        gs_link,
      )
      + api.post_process(
        StepLinkEquals,
        'Code coverage',
        'report',
        public_link,
      )
      + api.post_process(
        post_process.SummaryMarkdown,
        f'[Report]({public_link})',
      )
      + api.post_process(post_process.DropExpectation)
    )

  # Test a CI builder with 64 bits and release mode.
  hsh = 5 * 'deadbeef'
  yield test(
    builder_group='client.v8',
    builder_name='V8 Foobar',
    target_cpu='x64',
    is_debug=False,
    gs_link=f'{GS_URL}/ci/linux64_rel/{hsh}',
    public_link=f'{PUB_URL}/ci/linux64_rel/{hsh}/index.html',
  )

  # Test a trybot with 32 bits and debug mode.
  yield test(
    builder_group='tryserver.v8',
    builder_name='v8_foobar',
    target_cpu='x86',
    is_debug=True,
    gs_link=f'{GS_URL}/try/linux32_dbg/456789/12/1337000001',
    public_link=f'{PUB_URL}/try/linux32_dbg/456789/12/1337000001/index.html',
  )
