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
    chromium_checkout,
    chromium_swarming,
    chromium_tests,
    chromium_tests_builder_config,
    filter as filter_module,
    isolate,
    test_utils,
)
from RECIPE_MODULES.depot_tools import (
    bot_update,
    gclient,
    gerrit,
    git,
    gitiles,
    tryserver,
)
from RECIPE_MODULES.recipe_engine import (
    buildbucket,
    commit_position,
    context,
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
  chromium_checkout: chromium_checkout.API
  chromium_swarming: chromium_swarming.API
  chromium_tests: chromium_tests.API
  chromium_tests_builder_config: chromium_tests_builder_config.API
  commit_position: commit_position.API
  context: context.API
  file: file.API
  filter: filter_module.API
  gclient: gclient.API
  gerrit: gerrit.API
  git: git.API
  gitiles: gitiles.API
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
  tryserver: tryserver.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  chromium_tests: chromium_tests.TEST_API
  chromium_tests_builder_config: chromium_tests_builder_config.TEST_API
  context: context.TEST_API
  file: file.TEST_API
  gclient: gclient.TEST_API
  git: git.TEST_API
  gitiles: gitiles.TEST_API
  path: path.TEST_API
  raw_io: raw_io.TEST_API
  tryserver: tryserver.TEST_API


def get_component_revision_from_deps(api: DEPS, component, project,
                                     repository_url, branch):
  deps = api.gitiles.download_file(
      repository_url,
      'DEPS',
      branch=branch,
      step_name='fetch %s DEPS' % project)
  deps_file = api.path.mkdtemp(project) / 'DEPS'
  api.file.write_text('snapshot %s DEPS' % project, deps_file, deps)
  revision = api.gclient(
      'get %s_revision' % component,
      ['getdep',
       '--var=%s_revision' % component,
       '--deps-file=%s' % deps_file],
      stdout=api.raw_io.output_text(add_output_log=True),
  ).stdout.strip()
  return revision


def get_chromium_revision_from_angle_cl(api: DEPS):
  angle_revision = api.tryserver.gerrit_change_fetch_ref
  angle_dir = api.path.mkdtemp('angle')
  with api.context(cwd=angle_dir):
    api.git('init', name='init ANGLE repo')
    api.git(
        'fetch',
        'https://chromium.googlesource.com/angle/angle',
        '%s:change' % angle_revision,
        'main:base',
        name='fetch ANGLE CL')
    api.git('checkout', 'change', name='checkout ANGLE CL')
    api.git('rebase', 'base', '-v', name='rebase ANGLE CL')

  angle_deps_file = angle_dir / 'DEPS'
  chromium_revision = api.gclient(
      'get chromium_revision',
      ['getdep', '--var=chromium_revision',
       '--deps-file=%s' % angle_deps_file],
      stdout=api.raw_io.output_text(add_output_log=True),
  ).stdout.strip()
  return chromium_revision


def RunSteps(api: DEPS):
  is_angle_cl = (
      api.tryserver.gerrit_change.host == 'chromium-review.googlesource.com'
  ) and (api.tryserver.gerrit_change.project == 'angle/angle')
  if is_angle_cl:
    chromium_revision = get_chromium_revision_from_angle_cl(api)
  else:
    _, builder_config = api.chromium_tests_builder_config.lookup_builder()
    is_angle_tot = 'angle_top_of_tree' in builder_config.gclient_apply_config
    if is_angle_tot:
      angle_revision = 'refs/heads/main'
    else:
      angle_revision = get_component_revision_from_deps(
          api, 'angle', 'Chromium',
          'https://chromium.googlesource.com/chromium/src', 'refs/heads/main')
    chromium_revision = get_component_revision_from_deps(
        api, 'chromium', 'ANGLE',
        'https://chromium.googlesource.com/angle/angle', angle_revision)

  builder_id, builder_config = (
      api.chromium_tests_builder_config.lookup_builder())
  with api.chromium.chromium_layout():
    return api.chromium_tests.trybot_steps(
        builder_id, builder_config, root_solution_revision=chromium_revision)


def GenTests(api: TEST_DEPS):
  yield api.test(
      'linux-angle-chromium-try-chromium',
      api.chromium_tests_builder_config.try_build(
          builder_group='tryserver.chromium.angle',
          builder='linux-angle-chromium-try',
          builder_db=ctbc.BuilderDatabase.create({
              'chromium.angle': {
                  'linux-angle-chromium-builder':
                      ctbc.BuilderSpec.create(
                          gclient_config='chromium',
                          gclient_apply_config=[
                              'angle_top_of_tree',
                          ],
                          chromium_config='chromium',
                      ),
                  'linux-angle-chromium-intel':
                      ctbc.BuilderSpec.create(
                          execution_mode=ctbc.TEST,
                          parent_buildername='linux-angle-chromium-builder',
                          gclient_config='chromium',
                          gclient_apply_config=[
                              'angle_top_of_tree',
                          ],
                          chromium_config='chromium',
                      ),
              },
          }),
          try_db=ctbc.TryDatabase.create({
              'tryserver.chromium.angle': {
                  'linux-angle-chromium-try':
                      ctbc.TrySpec.create(mirrors=[
                          ctbc.TryMirror.create(
                              builder_group='chromium.angle',
                              buildername='linux-angle-chromium-builder',
                              tester='linux-angle-chromium-intel',
                          ),
                      ]),
              },
          }),
      ),
      api.chromium_tests.read_targets_spec(
          'chromium.angle',
          {
              'linux-angle-chromium-intel': {
                  'isolated_scripts': [{
                      'test': 'telemetry_gpu_integration_test',
                      'name': 'webgl_conformance_gl_tests',
                  }],
              },
          },
      ),
      api.step_data(
          'fetch ANGLE DEPS',
          api.gitiles.make_encoded_file('DEPS'),
      ),
  )

  yield api.test(
      'linux-angle-chromium-try-angle',
      api.chromium_tests_builder_config.try_build(
          git_repo='https://chromium.googlesource.com/angle/angle/',
          builder_group='tryserver.chromium.angle',
          builder='linux-angle-chromium-try',
          builder_db=ctbc.BuilderDatabase.create({
              'chromium.angle': {
                  'linux-angle-chromium-builder':
                      ctbc.BuilderSpec.create(
                          gclient_config='chromium',
                          gclient_apply_config=[
                              'angle_top_of_tree',
                          ],
                          chromium_config='chromium',
                      ),
                  'linux-angle-chromium-intel':
                      ctbc.BuilderSpec.create(
                          execution_mode=ctbc.TEST,
                          parent_buildername='linux-angle-chromium-builder',
                          gclient_config='chromium',
                          gclient_apply_config=[
                              'angle_top_of_tree',
                          ],
                          chromium_config='chromium',
                      ),
              },
          }),
          try_db=ctbc.TryDatabase.create({
              'tryserver.chromium.angle': {
                  'linux-angle-chromium-try':
                      ctbc.TrySpec.create(mirrors=[
                          ctbc.TryMirror.create(
                              builder_group='chromium.angle',
                              buildername='linux-angle-chromium-builder',
                              tester='linux-angle-chromium-intel',
                          ),
                      ]),
              },
          }),
      ),
      api.chromium_tests.read_targets_spec(
          'chromium.angle',
          {
              'linux-angle-chromium-intel': {
                  'isolated_scripts': [{
                      'test': 'telemetry_gpu_integration_test',
                      'name': 'webgl_conformance_gl_tests',
                  }],
              },
          },
      ),
  )

  yield api.test(
      'linux-swangle-try-tot-swiftshader-x64',
      api.chromium_tests_builder_config.try_build(
          git_repo='https://swiftshader.googlesource.com/SwiftShader/',
          builder_group='tryserver.chromium.swangle',
          builder='linux-swangle-try-tot-swiftshader-x64',
          builder_db=ctbc.BuilderDatabase.create({
              'chromium.swangle': {
                  'linux-swangle-tot-swiftshader-x64':
                      ctbc.BuilderSpec.create(
                          gclient_config='chromium',
                          gclient_apply_config=[
                              'swiftshader_top_of_tree',
                          ],
                          chromium_config='chromium',
                      ),
              },
          }),
          try_db=ctbc.TryDatabase.create({
              'tryserver.chromium.swangle': {
                  'linux-swangle-try-tot-swiftshader-x64':
                      ctbc.TrySpec.create(mirrors=[
                          ctbc.TryMirror.create(
                              builder_group='chromium.swangle',
                              buildername='linux-swangle-tot-swiftshader-x64',
                          ),
                      ]),
              },
          }),
      ),
      api.chromium_tests.read_targets_spec(
          'chromium.swangle',
          {
              'linux-swangle-tot-swiftshader-x64': {
                  'gtest_tests': [{
                      'test': 'angle_end2end_tests',
                  }],
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
