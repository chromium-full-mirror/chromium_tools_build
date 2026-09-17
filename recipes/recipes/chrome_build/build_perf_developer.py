# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Recipe to measure developer build performance.
See also go/chrome-developer-build-metrics
"""

from datetime import datetime, timedelta

from recipe_engine.config_types import Path

from PB.go.chromium.org.luci.buildbucket.proto import build as build_pb
from PB.go.chromium.org.luci.buildbucket.proto import (
  builds_service as builds_service_pb,
)
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb
from RECIPE_MODULES.build import chromium_types
from RECIPE_MODULES.build import chromium_tests_builder_config as ctbc
from recipe_engine import post_process

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import (
  builder_group,
  chromium,
  chromium_build_perf,
  chromium_checkout,
  chromium_tests,
  chromium_tests_builder_config,
  gn,
  reclient,
  siso,
)
from RECIPE_MODULES.depot_tools import gclient, git
from RECIPE_MODULES.recipe_engine import (
  buildbucket,
  context,
  file,
  path,
  platform,
  raw_io,
  step,
)


@dataclass
class DEPS(RecipeScriptApi):
  buildbucket: buildbucket.API
  builder_group: builder_group.API
  chromium: chromium.API
  chromium_build_perf: chromium_build_perf.API
  chromium_checkout: chromium_checkout.API
  chromium_tests: chromium_tests.API
  chromium_tests_builder_config: chromium_tests_builder_config.API
  context: context.API
  file: file.API
  gclient: gclient.API
  git: git.API
  gn: gn.API
  path: path.API
  platform: platform.API
  raw_io: raw_io.API
  reclient: reclient.API
  siso: siso.API
  step: step.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  buildbucket: buildbucket.TEST_API
  chromium: chromium.TEST_API
  chromium_tests_builder_config: chromium_tests_builder_config.TEST_API
  git: git.TEST_API
  platform: platform.TEST_API
  raw_io: raw_io.TEST_API
  reclient: reclient.TEST_API
  siso: siso.TEST_API
  step: step.TEST_API


def _raise_raw_result_on_failure(api: DEPS, raw_result):
  if raw_result.status != common_pb.SUCCESS:
    raise api.step.StepFailure(raw_result.summary_markdown)


def _incremental_build_with_one_day_changes(
  api: DEPS,
  source_dir: Path,
  default_build_dir: Path,
  target,
):
  """Steps to run an incremental build with 1-day of changes
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
        '2023-05-02 11:28:30 +0000\n'
      ),
    ).stdout.strip()
    cur_rev_at = datetime.strptime(cur_rev_at, time_format)

    base_rev = api.git(
      'log',
      '--pretty=%H',
      '--since="%s"' % (cur_rev_at - timedelta(days=1)).strftime(time_format),
      '--reverse',
      stdout=api.raw_io.output_text(),
      step_test_data=lambda: api.raw_io.test_api.stream_output_text(
        'abcd\nefgh\n'
      ),
    ).stdout.split()[0]

    # Run a warm up build for remote caches at the current revision.
    api.chromium_build_perf.checkout(source_dir, default_build_dir, cur_rev)

    build_dir_parent = default_build_dir.parent

    ##  Ninja+Reclient
    # b/498283357 - Stop Reclient workloads speculatively.
    # api.chromium_build_perf.recreate_build_dir(
    #     source_dir, default_build_dir, phase='ninja')
    # raw_result = api.chromium_build_perf.build_with_ninja(
    #     source_dir,
    #     default_build_dir,
    #     target,
    #     with_remote_cache=True,
    #     step_name_suffix=' at current revision (warmup)')
    # _raise_raw_result_on_failure(api, raw_result)

    ## Siso build
    siso_build_dir = build_dir_parent / 'siso'
    api.chromium_build_perf.recreate_build_dir(
      source_dir, siso_build_dir, phase='siso_native'
    )
    suffix = ' with Siso in native mode at current revision (warmup)'
    raw_result = api.chromium_build_perf.build_with_siso(
      source_dir,
      siso_build_dir,
      target,
      with_remote_cache=True,
      step_name_suffix=suffix,
    )
    _raise_raw_result_on_failure(api, raw_result)

    # Clean up deps cache and check out to the base revision.
    api.chromium_build_perf.checkout(source_dir, default_build_dir, base_rev)

    # Run a warm up build for local build dir.
    ## Ninja+Reclient
    # b/498283357 - Stop Reclient workloads speculatively.
    # api.chromium_build_perf.recreate_build_dir(
    #     source_dir, default_build_dir, phase='ninja', remove_deps_cache=True)
    # raw_result = api.chromium_build_perf.build_with_ninja(
    #     source_dir,
    #     default_build_dir,
    #     target,
    #     with_remote_cache=True,
    #     step_name_suffix=' at base revision (warmup)')
    # _raise_raw_result_on_failure(api, raw_result)

    ## Siso
    api.chromium_build_perf.recreate_build_dir(
      source_dir, siso_build_dir, phase='siso_native', remove_deps_cache=True
    )
    raw_result = api.chromium_build_perf.build_with_siso(
      source_dir,
      siso_build_dir,
      target,
      with_remote_cache=True,
      step_name_suffix=' with Siso in native mode at base revision (warmup)',
    )
    _raise_raw_result_on_failure(api, raw_result)

    # Incremental build with remote caches at the current revision.
    api.chromium_build_perf.checkout(source_dir, default_build_dir, cur_rev)

    ## Ninja+Reclient
    # b/498283357 - Stop Reclient workloads speculatively.
    # raw_result = api.chromium_build_perf.build_with_ninja(
    #     source_dir, default_build_dir, target, with_remote_cache=True)
    # _raise_raw_result_on_failure(api, raw_result)

    ## Siso
    raw_result = api.chromium_build_perf.build_with_siso(
      source_dir,
      siso_build_dir,
      target,
      with_remote_cache=True,
      step_name_suffix=' with Siso in native mode',
    )
    _raise_raw_result_on_failure(api, raw_result)


