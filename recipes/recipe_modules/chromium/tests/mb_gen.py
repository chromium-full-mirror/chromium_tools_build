# Copyright 2018 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process
import textwrap

from RECIPE_MODULES.build import chromium, chromium_types

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import chromium as chromium_module
from RECIPE_MODULES.recipe_engine import (
    json,
    path,
    platform,
    properties,
)


@dataclass
class DEPS(RecipeScriptApi):
  chromium: chromium_module.API
  json: json.API
  path: path.API
  platform: platform.API
  properties: properties.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  chromium: chromium_module.TEST_API
  json: json.TEST_API
  platform: platform.TEST_API
  properties: properties.TEST_API


@chromium.config.config_ctx()
def mb_overrides(c):
  c.project_generator.config_path = 'override/mb_config.pyl'
  c.project_generator.isolate_map_paths = ['override/gn_isolate_map.pyl']


@chromium.config.config_ctx()
def mb_one_isolate_map(c):
  c.project_generator.isolate_map_paths = ['foo/gn_isolate_map.pyl']


@chromium.config.config_ctx()
def mb_two_isolate_maps(c):
  c.project_generator.isolate_map_paths = [
      'foo/gn_isolate_map.pyl', 'bar/gn_isolate_map.pyl'
  ]


def RunSteps(api: DEPS):
  api.chromium.set_config(
      api.properties.get('chromium_config', 'chromium'),
      TARGET_PLATFORM=api.properties.get('target_platform', 'linux'),
      TARGET_CROS_BOARDS=api.properties.get('target_cros_boards'))

  for config in api.properties.get('chromium_apply_config', []):
    api.chromium.apply_config(config)

  source_dir = api.path.cache_dir / 'builder/src'
  build_dir = source_dir / 'out/Release'

  api.chromium.mb_gen(
      source_dir,
      build_dir,
      chromium_types.BuilderId.create_for_group('test-group', 'test builder'),
      phase='test_phase',
      isolated_targets=['base_unittests_run'],
      android_version_code=3,
      android_version_name='example',
      write_ide_json=api.properties.get('mb_write_ide_json', False),
  )


def GenTests(api: TEST_DEPS):

  yield api.test(
      'basic',
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'cros_boards',
      api.properties(
          target_platform='chromeos', target_cros_boards='x86-generic'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'mac',
      api.platform('mac', 64),
      api.properties(target_platform='mac'),
      api.post_process(post_process.StepEnvContains, 'lookup GN args',
                       {'FORCE_MAC_TOOLCHAIN': '1'}),
      api.post_process(post_process.StepEnvContains, 'generate_build_files',
                       {'FORCE_MAC_TOOLCHAIN': '1'}),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'mb_overrides',
      api.properties(chromium_apply_config=['mb_overrides']),
      api.post_process(post_process.StepCommandContains, 'generate_build_files',
                       [
                           '--config-file',
                           '[CACHE]/builder/src/override/mb_config.pyl',
                       ]),
      api.post_process(post_process.StepCommandContains, 'generate_build_files',
                       [
                           '--isolate-map-file',
                           '[CACHE]/builder/src/override/gn_isolate_map.pyl',
                       ]),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'mac_failure',
      api.platform('mac', 64),
      api.properties(target_platform='mac'),
      api.step_data(
          'generate_build_files',
          api.json.output({'output': 'ERROR at line 5: missing )'},
                          name="failure_summary"),
          retcode=1),
      api.expect_status('FAILURE'),
      api.post_process(
          post_process.SummaryMarkdown,
          textwrap.dedent('''
          #### Step _generate_build_files_ failed. Error logs are shown below:
          ```
          ERROR at line 5: missing )
          ```
          #### More information can be found in the stdout.
      ''').strip()),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'failure_with_tag',
      api.step_data('generate_build_files', retcode=1),
      api.expect_status('FAILURE'),
      api.post_process(
          post_process.TagEquals,
          'generate_build_files',
          'luci-bisection.is_bisectable',
          'true',
      ),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'win_failure',
      api.platform('win', 64),
      api.properties(target_platform='win'),
      api.step_data(
          'generate_build_files',
          api.json.output({'output': 'ERROR at line 5: missing )'},
                          name="failure_summary"),
          retcode=1),
      api.expect_status('FAILURE'),
      api.post_process(
          post_process.SummaryMarkdown,
          textwrap.dedent('''
          #### Step _generate_build_files_ failed. Error logs are shown below:
          ```
          ERROR at line 5: missing )
          ```
          #### More information can be found in the stdout.
      ''').strip()),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'mb_long_error',
      api.chromium.change_char_size_limit(350),
      api.chromium.change_line_limit(150),
      api.step_data(
          'generate_build_files',
          api.json.output(
              {
                  'output':
                      textwrap.dedent("""
                ERROR at //view_unittest.cc:38:11: Can't include header here.
                #include "ui/compositor_extra/shadow.h"
                :          ^---------------------------
                ERROR at //view_unittest.cc:38:12: Can't include header here.
                #include "ui/compositor_extra/shadow2.h"
                ERROR at //view_unittest.cc:38:13: Can't include header here.
                #include "ui/compositor_extra/shadow3.h"
                ERROR at //view_unittest.cc:38:14: Can't include header here.
                #include "ui/compositor_extra/shadow4.h"
              """).strip()
              },
              name="failure_summary"),
          retcode=1),
      api.expect_status('FAILURE'),
      api.post_process(
          post_process.SummaryMarkdown,
          textwrap.dedent("""
          #### Step _generate_build_files_ failed. Error logs are shown below:
          ```
          ERROR at //view_unittest.cc:38:11: Can't include header here.
          #include "ui/compositor_extra/shadow.h"
          :          ^---------------------------
          ERROR at //view_unittest.cc:38:12: Can't include header here.
          #include "ui/compositor_extra/shadow2.h"
          ERROR at //view_unittest.cc:38:13: Can't include header here.
          #include "ui/compositor_extra/shadow3.h"
          ```
          ##### ...The message was too long...
          #### More information can be found in the stdout.
        """).strip()),
      api.post_process(post_process.DropExpectation),
  )

  def _StepCommandNotContains(check, step_odict, step, arg):
    check(arg not in step_odict[step].cmd)

  yield api.test(
      'mb_no_luci_auth',
      api.properties(chromium_apply_config=['mb', 'mb_no_luci_auth']),
      api.post_process(_StepCommandNotContains, 'generate_build_files',
                       '--luci-auth'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'mb_one_isolate_map_allow_dup',
      api.properties(chromium_apply_config=[
          'mb_one_isolate_map', 'mb_allow_dup_isolate_entry'
      ]),
      api.post_process(_StepCommandNotContains, 'generate_build_files',
                       '--allow-dup-isolate-entry'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'mb_two_isolate_maps_allow_dup',
      api.properties(chromium_apply_config=[
          'mb_two_isolate_maps', 'mb_allow_dup_isolate_entry'
      ]),
      api.post_process(post_process.StepCommandContains, 'generate_build_files',
                       '--allow-dup-isolate-entry'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'mb_write_ide_json',
      api.properties(chromium_apply_config=['mb'], mb_write_ide_json=True),
      api.post_process(post_process.StepCommandContains, 'generate_build_files',
                       ['--write-ide-json']),
      api.post_process(post_process.DropExpectation),
  )
