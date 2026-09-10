# Copyright 2015 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import functools

from RECIPE_MODULES.build.chromium_tests_builder_config import (
  builder_db,
  builder_spec,
)
from RECIPE_MODULES.build import chromium_types

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import (
  chromium,
  chromium_checkout,
  chromium_tests,
  chromium_tests_builder_config,
  gn,
  webrtc,
)
from RECIPE_MODULES.depot_tools import depot_tools
from RECIPE_MODULES.recipe_engine import (
  context,
  platform,
  raw_io,
  step,
)


@dataclass
class DEPS(RecipeScriptApi):
  chromium: chromium.API
  chromium_checkout: chromium_checkout.API
  chromium_tests: chromium_tests.API
  chromium_tests_builder_config: chromium_tests_builder_config.API
  context: context.API
  depot_tools: depot_tools.API
  gn: gn.API
  platform: platform.API
  raw_io: raw_io.API
  step: step.API
  webrtc: webrtc.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  raw_io: raw_io.TEST_API
  webrtc: webrtc.TEST_API


BUILDERS_DB = builder_db.BuilderDatabase.create(
  {
    'client.webrtc': {
      'Linux64 Release (Libfuzzer)': builder_spec.BuilderSpec.create(
        chromium_config='webrtc_default',
        gclient_config='webrtc',
        chromium_config_kwargs={
          'BUILD_CONFIG': 'Release',
          'TARGET_BITS': 64,
        },
      ),
    },
    'tryserver.webrtc': {
      'linux_libfuzzer_rel': builder_spec.BuilderSpec.create(
        chromium_config='webrtc_default',
        gclient_config='webrtc',
        chromium_config_kwargs={
          'BUILD_CONFIG': 'Release',
          'TARGET_BITS': 64,
        },
      ),
    },
  }
)


def RunSteps(api: DEPS):
  builder_id, builder_config = api.chromium_tests_builder_config.lookup_builder(
    builder_db=BUILDERS_DB
  )
  api.chromium_tests.configure_build(builder_config)
  update_result = api.chromium_checkout.ensure_checkout()
  source_dir = update_result.source_root.path
  build_dir = api.chromium.default_build_dir(source_dir)
  api.chromium.runhooks(source_dir, build_dir)

  with api.chromium.guard_compile(build_dir):
    api.webrtc.run_mb(source_dir, build_dir, builder_id)
    with api.context(cwd=source_dir):
      args = [
        '--root=%s' % str(source_dir),
        'refs',
        str(build_dir),
        '--all',
        '--type=executable',
        '--as=output',
        '//test/fuzzers:webrtc_fuzzer_main',
      ]
      script = str(api.depot_tools.gn_py_path)
      cmd = ['vpython3', '-u', script] + args
      step_result = api.step(
        'calculate targets', cmd, stdout=api.raw_io.output_text()
      )

    targets = step_result.stdout.split()
    api.step.active_result.presentation.logs['targets'] = targets
    return api.chromium.compile(source_dir, build_dir, targets=targets)


def GenTests(api: TEST_DEPS):
  builders_db = BUILDERS_DB
  generate_builder = functools.partial(api.webrtc.generate_builder, builders_db)

  for builder_id in builders_db:
    yield (
      generate_builder(builder_id)
      + api.step_data(
        'calculate targets',
        stdout=api.raw_io.output_text('target1 target2 target3'),
      )
    )

  builder_id = chromium_types.BuilderId.create_for_group(
    'client.webrtc', 'Linux64 Release (Libfuzzer)'
  )
  yield generate_builder(
    builder_id, suffix='_compile_failure', fail_compile=True
  )