def _incremental_builds_with_patch(
  api: DEPS,
  source_dir: Path,
  default_build_dir: Path,
  target,
):
  """Steps to run incremental builds with a patch, which represent builds with
     local modifications.

  It builds for each revision of the git history excluding bot commits.
  Remote caches are disabled to pretend that the patches are new changes.
  """
  with api.step.nest('Incremental builds with patch'):
    # Get a revision from the last job of this builder.
    builds = None
    try:
      builds = api.buildbucket.search(
        builds_service_pb.BuildPredicate(
          builder=api.buildbucket.build.builder,
          status=common_pb.Status.ENDED_MASK,
        ),
        limit=1,
      )
    except api.step.InfraFailure:
      # Ignore search failure.
      pass

    last_rev = None
    if builds:
      last_rev = builds[0].input.gitiles_commit.id

    # List up commits between the current revision and the last revision.
    max_builds = 20
    gitlog_args = ['log', '-n', max_builds, "--format='%H %ae'", '--reverse']
    if last_rev:
      cur_rev = api.buildbucket.gitiles_commit.id or 'HEAD'
      gitlog_args += ['%s..%s' % (last_rev, cur_rev)]
    gitlog_result = api.git(
      *gitlog_args,
      stdout=api.raw_io.output_text(),
      step_test_data=lambda: api.raw_io.test_api.stream_output_text(
        'abcd foo@google.com\n'
        'efgh bot@example.gserviceaccount.com\n'
        'ijkl bar@chromium.org\n'
      ),
    )
    commits = [
      commit.strip("'") for commit in gitlog_result.stdout.strip().split('\n')
    ]
    gitlog_result.presentation.logs['commits'] = commits

    def gitiles_url(rev):
      return 'https://%s/%s/+/%s' % (
        api.buildbucket.gitiles_commit.host,
        api.buildbucket.gitiles_commit.project,
        rev,
      )

    revs = []
    for commit in commits:
      if not commit:
        continue
      rev, email = commit.split(' ')
      # Exclude bot commits.
      if email.endswith('gserviceaccount.com'):
        gitlog_result.presentation.links[rev + ' (excluded)'] = gitiles_url(rev)
      else:
        revs.append(rev)
        gitlog_result.presentation.links[rev] = gitiles_url(rev)

    if not revs:
      gitlog_result.presentation.step_text = 'No commits to build'
      return

    build_dir_parent = default_build_dir.parent

    # Set up build dirs for Ninja/Siso builds.
    api.chromium_build_perf.recreate_build_dir(
      source_dir, default_build_dir, phase='ninja', remove_deps_cache=True
    )
    api.chromium_build_perf.recreate_build_dir(
      source_dir,
      build_dir_parent / 'siso',
      phase='siso_native',
      remove_deps_cache=True,
    )

    # Run a build at each revision.
    for i, rev in enumerate(revs):
      api.chromium_build_perf.checkout(source_dir, default_build_dir, rev)

      if i == 0:
        # The warm up builds at base revision won't be included
        # in perf metrics.
        with_remote_cache = True
        step_name_suffix = ' at base revision (warmup)'
      else:
        # It assumes that remote caches are not available
        # for incremental builds with local modifications.
        with_remote_cache = False
        step_name_suffix = ''

      # Ninja+Reclient
      # b/498283357 - Stop Reclient workloads speculatively.
      # raw_result = api.chromium_build_perf.build_with_ninja(
      #     source_dir,
      #     default_build_dir,
      #     target,
      #     with_remote_cache=with_remote_cache,
      #     step_name_suffix=step_name_suffix)
      # _raise_raw_result_on_failure(api, raw_result)

      # Siso
      siso_build_dir = build_dir_parent / 'siso'
      raw_result = api.chromium_build_perf.build_with_siso(
        source_dir,
        siso_build_dir,
        target,
        with_remote_cache=with_remote_cache,
        step_name_suffix=' with Siso in native mode' + step_name_suffix,
      )
      _raise_raw_result_on_failure(api, raw_result)


