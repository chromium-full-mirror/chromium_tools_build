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
from PB.recipes.build.chrome_build import build_perf as build_perf_pb
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
  siso,
)
from RECIPE_MODULES.depot_tools import gclient, git
from RECIPE_MODULES.recipe_engine import (
  buildbucket,
  context,
  file,
  path,
  platform,
  properties,
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
  properties: properties.API
  raw_io: raw_io.API
  siso: siso.API
  step: step.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  buildbucket: buildbucket.TEST_API
  chromium: chromium.TEST_API
  chromium_tests_builder_config: chromium_tests_builder_config.TEST_API
  git: git.TEST_API
  platform: platform.TEST_API
  properties: properties.TEST_API
  raw_io: raw_io.TEST_API
  siso: siso.TEST_API
  step: step.TEST_API


PROPERTIES = build_perf_pb.InputProperties


def _raise_raw_result_on_failure(api: DEPS, raw_result):
  if raw_result.status != common_pb.SUCCESS:
    raise api.step.StepFailure(raw_result.summary_markdown)


def _build_variants(
  weights_file: Path | None,
) -> list[dict[str, str | list[str]]]:
  """Returns the Siso args, step suffix, and build dir name for each variant."""
  variants: list[dict[str, str | list[str]]] = [
    {
      "siso_args": [],
      "step_suffix": "",
      "build_dir_name": "siso",
    },
  ]
  if weights_file:
    variants.append(
      {
        "siso_args": [
          f"-weights_file={weights_file}",
          "-action_salt=step-weights",
        ],
        "step_suffix": " with step-weights",
        "build_dir_name": "siso_weights",
      }
    )
  return variants


def _incremental_build_with_one_day_changes(
  api: DEPS,
  source_dir: Path,
  default_build_dir: Path,
  target,
  weights_file: Path | None = None,
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

    build_dir_parent = default_build_dir.parent

    # Clean up deps cache and check out to the base revision.
    api.chromium_build_perf.checkout(source_dir, default_build_dir, base_rev)

    # Run warm up builds for local build dirs. Omit -batch=false on warmup
    # builds so fast_local does not populate remote caches non-deterministically.
    variants = _build_variants(weights_file)
    for variant in variants:
      variant_build_dir = build_dir_parent / str(variant["build_dir_name"])
      api.chromium_build_perf.recreate_build_dir(
        source_dir,
        variant_build_dir,
        phase="siso_native",
        remove_deps_cache=True,
      )
      raw_result = api.chromium_build_perf.build_with_siso(
        source_dir,
        variant_build_dir,
        target,
        with_remote_cache=True,
        step_name_suffix=(
          f" with Siso in native mode{variant['step_suffix']}"
          " at base revision (warmup)"
        ),
        siso_args=list(variant["siso_args"]),
      )
      _raise_raw_result_on_failure(api, raw_result)

    # Incremental build with remote caches at the current revision.
    # Specify -batch=false to emulate interactive developer builds on bots where
    # stdout is not a terminal. This implicitly enables fastlocal (and other
    # optimizations) that affect the benchmark results.
    api.chromium_build_perf.checkout(source_dir, default_build_dir, cur_rev)

    for variant in variants:
      raw_result = api.chromium_build_perf.build_with_siso(
        source_dir,
        build_dir_parent / str(variant["build_dir_name"]),
        target,
        with_remote_cache=True,
        step_name_suffix=f" with Siso in native mode{variant['step_suffix']}",
        siso_args=["-batch=false", *variant["siso_args"]],
      )
      _raise_raw_result_on_failure(api, raw_result)


def _incremental_builds_with_patch(
  api: DEPS,
  source_dir: Path,
  default_build_dir: Path,
  target,
  weights_file: Path | None = None,
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
    variants = _build_variants(weights_file)

    # Set up a separate build dir for each Siso variant so incremental build
    # state (.siso_deps, .siso_fs_state, ninja log) does not collide across
    # variants as we step through revisions.
    for variant in variants:
      api.chromium_build_perf.recreate_build_dir(
        source_dir,
        build_dir_parent / str(variant["build_dir_name"]),
        phase="siso_native",
        remove_deps_cache=True,
      )

    # Check out each revision in chronological order and build every variant in
    # its own build directory:
    # - Revision 0 (base revision) runs a warmup build with remote cache enabled
    #   to populate each variant's local build directory and remote cache.
    # - Subsequent revisions run incremental builds with remote cache disabled
    #   and -batch=false to measure local incremental build performance for
    #   each commit as if it were an unsubmitted local patch.
    for i, rev in enumerate(revs):
      api.chromium_build_perf.checkout(source_dir, default_build_dir, rev)

      if i == 0:
        # The warm up builds at base revision won't be included
        # in perf metrics. Omit -batch=false so fast_local does not populate
        # remote caches non-deterministically.
        with_remote_cache = True
        step_name_suffix = ' at base revision (warmup)'
        siso_args = []
      else:
        # It assumes that remote caches are not available
        # for incremental builds with local modifications.
        # Specify -batch=false to emulate interactive developer builds on bots
        # where stdout is not a terminal. This implicitly enables fastlocal
        # (and other optimizations) that affect the benchmark results.
        with_remote_cache = False
        step_name_suffix = ''
        siso_args = ["-batch=false"]

      for variant in variants:
        raw_result = api.chromium_build_perf.build_with_siso(
          source_dir,
          build_dir_parent / str(variant["build_dir_name"]),
          target,
          with_remote_cache=with_remote_cache,
          step_name_suffix=(
            f" with Siso in native mode{variant['step_suffix']}"
            f"{step_name_suffix}"
          ),
          siso_args=[*siso_args, *variant["siso_args"]],
        )
        _raise_raw_result_on_failure(api, raw_result)


def _clean_builds(
  api: DEPS,
  source_dir: Path,
  build_dir: Path,
  target,
  enable_missing_deps_check: bool,
  weights_file: Path | None = None,
):
  """Steps to run clean builds."""
  with api.step.nest('Clean builds'):
    # Siso builds.
    phase = 'siso_native'
    step_name_suffix = ' with Siso in native mode'
    variants = _build_variants(weights_file)
    for variant in variants:
      api.chromium_build_perf.recreate_build_dir(
        source_dir, build_dir, phase=phase, remove_deps_cache=True
      )
      result = api.chromium_build_perf.build_with_siso(
        source_dir,
        build_dir,
        target,
        with_remote_cache=True,
        step_name_suffix=(
          f"{step_name_suffix}{variant['step_suffix']}"
          " at current revision (warmup)"
        ),
        siso_args=[*variant["siso_args"], "-strict_remote"],
      )
      _raise_raw_result_on_failure(api, result)

    # Specify -batch=false to emulate interactive developer builds on bots where
    # stdout is not a terminal. This implicitly enables fastlocal (and other
    # optimizations) that affect the benchmark results.
    for variant in variants:
      for with_remote_cache in (False, True):
        api.chromium_build_perf.recreate_build_dir(
          source_dir,
          build_dir,
          phase=phase,
          remove_deps_cache=not with_remote_cache,
        )
        result = api.chromium_build_perf.build_with_siso(
          source_dir,
          build_dir,
          target,
          with_remote_cache=with_remote_cache,
          step_name_suffix=f"{step_name_suffix}{variant['step_suffix']}",
          siso_args=["-batch=false", *variant["siso_args"]],
        )
        _raise_raw_result_on_failure(api, result)

    # Enable gn check --check-generated as an additional non-fatal step before
    # enabling by default. See crbug.com/557053937.
    # raise_on_failure=False keeps the step marked as a failure without failing
    # the overall build. get_env() sets FORCE_MAC_TOOLCHAIN, which gn needs
    # to use the hermetic Xcode on Mac.
    # TODO(crbug.com/581653605): Use a chromium API for gn check.
    with api.context(env=api.chromium.get_env(source_dir)):
      api.gn.check(
        build_dir,
        check_generated=True,
        step_name='gn check --check-generated',
        raise_on_failure=False,
      )

    # Missing-deps check as an additional step for continuous benchmarking
    # before enabling by default. Opt-in via the enable_missing_deps_check
    # property. See crbug.com/346429448.
    # Note: Do not fail the build if this step fails to avoid interrupting
    # continuous performance metrics collection. See crbug.com/556013464.
    if enable_missing_deps_check:
      api.chromium_build_perf.recreate_build_dir(
        source_dir, build_dir, phase=phase
      )
      api.chromium_build_perf.build_with_siso(
        source_dir,
        build_dir,
        target,
        with_remote_cache=True,
        step_name_suffix=step_name_suffix + ' with missing-deps check',
        siso_args=["-batch=false", "-missing_deps=error"],
      )

    api.file.rmtree(f"rmtree {build_dir}", build_dir)


def RunSteps(api: DEPS, props: build_perf_pb.InputProperties):
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
  update_result = api.chromium_checkout.ensure_checkout()
  source_dir = update_result.source_root.path
  build_dir = api.chromium.default_build_dir(source_dir)
  api.chromium.ensure_toolchains(checkout_dir=update_result.checkout_dir)
  with api.context(cwd=solution_path):
    api.chromium.runhooks(source_dir, build_dir)

  api.siso.check_version(source_dir)

  weights_file = None
  if props.enable_step_weights:
    weights_file = api.siso.ensure_weights(api.chromium.c.TARGET_PLATFORM)

  # Build target: chrome or chrome_public_apk
  target = 'chrome'
  if builder_config.android_config:
    target = 'chrome_public_apk'

  out_dir = build_dir.parent
  api.file.rmtree(f"rmtree {out_dir}", out_dir)
  try:
    # Clean builds.
    _clean_builds(
      api,
      source_dir,
      build_dir,
      target,
      enable_missing_deps_check=props.enable_missing_deps_check,
      weights_file=weights_file,
    )

    # Incremental build with 1-day of changes. a.k.a morning build.
    _incremental_build_with_one_day_changes(
      api, source_dir, build_dir, target, weights_file=weights_file
    )

    _incremental_builds_with_patch(
      api, source_dir, build_dir, target, weights_file=weights_file
    )
  finally:
    # Remove the out dir to reduce the builder cache size.
    api.file.rmtree(f"rmtree {out_dir}", out_dir)


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
    api.properties(
      build_perf_pb.InputProperties(
        enable_missing_deps_check=True,
        enable_step_weights=True,
      )
    ),
    api.siso.properties(),
    _buildbucket_search_results(),
    api.post_process(
      post_process.StepCommandContains,
      "Clean builds.Build chrome with remote cache with Siso in native mode "
      "at current revision (warmup)",
      ["-strict_remote"],
    ),
    api.post_process(
      post_process.StepCommandContains,
      "Clean builds.Build chrome with remote cache with Siso in native mode "
      "with step-weights at current revision (warmup)",
      [
        "-weights_file=[CACHE]/siso_weights/weights_linux.json.gz",
        "-action_salt=step-weights",
        "-strict_remote",
      ],
    ),
    api.post_process(
      post_process.StepCommandContains,
      "Clean builds.Build chrome without remote cache with Siso in native mode",
      ["-batch=false"],
    ),
    api.post_process(
      post_process.StepCommandContains,
      "Clean builds.Build chrome without remote cache with Siso in native mode "
      "with step-weights",
      [
        "-batch=false",
        "-weights_file=[CACHE]/siso_weights/weights_linux.json.gz",
        "-action_salt=step-weights",
      ],
    ),
    api.post_process(
      post_process.StepCommandContains,
      "Clean builds.Build chrome with remote cache with Siso in native mode "
      "with step-weights",
      [
        "-batch=false",
        "-weights_file=[CACHE]/siso_weights/weights_linux.json.gz",
        "-action_salt=step-weights",
      ],
    ),
    api.post_process(
      post_process.StepCommandContains,
      "Incremental build with 1-day of changes.Build chrome with remote cache "
      "with Siso in native mode with step-weights",
      [
        "-batch=false",
        "-weights_file=[CACHE]/siso_weights/weights_linux.json.gz",
        "-action_salt=step-weights",
      ],
    ),
    api.post_process(
      post_process.StepCommandContains,
      "Incremental builds with patch.Build chrome without remote cache with "
      "Siso in native mode with step-weights",
      [
        "-batch=false",
        "-weights_file=[CACHE]/siso_weights/weights_linux.json.gz",
        "-action_salt=step-weights",
      ],
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'full_linux_without_missing_deps_check',
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
    api.siso.properties(),
    _buildbucket_search_results(),
    api.post_process(
      post_process.DoesNotRun,
      'Clean builds.Build chrome with remote cache with Siso in native mode '
      'with missing-deps check',
    ),
    api.post_process(
      post_process.DoesNotRun,
      "Clean builds.Build chrome without remote cache with Siso in native mode "
      "with step-weights",
    ),
    api.post_process(
      post_process.DoesNotRun,
      "Clean builds.Build chrome with remote cache with Siso in native mode "
      "with step-weights",
    ),
    api.post_process(
      post_process.DoesNotRun,
      "Incremental build with 1-day of changes.Build chrome with remote cache "
      "with Siso in native mode with step-weights",
    ),
    api.post_process(
      post_process.DoesNotRun,
      "Incremental builds with patch.Build chrome without remote cache with "
      "Siso in native mode with step-weights",
    ),
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
    api.siso.properties(),
    api.step_data(
      'Clean builds.Build chrome without remote cache with Siso in native mode',
      retcode=1,
    ),
    api.expect_status('FAILURE'),
    api.post_process(
      post_process.MustRun,
      "rmtree [CACHE]/builder/src/out (2)",
    ),
    api.post_process(post_process.DropExpectation),
  )
