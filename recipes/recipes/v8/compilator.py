# Copyright 2022 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Compilator recipe.

Triggered by the orchestrator recipe to perform all
check-out-related tasks: sync, hooks, compile, isolate and read all
configs from check-out: MB for building and builders.pyl for testing.

All properties needed by the orchestrator are returned at the end of the
build: CAS digests and test configurations.

The compilator is only used in a trybot setting.
"""

import json

from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb
from PB.recipe_engine import result as result_pb2

from recipe_engine.post_process import DropExpectation, SummaryMarkdown
from recipe_engine.recipe_api import Property

DEPS = [
    'builder_group',
    'chromium',
    'recipe_engine/buildbucket',
    'recipe_engine/file',
    'recipe_engine/json',
    'recipe_engine/path',
    'recipe_engine/platform',
    'recipe_engine/runtime',
    'recipe_engine/step',
    'v8',
    'v8_tests',
]

PROPERTIES = {
    # Mapping of custom dependencies to sync (dependency name as in DEPS
    # file -> deps url).
    'custom_deps': Property(default=None, kind=dict),
    # Optional list of default targets. If not specified the implicit "all"
    # target will be built.
    'default_targets': Property(default=None, kind=list),
    # Mapping of additional gclient variables to set (map name -> value).
    'gclient_vars': Property(default=None, kind=dict),
    # One of intel|arm|mips.
    'target_arch': Property(default=None, kind=str),
    # One of android|fuchsia|linux|mac|win.
    'target_platform': Property(default=None, kind=str),
    # Revision to compile
    'revision': Property(default=None, kind=str),
}

CANCELLATION_MESSAGE = (
    'Parent orchestrator build ended, causing this build to be canceled.')


def orchestrator_name(api):
  """Contruct orchestrator name by compilator name.

  Returns the orchestrator name by naming convention. Same as compilator
      name with the "_compile" infix removed.
  """
  name = api.buildbucket.builder_name

  if api.buildbucket.build.builder.bucket.startswith('try'):
    allowed_suffixes = ('_compile_rel', '_compile_dbg', '_compile_perf_try')
    assert name.endswith(allowed_suffixes), (
        f'Compilator name doesn\'t follow the naming convention. Must end '
        f'in {", ".join(allowed_suffixes)}, but was {name}.')
    prefix, suffix = name.rsplit('_compile_', 1)
    return f'{prefix}_{suffix}'

  return name


def read_test_spec(api, source_dir):
  """Dynamically load test specifications from all discovered test roots."""
  test_spec = api.v8_tests.TEST_SPEC()
  for test_root in api.v8.get_test_roots(source_dir):
    test_spec.update(api.v8.read_test_spec(test_root, [orchestrator_name(api)]))
  return test_spec


def emit_compilator_properties(api, test_spec):
  properties = dict(test_spec.as_properties_dict_single())
  properties['swarm_hashes'] = api.v8_tests.isolated_tests
  properties['gn_args'] = api.v8_tests.gn_args

  properties_step = api.step('compilator properties', [])
  properties_step.presentation.properties['compilator_properties'] = properties
  properties_step.presentation.logs['compilator_properties'] = api.json.dumps(
      properties, indent=2)


def compilator_steps(api, custom_deps, default_targets, gclient_vars,
                     target_arch, target_platform, revision):
  v8 = api.v8
  api.v8_tests.load_static_test_configs()
  bot_config = v8.get_bot_config(
      binary_size_tracking=None,
      clusterfuzz_archive=None,
      coverage=None,
      enable_swarming=True,
      target_arch=target_arch,
      target_platform=target_platform,
      track_build_dependencies=None,
      triggers=None,
      triggers_proxy=None,
  )
  v8.apply_bot_config(bot_config, revision=revision)
  v8.set_gclient_custom_vars(gclient_vars)
  v8.set_gclient_custom_deps(custom_deps)
  v8.set_chromium_configs(clobber=False, default_targets=default_targets)

  with api.step.nest('initialization'):
    update_result = v8.checkout(revision)
    source_dir = update_result.source_root.path
    build_dir = api.v8.build_dir(source_dir)
    api.v8_tests.set_up_swarming()
    api.v8.runhooks(source_dir, build_dir)

    test_spec = read_test_spec(api, source_dir)

  with api.step.nest('build'):
    compile_failure = v8.compile(source_dir, build_dir, test_spec)
    if compile_failure:
      return compile_failure

  emit_compilator_properties(api, test_spec)


def RunSteps(api, custom_deps, default_targets, gclient_vars, target_arch,
             target_platform, revision):
  try:
    return compilator_steps(
        api, custom_deps, default_targets, gclient_vars, target_arch,
        target_platform, revision)
  finally:
    if api.runtime.in_global_shutdown:
      # pylint: disable=lost-exception
      # Cancellation can cause all sorts of spurious exceptions.
      return result_pb2.RawResult(
          status=common_pb.CANCELED,
          summary_markdown=CANCELLATION_MESSAGE)


def GenTests(api):
  def test(name, builder_name='v8_foobar_compile_rel', status='SUCCESS'):
    build_msg = api.buildbucket.try_build_message(
        project='v8',
        revision='deadbeef' * 5,
        builder=builder_name,
        git_repo='https://chromium.googlesource.com/v8/v8',
        change_number=456789,
        patch_set=12,
        tags=api.buildbucket.tags(
            user_agent='cq',
            buildset='patch/gerrit/chromium-review.googlesource.com/456789/12'),
    )
    api.buildbucket.update_backend_service_account(
        build_msg,
        'v8-try-builder@chops-service-accounts.iam.gserviceaccount.com')
    return (api.test(name, status=status) +
            api.builder_group.for_current('tryserver.v8') +
            api.platform('linux', 64) + api.buildbucket.build(build_msg) +
            api.v8.example_test_roots('test_checkout') +
            api.v8.hide_infra_steps())

  test_spec = json.dumps({'tests': [{'name': 'v8testing'}]}, indent=2)

  yield (
      test('basic') +
      api.v8.test_spec_in_checkout('v8_foobar_rel', test_spec)
  )

  yield (
      api.test('basic_ci', status='SUCCESS') +
      api.builder_group.for_current('client.v8') +
      api.buildbucket.ci_build(
          project='v8',
          revision='deadbeef'*5,
          builder='Random CI builder',
          git_repo='https://chromium.googlesource.com/v8/v8',
      ) +
      api.v8.example_test_roots('test_checkout') +
      api.v8.hide_infra_steps() +
      api.v8.test_spec_in_checkout('Random CI builder', test_spec)
  )

  yield (
      test('windows', 'v8_foobar_compile_dbg') +
      api.v8.test_spec_in_checkout('v8_foobar_dbg', test_spec) +
      api.platform('win', 64) +
      api.post_process(DropExpectation)
  )

  yield (test('compile_failure', status='FAILURE') +
         api.step_data('build.compile', retcode=1) +
         api.post_process(DropExpectation))

  yield (test('cancellation', status='CANCELED') +
         api.runtime.global_shutdown_on_step('build.compile') +
         api.post_process(SummaryMarkdown, CANCELLATION_MESSAGE) +
         api.post_process(DropExpectation))