def _clean_builds(api: DEPS, source_dir: Path, build_dir: Path, target):
  """Steps to run clean builds."""
  with api.step.nest('Clean builds'):
    # Ninja+Reclient builds.
    # b/498283357 - Stop Reclient workloads speculatively.
    # phase = 'ninja'
    # api.chromium_build_perf.recreate_build_dir(
    #     source_dir, build_dir, phase=phase, remove_deps_cache=True)
    # result = api.chromium_build_perf.build_with_ninja(
    #     source_dir, build_dir, target, with_remote_cache=False)
    # _raise_raw_result_on_failure(api, result)

    # api.chromium_build_perf.recreate_build_dir(
    #     source_dir, build_dir, phase=phase)
    # result = api.chromium_build_perf.build_with_ninja(
    #     source_dir, build_dir, target, with_remote_cache=True)

    # Siso builds.
    phase = 'siso_native'
    step_name_suffix = ' with Siso in native mode'
    api.chromium_build_perf.recreate_build_dir(
      source_dir, build_dir, phase=phase, remove_deps_cache=True
    )
    result = api.chromium_build_perf.build_with_siso(
      source_dir,
      build_dir,
      target,
      with_remote_cache=False,
      step_name_suffix=step_name_suffix,
    )
    _raise_raw_result_on_failure(api, result)

    api.chromium_build_perf.recreate_build_dir(
      source_dir, build_dir, phase=phase
    )
    result = api.chromium_build_perf.build_with_siso(
      source_dir,
      build_dir,
      target,
      with_remote_cache=True,
      step_name_suffix=step_name_suffix,
    )
    _raise_raw_result_on_failure(api, result)

    # Enable gn check --check-generated as an additional non-fatal step on
    # Linux target builds before enabling by default. See crbug.com/557053937.
    # raise_on_failure=False keeps the step marked as a failure without failing
    # the overall build.
    if api.chromium.c.TARGET_PLATFORM == 'linux':
      api.gn.check(
        build_dir,
        check_generated=True,
        step_name='gn check --check-generated',
        raise_on_failure=False,
      )

    # Enable missing-deps check as an additional step on Linux and Mac hosts for
    # continuous benchmarking before enabling by default.
    # See crbug.com/346429448.
    # Note: Do not fail the build if this step fails to avoid interrupting
    # continuous performance metrics collection. See crbug.com/556013464.
    if api.platform.is_linux or api.platform.is_mac:
      api.chromium_build_perf.recreate_build_dir(
        source_dir, build_dir, phase=phase
      )
      api.chromium_build_perf.build_with_siso(
        source_dir,
        build_dir,
        target,
        with_remote_cache=True,
        step_name_suffix=step_name_suffix + ' with missing-deps check',
        siso_args=['-missing_deps=error'],
      )


