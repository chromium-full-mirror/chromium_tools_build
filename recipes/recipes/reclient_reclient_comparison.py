# Copyright 2022 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Recipe to compare builds with different reclient configurations.

"""

from recipe_engine import post_process
from recipe_engine.engine_types import freeze
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb
from RECIPE_MODULES.build import chromium

DEPS = [
    'builder_group',
    'chromium',
    'chromium_checkout',
    'depot_tools/gclient',
    'isolate',
    'recipe_engine/buildbucket',
    'recipe_engine/context',
    'recipe_engine/file',
    'recipe_engine/path',
    'recipe_engine/platform',
    'recipe_engine/properties',
    'recipe_engine/step',
    'reclient',
]

COMPARISON_BUILDERS = freeze({
    'Comparison Linux (reclient vs reclient remote links)': {
        'chromium_config': 'chromium',
        'gclient_config': 'chromium',
        'chromium_apply_config': ['mb',],
        'gclient_apply_config_1': ['reclient_test'],
        'gclient_apply_config_2': ['reclient_test'],
        'platform': 'linux',
        'targets': ['all'],
    },
    'Comparison Linux (reclient vs reclient remote links)(small)': {
        'chromium_config': 'chromium',
        'gclient_config': 'chromium',
        'chromium_apply_config': ['mb',],
        'gclient_apply_config_1': ['reclient_test'],
        'gclient_apply_config_2': ['reclient_test'],
        'platform': 'linux',
        'targets': ['all'],
    },
    'Comparison Linux (reclient)': {
        'chromium_config': 'chromium',
        'gclient_config': 'chromium',
        'chromium_apply_config': ['mb',],
        'gclient_apply_config_1': [],
        'gclient_apply_config_2': ['reclient_test'],
        'platform': 'linux',
        'targets': ['all'],
    },
})


def _configure_chromium_builder(api, recipe_config, build_number):
  api.chromium.set_config(
      recipe_config['chromium_config'],
      **recipe_config.get('chromium_config_kwargs',
                          {'BUILD_CONFIG': 'Release'}))
  api.gclient.set_config(recipe_config['gclient_config'],
                         **recipe_config.get('gclient_config_kwargs', {}))

  for c in recipe_config.get('chromium_apply_config', []):
    api.chromium.apply_config(c)

  for c in recipe_config.get(f'gclient_apply_config_{build_number}', []):
    api.gclient.apply_config(c)

  api.chromium.apply_config('reclient_deps_cache_by_step')

  # Checkout chromium.
  api.chromium_checkout.ensure_checkout()


def _compile(api, recipe_config, build_number):
  # Execute reclient build in '.{build_number}' out directory
  target = f'{api.chromium.c.build_config_fs}.{build_number}'
  build_dir = '//out/%s' % target

  builder_id = chromium.BuilderId.create_for_group(
      api.builder_group.for_current, api.buildbucket.builder_name)
  api.chromium.mb_gen(
      builder_id,
      build_dir=build_dir,
      phase=f'build{build_number}',
      recursive_lookup=True)

  return api.chromium.compile(
      recipe_config['targets'],
      name=f'Build {build_number}',
      use_goma_module=False,
      use_reclient=True,
      target=target)


def _clean_output_dirs(api, out_dirs):
  with api.step.nest('clean_output_dirs'):
    for out_dir in out_dirs:
      api.file.rmtree('rmtree %s' % out_dir, out_dir)


def _sanitize_nonalpha(text):
  return ''.join(c if c.isalnum() else '_' for c in text)


def RunSteps(api):
  buildername = api.buildbucket.builder_name
  recipe_config = COMPARISON_BUILDERS[buildername]

  # Set up a named cache so runhooks doesn't redownload everything on each run.
  solution_path = api.path['cache'].join('builder')
  api.file.ensure_directory('init cache if not exists', solution_path)

  with api.context(cwd=solution_path):
    _configure_chromium_builder(api, recipe_config, 1)

  base_out_dir = str(api.chromium.output_dir).rstrip('\\/')
  out_dirs = [base_out_dir] + [base_out_dir + '.' + ext for ext in '12']

  # Clear output directories for build
  _clean_output_dirs(api, out_dirs)

  api.chromium.ensure_toolchains()
  with api.context(cwd=solution_path):
    api.chromium.runhooks()

  try:
    raw_result = _compile(api, recipe_config, 1)
    if raw_result.status != common_pb.SUCCESS:
      return raw_result

    with api.context(cwd=solution_path):
      _configure_chromium_builder(api, recipe_config, 2)

    _clean_output_dirs(api, out_dirs)

    raw_result = _compile(api, recipe_config, 2)
    if raw_result.status != common_pb.SUCCESS:
      return raw_result
  finally:
    # Always clean output directories after build
    _clean_output_dirs(api, out_dirs)


def GenTests(api):
  builder_group = 'chromium.swarm'
  for buildername in COMPARISON_BUILDERS:
    test_name = 'full_%s_%s' % (_sanitize_nonalpha(builder_group),
                                _sanitize_nonalpha(buildername))
    mac_test_props = {
        'xcode_build_version': '12345',
        'configs': ['mac_toolchain']
    } if COMPARISON_BUILDERS[buildername]['platform'] == 'mac' else {}
    yield api.test(
        test_name,
        api.chromium.ci_build(builder_group=builder_group, builder=buildername),
        api.reclient.properties(),
        api.platform(COMPARISON_BUILDERS[buildername]['platform'], 64),
        api.properties(
            buildername=buildername,
            buildnumber=571,
            configuration='Release',
            **mac_test_props),
        api.post_process(post_process.StatusSuccess),
        api.post_process(post_process.DropExpectation),
    )

  yield api.test(
      'first_build_compile_fail',
      api.chromium.ci_build(
          builder_group=builder_group,
          builder='Comparison Linux (reclient vs reclient remote links)'),
      api.reclient.properties(),
      api.properties(
          buildername='Comparison Linux (reclient vs reclient remote links)',
          buildnumber=571),
      api.platform(
          COMPARISON_BUILDERS[
              'Comparison Linux (reclient vs reclient remote links)']
          ['platform'], 64),
      api.properties(configuration='Release'),
      api.step_data('Build 1', retcode=1),
      api.post_process(post_process.StatusFailure),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'second_build_compile_fail',
      api.chromium.ci_build(
          builder_group=builder_group,
          builder='Comparison Linux (reclient vs reclient remote links)'),
      api.reclient.properties(),
      api.properties(
          buildername='Comparison Linux (reclient vs reclient remote links)',
          buildnumber=571),
      api.platform(
          COMPARISON_BUILDERS[
              'Comparison Linux (reclient vs reclient remote links)']
          ['platform'], 64),
      api.properties(configuration='Release'),
      api.step_data('Build 2', retcode=1),
      api.post_process(post_process.StatusFailure),
      api.post_process(post_process.DropExpectation),
  )
