# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process
from recipe_engine.post_process import DropExpectation
from PB.recipe_modules.build.code_coverage.tests.api.get_chromium_fuzz_coverage import (
    InputProperties,)
from RECIPE_MODULES.build.chromium_tests import steps
from RECIPE_MODULES.build.code_coverage import constants

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import code_coverage
from RECIPE_MODULES.recipe_engine import path, properties, raw_io


@dataclass
class DEPS(RecipeScriptApi):
  code_coverage: code_coverage.API
  path: path.API
  properties: properties.API
  raw_io: raw_io.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  properties: properties.TEST_API
  raw_io: raw_io.TEST_API
PROPERTIES = InputProperties


def RunSteps(api: DEPS, properties: InputProperties):
  overall = properties.overall if properties.HasField('overall') else True
  api.code_coverage.get_chromium_fuzz_coverage(
      api.path.start_dir / 'checkout',
      api.path.start_dir / 'build',
      '',
      set(properties.targets),
      overall,
  )


def GenTests(api: TEST_DEPS):
  yield api.test(
      'overall',
      api.properties(targets=['blink_unittest_fake_fuzzer', 'fake_fuzzer']),
      api.step_data(
          'process fuzz coverage (overall).calculate gn refs',
          stdout=api.raw_io.output_text('\n'.join([
              '//third_party/blink/renderer:blink_unittest_fake_fuzzer',
              '//base:fake_fuzzer',
          ]))),
      api.step_data(
          'process fuzz coverage (overall).gn desc (deps --type=executable)',
          stdout=api.raw_io.output_text('\n'.join([
              'centipede',
              'blink_unittest',
              'build-time/binary',
              '',
          ]))),
      api.step_data(
          'process fuzz coverage (overall).gn desc (runtime_deps)',
          stdout=api.raw_io.output_text('\n'.join([
              'not/a/binary.dat',
              './blink_unittest_fake_fuzzer',
              './centipede',
              './blink_unittest',
              '',
          ]))),
      api.post_process(post_process.MustRun,
                       'process fuzz coverage (overall).chmod llvm file'),
      api.post_process(
          post_process.MustRun, 'process fuzz coverage (overall).'
          'ensure metadata dir for clang coverage'),
      api.post_process(
          post_process.StepCommandContains,
          'process fuzz coverage (overall).generate coverage metadata', [
              '--exclusion-pattern',
              constants.EXCLUDED_FILE_REGEX,
              '--binaries',
              '[START_DIR]/build/blink_unittest',
              '[START_DIR]/build/blink_unittest_fake_fuzzer',
              '[START_DIR]/build/centipede',
              '[START_DIR]/build/fake_fuzzer',
          ]),
      api.post_process(
          post_process.MustRun,
          'process fuzz coverage (overall).gsutil Upload coverage artifacts'),
      api.post_process(DropExpectation))

  yield api.test(
      'per-target', api.properties(targets=['fake_fuzzer'], overall=False),
      api.post_process(post_process.MustRun,
                       'process fuzz coverage (fake_fuzzer).chmod llvm file'),
      api.post_process(
          post_process.MustRun, 'process fuzz coverage (fake_fuzzer).'
          'ensure metadata dir for clang coverage'),
      api.post_process(
          post_process.StepCommandContains,
          'process fuzz coverage (fake_fuzzer).generate coverage metadata', [
              '--exclusion-pattern',
              constants.EXCLUDED_FILE_REGEX,
              '--binaries',
              '[START_DIR]/build/fake_fuzzer',
          ]),
      api.post_process(
          post_process.MustRun, 'process fuzz coverage (fake_fuzzer).'
          'gsutil Upload coverage artifacts'),
      api.post_process(DropExpectation))

  yield api.test(
      'no-targets', api.properties(targets=[]),
      api.post_process(post_process.MustRun,
                       'no fuzz targets to generate coverage for'),
      api.post_process(DropExpectation))