def RunSteps(api: DEPS):
  # Set up a named cache so runhooks doesn't redownload everything on each run.
  solution_path = api.path.cache_dir / 'builder'
  api.file.ensure_directory('init cache if not exists', solution_path)

  # Checkout and gclient hooks.
  builder_id = chromium_types.BuilderId.create_for_group(
    api.builder_group.for_current, api.buildbucket.builder_name
  )
  _, builder_config = api.chromium_tests_builder_config.lookup_builder(
    builder_id, use_try_db=False
  )
  api.chromium_tests.configure_build(builder_config)
  api.reclient.download_reclient(api.gclient.c.solutions[0])
  update_result = api.chromium_checkout.ensure_checkout()
  source_dir = update_result.source_root.path
  build_dir = api.chromium.default_build_dir(source_dir)
  api.chromium.ensure_toolchains(checkout_dir=update_result.checkout_dir)
  with api.context(cwd=solution_path):
    api.chromium.runhooks(source_dir, build_dir)

  api.siso.check_version(source_dir)

  # Build target: chrome or chrome_public_apk
  target = 'chrome'
  if builder_config.android_config:
    target = 'chrome_public_apk'

  # Enable Reclient racing feature.
  # The parameters should be same with the ones in reclent_helper.py.
  # https://crsrc.org/d/reclient_helper.py;l=220;drc=1077fbe08a1c03ef7f7fa8eb925edd18688357ae
  env = {
    'RBE_exec_strategy': 'racing',
    'RBE_local_resource_fraction': '0.2',
    'RBE_racing_bias': '0.95',
  }
  with api.context(env=env):
    # Clean builds.
    _clean_builds(api, source_dir, build_dir, target)

    # Incremental build with 1-day of changes. a.k.a morning build.
    _incremental_build_with_one_day_changes(api, source_dir, build_dir, target)

    _incremental_builds_with_patch(api, source_dir, build_dir, target)

  # Remove the out dir to reduce the builder cache size.
  api.file.rmtree('rmtree %s' % str(build_dir), str(build_dir))


def GenTests(api: TEST_DEPS):
  ctbc_api = api.chromium_tests_builder_config

  # Test data.
  builder = {
    'builder_group': 'fake-group',
    'builder': 'fake-builder',
  }

  def _buildbucket_search_results():
    return api.buildbucket.simulated_search_results(
      [
        build_pb.Build(
          input=build_pb.Build.Input(
            gitiles_commit=common_pb.GitilesCommit(
              host='chromium.googlesource.com',
              project='chromium/src',
              id='abcd',
              ref='refs/heads/main',
            )
          )
        )
      ],
      step_name='Incremental builds with patch.buildbucket.search',
    )

  yield api.test(
    'full_linux',
    api.chromium.ci_build(**builder),
    ctbc_api.properties(
      ctbc_api.properties_assembler_for_ci_builder(
        builder_spec=ctbc.BuilderSpec.create(
          gclient_config='chromium',
          chromium_config='chromium',
        ),
        **builder,
      ).assemble()
    ),
    api.reclient.properties(),
    api.siso.properties(),
    _buildbucket_search_results(),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'buildbucket_search_error',
    api.chromium.ci_build(**builder),
    ctbc_api.properties(
      ctbc_api.properties_assembler_for_ci_builder(
        builder_spec=ctbc.BuilderSpec.create(
          gclient_config='chromium',
          chromium_config='chromium',
        ),
        **builder,
      ).assemble()
    ),
    api.reclient.properties(),
    api.siso.properties(),
    api.buildbucket.simulated_batch_search_output(
      builds_service_pb.BatchResponse(
        responses=[dict(error=dict(code=5, message="error"))]
      ),
      step_name='Incremental builds with patch.buildbucket.search',
    ),
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
        ),
        **builder,
      ).assemble()
    ),
    api.reclient.properties(),
    api.siso.properties(),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'no_gitlogs_for_incremental_builds_with_patch',
    api.chromium.ci_build(**builder),
    ctbc_api.properties(
      ctbc_api.properties_assembler_for_ci_builder(
        builder_spec=ctbc.BuilderSpec.create(
          gclient_config='chromium',
          chromium_config='chromium',
        ),
        **builder,
      ).assemble()
    ),
    api.reclient.properties(),
    api.siso.properties(),
    api.step_data(
      'Incremental builds with patch.git log', api.raw_io.stream_output_text('')
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'android',
    api.chromium.ci_build(**builder),
    ctbc_api.properties(
      ctbc_api.properties_assembler_for_ci_builder(
        builder_spec=ctbc.BuilderSpec.create(
          gclient_config='chromium',
          gclient_apply_config=['android'],
          chromium_config='android',
          android_config='base_config',
        ),
        **builder,
      ).assemble()
    ),
    api.reclient.properties(),
    api.siso.properties(),
    _buildbucket_search_results(),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'clean_build_failure',
    api.chromium.ci_build(**builder),
    ctbc_api.properties(
      ctbc_api.properties_assembler_for_ci_builder(
        builder_spec=ctbc.BuilderSpec.create(
          gclient_config='chromium',
          chromium_config='chromium',
        ),
        **builder,
      ).assemble()
    ),
    api.reclient.properties(),
    api.siso.properties(),
    api.step_data(
      'Clean builds.Build chrome without remote cache with Siso in native mode',
      retcode=1,
    ),
    api.expect_status('FAILURE'),
    api.post_process(post_process.DropExpectation),
  )
