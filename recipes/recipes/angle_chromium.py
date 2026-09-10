# Copyright 2020 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from RECIPE_MODULES.build import chromium_tests_builder_config as ctbc

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import (
  chromium,
  chromium_android,
  chromium_swarming,
  chromium_tests,
  chromium_tests_builder_config,
  isolate,
  test_utils,
)
from RECIPE_MODULES.depot_tools import (
  bot_update,
  gclient,
  gitiles,
  gsutil,
)
from RECIPE_MODULES.recipe_engine import (
  buildbucket,
  commit_position,
  file,
  json,
  legacy_annotation,
  path,
  platform,
  properties,
  raw_io,
  runtime,
  step,
)


@dataclass
class DEPS(RecipeScriptApi):
  bot_update: bot_update.API
  buildbucket: buildbucket.API
  chromium: chromium.API
  chromium_android: chromium_android.API
  chromium_swarming: chromium_swarming.API
  chromium_tests: chromium_tests.API
  chromium_tests_builder_config: chromium_tests_builder_config.API
  commit_position: commit_position.API
  file: file.API
  gclient: gclient.API
  gitiles: gitiles.API
  gsutil: gsutil.API
  isolate: isolate.API
  json: json.API
  legacy_annotation: legacy_annotation.API
  path: path.API
  platform: platform.API
  properties: properties.API
  raw_io: raw_io.API
  runtime: runtime.API
  step: step.API
  test_utils: test_utils.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  chromium_tests: chromium_tests.TEST_API
  chromium_tests_builder_config: chromium_tests_builder_config.TEST_API
  file: file.TEST_API
  gclient: gclient.TEST_API
  gitiles: gitiles.TEST_API
  path: path.TEST_API
  raw_io: raw_io.TEST_API


def get_component_revision_from_deps(
  api: DEPS, component, project, repository_url, branch
):
  deps = api.gitiles.download_file(
    repository_url, 'DEPS', branch=branch, step_name='fetch %s DEPS' % project
  )
  deps_file = api.path.mkdtemp(project) / 'DEPS'
  api.file.write_text('snapshot %s DEPS' % project, deps_file, deps)
  revision = api.gclient(
    'get %s_revision' % component,
    ['getdep', '--var=%s_revision' % component, '--deps-file=%s' % deps_file],
    stdout=api.raw_io.output_text(add_output_log=True),
  ).stdout.strip()
  return revision


def RunSteps(api: DEPS):
  builder_id, builder_config = (
    api.chromium_tests_builder_config.lookup_builder()
  )
  is_angle_tot = 'angle_top_of_tree' in builder_config.gclient_apply_config
  if is_angle_tot:
    angle_revision = 'refs/heads/main'
  else:
    angle_revision = get_component_revision_from_deps(
      api,
      'angle',
      'Chromium',
      'https://chromium.googlesource.com/chromium/src',
      'refs/heads/main',
    )

  chromium_revision = get_component_revision_from_deps(
    api,
    'chromium',
    'ANGLE',
    'https://chromium.googlesource.com/angle/angle',
    angle_revision,
  )

  with api.chromium.chromium_layout():
    build_result, _ = api.chromium_tests.main_waterfall_steps(
      builder_id, builder_config, root_solution_revision=chromium_revision
    )
    return build_result


def GenTests(api: TEST_DEPS):
  yield api.test(
    'linux-angle-chromium-intel',
    api.chromium_tests_builder_config.ci_build(
      builder_group='chromium.angle',
      builder='linux-angle-chromium-intel',
      parent_buildername='linux-angle-chromium-builder',
      builder_db=ctbc.BuilderDatabase.create(
        {
          'chromium.angle': {
            'linux-angle-chromium-intel': ctbc.BuilderSpec.create(
              gclient_config='chromium',
              gclient_apply_config=[
                'angle_top_of_tree',
              ],
              chromium_config='chromium',
            ),
          },
        }
      ),
    ),
    api.chromium_tests.read_targets_spec(
      'chromium.angle',
      {
        'linux-angle-chromium-intel': {
          'isolated_scripts': [
            {
              'test': 'telemetry_gpu_integration_test',
              'name': 'webgl_conformance_gl_tests',
            }
          ],
        },
      },
    ),
    api.step_data(
      'fetch ANGLE DEPS',
      api.gitiles.make_encoded_file('DEPS'),
    ),
  )

  yield api.test(
    'linux-swangle-tot-swiftshader-x64',
    api.chromium_tests_builder_config.ci_build(
      builder_group='chromium.swangle',
      builder='linux-swangle-tot-swiftshader-x64',
      builder_db=ctbc.BuilderDatabase.create(
        {
          'chromium.swangle': {
            'linux-swangle-tot-swiftshader-x64': ctbc.BuilderSpec.create(
              gclient_config='chromium',
              chromium_config='chromium',
            ),
          },
        }
      ),
    ),
    api.chromium_tests.read_targets_spec(
      'chromium.swangle',
      {
        'linux-swangle-tot-swiftshader-x64': {
          'gtest_tests': [
            {
              'test': 'angle_end2end_tests',
            }
          ],
        },
      },
    ),
    api.step_data(
      'fetch Chromium DEPS',
      api.gitiles.make_encoded_file('DEPS'),
    ),
    api.step_data(
      'fetch ANGLE DEPS',
      api.gitiles.make_encoded_file('DEPS'),
    ),
  )
