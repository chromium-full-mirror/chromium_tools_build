# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process
from recipe_engine.engine_types import freeze

from PB.recipes.build.chromium_toolchain.package import InputProperties
from RECIPE_MODULES.build import chromium_types

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import chromium, chromium_checkout
from RECIPE_MODULES.depot_tools import depot_tools, gsutil, osx_sdk
from RECIPE_MODULES.recipe_engine import (
  buildbucket,
  context,
  platform,
  properties,
  runtime,
  step,
)


@dataclass
class DEPS(RecipeScriptApi):
  buildbucket: buildbucket.API
  chromium: chromium.API
  chromium_checkout: chromium_checkout.API
  context: context.API
  depot_tools: depot_tools.API
  gsutil: gsutil.API
  osx_sdk: osx_sdk.API
  platform: platform.API
  properties: properties.API
  runtime: runtime.API
  step: step.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  chromium: chromium.TEST_API
  platform: platform.TEST_API


PROPERTIES = InputProperties

BUILDERS = {
  'tryserver.chromium.linux': {
    'builders': {
      'linux_upload_rust': chromium_types.BuilderSpec.create(
        chromium_config_kwargs={
          'BUILD_CONFIG': 'Release',
          'TARGET_PLATFORM': 'linux',
          'TARGET_BITS': 64,
        },
      ),
    },
  },
  'tryserver.chromium.mac': {
    'builders': {
      'mac_upload_rust': chromium_types.BuilderSpec.create(
        chromium_config_kwargs={
          'BUILD_CONFIG': 'Release',
          'TARGET_PLATFORM': 'mac',
          'TARGET_BITS': 64,
        },
      ),
      'mac_upload_rust_arm': chromium_types.BuilderSpec.create(
        chromium_config_kwargs={
          'BUILD_CONFIG': 'Release',
          'TARGET_PLATFORM': 'mac',
          'TARGET_BITS': 64,
        },
      ),
    },
  },
  'tryserver.chromium.win': {
    'builders': {
      'win_upload_rust': chromium_types.BuilderSpec.create(
        chromium_config_kwargs={
          'BUILD_CONFIG': 'Release',
          'TARGET_PLATFORM': 'win',
          'TARGET_BITS': 32,
        },
      ),
    },
  },
}

# Copy bot config for toolchain packagers.
BUILDERS['official.toolchain'] = {
  'builders': {
    'toolchain-packager-linux': BUILDERS['tryserver.chromium.linux'][
      'builders'
    ]['linux_upload_rust'],
    'toolchain-packager-mac': BUILDERS['tryserver.chromium.mac']['builders'][
      'mac_upload_rust'
    ],
    'toolchain-packager-mac-arm': BUILDERS['tryserver.chromium.mac'][
      'builders'
    ]['mac_upload_rust_arm'],
    'toolchain-packager-windows': BUILDERS['tryserver.chromium.win'][
      'builders'
    ]['win_upload_rust'],
  },
}

BUILDERS = freeze(BUILDERS)

ARM_MAC_BUILDERS = (
  'mac_upload_rust_arm',
  'toolchain-packager-mac-arm',
)


def RunSteps(api: DEPS, properties):
  _, bot_config = api.chromium.configure_bot(BUILDERS)

  update_result = api.chromium_checkout.ensure_checkout(
    clobber=bot_config.clobber
  )
  source_dir = update_result.source_root.path

  api.step(
    'update win toolchain',
    ['python3', source_dir.joinpath('build', 'vs_toolchain.py'), 'update'],
  )

  with api.osx_sdk('ios'):
    with api.depot_tools.on_path():
      args = ['--upload']
      # TODO: specify --revision as package_clang.py does.
      api.step(
        'package rust',
        ['python3', source_dir.joinpath('tools', 'rust', 'package_rust.py')]
        + args,
      )


def GenTests(api: TEST_DEPS):
  yield api.test(
    'mac',
    api.platform.name('mac'),
    api.chromium.try_build(
      builder_group='tryserver.chromium.mac', builder='mac_upload_rust_arm'
    ),
    api.post_process(post_process.MustRun, 'install xcode'),
    api.post_process(post_process.MustRun, 'select XCode'),
    api.post_process(post_process.StatusSuccess),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'linux',
    api.platform.name('linux'),
    api.chromium.try_build(
      builder_group='tryserver.chromium.linux', builder='linux_upload_rust'
    ),
    api.post_process(post_process.StatusSuccess),
    api.post_process(post_process.DropExpectation),
  )
