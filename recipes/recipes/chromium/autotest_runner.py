# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Triggers building and running tests for the Autotest script"""

from PB.recipes.build.chromium.autotest_runner import (
    InputProperties,
    EnvProperties,
    AutotestInvocation,
)
from PB.recipe_engine import result as result_pb
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb

from recipe_engine.recipe_api import RecipeApi
from recipe_engine.recipe_test_api import RecipeTestApi
from recipe_engine.post_process import StepCommandRE, DropExpectation

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import chromium, chromium_checkout, gn
from RECIPE_MODULES.depot_tools import depot_tools, gclient, osx_sdk
from RECIPE_MODULES.recipe_engine import (
    context,
    file,
    path,
    platform,
    properties,
    step,
)


@dataclass
class DEPS(RecipeScriptApi):
  chromium: chromium.API
  chromium_checkout: chromium_checkout.API
  context: context.API
  depot_tools: depot_tools.API
  file: file.API
  gclient: gclient.API
  gn: gn.API
  osx_sdk: osx_sdk.API
  path: path.API
  platform: platform.API
  properties: properties.API
  step: step.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  context: context.TEST_API
  platform: platform.TEST_API
  properties: properties.TEST_API
  step: step.TEST_API

PROPERTIES = InputProperties


def run_autotest(
    api: RecipeApi,
    base_cmd: list[str],
    test: AutotestInvocation,
):
  cmd = base_cmd + list(test.args)
  step_name = test.step_name or f"autotest: {' '.join(test.args)}"
  api.step(step_name, cmd)


def run_tests(api: RecipeApi, properties: InputProperties, src_dir, build_dir):
  with api.depot_tools.on_path(), api.context(
      cwd=src_dir, env={
          'LANG': 'en_US.UTF-8',
          'PYTHONIOENCODING': 'utf-8'
      }):
    autotest_path = src_dir / "tools" / "autotest.py"
    base_cmd = [autotest_path, "-C", build_dir, "--run-all"]
    for test in properties.tests:
      run_autotest(api, base_cmd, test)

def RunSteps(api: RecipeApi, properties: InputProperties):
  if not properties.tests:
    return result_pb.RawResult(
        status=common_pb.SUCCESS,
        summary_markdown="No tests provided in InputProperties. Exiting early.",
    )

  ctx = api.osx_sdk('mac') if api.platform.is_mac else api.context()
  with ctx:
    api.gclient.set_config("chromium")
    api.chromium.set_config("chromium")
    api.chromium_checkout.ensure_checkout()
    src_dir = api.chromium_checkout.source_dir

    build_dir = api.chromium.default_build_dir(src_dir)
    api.file.ensure_directory("ensure out/Default", build_dir)

    api.chromium.runhooks(src_dir, build_dir)
    api.chromium.run_gn(src_dir, build_dir)

    run_tests(api, properties, src_dir, build_dir)


def GenTests(api: RecipeTestApi):
  yield api.test(
      "happy_path",
      api.properties(
          InputProperties(tests=[
              AutotestInvocation(
                  step_name="run base/strings",
                  args=["base/strings"],
              ),
              AutotestInvocation(
                  step_name="run pickle_unittest",
                  args=["base/pickle_unittest.cc"],
              ),
          ])),
      api.post_process(
          StepCommandRE,
          "run base/strings",
          [
              ".*tools/autotest.py",
              "-C",
              ".*",
              "--run-all",
              "base/strings",
          ],
      ),
      api.post_process(
          StepCommandRE,
          "run pickle_unittest",
          [
              ".*tools/autotest.py",
              "-C",
              ".*",
              "--run-all",
              "base/pickle_unittest.cc",
          ],
      ),
      api.post_process(DropExpectation),
  )

  yield api.test(
      "mac_happy_path",
      api.platform('mac', 64),
      api.properties(
          InputProperties(tests=[
              AutotestInvocation(
                  step_name="run base/strings",
                  args=["base/strings"],
              ),
          ])),
      api.post_process(DropExpectation),
  )

  yield api.test(
      "empty_inputs",
      api.properties(InputProperties(tests=[])),
      api.expect_status("SUCCESS"),
      api.post_process(DropExpectation),
  )

  yield api.test(
      "implicit_step_name",
      api.properties(
          InputProperties(tests=[
              AutotestInvocation(args=["StringUtilTest.IsStringUTF8"]),
          ])),
      api.post_process(
          StepCommandRE,
          "autotest: StringUtilTest.IsStringUTF8",
          [
              ".*tools/autotest.py",
              "-C",
              ".*",
              "--run-all",
              "StringUtilTest.IsStringUTF8",
          ],
      ),
      api.post_process(DropExpectation),
  )
