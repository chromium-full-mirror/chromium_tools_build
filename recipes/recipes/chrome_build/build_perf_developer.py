# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Recipe to measure developer build performance.
   See also go/chrome-developer-build-metrics
"""

import copy
from datetime import datetime, timedelta

from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb
from RECIPE_MODULES.build import chromium
from RECIPE_MODULES.build import chromium_tests_builder_config as ctbc
from recipe_engine import post_process

DEPS = [
    'builder_group',
    'chromium',
    'chromium_build_perf',
    'chromium_checkout',
    'chromium_tests',
    'chromium_tests_builder_config',
    'depot_tools/gclient',
    'depot_tools/git',
    'recipe_engine/buildbucket',
    'recipe_engine/context',
    'recipe_engine/file',
    'recipe_engine/path',
    'recipe_engine/raw_io',
    'recipe_engine/step',
    'reclient',
]


def _incremental_build_with_one_day_changes(api, target):
  """Steps to run an incremenatl build with 1-day of changes
     (a.k.a morning build).
  """
  time_format = '%Y-%m-%d %H:%M:%S %z'

  with api.step.nest('Incremental build with 1-day of changes'):
    cur_rev = api.buildbucket.gitiles_commit.id or 'HEAD'
    cur_rev_at = api.git(
        'show',
        '--quiet',
        '--format=%ci',
        cur_rev,
        stdout=api.raw_io.output_text(),
        step_test_data=lambda: api.raw_io.test_api.stream_output_text(
            '2023-05-02 11:28:30 +0000\n')).stdout.strip()
    cur_rev_at = datetime.strptime(cur_rev_at, time_format)

    base_rev = api.git(
        'log',
        '--pretty=%H',
        '--since="%s"' % (cur_rev_at - timedelta(days=1)).strftime(time_format),
        '--reverse',
        stdout=api.raw_io.output_text(),
        step_test_data=lambda: api.raw_io.test_api.stream_output_text(
            'abcd\nefgh\n')).stdout.split()[0]

    with api.context(cwd=api.path['cache'].join('builder')):
      cfg = copy.deepcopy(api.gclient.c)

      # Run a warm up build for remote caches at the current revision.
      cfg.revisions['src'] = cur_rev
      api.gclient.sync(cfg)
      raw_result = api.chromium_build_perf.build(
          target,
          with_remote_cache=True,
          step_name_suffix=' at current revision (warmup)')
      if raw_result.status != common_pb.SUCCESS:
        return raw_result

      # Clean up build dir.
      api.chromium_build_perf.remove_build_dir()

      # Run a warm up build for local build dir at the base revision.
      cfg = copy.deepcopy(api.gclient.c)
      cfg.revisions['src'] = base_rev
      api.gclient.sync(cfg)
      raw_result = api.chromium_build_perf.build(
          target,
          with_remote_cache=True,
          step_name_suffix=' at base revision (warmup)')
      if raw_result.status != common_pb.SUCCESS:
        return raw_result

      # Incremental build with remote caches at the current revision.
      cfg.revisions['src'] = cur_rev
      api.gclient.sync(cfg)
      return api.chromium_build_perf.build(target, with_remote_cache=True)


def _clean_build(api, target):
  """Steps to run a clean build."""
  with api.step.nest('Clean build'):
    api.chromium_build_perf.remove_build_dir()
    return api.chromium_build_perf.build(target, with_remote_cache=False)


def RunSteps(api):
  # Set up a named cache so runhooks doesn't redownload everything on each run.
  solution_path = api.path['cache'].join('builder')
  api.file.ensure_directory('init cache if not exists', solution_path)

  # Checkout and gclient hooks.
  builder_id = chromium.BuilderId.create_for_group(
      api.builder_group.for_current, api.buildbucket.builder_name)
  _, builder_config = api.chromium_tests_builder_config.lookup_builder(
      builder_id, use_try_db=False)
  api.chromium_tests.configure_build(builder_config)
  api.chromium_checkout.ensure_checkout()
  with api.context(cwd=solution_path):
    api.chromium.runhooks()

  # Build target: chrome or chrome_public_apk
  target = 'chrome'
  if builder_config.chromium_config == 'android':
    target = 'chrome_public_apk'

  # Incrmenal build with 1-day of changes. a.k.a morning build.
  raw_result = _incremental_build_with_one_day_changes(api, target)
  if raw_result.status != common_pb.SUCCESS:
    return raw_result

  # TODO(b/270902505): add incremenal build with a patch.

  return _clean_build(api, target)


def GenTests(api):
  ctbc_api = api.chromium_tests_builder_config

  # Test data.
  builder = {
      'builder_group': 'fake-group',
      'builder': 'fake-builder',
  }

  def _build_steps(target):
    return [
        'Incremental build with 1-day of changes.Build %s with remote cache at current revision (warmup)'
        % target,
        'Incremental build with 1-day of changes.Build %s with remote cache at base revision (warmup)'
        % target,
        'Incremental build with 1-day of changes.Build %s with remote cache' %
        target,
        'Clean build.Build %s without remote cache' % target,
    ]

  def _success_builds(target):
    return [
        api.post_process(post_process.StepSuccess, s)
        for s in _build_steps(target)
    ]

  yield api.test(
      'full_linux',
      api.chromium.ci_build(**builder),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_ci_builder(
              builder_spec=ctbc.BuilderSpec.create(
                  gclient_config='chromium',
                  chromium_config='chromium',
                  build_gs_bucket=None,
              ),
              **builder).assemble()),
      api.reclient.properties(),
      *_success_builds('chrome'),
      api.post_process(post_process.StatusSuccess),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'no_gitiles',
      api.chromium.generic_build(**builder),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_ci_builder(
              builder_spec=ctbc.BuilderSpec.create(
                  gclient_config='chromium',
                  chromium_config='chromium',
                  build_gs_bucket=None,
              ),
              **builder).assemble()),
      api.reclient.properties(),
      *_success_builds('chrome'),
      api.post_process(post_process.StatusSuccess),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'full_android',
      api.chromium.ci_build(**builder),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_ci_builder(
              builder_spec=ctbc.BuilderSpec.create(
                  gclient_config='chromium',
                  gclient_apply_config=['android'],
                  chromium_config='android',
                  build_gs_bucket=None,
              ),
              **builder).assemble()),
      api.reclient.properties(),
      *_success_builds('chrome_public_apk'),
      api.post_process(post_process.StatusSuccess),
      api.post_process(post_process.DropExpectation),
  )

  for step in _build_steps('chrome'):
    yield api.test(
        '%s_fail' % step,
        api.chromium.ci_build(**builder),
        ctbc_api.properties(
            ctbc_api.properties_assembler_for_ci_builder(
                builder_spec=ctbc.BuilderSpec.create(
                    gclient_config='chromium',
                    chromium_config='chromium',
                    build_gs_bucket=None,
                ),
                **builder).assemble()),
        api.reclient.properties(),
        api.step_data(step, retcode=1),
        api.expect_status('FAILURE'),
        api.post_process(post_process.DropExpectation),
    )
