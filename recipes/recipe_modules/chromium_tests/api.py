# Copyright 2014 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import attr
import collections
from collections.abc import Callable, Collection, Iterable, Mapping
import contextlib
from functools import reduce
import itertools
import time
import traceback

from google.protobuf import timestamp_pb2
from recipe_engine import recipe_api
from recipe_engine.config_types import Path
from recipe_engine.engine_types import freeze

from PB.recipe_engine import result as result_pb2
from PB.recipe_modules.build.archive import properties as arch_prop
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb
from PB.go.chromium.org.luci.buildbucket.proto import (
  builds_service as builds_service_pb2,
)

from RECIPE_MODULES.build import chromium_types
from RECIPE_MODULES.build import chromium_tests_builder_config as ctbc
from RECIPE_MODULES.build.attr_utils import attrib, mapping, sequence, attrs
from RECIPE_MODULES.build.filter import api as filter_api
from RECIPE_MODULES.depot_tools import bot_update
from RECIPE_MODULES.build.chromium_checkout import checkout_result

from . import generators, steps
from . import targets_config as targets_config_module

# These account ids are obtained by looking at gerrit API responses.
# Specifically, just find a build from a desired CL author, look at the
# json output of the "gerrit fetch current CL info" recipe step, and find the
# values of owner._account_id.
# chromium and v8-ci-autorollers
AUTOROLLER_ACCOUNT_IDS = (1302611, 1274527)

ALL_TEST_BINARIES_ISOLATE_NAME = 'all_test_binaries'
UNIFIED_RUNTIME_DEPS_NAME = 'unified_runtime_deps'

REPOSITORY_MAPPING = {
  'chromium': 'chromium',
  'chromium/src': 'chromium',
  'v8/v8': 'v8',
  'src/webrtc': 'webrtc',
}

TEST_TRIGGER_AND_COLLECT_DEPS_TARGET = 'infra/orchestrator:orchestrator_all'
TEST_TRIGGER_AND_COLLECT_DEPS_RUNTIME_DEPS_FILE = (
  'orchestrator_all.runtime_deps'
)

SOURCE_CHECK_ID = 'checkout'
WITHOUT_PATCH_SOURCE_CHECK_ID = 'checkout (without patch)'

BUILD_CHECK_ID = 'compile'
WITHOUT_PATCH_BUILD_CHECK_ID = 'compile (without patch)'


@attrs()
class SwarmingExecutionInfo:
  """Information about how to execute a set of swarming tests."""

  # Maps isolate names to the digest for that isolate.
  # Should be renamed to 'digest_by_isolate_name'.
  digest_by_isolate_name = attrib(mapping[str, str], default={})

  # The CAS digest for a file which contains the command lines needed to execute
  # each test.
  # Should be renamed to 'command_lines_file_digest'
  command_lines_file_digest = attrib(str, default='')

  # The mapping of isolate to command lines.
  command_lines = attrib(mapping[str, sequence], default={})

  # The working directory to run the isolates in (usually something like
  # out/Release).
  # Should be renamed to 'command_line_cwd'
  command_lines_cwd = attrib(str, default='')

  # The CAS digest to use for triggering tests instead of getting a checkout if
  # enabled.
  test_trigger_deps_digest = attrib(str, default='')

  # The mapping of command line variants.
  command_line_variants = attrib(
    mapping[str, mapping[str, sequence]], default={}
  )

  # The mapping of CAS digests for the command line variants.
  command_line_variant_digests = attrib(mapping[str, str], default={})

  def ensure_command_lines_archived(self, chromium_tests_api):
    """Ensures the command lines are archived to CAS.

    Makes sure that the data stored in self.command_lines is archived to CAS,
    and the digest of the file containing this information is stored in
    self.swarming_command_lines_digest.
    """
    if self.command_lines_file_digest:
      return self

    variant_digests = dict(self.command_line_variant_digests)
    for variant, command_lines in self.command_line_variants.items():
      if variant not in variant_digests:
        variant_digests[variant] = chromium_tests_api.archive_command_lines(
          command_lines, suffix=variant
        )

    return attr.evolve(
      self,
      command_lines_file_digest=(
        chromium_tests_api.archive_command_lines(self.command_lines)
      ),
      command_line_variant_digests=variant_digests,
    )

  def as_trigger_prop(self):
    """Gets the set of properties needed to trigger a child build.

    Builds depending on the tests compiled and isolated in this build need to
    know the corresponding CAS digest for each isolate target, as well as
    the command line to run it.

    Command line information is passed between builders via CAS, since the
    command line information can be a few MB, which is too large for properties.

    This uses different field names than the actual field names on this object.
    This is because these are put into properties, where the names aren't in
    the context of this object. Also, current code relies on the names, so
    it's tricky to rename them.
    """
    props = {
      'swarm_hashes': dict(self.digest_by_isolate_name),
      'swarming_command_lines_digest': self.command_lines_file_digest,
      'swarming_command_lines_cwd': self.command_lines_cwd,
    }
    if self.command_line_variant_digests:
      props['swarming_command_lines_variant_digests'] = dict(
        self.command_line_variant_digests
      )
    if self.test_trigger_deps_digest:
      props['test_trigger_deps_digest'] = self.test_trigger_deps_digest
    return props


@attrs()
class Task:
  """Represents the configuration for build/test tasks."""

  # BuilderConfig of the task runner bot.
  builder_config = attrib(ctbc.BuilderConfig)

  # A list of Test objects [see chromium_tests/steps.py]. Stateful objects
  # that can run tests [possibly remotely via swarming] and parse the
  # results. Running tests multiple times is not idempotent the
  # results of previous runs affect future runs.
  test_suites = attrib(sequence[steps.AbstractTest])

  # Holds state on build properties. Used to pass state between methods.
  update_result = attrib(checkout_result.CheckoutResult)

  # The path to the directory containing built outputs.
  build_dir = attrib(Path)

  # A list of paths (strings) affected by the CL, relative to source_dir.
  affected_files = attrib(sequence[str])

  # How to execute each test in 'tests' which runs on swarming.
  swarming_execution_info = attrib(SwarmingExecutionInfo, default=None)

  @property
  def should_retry_failures_with_changes(self):
    return self.builder_config.retry_failed_shards

  @property
  def checkout_dir(self):
    return self.update_result.checkout_dir

  @property
  def source_dir(self):
    return self.update_result.source_root.path


@attrs()
class CompileOutput:
  """Output from the compiling."""

  # A list of Test objects [see chromium_tests/steps.py] that will be isolated
  # on remote swarming or locally.
  isolated_tests = attrib(sequence[steps.AbstractTest])

  # If the execution information should be exposed as build properties.
  expose_to_properties = attrib(bool, default=False)

  # A list of skylab Test objects [see chromium_tests/steps.py] that will be
  # uploaded to GCS server.
  skylab_isolate_tests = attrib(sequence[steps.AbstractTest])


class ChromiumTestsApi(recipe_api.RecipeApi):
  # These are defined in //infra/config/lib/try.star in chromium/src.
  MEGA_CQ_MODE_NAMES = ('CQ_MODE_MEGA_DRY_RUN', 'CQ_MODE_MEGA_FULL_RUN')

  Task = Task

  def __init__(self, input_properties, **kwargs):
    super().__init__(**kwargs)

    self.filter_files_dir = None
    self.base_variant_getter = lambda _: {
      'builder': self.m.buildbucket.builder_name
    }

    self._enable_snoopy = input_properties.enable_snoopy

    self._enabled_ci_only_tests_enabled_by_builder = None

  def initialize(self):
    # TODO: crbug.com/1421068 - Once parent relationship is propagated through
    # scheduler, this can be removed
    parent_build_id = self.m.properties.get('parent_build_id')
    if parent_build_id is not None:
      result = self.m.step.empty('parent build link')
      result.presentation.links['parent build'] = (
        f'https://ci.chromium.org/ui/b/{parent_build_id}'
      )

  def log(self, message):
    presentation = self.m.step.active_result.presentation
    presentation.logs.setdefault('stdout', []).append(message)

  def configure_build(
    self,
    builder_config: ctbc.BuilderConfig,
    test_only: bool = False,
    report_target_platform: bool = True,
  ):
    """Configure the modules that will be used by chromium_tests code.

    Args:
      builder_config: The BuilderConfig instance that defines the
        configuration to use for the various modules.
      test_only: Whether or not the builder is just triggering tests.
        If the builder is not performing compilation, then some
        inapplicable validation is disabled. By default, the compilation
        validation is skipped only if the builder config's
        execution_mode is TEST.
      report_target_platform: Whether or not reporting the "target_platform"
        to the build output properties.
    """
    # Configure chromium module
    test_only = test_only or builder_config.execution_mode == ctbc.TEST
    self.m.chromium.set_config(
      builder_config.chromium_config,
      TEST_ONLY=test_only,
      **builder_config.chromium_config_kwargs,
    )
    for c in builder_config.chromium_apply_config:
      self.m.chromium.apply_config(c)

    # TODO(crbug.com/356461014): Switch to ResultDB after its RPC call supports
    # updating invocation-level tags.
    if report_target_platform:
      step_result = self.m.step.empty('Report target_platform')
      step_result.presentation.properties['target_platform'] = (
        self.m.chromium.c.TARGET_PLATFORM
      )

    # Configure gclient module
    self.m.gclient.set_config(builder_config.gclient_config)
    for c in builder_config.gclient_apply_config:
      self.m.gclient.apply_config(c)

    if (
      self.m.chromium.c.TARGET_CROS_BOARDS
      or self.m.chromium.c.CROS_BOARDS_WITH_QEMU_IMAGES
    ):
      gclient_solution = self.m.gclient.c.solutions[0]
      if self.m.chromium.c.CROS_BOARDS_WITH_QEMU_IMAGES:
        gclient_solution.custom_vars['cros_boards_with_qemu_images'] = (
          self.m.chromium.c.CROS_BOARDS_WITH_QEMU_IMAGES
        )
      if self.m.chromium.c.TARGET_CROS_BOARDS:
        gclient_solution.custom_vars['cros_boards'] = (
          self.m.chromium.c.TARGET_CROS_BOARDS
        )

    # Configure chromium_android module
    if builder_config.android_config:
      self.m.chromium_android.set_config(builder_config.android_config)
      for c in builder_config.android_apply_config:
        self.m.chromium_android.apply_config(c)

  _COMPUTE_PRECOMMIT_DETAILS = object()

  def get_targets_spec_dir(
    self,
    source_dir: Path,
    builder_config: ctbc.BuilderConfig,
  ) -> Path:
    if builder_config.targets_spec_directory:
      return source_dir / builder_config.targets_spec_directory
    return self.m.chromium.targets_spec_dir(source_dir)

  def check_builder_cache(self, checkout_dir: Path):
    """Displays a step if the builder cache is present on the machine.
    Also records the same info in an output property.

    Args:
      checkout_dir: The directory where the checkout was performed.
    """
    with self.m.step.nest('builder cache') as presentation:
      contents = self.m.file.listdir('check if empty', checkout_dir)
      is_cached = bool(contents)
      presentation.properties['is_cached'] = is_cached
      if is_cached:
        presentation.step_text = (
          'builder cache is present, '
          'build may or may not be fast depending on state of cache'
        )
      else:
        presentation.step_text = 'builder cache is absent, expect a slow build'

  _DOC_REF = (
    'see Options section in //docs/infra/cq.md'
    ' in chromium/src for more information'
  )

  def get_footer_enabled_ci_only_tests(self) -> Mapping[str, Collection[str]]:
    """Compute the ci_only tests that are enabled by a footer.

    Returns:
      A mapping with the string chromium builder ID (builder-group:builder) as
      keys and the collection of tests enabled for the corresponding CI builder
      as the values. A wildcard can appear as either a builder ID or a test. A
      wildcard for builder indicates the corresponding tests are enabled for all
      builders. A wildcard for a test indicates all tests are enabled for the
      corresponding builder.
    """
    if self._enabled_ci_only_tests_enabled_by_builder is None:
      footer_vals = self.m.tryserver.get_footer(steps.INCLUDE_CI_FOOTER)
      enabled_tests_by_builder = {}

      for f in footer_vals:
        if f.lower() == 'true':
          enabled_tests_by_builder = {'*': {'*'}}
          break

        footer_pieces = f.split('|')
        if len(footer_pieces) != 2:
          raise self.m.step.StepFailure(
            f"invalid format for {steps.INCLUDE_CI_FOOTER} footer: '{f}',"
            f' {self._DOC_REF}'
          )
        builders = footer_pieces[0].split(',')
        tests = footer_pieces[1].split(',')
        for b in builders:
          if b != '*' and len(b.split(':')) != 2:
            raise self.m.step.StepFailure(
              f"invalid format for builder '{b}'"
              f' in {steps.INCLUDE_CI_FOOTER} footer, {self._DOC_REF}'
            )
          enabled_tests_by_builder.setdefault(b, set()).update(tests)

      self._enabled_ci_only_tests_enabled_by_builder = freeze(
        enabled_tests_by_builder
      )

    return self._enabled_ci_only_tests_enabled_by_builder

  def create_targets_config(
    self,
    builder_config,
    got_revisions,
    source_dir,
    build_dir,
    *,
    targets_spec_dir: Path | None = None,
    checkout_dir: Path | None = None,
    precommit_details=_COMPUTE_PRECOMMIT_DETAILS,
    scripts_compile_targets_fn=None,
    remote_tests_only=False,
    force_experimental_tests=False,
  ):
    """
    Args:
      builder_config (BuilderConfig): config for the current builder
      got_revisions (dict): revisions checked out for src and other deps.
        Usually stored in the bot_update step presentation properties.
      source_dir: The path to the top-level repo.
      build_dir: Path to the build dir.
      targets_spec_dir: Path to directory containing targets specs. If
        this is None, checkout_dir must be non-None and
        get_targets_spec_dir will be called.
      checkout_dir: The directory where the checkout was performed.
      precommit_details: Details used for the pre-commit-specific
        behavior when generating tests. If None, then the generation
        will use the non-pre-commit behavior. By default, the
        pre-commit-specific behavior will be used if there is a CL for
        the build, with the footers being taken from the CL description.
      scripts_compile_targets_fn: A callable taking no arguments that
        returns a mapping that maps from the script basename to a list
        of compile targets for the script for all supported scripts.
        Results will be memoized, so it will only be called a single
        time.
      remote_tests_only (bool): only include targets for remote tests
      force_experimental_tests (bool): If True, treats any experimental test
        as if it was a normal non-experimental test. Should be used sparingly.

    Returns: TargetsConfig for current builder
    """
    if not targets_spec_dir:
      assert checkout_dir, (
        'either checkout_dir or targets_spec_dir must be passed'
      )
      targets_spec_dir = self.get_targets_spec_dir(source_dir, builder_config)

    # The scripts_compile_targets is indirected through a function so that we
    # don't execute unnecessary steps if there are no scripts that need to be
    # run
    # Memoize the call to get_compile_targets_for_scripts so that we only
    # execute the step once
    scripts_compile_targets = None
    scripts_compile_targets_fn = (
      scripts_compile_targets_fn or self.get_compile_targets_for_scripts
    )

    test_names_to_skip = self.find_suites_to_skip()

    def memoized_scripts_compile_targets_fn():
      nonlocal scripts_compile_targets
      if scripts_compile_targets is None:
        scripts_compile_targets = scripts_compile_targets_fn(
          source_dir, build_dir
        )
      return scripts_compile_targets

    targets_specs_by_builder_by_group = {}
    for group, spec_file in sorted(builder_config.targets_spec_files.items()):
      targets_specs_by_builder_by_group[group] = self.read_targets_spec(
        spec_file, targets_spec_dir=targets_spec_dir
      )
    if precommit_details is self._COMPUTE_PRECOMMIT_DETAILS:
      precommit_details = None
      if self.m.tryserver.is_tryserver:
        precommit_details = generators.PrecommitDetails(
          footers=self.m.tryserver.get_footers()
        )

    generator = generators.Generator(
      self,
      got_revisions,
      source_dir,
      remote_tests_only,
      precommit_details,
      memoized_scripts_compile_targets_fn,
      force_experimental_tests=force_experimental_tests,
    )

    targets_by_builder_id = {}
    for builder_id in builder_config.builder_ids_in_scope_for_testing:
      targets_spec = targets_specs_by_builder_by_group[builder_id.group].get(
        builder_id.builder, {}
      )
      tests = self._generate_tests_from_targets_spec(
        generator,
        builder_id.group,
        builder_id.builder,
        targets_spec,
        test_names_to_skip,
      )
      additional_compile_targets = targets_spec.get(
        'additional_compile_targets', []
      )
      targets_by_builder_id[builder_id] = targets_config_module.Targets(
        tests=tests,
        additional_compile_targets=sorted(additional_compile_targets),
      )

    return targets_config_module.TargetsConfig.create(
      builder_config=builder_config,
      targets_by_builder_id=targets_by_builder_id,
      skip_tests=test_names_to_skip,
    )

  def prepare_checkout(
    self,
    builder_config: ctbc.BuilderConfig,
    report_cache_state: bool = True,
    set_output_commit: bool = True,
    root_solution_revision: str | None = None,
    runhooks_suffix: str | None = None,
    *,
    turboci_source_check_id: str = '',
    **kwargs,
  ) -> tuple[
    checkout_result.CheckoutResult, Path, targets_config_module.TargetsConfig
  ]:
    """Perform the checkout to enable testing.

    Args:
      builder_config: The configuration for the builder being executed.
      report_cache_state: Whether to check and report the builder cache state.
      set_output_commit: Whether to set the output commit in Buildbucket.
      root_solution_revision: Revision to checkout for the root solution.
      runhooks_suffix: Suffix for gclient runhooks step name.
      turboci_source_check_id: The ID of the TurboCI source check to
        create for the checkout.
      **kwargs: Keyword arguments to forward to
        `chromium_checkout.ensure_checkout`. Ignored if
        `builder_config.use_test_trigger_cas` is True.

    Returns:
      A tuple containing:
      * The CheckoutResult object describing the checkout.
      * The path to the build directory that was readied.
      * The configuration for the builder's targets.
    """
    checkout_dir = self.m.chromium_checkout.default_checkout_dir

    if builder_config.use_test_trigger_cas:
      digest = self.m.properties.get('test_trigger_deps_digest')
      if not digest:
        raise self.m.step.StepFailure(
          'use_test_trigger_cas is True but test_trigger_deps_digest '
          'property is missing'
        )

      # Clean checkout_dir to avoid stale files from previous builds.
      self.m.file.rmtree('clean test trigger deps directory', checkout_dir)
      self.m.file.ensure_directory(
        'ensure test trigger deps directory', checkout_dir
      )

      # Determine source_dir from gclient config if available, default to 'src'.
      solution_name = 'src'
      if self.m.gclient.c and self.m.gclient.c.solutions:
        solution_name = self.m.gclient.c.solutions[0].name
      source_dir = checkout_dir / solution_name

      self.m.chromium_checkout.set_paths(checkout_dir, source_dir)

      self.m.cas.download('download test trigger CAS', digest, source_dir)

      update_result = checkout_result.CasCheckoutResult(
        checkout_dir, source_dir, self.m.properties, solution_name
      )
      self.m.chromium.set_build_properties(update_result.properties)
      build_dir = self.m.chromium.default_build_dir(source_dir)

      self.m.code_coverage.source_dir = source_dir
      self.m.code_coverage.build_dir = build_dir
      self.m.profiles.source_dir = source_dir

    else:
      if report_cache_state:
        self.check_builder_cache(checkout_dir)

      # The root_solution_revision input property can be used to checkout
      # the root solution at a certain branch. This can be used when attempting
      # to run a builder for a child repository on a certain branch,
      # and the same branch needs to be checked out for the root solution
      root_solution_revision = root_solution_revision or self.m.properties.get(
        'root_solution_revision'
      )
      real_result = self.m.chromium_checkout.ensure_checkout(
        clobber=builder_config.clobber,
        set_output_commit=set_output_commit,
        root_solution_revision=root_solution_revision,
        turboci_check_id=turboci_source_check_id,
        no_history=builder_config.no_history,
        shallow=builder_config.shallow,
        **kwargs,
      )
      update_result = checkout_result.BotUpdateResultAdapter(real_result)
      source_dir = update_result.source_root.path
      build_dir = self.m.chromium.default_build_dir(source_dir)
      self.m.code_coverage.source_dir = source_dir
      self.m.code_coverage.build_dir = build_dir
      self.m.profiles.source_dir = source_dir

      # Installs toolchains configured in the current bot, if any.
      self.m.chromium.ensure_toolchains(checkout_dir)

      # For some reason, we treat the runhooks step as special and support a
      # suffix (automatically using 'without patch' for try builders), even
      # though we don't add a suffix to bot_update. This is legacy behavior and
      # who knows what queries depend on it.
      runhooks_kwargs = {}
      if runhooks_suffix:
        runhooks_kwargs['name'] = f'runhooks ({runhooks_suffix})'
      elif self.m.tryserver.is_tryserver:
        runhooks_kwargs['name'] = 'runhooks (with patch)'
      self.m.chromium.runhooks(source_dir, build_dir, **runhooks_kwargs)

    targets_config = self.create_targets_config(
      builder_config,
      update_result.properties,
      source_dir,
      build_dir,
      checkout_dir=update_result.checkout_dir,
    )

    return update_result, build_dir, targets_config

  def _generate_tests_from_targets_spec(
    self,
    generator: generators.Generator,
    builder_group: str,
    builder: str,
    targets_spec: generators.TargetsSpec,
    test_names_to_skip: Iterable[str] = (),
  ) -> Iterable[steps.AbstractTest]:
    test_specs = list(
      generator.generate(
        builder_group, builder, targets_spec, test_names_to_skip
      )
    )
    return tuple(spec.get_test(self) for spec in test_specs)

  def read_targets_spec(self, targets_spec_file: str, targets_spec_dir: Path):
    targets_spec_path = targets_spec_dir / targets_spec_file
    targets_spec = self.m.file.read_json(
      'read test spec (%s)' % self.m.path.basename(targets_spec_path),
      targets_spec_path,
      test_data={},
    )

    self.m.step.active_result.presentation.step_text = (
      'path: %s' % targets_spec_path
    )

    return targets_spec

  def create_test_runner(
    self,
    checkout_dir: Path,
    source_dir: Path,
    build_dir: Path,
    tests,
    *,
    suffix='',
    serialize_tests=False,
    retry_failed_shards=False,
    retry_invalid_shards=False,
    surface_invalid_results_as_infra_failure=False,
    include_utr_instruction=False,
  ):
    """Creates a test runner to run a set of tests.

    Args
      checkout_dir: The directory where the checkout was performed.
      source_dir: The path to the top-level repo.
      build_dir: Path to the build dir.
      tests: List of step.Test objects to be run.
      suffix: Suffix to be passed when running the tests.
      serialize_tests: True if this bot should run all tests serially
        (specifically, tests run on Swarming). Used to reduce the load
        generated by waterfall bots.
      retry_failed_shards: If true, retry swarming tests that fail. See
        run_tests documentation in test_utils module.
      retry_invalid_shards: If true, retry swarming tests with no valid results,
        See run_tests documentation in test_utils module.
      surface_invalid_results_as_infra_failure: If true, an infra failure will
        be returned when all the failed tests have invalid results.
      include_utr_instruction: Whether or not to include UTR in reproduction
          instructions

    Returns:
      A function that can be passed to setup_chromium_tests or run directly.

    """

    def test_runner():
      if serialize_tests:
        tests_list = [[t] for t in tests]
      else:
        tests_list = [tests]

      all_failed_tests = set()
      retry_success_tests = set()
      for tl in tests_list:
        invalid_tests, failed_tests = self.m.test_utils.run_tests(
          checkout_dir,
          source_dir,
          build_dir,
          tl,
          suffix,
          retry_failed_shards=retry_failed_shards,
          retry_invalid_shards=retry_invalid_shards,
          include_utr_instruction=include_utr_instruction,
        )
        all_failed_tests = all_failed_tests.union(failed_tests, invalid_tests)
        for test in tl:
          if test not in failed_tests:
            retry_suffix = self.m.test_utils.prepend_retry_shards(suffix)
            if test.get_status(retry_suffix) == steps.SUCCESS_SUITE_STATUS:
              retry_success_tests.add(test)
      self.m.chromium_swarming.report_stats()
      if all_failed_tests:
        status = self.determine_build_status_from_tests(
          all_failed_tests, suffix
        )
        if surface_invalid_results_as_infra_failure and all(
          not t.has_valid_results(suffix) for t in all_failed_tests
        ):
          status = common_pb.INFRA_FAILURE
        return result_pb2.RawResult(
          status=status,
          summary_markdown=self.format_unrecoverable_failures(
            all_failed_tests, suffix
          ),
        )
      # If we don't have any failed tests, surface any successful retried test suites to
      # markdown summary
      if retry_success_tests:
        return result_pb2.RawResult(
          status=common_pb.SUCCESS,
          summary_markdown=self.format_success_retry_tests(retry_success_tests),
        )

    return test_runner

  _ARCHITECTURE_DIGIT_MAP = {
    ('arm', 32): 0,
    ('arm', 64): 5,
    ('intel', 32): 1,
    ('intel', 64): 6,
    ('mips', 32): 2,
  }

  def get_android_version_details(
    self,
    source_dir: Path,
    version_file,
    *,
    log_details=False,
  ):
    if not version_file:
      return None, None

    version = self.m.chromium.get_version_from_file(source_dir / version_file)

    chromium_config = self.m.chromium.c
    arch_id = chromium_config.TARGET_ARCH, chromium_config.TARGET_BITS
    arch_digit = self._ARCHITECTURE_DIGIT_MAP.get(arch_id, None)
    assert arch_digit is not None, (
      f'Architecture and bits ({arch_id!r})'
      ' does not have a version digit assigned'
    )

    android_version_name = '%(MAJOR)s.%(MINOR)s.%(BUILD)s.%(PATCH)s' % version
    android_version_code = '%d%03d%d0' % (
      int(version['BUILD']),
      int(version['PATCH']),
      arch_digit,
    )
    if log_details:
      self.log('version:%s' % version)
      self.log('android_version_name:%s' % android_version_name)
      self.log('android_version_code:%s' % android_version_code)
    return android_version_name, android_version_code

  def compile_specific_targets(
    self,
    build_dir: Path,
    builder_id,
    builder_config,
    update_result: bot_update.Result,
    targets_config,
    compile_targets,
    tests,
    *,
    mb_phase=None,
    mb_config_path=None,
    mb_recursive_lookup=True,
    mb_write_ide_json=False,
    override_execution_mode=None,
    include_utr_instruction=False,
    affected_files=None,
  ):
    """Runs compile and related steps for given builder.

    Allows finer-grained control about exact compile targets used.

    Args:
      build_dir: The path to the directory containing built outputs.
      builder_id: A BuilderId identifying the configuration to use when running
        mb.
      builder_config: The configuration for the builder being executed.
      update_result: The result from the checkout.
      targets_config: The configuration of the current build.
      compile_targets: The list of targets to compile.
      tests: The list of tests to be built for this builder. The tests may or
        may not be executed by the builder and may be executed by another
        builder that is triggered.
      mb_phase: A phase argument to be passed to mb. Must be provided if the
        configuration identified by `builder_id` uses phases and must not be
        provided if the configuration identified by `builder_id` does not use
        phases.
      mb_config_path: An optional override specifying the file where mb will
        read configurations from.
      mb_recursive_lookup: A boolean indicating whether the lookup operation
        should recursively expand any included files. If False, then the lookup
        output will contain the include statement.
      mb_write_ide_json: A boolean indicating if mb should have gn generate
        a large JSON file containing target information for the project.
      override_execution_mode: An optional override to change the execution
        mode.
      include_utr_instruction: Whether or not to include UTR reproduction
        instructions.
      affected_files: List of paths to files affected by the current change.

    Returns:
      A tuple of:
        A RawResult object with compile step status, or None if the execution
          mode is not COMPILE_AND_TEST.
        CompileOutput containing the tests to be isolated (swarming and skylab),
          or None if compile failed or execution mode is not COMPILE_AND_TEST.
    """

    assert isinstance(targets_config, targets_config_module.TargetsConfig), (
      "targets_config argument %r was not a TargetsConfig" % targets_config
    )
    execution_mode = override_execution_mode or builder_config.execution_mode

    child_builder_ids = builder_config.builder_db.builder_graph.get(
      builder_id, set()
    )
    any_child_use_test_trigger_cas = any(
      builder_config.builder_db[child_id].use_test_trigger_cas
      for child_id in child_builder_ids
    )
    expose_to_properties = (
      builder_config.expose_trigger_properties or any_child_use_test_trigger_cas
    )

    source_dir = update_result.source_root.path

    if self.m.chromium.c.TARGET_PLATFORM == 'android':
      self.m.chromium_android.clean_local_files(source_dir)
      # The underlying script call here relies on a git checkout, which is not
      # present if using the test trigger CAS codepath.
      if not builder_config.use_test_trigger_cas:
        self.m.chromium_android.run_tree_truth(update_result)

    if execution_mode != ctbc.COMPILE_AND_TEST:
      return None, None

    isolated_tests = [t for t in tests if t.uses_isolate and t.is_enabled]
    # Skylab tests pretend to be isolated_tests at run_mb_and_compile step,
    # for generating the runtime deps. We upload the deps to GCS instead of
    # isolate server, because skylab DUT does not support isolate.
    skylab_isolate_tests = [
      t
      for t in tests
      # Skylab test has different runner script and dependencies. A skylab
      # test should not appear in isolated_tests.
      if t.runs_on_skylab and not t in isolated_tests and t.is_enabled
    ]

    name_suffix = ' (with patch)' if self.m.tryserver.is_tryserver else ''

    android_version_name, android_version_code = (
      self.get_android_version_details(
        source_dir, builder_config.android_version, log_details=True
      )
    )

    # Compile the src side deps so it is ready to be uploaded to CAS.
    # This is only useful when uploading test isolate to be executed in
    # a different srcless builder.
    if isolated_tests and expose_to_properties:
      compile_targets = sorted(
        set(compile_targets) | {TEST_TRIGGER_AND_COLLECT_DEPS_TARGET}
      )

    raw_result = self.run_mb_and_compile(
      source_dir,
      build_dir,
      builder_id,
      compile_targets,
      (
        [t.isolate_target for t in isolated_tests]
        + [t.target_name for t in skylab_isolate_tests]
      ),
      name_suffix=name_suffix,
      mb_phase=mb_phase,
      mb_config_path=mb_config_path,
      mb_recursive_lookup=mb_recursive_lookup,
      mb_write_ide_json=mb_write_ide_json,
      android_version_code=android_version_code,
      android_version_name=android_version_name,
      include_utr_instruction=include_utr_instruction,
    )

    if raw_result.status != common_pb.SUCCESS:
      self.m.tryserver.set_compile_failure_tryjob_result()
      return raw_result, None

    self.m.chromium_rts.generate_filter_files(
      source_dir,
      build_dir,
      affected_files=affected_files if affected_files is not None else [],
      tests=tests,
    )

    return raw_result, CompileOutput(
      isolated_tests=isolated_tests,
      expose_to_properties=expose_to_properties,
      skylab_isolate_tests=skylab_isolate_tests,
    )

  def isolate_test_targets(
    self,
    source_dir: Path,
    build_dir: Path,
    builder_config,
    update_result: bot_update.Result,
    compile_output: CompileOutput,
    *,
    suffix=None,
    isolate_output_files_for_coverage=False,
    swarm_hashes_property_name='',
  ):
    """Perform the isolation for the compiled test targets.

    Args:
      source_dir: The path to the top-level repo.
      build_dir: The path to the build directory.
      builder_config: The BuilderConfig object.
      update_result: The bot_update result.
      compile_output: The output from previous compile step.
      suffix: Suffix for step names (defaults to 'with patch' if tryserver
        else '').
      isolate_output_files_for_coverage: Whether to isolate output files for
        code coverage.
      swarm_hashes_property_name: The property name to output swarming hashes
        into.

    Returns:
      * An instance of step_data.StepData from isolate_tests indicating the
        isolation result, or None when there are no targets to isolate.
      * SwarmingExecutionInfo describing how to execute the isolated tests.
    """
    if suffix is None:
      suffix = 'with patch' if self.m.tryserver.is_tryserver else ''

    isolate_result = None
    execution_info = None
    if compile_output.isolated_tests:
      additional_isolate_targets = []
      if isolate_output_files_for_coverage:
        file_paths = self.m.code_coverage.get_required_build_output_files(
          compile_output.isolated_tests
        )

        self.m.isolate.write_isolate_files_for_binary_file_paths(
          file_paths, ALL_TEST_BINARIES_ISOLATE_NAME, source_dir, build_dir
        )

        additional_isolate_targets.append(ALL_TEST_BINARIES_ISOLATE_NAME)

      isolate_result, execution_info = self.isolate_tests(
        source_dir,
        build_dir,
        builder_config,
        compile_output.isolated_tests,
        suffix,
        update_result.properties.get('got_revision_cp'),
        swarm_hashes_property_name=swarm_hashes_property_name,
        additional_isolate_targets=additional_isolate_targets,
        expose_to_properties=compile_output.expose_to_properties,
      )

      if builder_config.perf_isolate_upload:
        instance = self.m.cas.instance
        repo = (
          self.m.buildbucket.build.input.gitiles_commit.project or 'chromium'
        )
        git_hash = (
          self.m.buildbucket.build.input.gitiles_commit.id
          or update_result.properties['got_revision']
        )
        self.m.perf_dashboard.upload_isolate(
          self.m.buildbucket.builder_name,
          self.m.perf_dashboard.get_change_info(
            [
              {
                'repository': REPOSITORY_MAPPING.get(repo),
                'git_hash': git_hash,
              }
            ]
          ),
          instance,
          self.m.isolate.isolated_tests,
        )

    if compile_output.skylab_isolate_tests:
      self.prepare_artifact_for_skylab(
        builder_config,
        update_result.checkout_dir,
        source_dir,
        build_dir,
        compile_output.skylab_isolate_tests,
        phase=suffix or 'with patch',
      )

    return isolate_result, execution_info

  def find_swarming_command_lines(self, suffix, build_dir: Path):

    script = self.m.chromium_tests.resource('find_command_lines.py')
    args = ['--build-dir', build_dir, '--output-json', self.m.json.output()]

    step_name = 'find command lines%s' % suffix

    step_result = self.m.step(
      step_name,
      ['python3', '-u', script] + args,
      step_test_data=lambda: self.m.json.test_api.output({}),
    )
    assert isinstance(step_result.json.output, dict)

    return step_result.json.output

  def isolate_tests(
    self,
    source_dir: Path,
    build_dir: Path,
    builder_config,
    tests,
    suffix,
    got_revision_cp,
    *,
    swarm_hashes_property_name='',
    additional_isolate_targets=None,
    expose_to_properties=False,
  ):
    """Uploads prepared isolated tests.

    This also updates the test objects with the commands which are generated
    when we create the isolates for the tests. See
    set_swarming_test_execution_info for its potential side effects.

    Args:
      * source_dir: The path to the top-level repo.
      * build_dir: A Path to the build directory to create the isolates
      * builder_config: The builder we're isolating tests for.
      * tests: A list of Test objects we should isolate. The isolate_target
        attribute for each test will be isolated.
      * suffix: A suffix to add to each step.
      * got_revision_cp: The commit position for the main repository we're
        checking out. If this is set, swarm_hashes_property_name is changed
        to include the revision, as well as if we're running tests with a
        patch applied.
      * swarm_hashes_property_name: The property name to output swarming hashes
        into.
      * additional_isolate_targets: Any additional isolate targets which aren't
        already included in 'tests'.

    Returns:
      * An instance of step_data.StepData indicating the isolation result,
        or None when there are no targets to isolate.
      * SwarmingExecutionInfo describing how to execute the isolate tests in the
        tests input.
    """
    if got_revision_cp:
      # Some recipes such as Findit's may build different revisions in the
      # same build. Hence including the commit position as part of the
      # property name.
      swarm_hashes_property_name = 'swarm_hashes_%s_%s' % (
        got_revision_cp.replace(
          # At sign may clash with annotations format.
          '@',
          '(at)',
        ),
        # We include without_patch when there's no suffix because existing
        # builders and systems do this, and we don't want to break anything
        # which depends on that in the property name.
        suffix.replace(' ', '_') if suffix else 'without_patch',
      )
    targets = list({t.isolate_target for t in tests})
    if additional_isolate_targets:
      targets.extend(additional_isolate_targets)

    # These functions append suffix to step names, but expect it to be wrapped
    # in parentheses, if it exists. Suffix currently is something like 'with
    # patch', with no parentheses, or ''. Wrap it in parens if needed.
    name_suffix = ' (%s)' % suffix if suffix else ''
    # When Siso build enables `without bytes` option, `isolate tests` step
    # needs to use `siso isolate` command.
    use_siso_isolate = self.m.siso.enabled and self.m.siso.without_bytes

    self.m.chromium_rts.isolate_filter_files(build_dir, tests)

    isolate_result = self.m.isolate.isolate_tests(
      build_dir,
      targets,
      suffix=name_suffix,
      swarm_hashes_property_name=swarm_hashes_property_name,
      source_dir=source_dir,
      verbose=True,
      use_siso_isolate=use_siso_isolate,
    )

    command_lines = self.find_swarming_command_lines(name_suffix, build_dir)
    command_line_variants = self.m.chromium_rts.get_command_line_variants(
      build_dir, command_lines, tests
    )

    execution_info = self.set_swarming_test_execution_info(
      source_dir,
      build_dir,
      tests,
      command_lines,
      self.m.path.relpath(build_dir, source_dir),
      expose_to_properties=expose_to_properties,
      builder_config=builder_config,
      command_line_variants=command_line_variants,
    )

    return isolate_result, execution_info

  def set_swarming_test_execution_info(
    self,
    source_dir: Path,
    build_dir: Path,
    tests,
    command_lines,
    rel_cwd,
    *,
    expose_to_properties=False,
    builder_config=None,
    command_line_variants=None,
  ):
    """Sets the execution information for a list of swarming tests.

    Each test gets the command line in 'command_lines' corresponding to
    the test's 'target_name', as well as the relative working directory to run
    the command line from.

    It also optionally exposes the execution information as build properties,
    which can be used by other processes to use the results of this build.

    Args:
      * source_dir: The path to the top-level repo.
      * build_dir: The path to the directory containing built outputs.
      * tests: The list of tests to set command lines for.
      * command_lines: A dict mapping target name to a list of strings that
        represent the command line invocation to execute that test.
      * rel_cwd: The relative path to the current working directory for a
        Chromium checkout.
      * expose_to_properties: If we should expose the execution information as
        build properties.

    Returns:
      An instance of SwarmingExecutionInfo which describes how to execute these
      tests.
    """
    for test in tests:
      if test.runs_on_swarming or test.uses_isolate:
        command_line = command_lines.get(test.target_name, [])

        if command_line:
          test.raw_cmd = command_line
          test.relative_cwd = rel_cwd

        self.m.chromium_rts.set_swarming_test_execution_info(
          test, command_line_variants
        )

    execution_info = SwarmingExecutionInfo(
      digest_by_isolate_name=self.m.isolate.isolated_tests,
      command_lines=command_lines,
      command_lines_cwd=rel_cwd,
      command_line_variants=command_line_variants or {},
    )

    if expose_to_properties:
      execution_info = execution_info.ensure_command_lines_archived(self)
      digest = self._archive_test_trigger_deps_digest(
        source_dir, build_dir, builder_config=builder_config
      )
      execution_info = attr.evolve(
        execution_info, test_trigger_deps_digest=digest or ''
      )
      trigger_properties = execution_info.as_trigger_prop()

      step_result = self.m.step.empty('expose execution properties')
      step_result.presentation.properties['trigger_properties'] = (
        trigger_properties
      )

    return execution_info

  def archive_build(
    self,
    build_dir: Path,
    update_result: bot_update.Result,
    *,
    enable_snoopy=False,
  ):
    """Archive the build if the bot is configured to do so.

    There are three types of builds that get archived: regular builds,
    clustefuzz builds, and generic archives.

    See api.archive.clusterfuzz_archive and archive_build.py for more
    information.

    This is currently used to store builds long-term and to transfer them
    to clusterfuzz.
    """
    # TODO(crbug.com/1138672) Move custom_vars to higher level of recipes.
    custom_vars = {}
    custom_vars['chrome_version'] = self._get_chrome_version()

    # The goal of generic archive is to eventually replace most of the custom
    # archive logic with InputProperties driven archiving.
    # https://crbug.com/1076679.
    upload_results = self.m.archive.generic_archive(
      update_result.checkout_dir,
      update_result.source_root.path,
      build_dir=build_dir,
      update_properties=update_result.properties,
      custom_vars=custom_vars,
      report_artifacts=enable_snoopy,
    )

    self.m.symupload(build_dir)
    return upload_results

  def archive_clusterfuzz(
    self,
    builder_id,
    update_result: bot_update.Result,
    builder_config,
    build_dir: Path,
    compile_targets: list[str] | None = None,
  ) -> None:
    """Archives the build for ClusterFuzz, if necessary.

    Checks if builder_spec specifies that this build should be archived for
    ClusterFuzz. Skips this step otherwise.

    If the ClusterFuzz archive schema version is non-zero, the resulting
    archive contains the exact set of all runtime deps of all the given
    `compile_targets`. Otherwise uses a set of heuristics to zip most of the
    contents of the build directory.
    """
    if (
      self.m.tryserver.is_tryserver
      and builder_id not in builder_config.builder_db
    ):
      # `builder_config.builder_db` contains a spec for the CI builder that the
      # trybot mirrors, if any, but no spec for the trybot itself. Get the CI
      # builder's ID from the list in `builder_config.builder_ids` to perform the
      # spec lookup instead.
      builder_id = builder_config.builder_ids[0]

    builder_spec = builder_config.builder_db[builder_id]
    if not builder_spec.cf_archive_build:
      return

    step_result = self.m.step.empty('archive clusterfuzz')

    source_dir = update_result.source_root.path
    common_kwargs = {
      'source_dir': source_dir,
      'update_properties': update_result.properties,
      'gs_bucket': builder_spec.cf_gs_bucket,
      'gs_acl': builder_spec.cf_gs_acl,
      'archive_path': builder_spec.cf_archive_path,
    }
    if builder_spec.cf_archive_schema_version == 0:
      archive_root = build_dir
      self.m.archive.clusterfuzz_archive(
        archive_root=archive_root,
        **common_kwargs,
      )
    else:
      step_result.presentation.logs['compile_targets'] = (
        compile_targets if compile_targets else ['no compile targets']
      )
      archive_root = source_dir
      # `clusterfuzz_archive_targets()` resolves GN runtime dependencies
      # before calling `clusterfuzz_archive()`, which requires the chromium
      # environment.
      with self.m.context(env=self.m.chromium.get_env(source_dir)):
        self.m.archive.clusterfuzz_archive_targets(
          archive_root=archive_root,
          build_dir=build_dir,
          compile_targets=compile_targets,
          archive_schema_version=builder_spec.cf_archive_schema_version,
          **common_kwargs,
        )

  def _get_chrome_version(self):
    chrome_version = self.m.properties.get('chrome_version')
    if not chrome_version:
      ref = self.m.buildbucket.gitiles_commit.ref
      if ref.startswith('refs/tags/'):
        chrome_version = str(ref[len('refs/tags/') :])
    return chrome_version

  def _get_affected_spec_files(
    self,
    source_dir: Path,
    affected_files: Iterable[str],
    builder_config: ctbc.BuilderConfig,
    targets_spec_dir: Path,
  ) -> set[str]:
    """Returns any files in the CL that affects the builder's testing specs.

    Args:
      source_dir: The path to the top level repo.
      affected_files: The paths to the files that are modified by the CL being
        tested, relative to source_dir.
      builder_config: A BuilderConfig object for the currently executing
        builder.
      targets_spec_dir: The directory containing the targets spec files for the
        builder.

    Returns:
      A set of absolute file paths of the targets spec files that are affected
      by the CL being tested.
    """
    absolute_affected_files = set(
      str(source_dir / f).replace('/', self.m.path.sep) for f in affected_files
    )
    absolute_spec_files = set(
      str(targets_spec_dir / f)
      for f in builder_config.targets_spec_files.values()
    )
    return absolute_spec_files & absolute_affected_files

  def _get_builders_to_trigger(self, builder_id, builder_config):
    """Get the builders to trigger.

    Args:
      * builder_id - The `BuilderId` identifying the builder to find the
        child builders for.
      * builder_config - The `BuilderConfig` associated with `builder_id`.

    Returns:
      A list of the builder names to trigger.
    """
    return sorted(
      set(
        b.builder for b in builder_config.builder_db.builder_graph[builder_id]
      )
    )

  def _trigger_led_builds(
    self,
    to_trigger: Iterable[str],
    commit: common_pb.GitilesCommit,
    properties: dict[str, object],
  ) -> None:
    """Trigger builders using led.

    Args:
      to_trigger: The name of builders to trigger.
      commit: The gitiles commit to set on the triggered builds.
      properties: Properties to set on the triggered builds.
    """
    property_args = []
    for k, v in properties.items():
      property_args.append('-p')
      property_args.append('{}={}'.format(k, self.m.json.dumps(v)))

    project = self.m.buildbucket.build.builder.project
    # If this is an led real build, then the bucket will be the shadow bucket
    # and getting a builder from the shadow bucket doesn't work, so get the
    # builder from the shadowed bucket
    bucket = (
      self.m.led.shadowed_bucket or self.m.buildbucket.build.builder.bucket
    )
    with self.m.step.nest('trigger') as trigger_presentation:
      # Clear out SWARMING_TASK_ID in the environment so that the created tasks
      # do not have a parent task ID. This allows the triggered tasks to outlive
      # the current task instead of being cancelled when the current task
      # completes.
      # TODO(https://crbug.com/1140621) Use command-line option instead of
      # changing environment.
      with self.m.context(env={'SWARMING_TASK_ID': None}):
        commit_url = f'https://{commit.host}/{commit.project}/+/{commit.id}'
        change_url = None
        if (change := self.m.tryserver.gerrit_change) is not None:
          change_url = (
            f'https://{change.host}/c/{change.project}'
            f'/+/{change.change}/{change.patchset}'
          )

        for child_builder in to_trigger:
          child_builder_name = '{}/{}/{}'.format(project, bucket, child_builder)
          with self.m.step.nest(child_builder_name):
            led_builder_id = 'luci.{}.{}:{}'.format(
              project, bucket, child_builder
            )
            led_job = self.m.led('get-builder', led_builder_id)
            led_job = led_job.then(
              'edit-gitiles-commit', '-ref', commit.ref, commit_url
            )
            if change_url:
              led_job = led_job.then('edit-gerrit-cl', change_url)
            led_job = self.m.led.inject_input_recipes(led_job)
            led_job = led_job.then('edit', *property_args)
            result = led_job.then('launch').launch_result

            child_link = result.build_url
            trigger_presentation.links[child_builder_name] = child_link

  def trigger_child_builds(
    self,
    builder_id,
    update_step,
    builder_config,
    additional_properties=None,
    commit=None,
    to_trigger: Iterable[str] | None = None,
  ):
    """Trigger builders that configure the current builder as parent.

    Args:
      * builder_id - The ID of the running builder. The
        `parent_builder_group` and `parent_buildername` properties will
        be set to refer to this builder.
      * update_step - The step result of the bot_update step. For each
        property in `update_step.presentation.properties` that starts
        with `got_`, the returned properties will contain a property
        with `parent_` prepended to the property and the same value. If
        `update_step.presentation.properties` contains a `got_revision`
        property, then the returned properties will have the `revision`
        property set to the same value. The `deps_revision_overrides`
        property will be set with a mapping to ensure that the triggered
        build checks out the same versions for the paths in
        `update_step.json.output['manifest']`.
      * builder_config - The configuration of the running builder.
      * additional_properties - Additional properties to set for the
        triggered builds. These properties will take precedence over
        properties computed from `builder_id` and `update_step`.
      * commit - The GitilesCommit message to set on the input of the
        triggered builds. If not provided,
        buildbucket.build.output.gitiles_commit will be used.
      * to_trigger - An iterable of builder names that should be triggered
        instead of predefined child builders as specified by builder_config.
    """
    with self.m.context(infra_steps=True):
      if not to_trigger:
        to_trigger = self._get_builders_to_trigger(builder_id, builder_config)
      if not to_trigger:
        return

      properties = self._get_trigger_properties(
        builder_id, update_step, additional_properties
      )

      if commit is None and self.m.buildbucket.build.output.HasField(
        'gitiles_commit'
      ):
        commit = self.m.buildbucket.build.output.gitiles_commit

      if commit is None:
        step_result = self.m.step('no commit for trigger', [])
        step_result.presentation.status = self.m.step.EXCEPTION
        step_result.presentation.step_text = '\n'.join(
          [
            'no commit was provided for trigger',
            'one of the following fixes should be made to the recipe:',
            '* pass `set_output_commit=True` to bot_update',
            "* pass `commit` to trigger_child_builds",
          ]
        )
        self.m.step.raise_on_failure(step_result)

      if self.m.led.launched_by_led:
        self._trigger_led_builds(to_trigger, commit, properties)
        return

      repo = 'https://{}/{}'.format(commit.host, commit.project)
      trigger = self.m.scheduler.GitilesTrigger(
        repo=repo,
        ref=commit.ref,
        revision=commit.id,
        properties=properties,
      )

      project = self.m.buildbucket.build.builder.project
      scheduler_triggers = [(trigger, project, to_trigger)]
      self.m.scheduler.emit_triggers(scheduler_triggers, step_name='trigger')

  def _get_trigger_properties(
    self,
    builder_id,
    update_result: bot_update.Result,
    additional_properties=None,
  ):
    """Get the properties used for triggering child builds.

    Arguments:
      * builder_id - The ID of the running builder. The
        `parent_builder_group` and `parent_buildername` properties will
        be set to refer to this builder.
      * update_step - The step result of the bot_update step. For each
        property in `update_step.presentation.properties` that starts
        with `got_`, the returned properties will contain a property
        with `parent_` prepended to the property and the same value. If
        `update_step.presentation.properties` contains a `got_revision`
        property, then the returned properties will have the `revision`
        property set to the same value. The `deps_revision_overrides`
        property will be set with a mapping to ensure that the triggered
        build checks out the same versions for the paths in
        `update_step.json.output['manifest']`.
      * additional_properties - Additional properties to set for the
        triggered builds. These properties will take precedence over
        properties computed from `builder_id` and `update_step`.

    Returns:
      A dict containing the properties to be set when triggering another
      builder.
    """
    # LUCI-Scheduler-based triggering (required on luci stack).
    properties = {
      'parent_builder_group': builder_id.group,
      'parent_buildername': builder_id.builder,
      # TODO: crbug.com/1421068 - Once parent relationship is propagated
      # through scheduler, this can be removed
      'parent_build_id': str(self.m.buildbucket.build.id),
    }
    for name, value in update_result.properties.items():
      if name.startswith('got_'):
        properties['parent_' + name] = value
    # Work around https://crbug.com/785462 in LUCI UI that ignores
    # buildset's revision and needs actual 'revision' property.
    if 'parent_got_revision' in properties:
      properties['revision'] = properties['parent_got_revision']

    properties['deps_revision_overrides'] = {
      path: update_result.manifest[path]['revision']
      for path in update_result.fixed_revisions
    }

    properties.update(self.m.repro_instructions.trigger_properties())

    properties.update(additional_properties or {})

    self.m.chromium_bootstrap.update_trigger_properties(properties)

    return properties

  def run_mb_and_compile(
    self,
    source_dir: Path,
    build_dir: Path,
    builder_id,
    compile_targets,
    isolated_targets,
    name_suffix,
    *,
    mb_phase=None,
    mb_config_path=None,
    mb_recursive_lookup=False,
    mb_write_ide_json=False,
    android_version_code=None,
    android_version_name=None,
    include_utr_instruction=False,
  ):
    with self.m.chromium.guard_compile(build_dir, suffix=name_suffix):
      _mb_gen = None
      if self.m.chromium.c.project_generator.tool == 'mb':

        def _mb_gen():
          return self.m.chromium.mb_gen(
            source_dir,
            build_dir,
            builder_id,
            phase=mb_phase,
            mb_config_path=mb_config_path,
            isolated_targets=isolated_targets,
            name='generate_build_files%s' % name_suffix,
            recursive_lookup=mb_recursive_lookup,
            write_ide_json=mb_write_ide_json,
            android_version_code=android_version_code,
            android_version_name=android_version_name,
          )

        _mb_gen()

      # run experimental dependency analysis for SSCI.
      if 'ssci.experimental' in self.m.buildbucket.build.input.experiments:
        with self.m.context(env=self.m.chromium.get_env(source_dir)):
          self.m.ssci.run(
            src_dir=source_dir,
            build_dir=build_dir,
            chrome_version=self._get_chrome_version(),
          )

      if (
        'chromium.enable_cleandead'
        in self.m.buildbucket.build.input.experiments
      ):
        try:
          self.m.chromium.cleandead(source_dir, build_dir)
        except self.m.step.StepFailure:
          self.m.file.rmtree('remove output dir' + name_suffix, build_dir)
          if _mb_gen:
            _mb_gen()
          clean_step_presentation = self.m.step.active_result.presentation
          clean_step_presentation.step_text = 'reason: cleandead unsuccessful'

      ret = self.m.chromium.compile(
        source_dir,
        build_dir,
        targets=compile_targets,
        name='compile%s' % name_suffix,
        include_utr_instruction=include_utr_instruction,
        builder_id=builder_id,
      )
      if include_utr_instruction:
        self.m.repro_instructions.update_invocation_instructions()
      return ret

  @contextlib.contextmanager
  def wrap_chromium_tests(
    self, checkout_dir: Path, source_dir: Path, build_dir: Path, *, tests=None
  ):
    with self.m.context(
      cwd=checkout_dir, env=self.m.chromium.get_env(source_dir)
    ):
      # Some recipes use this wrapper to setup devices and have their own way
      # to run tests. If platform is Android and tests is None, run device
      # steps.
      require_device_steps = tests is None or any(
        t.uses_local_devices for t in tests
      )

      if (
        self.m.chromium.c.TARGET_PLATFORM == 'android' and require_device_steps
      ):
        self.m.chromium_android.common_tests_setup_steps(source_dir)

      try:
        yield
      finally:
        if self.m.chromium.c.TARGET_PLATFORM == 'android':
          if require_device_steps:
            self.m.chromium_android.common_tests_final_steps(
              source_dir, build_dir, run_stackwalker=True
            )

  def deapply_patch(
    self,
    update_result: bot_update.Result,
    build_dir: Path,
    turboci_source_check_id: str = '',
  ) -> None:
    assert self.m.tryserver.is_tryserver

    with self.m.context(cwd=update_result.checkout_dir):
      self.m.bot_update.deapply_patch(
        update_result, turboci_check_id=turboci_source_check_id
      )

    source_dir = update_result.source_root.path
    with self.m.context(cwd=source_dir):
      self.m.chromium.runhooks(
        source_dir, build_dir, name='runhooks (without patch)'
      )

  def build_and_isolate_failing_tests(
    self,
    build_dir: Path,
    builder_id,
    builder_config,
    failing_tests,
    update_result: bot_update.Result,
    suffix,
    *,
    additional_compile_targets=None,
    include_utr_instruction=False,
    turboci_build_check_id: str = '',
  ):
    """Builds and isolates test suites in |failing_tests|.

    Args:
      build_dir: The path to the directory containing built outputs.
      builder_config: A BuilderConfig wth the configuration for the running bot.
      failing_tests: An iterable of test_suites that need to be rebuilt.
      bot_update_step: Contains information about the current checkout. Used to
                       set swarming properties.
      suffix: Should be 'without patch'. Used to annotate steps and swarming
              properties.
      additional_compile_targets (List[str]): Additional compile targets
        specified recipe-side. This field is intended for recipes to add
        targets needed for recipe functionality and not for configuring builder
        outputs (which should be specified src-side in waterfalls.pyl).
      include_utr_instruction: Whether or not to include UTR reproduction
                               instructions
      turboci_build_check_id: The check ID of the build check to create for the
        without patch compile. If empty, a unique check ID will be used instead.

    Returns:
      A tuple of:
        A RawResult object with the message and status or None if compile is
          not needed.
        A SwarmingExecutionInfo object containing information about how
          to execute the swarming tests in failing_tests.

    """
    skylab_isolate_tests = [t for t in failing_tests if t.runs_on_skylab]

    compile_targets = set(
      itertools.chain(*[t.compile_targets() for t in failing_tests])
    )

    turboci_build_check_id = self.m.chromium_turboci.ensure_check_id(
      turboci_build_check_id, WITHOUT_PATCH_BUILD_CHECK_ID
    )

    self.m.chromium_turboci.update_build_check_compile_targets(
      turboci_build_check_id, compile_targets, 'failing test targets'
    )

    if additional_compile_targets:
      compile_targets.update(additional_compile_targets)
      self.m.chromium_turboci.update_build_check_compile_targets(
        turboci_build_check_id,
        compile_targets,
        'targets explicitly requested by recipe',
      )

    self.m.chromium_turboci.set_build_check_planned(
      turboci_build_check_id, 'compile targets determined'
    )

    if not compile_targets:
      self.m.chromium_turboci.finalize_build_check(
        turboci_build_check_id, 'no compile necessary'
      )
      return None, None

    compile_targets = sorted(compile_targets)
    isolated_tests = [t for t in failing_tests if t.uses_isolate]

    source_dir = update_result.source_root.path
    with self.m.context(
      cwd=update_result.checkout_dir, env=self.m.chromium.get_env(source_dir)
    ):
      raw_result = self.run_mb_and_compile(
        source_dir,
        build_dir,
        builder_id,
        compile_targets,
        (
          [t.isolate_target for t in isolated_tests]
          + [t.target_name for t in skylab_isolate_tests]
        ),
        ' (%s)' % suffix,
        include_utr_instruction=include_utr_instruction,
      )

      self.m.chromium_turboci.finalize_build_check(
        turboci_build_check_id, 'executed compile', raw_result=raw_result
      )

      if raw_result:
        # Clobber the bot upon compile failure without patch.
        # See crbug.com/724533 for more detail.
        if raw_result.status == common_pb.FAILURE:
          self.m.file.rmtree('clobber', build_dir)

        if raw_result.status != common_pb.SUCCESS:
          return raw_result, None

      compile_output = CompileOutput(
        isolated_tests=isolated_tests,
        skylab_isolate_tests=skylab_isolate_tests,
      )
      _, execution_info = self.isolate_test_targets(
        source_dir,
        build_dir,
        builder_config,
        update_result,
        compile_output,
        suffix=suffix,
        swarm_hashes_property_name='swarm_hashes',
      )

      return raw_result, execution_info

  def should_skip_without_patch(
    self,
    builder_config: ctbc.BuilderConfig,
    source_dir: Path,
    affected_files: Iterable[str],
    targets_spec_dir: Path,
  ) -> bool:
    """Determine whether the without patch steps should be skipped.

    If the without patch steps should be skipped, a no-op step will be
    output to indicate why it's being skipped.

    Args:
      builder_config: A BuilderConfig object for the currently executing
        builder.
      source_dir: The path to the top level repo.
      affected_files: The paths to the files that are modified by the CL being
        tested, relative to source_dir.
      targets_spec_dir: The directory containing the targets spec files for the
        builder.

    Returns:
      Whether or not the without patch steps should be skipped.
    """
    reasons = []
    logs = {}
    affected_spec_files = self._get_affected_spec_files(
      source_dir, affected_files, builder_config, targets_spec_dir
    )
    if affected_spec_files:
      reasons.append(
        'test specs that are consumed by the builder '
        'are also affected by the CL'
      )
      logs['affected_spec_files'] = sorted(affected_spec_files)

    if not builder_config.retry_without_patch:
      reasons.append('retry without patch is disabled in builder config')

    if not reasons:
      return False

    result = self.m.step('without patch steps are skipped', [])
    result.presentation.step_text = '\n'.join('* {}'.format(r) for r in reasons)
    result.presentation.logs.update(logs)
    return True

  def summarize_test_failures(
    self, test_suites, retried_without_patch_suites=()
  ):
    """
    Takes test suites and an optional list of suites retried without patch.
    Summarizes the test results in the step UI, and returns the suites which
    can be presumptively attributed to the CL.
    Args:
      test_suites: Iterable of test suites
      retried_without_patch_suites (optional): Iterable of test suites retried
        on ToT. Must be a subset of the test_suites field. Default ().
    Returns:
      An array of test suites which failed and should not be forgiven.
    """
    culpable_failures = []
    for t in test_suites:
      if not t.has_failures_to_summarize():
        continue
      if t not in retried_without_patch_suites:
        self.m.test_utils.summarize_failing_test_with_no_retries(t)
        continue
      is_tot_fail = self.m.test_utils.summarize_test_with_patch_deapplied(t)
      if not is_tot_fail:
        culpable_failures.append(t)

    self.m.test_utils.record_suite_statuses(test_suites, 'with patch')
    return culpable_failures

  def _run_tests_with_patch(self, task: Task) -> tuple[bool, list[steps.Test]]:
    """Run tests with the patch applied.

    Args:
      task: The task object for the build.

    Returns:
      A tuple (run_without_patch, failing_tests):
        run_without_patch: Whether or not the failing tests should be rerun
          without the patch
        failing_tests: The failing tests
    """
    self.configure_swarming(
      self.m.tryserver.is_tryserver, task_output_stdout='none'
    )

    # crbug/1346781
    # src/third_party/llvm-build/Release+Asserts/bin/llvm-profdata is needed
    # when running code_coverage merge scripts
    if self.m.code_coverage.use_clang_coverage:
      self.m.code_coverage.ensure_clang_coverage_tools()

    with self.wrap_chromium_tests(
      task.checkout_dir, task.source_dir, task.build_dir, tests=task.test_suites
    ):
      # Run the test. The isolates have already been created.
      invalid_test_suites, failing_test_suites = (
        self.m.test_utils.run_tests_with_patch(
          task.checkout_dir,
          task.source_dir,
          task.build_dir,
          task.test_suites,
          retry_failed_shards=task.should_retry_failures_with_changes,
          include_utr_instruction=True,
        )
      )

      if self.m.code_coverage.using_coverage:
        self.m.code_coverage.process_coverage_data(task.test_suites)

      self.m.chromium_rts.start_evaluation(task.build_dir, task.test_suites)

      # We explicitly do not want all trybots to upload profiles to GS. We
      # prevent this by ensuring most trybots wanting to run the PGO workflow
      # have skip_profile_upload. Some are allowed to upload by setting a custom
      # GS bucket for trybots.
      if self.m.pgo.using_pgo and not self.m.pgo.skip_profile_upload:
        self.m.pgo.process_pgo_data(task.source_dir, task.test_suites)

      # We explicitly do not want trybots to upload orderfiles to CIPD. We
      # prevent this by ensuring all trybots wanting to run the orderfile
      # workflow do not have upload_orderfile set.
      if (
        self.m.orderfile.using_orderfile
        and not self.m.orderfile.upload_orderfile
      ):
        self.m.orderfile.process_orderfile_data(
          task.source_dir, task.update_result
        )

      # Exit without retries if there were invalid tests or if all tests passed
      if invalid_test_suites or not failing_test_suites:
        self.summarize_test_failures(task.test_suites)
        return False, invalid_test_suites or []

      # Also exit if there are failures but we shouldn't deapply the patch
      targets_spec_dir = self.get_targets_spec_dir(
        task.source_dir, task.builder_config
      )
      if self.should_skip_without_patch(
        task.builder_config,
        task.source_dir,
        task.affected_files,
        targets_spec_dir,
      ):
        self.summarize_test_failures(task.test_suites)
        return False, failing_test_suites

    return True, failing_test_suites

  def _run_tests_without_patch(
    self,
    task: Task,
    failing_test_suites: list[steps.Test],
  ) -> list[steps.Test]:
    """Run tests without the patch applied.

    Args:
      task: The task object for the build.
      failing_tests_suites: The failing tests to potentially exonerate with the
        results from running without the patch.

    Returns:
      The tests that could not be exonerated.
    """
    output_without_patch_property = self.m.step.empty(
      'record ran_tests_without_patch'
    )
    output_without_patch_property.presentation.properties[
      'ran_tests_without_patch'
    ] = True
    with self.wrap_chromium_tests(
      task.checkout_dir,
      task.source_dir,
      task.build_dir,
      tests=failing_test_suites,
    ):
      self.m.test_utils.run_tests(
        task.checkout_dir,
        task.source_dir,
        task.build_dir,
        failing_test_suites,
        'without patch',
        sort_by_shard=True,
        include_utr_instruction=True,
      )

      return self.summarize_test_failures(task.test_suites, failing_test_suites)

  def get_common_args_for_scripts(self, source_dir: Path, build_dir: Path):
    args = []

    args.extend(['--build-dir', build_dir])

    paths = {
      'checkout': source_dir,
    }
    args.extend(['--paths', self.m.json.input(paths)])

    properties = {}
    # TODO(phajdan.jr): Remove buildnumber when no longer used.

    properties['buildername'] = self.m.buildbucket.builder_name
    properties['buildnumber'] = self.m.buildbucket.build.number
    properties['bot_id'] = self.m.swarming.bot_id
    # TODO(gbeaty) Audit scripts and remove/update this as necessary
    properties['slavename'] = self.m.swarming.bot_id
    # TODO(gbeaty) Audit scripts and remove/update this as necessary
    properties['mastername'] = self.m.builder_group.for_current

    properties['target_platform'] = self.m.chromium.c.TARGET_PLATFORM

    args.extend(['--properties', self.m.json.input(properties)])

    return args, paths, properties

  def get_compile_targets_for_scripts(self, source_dir: Path, build_dir: Path):
    """This gets the combined compile_targets information from the
    //testing/scripts/get_compile_targets.py script.

    This script returns the compile targets for all of the 'script tests' in
    chromium (including ones that we don't plan to run on this configuration,
    see TODO). The information is returned in the following format:

    {
      "some_script_name.py": ["list", "of", "compile", "targets"],
    }

    Where "some_script_name.py" corresponds to
    "//testing/scripts/some_script_name.py".

    Args:
      source_dir: The path to the top-level repo.

    Returns:
      The compile target data in the form described above.

    TODO:
      * Only gather targets for the scripts that we might concievably run.
    """
    common_args, _, _ = self.get_common_args_for_scripts(source_dir, build_dir)
    result = self.m.step(
      name='get compile targets for scripts',
      cmd=[
        'vpython3',
        (source_dir / 'testing/scripts/get_compile_targets.py'),
        '--output',
        self.m.json.output(),
        '--',
      ]
      + common_args,
      step_test_data=lambda: self.m.json.test_api.output({}),
    )
    return result.json.output

  def main_waterfall_steps(
    self,
    builder_id,
    builder_config,
    mb_config_path=None,
    mb_phase=None,
    root_solution_revision=None,
  ) -> tuple[result_pb2.RawResult | None, bot_update.Result]:
    """Compiles and runs tests for chromium recipe.

    Args:
      root_solution_revision: Git revision of Chromium to check out.
        Passed down to bot_update.ensure_checkout.
        Used by CI bots of projects which are Chromium components,
        like ANGLE CI bots, to run tests with a known good version of Chromium.
        If omitted, ToT Chromium is checked out.

    Returns:
      A tuple with the following values
      * None if no failure occurred, otherwise a RawResult object
        describing the failure that can be used as the recipe's result.
      * The bot_update Result describing the checkout. This enables a
        recipe to correctly access the checkout after
        main_waterfall_steps has completed.
    """
    if self._enable_snoopy:
      self.m.bcid_reporter.report_stage('start')

    self.report_builders(builder_config, report_mirroring_builders=True)
    self.print_link_to_results()
    self.configure_build(builder_config)

    if self._enable_snoopy:
      self.m.bcid_reporter.report_stage('fetch')
    update_result, build_dir, targets_config = self.prepare_checkout(
      builder_config,
      timeout=3600,
      root_solution_revision=root_solution_revision,
      add_blamelists=True,
    )
    checkout_dir = update_result.checkout_dir
    source_dir = update_result.source_root.path
    if builder_config.execution_mode == ctbc.TEST:
      self.lookup_builder_gn_args(
        source_dir,
        builder_id,
        builder_config,
        mb_config_path=mb_config_path,
        mb_phase=mb_phase,
      )

    if self.m.pgo.using_pgo:
      is_cros = self.m.chromium.c.TARGET_PLATFORM == 'chromeos'
      self.m.pgo.configure_llvm_tooling_path(
        source_dir, builder_id, is_cros=is_cros
      )

    if self.m.orderfile.using_orderfile:
      self.m.orderfile.configure_custom_pgo_profile(source_dir)

    if self._enable_snoopy:
      self.m.bcid_reporter.report_stage('compile')
    compile_result, compile_output = self.compile_specific_targets(
      build_dir,
      builder_id,
      builder_config,
      update_result,
      targets_config,
      targets_config.compile_targets,
      targets_config.all_tests,
      mb_config_path=mb_config_path,
      mb_phase=mb_phase,
      include_utr_instruction=True,
    )

    if compile_result and compile_result.status != common_pb.SUCCESS:
      return compile_result, update_result

    swarming_execution_info = None
    if compile_output:
      _, swarming_execution_info = self.isolate_test_targets(
        source_dir, build_dir, builder_config, update_result, compile_output
      )

    self.inbound_transfer(
      build_dir, builder_config, builder_id, update_result, targets_config
    )
    additional_trigger_properties = self.outbound_transfer(
      builder_id,
      builder_config,
      update_result,
      targets_config,
      swarming_execution_info,
    )

    self.trigger_child_builds(
      builder_id,
      update_result,
      builder_config,
      additional_properties=additional_trigger_properties,
    )

    if self._enable_snoopy:
      self.m.bcid_reporter.report_stage('upload')

    self.archive_clusterfuzz(
      builder_id,
      update_result,
      builder_config,
      build_dir,
      targets_config.compile_targets,
    )
    upload_results = self.archive_build(
      build_dir, update_result, enable_snoopy=self._enable_snoopy
    )

    if self._enable_snoopy:
      self.m.bcid_reporter.report_stage('upload-complete')

    tests = targets_config.tests_on(builder_id)
    tests_result = self.run_tests(
      checkout_dir,
      source_dir,
      build_dir,
      builder_id,
      builder_config,
      tests,
      update_result=update_result,
      upload_results=upload_results,
    )
    return tests_result, update_result

  def outbound_transfer(
    self,
    builder_id,
    builder_config,
    bot_update_step,
    targets_config,
    execution_info=None,
  ):
    """Handles the builder half of the builder->tester transfer flow.

    We support two different transfer mechanisms:
     - Isolate transfer: builders upload tests + any required runtime
       dependencies to isolate, then pass the isolate hashes and command line
       information to testers via properties. Testers use those hashes and
       command line information to trigger swarming tasks but do not directly
       download the isolates.
     - Skylab transfer: properties required for trigger the tests already
       uploaded to gcs are passed through as additional trigger properties

    These can be used concurrently -- e.g., a builder that triggers two
    different testers, one that supports isolate transfer and one that
    doesn't, would run both the isolate transfer flow *and* the package
    transfer flow.

    For isolate-based transfers, this function just determines trigger
    properties, as tests get isolated immediately after compilation (see
    compile_specific_targets).

    For package-based transfers, this uploads some of the output directory
    to GS. (See package_build for more details.)

    Args:
      builder_id: a BuilderId object for the currently executing builder.
      builder_config: a BuilderConfig object for the currently executing
        builder.
      bot_update_step: the result of a previously executed bot_update step.
      targets_config: a TargetsConfig object.
      execution_info: A SwarmingExecutionInfo object describing how to
        execute the tests configured for this build.
    Returns:
      A dict containing additional properties that should be added to any
      triggered child builds.
    """
    isolate_transfer = any(
      t.uses_isolate for t in targets_config.tests_triggered_by(builder_id)
    )
    trigger_properties = {}
    if isolate_transfer and execution_info:
      trigger_properties = execution_info.ensure_command_lines_archived(
        self
      ).as_trigger_prop()

    skylab_tests = [
      t
      for t in targets_config.tests_triggered_by(builder_id)
      if t.runs_on_skylab
    ]

    if skylab_tests:
      trigger_properties['skylab_trigger_properties'] = (
        self._get_skylab_trigger_properties(skylab_tests)
      )

    return trigger_properties

  def inbound_transfer(
    self,
    build_dir: Path,
    builder_config,
    builder_id,
    update_result,
    targets_config,
  ):
    """Handles the tester half of the builder->tester transfer flow.

    See outbound_transfer for a discussion of transfer mechanisms.

    For isolate-based transfers, this merely clears out the output directory.
    For package-based transfer, this downloads the build from GS.

    Args:
      build_dir: The path to the directory containing built outputs.
      builder_config: a BuilderConfig object for the currently executing tester.
      update_result: the result of a previously executed bot_update step.
      targets_config: a TargetsConfig object.
    """
    if builder_config.execution_mode != ctbc.TEST:
      return SwarmingExecutionInfo()

    tests = targets_config.tests_on(builder_id)

    tests_using_isolates = [t for t in tests if t.uses_isolate]
    tests_using_skylab = [t for t in tests if t.runs_on_skylab]

    # Protect against hard to debug mismatches between directory names
    # used to run tests from and extract build to. We've had several cases
    # where a stale build directory was used on a tester, and the extracted
    # build was not used at all, leading to confusion why source code changes
    # are not taking effect.
    #
    # The best way to ensure the old build directory is not used is to
    # remove it.
    self.m.file.rmtree('remove build directory', build_dir)

    if set(tests_using_isolates + tests_using_skylab) != set(tests):
      raise self.m.step.StepFailure(
        'Only isolated and/or Skylab tests are allowed on child testers.'
      )

    self.download_command_lines_for_tests(
      update_result.source_root.path,
      build_dir,
      tests_using_isolates,
      builder_config,
    )
    self._set_skylab_test_execution_info(tests_using_skylab)

  def _set_skylab_test_execution_info(self, skylab_tests):
    trigger_properties = self.m.properties.get('skylab_trigger_properties')
    for t in skylab_tests:
      target_properties = trigger_properties[t.target_name]
      t.exe_rel_path = target_properties.get("exe_rel_path", '')
      t.lacros_gcs_path = target_properties.get("lacros_gcs_path", '')
      t.build_output_dir = target_properties.get('build_output_dir', '')

  def _get_skylab_trigger_properties(self, skylab_tests):
    properties = {}
    for t in skylab_tests:
      properties[t.target_name] = {
        "exe_rel_path": t.exe_rel_path,
        "lacros_gcs_path": t.lacros_gcs_path,
        "build_output_dir": t.build_output_dir,
      }
    return properties

  def download_command_lines_for_tests(
    self,
    source_dir: Path,
    build_dir: Path,
    tests,
    builder_config,
    *,
    swarming_command_lines_digest=None,
    swarming_command_lines_cwd=None,
    swarming_command_lines_variant_digests=None,
  ):
    """Download and set command lines for tests.

    This method checks the 'swarming_command_lines_digest',
    'swarming_command_lines_cwd', and 'swarming_command_lines_variant_digests'
    input properties to find the appropriate digest to download.

    Args:
      source_dir: The path to the top-level repo.
      build_dir: The path to the directory containing built outputs.
      tests: The tests to download command line arguments for.
      builder_config: The currently configured builder.
      swarming_command_lines_digest: If set, the digest we should download.
      swarming_command_lines_cwd: If set, the cwd for command lines.
      swarming_command_lines_variant_digests: If set, the variant digests to download.
    """
    digest = swarming_command_lines_digest or self.m.properties.get(
      'swarming_command_lines_digest'
    )
    rel_cwd = swarming_command_lines_cwd or self.m.properties.get(
      'swarming_command_lines_cwd'
    )
    variant_digests = (
      swarming_command_lines_variant_digests
      or self.m.properties.get('swarming_command_lines_variant_digests', {})
    )
    if digest:
      command_lines = self._download_command_lines(digest)
      command_line_variants = {}
      for variant, variant_digest in variant_digests.items():
        command_line_variants[variant] = self._download_command_lines(
          variant_digest, suffix=variant
        )
      self.set_swarming_test_execution_info(
        source_dir,
        build_dir,
        tests,
        command_lines,
        rel_cwd,
        expose_to_properties=builder_config.expose_trigger_properties,
        builder_config=builder_config,
        command_line_variants=command_line_variants,
      )

  def archive_command_lines(self, command_lines, suffix=''):
    name = f'{suffix}_command_lines' if suffix else 'command_lines'
    step_name = f'{suffix} command lines' if suffix else 'command lines'
    command_lines_file = self.m.path.cleanup_dir / f'{name}.json'
    self.m.file.write_json(
      f'write {step_name}', command_lines_file, command_lines
    )
    return self.m.cas.archive(
      f'archive {step_name} to RBE-CAS',
      self.m.path.cleanup_dir,
      command_lines_file,
    )

  def _archive_test_trigger_deps_digest(
    self,
    source_dir: Path,
    build_dir: Path,
    *,
    builder_config=None,
  ):
    """Archives an extra CAS isolate for builds that need it.

    This handles both the CAS isolate used by orchestrators and the CAS isolate
    used in normal parent/child builder setups if a child has opted in to the
    test trigger CAS codepath.
    """
    # Runtime files are listed relative to output dir, we upload to CAS relative
    # to checkout dir (src checkout folder) as those will be used in the srcless
    # builder as they were checked out. Note, it's possible that test-trigger
    # deps doesn't compile in the builder as the builder itself can be a tester
    # or a srcless builder.
    #
    # Runtime files (TEST_TRIGGER_AND_COLLECT_DEPS_TARGET) are defined here:
    # https://source.chromium.org/chromium/chromium/src/+/main:infra/orchestrator/BUILD.gn
    base_dir = source_dir
    runtime_deps_file = (
      build_dir / TEST_TRIGGER_AND_COLLECT_DEPS_RUNTIME_DEPS_FILE
    )

    if not self.m.path.exists(runtime_deps_file):
      self.m.step.empty(
        'test-trigger deps not compiled, ignored (%s)' % runtime_deps_file
      )
      return None

    with self.m.step.nest('archive test-trigger deps') as result:
      dep_paths = set()
      paths = (
        self.m.file.read_text('read test-trigger deps file', runtime_deps_file)
        .rstrip()
        .split('\n')
      )
      for path in paths:
        file_path = self.m.path.relpath(build_dir / path, base_dir)
        file_path = base_dir / file_path

        if "*" in str(file_path):
          # Glob files if it contains a wildcard
          paths = self.m.file.glob_paths(
            'get files that match pattern', base_dir, str(file_path)
          )
          dep_paths.update([str(p) for p in paths])
        else:
          dep_paths.add(str(file_path))

      if builder_config:
        targets_spec_dir = self.get_targets_spec_dir(source_dir, builder_config)
        for f in builder_config.targets_spec_files.values():
          dep_paths.add(str(targets_spec_dir / f))

      digest = self.m.cas.archive(
        'archive test-trigger deps to RBE-CAS', base_dir, *dep_paths
      )
      result.logs["collected test-trigger deps"] = [str(x) for x in dep_paths]
      return digest

  def _download_command_lines(self, command_lines_digest, suffix=''):
    name = f'{suffix}_command_lines' if suffix else 'command_lines'
    step_name = f'{suffix} command lines' if suffix else 'command lines'
    self.m.cas.download(
      f'download {step_name}', command_lines_digest, self.m.path.cleanup_dir
    )
    command_lines_file = self.m.path.cleanup_dir / f'{name}.json'
    return self.m.file.read_json(
      f'read {step_name}', command_lines_file, test_data={}
    )

  def _get_valid_and_invalid_results(self, unrecoverable_test_suites):
    valid = []
    invalid = []
    for test_suite in unrecoverable_test_suites:
      # Both 'with patch' and 'without patch' must have valid results to
      # skip CQ retries.
      valid_results_with_patch, _ = (
        test_suite.with_patch_failures_including_retry()
      )
      if valid_results_with_patch and test_suite.has_valid_results(
        'without patch'
      ):
        valid.append(test_suite)
      else:
        invalid.append(test_suite)

    return valid, invalid

  def deapply_deps(
    self,
    update_result: bot_update.Result,
    build_dir: Path,
    turboci_source_check_id: str,
  ) -> None:
    with self.m.context(cwd=update_result.checkout_dir):
      # If tests fail, we want to fix Chromium revision only. Tests will use
      # the dependencies versioned in 'src' tree.
      self.m.gclient.c.revisions = {
        'src': update_result.manifest['src']['revision']
      }

      # NOTE: 'ignore_input_commit=True' gets a checkout using the commit
      # before the tested commit, effectively deapplying the gitiles commit
      # (latest commit currently being tested) and reverts back to DEPS
      # revisions.
      # Chromium has a lot of tags which slow us down, we don't need them to
      # deapply, so don't fetch them.
      self.m.bot_update.ensure_checkout(
        patch=False,
        no_fetch_tags=True,
        update_presentation=False,
        ignore_input_commit=True,
        set_output_commit=False,
        turboci_check_id=turboci_source_check_id,
      )

    source_dir = update_result.source_root.path
    with self.m.context(cwd=source_dir):
      # NOTE: "without patch" phrase is used to keep consistency with the API
      self.m.chromium.runhooks(
        source_dir, build_dir, name='runhooks (without patch)'
      )

  def integration_steps(self, builder_id, builder_config):
    return self.trybot_steps(
      builder_id, builder_config, deapply_changes=self.deapply_deps
    )

  def trybot_steps(
    self,
    builder_id: chromium_types.BuilderId,
    builder_config: ctbc.BuilderConfig,
    root_solution_revision: str | None = None,
    files_relative_to: str | None = None,
    deapply_changes: Callable[[bot_update.Result, Path, str], None]
    | None = None,
  ) -> result_pb2.RawResult | None:
    """Compiles and runs tests for chromium recipe.

    Args:
      builder_id: A BuilderId for identifying a builder.
      builder_config: A BuilderConfig for accessing the static builder
        configuration.
      root_solution_revision: Git revision of Chromium to check out.
        Passed down to bot_update.ensure_checkout.
        Used by bots on CQs of projects which are Chromium components,
        like ANGLE CQ, to run tests with a known good version of Chromium.
        If omitted, ToT Chromium is checked out.
      files_relative_to: Directory that files should be made relative to.
        Passed down to chromium_checkout.get_files_affected_by_patch. If
        omitted, the default value set by that function will be used.
      deapply_changes: A callable that takes the bot_update result and
        the build directory and updates the checkout to deapply the
        patch under test. Will be called when necessary to run tests
        without the patch to exonerate failures that occurred with the
        patch.

    Returns:
      A result with the status of the build and a failure message if a
      failure occurred, None otherwise.
    """
    self.raise_failure_if_cq_depends_footer_exists()

    self.report_builders(builder_config)
    self.print_link_to_results()
    self.configure_build(builder_config)
    self.m.chromium.apply_config('trybot_flavor')

    # This rolls chromium checkout, applies the patch, runs gclient sync to
    # update all DEPS.
    # Chromium has a lot of tags which slow us down, we don't need them on
    # trybots, so don't fetch them.
    update_result, build_dir, targets_config = self.prepare_checkout(
      builder_config,
      timeout=3600,
      no_fetch_tags=True,
      root_solution_revision=root_solution_revision,
      turboci_source_check_id=SOURCE_CHECK_ID,
    )

    # Though the function name is "trybot_steps", there are work flows that
    # call this function in a non-trybot environment where the analyze step is
    # skipped. So don't create the analyze check when it is not a tryserver.
    turboci_analyze_check_id = self.m.chromium_turboci.create_build_check(
      BUILD_CHECK_ID,
      SOURCE_CHECK_ID,
      create_analyze_check=self.m.tryserver.is_tryserver,
    )

    # When everything is on TurboCI, this could possibly be flattened further by
    # lifting the analyze-related logic out of build_affected_targets; at that
    # point there wouldn't need to be separate trybot and compilator recipes and
    # so there wouldn't be a concern with having to keep the sequence of calls
    # in sync
    compile_result, task = self.build_affected_targets(
      builder_id,
      builder_config,
      update_result,
      build_dir,
      targets_config,
      files_relative_to=files_relative_to,
      turboci_build_check_id=BUILD_CHECK_ID,
      turboci_analyze_check_id=turboci_analyze_check_id,
    )
    assert compile_result
    if compile_result.status != common_pb.SUCCESS:
      return compile_result

    # Does nothing if the builder is not configured to upload builds for ClusterFuzz.
    self.archive_clusterfuzz(
      builder_id,
      update_result,
      builder_config,
      build_dir,
      targets_config.compile_targets,
    )

    self.archive_build(
      build_dir, update_result, enable_snoopy=self._enable_snoopy
    )

    self.m.step.empty('mark: before_tests')
    if not task.test_suites:
      return None

    try:
      run_without_patch, failing_test_suites = self._run_tests_with_patch(task)
      if not run_without_patch:
        if failing_test_suites:
          return self.handle_unrecoverable_test_suites(failing_test_suites)

      else:
        deapply_changes = deapply_changes or self.deapply_patch
        deapply_changes(update_result, build_dir, WITHOUT_PATCH_SOURCE_CHECK_ID)
        self.m.chromium_turboci.create_build_check(
          WITHOUT_PATCH_BUILD_CHECK_ID, WITHOUT_PATCH_SOURCE_CHECK_ID
        )
        compile_result, _ = self.build_and_isolate_failing_tests(
          build_dir,
          builder_id,
          task.builder_config,
          failing_test_suites,
          update_result,
          'without patch',
          include_utr_instruction=True,
          turboci_build_check_id=WITHOUT_PATCH_BUILD_CHECK_ID,
        )
        if compile_result and compile_result.status != common_pb.SUCCESS:
          return compile_result

        unrecoverable_test_suites = self._run_tests_without_patch(
          task, failing_test_suites
        )
        if unrecoverable_test_suites:
          return self.handle_unrecoverable_test_suites(
            unrecoverable_test_suites
          )

    finally:
      if not self.m.runtime.in_global_shutdown:
        self.m.chromium_swarming.report_stats()
        self.m.chromium_rts.wait_for_evaluation()

    # If this point is reached, it means the tests have passed and/or been
    # exonerated and we'll check for new flaky tests if enabled for the builder
    new_tests = self.m.flakiness.find_tests_for_flakiness(task.test_suites)
    if new_tests:
      # Executing for flakiness checks is done in chromium_tests so that we
      # avoid a circular dependency between chromium_tests and flakiness.
      return self.run_tests_for_flakiness(
        task.checkout_dir,
        task.source_dir,
        build_dir,
        new_tests,
      )

    return None

  def raise_failure_if_cq_depends_footer_exists(self):
    # CrOS CQ supports linking & testing CLs across different repos in one
    # build via the `Cq-Depends` footer. But Chrome's CQ does not. So check
    # if the CL author has mistakenly added the footer to their chromium CL
    # and fail loudly in that case to avoid confusion.
    if self.m.tryserver.is_tryserver:
      cq_depends_footer = self.m.tryserver.get_footer(
        self.m.tryserver.constants.CQ_DEPEND_FOOTER
      )
      if cq_depends_footer:
        raise self.m.step.StepFailure(
          'Commit message footer {} is not supported on Chrome builders. '
          'Please remove the line(s) from the commit message and try '
          'again.'.format(self.m.tryserver.constants.CQ_DEPEND_FOOTER)
        )

  def handle_unrecoverable_test_suites(self, test_suites):
    self.handle_invalid_test_suites(test_suites)
    status = self.determine_build_status_from_tests(test_suites, 'with patch')
    return result_pb2.RawResult(
      summary_markdown=self.format_unrecoverable_failures(
        test_suites, 'with patch'
      ),
      status=status,
    )

  def handle_invalid_test_suites(self, test_suites):
    # This means there was a failure of some sort
    if self.m.tryserver.is_tryserver:
      _, invalid_suites = self._get_valid_and_invalid_results(test_suites)
      # For DEPS autoroll analysis
      if not invalid_suites:
        self.m.cv.set_do_not_retry_build()

  def determine_build_status_from_tests(self, test_suites, suffix):
    """Determines the appropriate build status based on the tests' results.

    Args:
      test_suites: List of steps.Tests that ran in this build.
      suffix: Phase of the build to check test results for.
              Note: not necessarily the current phase of the build.

    Returns: a buildbucket.proto.common.Status
    """
    status = common_pb.SUCCESS
    retry_suffix = self.m.test_utils.prepend_retry_shards(suffix)
    for t in test_suites:
      if not t.has_valid_results(suffix) or t.deterministic_failures(suffix):
        status = common_pb.FAILURE
      if not t.did_complete(suffix) and not t.did_complete(retry_suffix):
        return common_pb.INFRA_FAILURE  # Nothing should override INFRA_FAILURE
    return status

  def format_success_retry_tests(
    self,
    success_retry_test_suites,
    size_limit=700,
  ):
    """Creates list of tests succeeded after retry formatted using markdown.

    Args:
      success_retry_test_suites: Set of succeeded after retry Test

    Returns:
      String containing a markdown formatted list of tests succeeded after retry
    """
    num_failed_suites = len(success_retry_test_suites)
    success_retry_markdown = f'{len(success_retry_test_suites)} Test Suite(s) succeeded after retry.\n\n'
    for idx, suite in enumerate(
      sorted(success_retry_test_suites, key=lambda t: t.name)
    ):
      if len(success_retry_markdown) > 700:
        success_retry_markdown += (
          f'- ...{num_failed_suites - idx} more failure(s)...\n'
        )
        break
      success_retry_markdown += f'- {suite.name}\n'

    return success_retry_markdown

  def format_unrecoverable_failures(
    self, unrecoverable_test_suites, suffix, size_limit=700, failure_limit=4
  ):
    """Creates list of failed tests formatted using markdown.

    Args:
      unrecoverable_test_suites: List of failed Test
          (definition can be found in steps.py)
      suffix: phase of the build to format tests for.
              Note: not necessarily the current phase of the build.
      size_limit: max size of the message in characters
      failure_limit: max number of deterministic failures listed per test suite

    Returns:
      String containing a markdown formatted list of test failures
    """
    test_size = len(unrecoverable_test_suites)
    header = '%d Test Suite(s) failed.' % test_size
    test_summary_lines = [header]
    if self._test_data.enabled:
      size_limit = self._test_data.get('change_size_limit', 200)
      failure_limit = size_limit / 100

    if not self.m.tryserver.is_tryserver:
      failed_tests_by_builder_id = {}
      for t in unrecoverable_test_suites:
        if t.is_ci_only:
          builder_id = (
            f'{t.spec.waterfall_builder_group}:{t.spec.waterfall_buildername}'
          )
          failed_tests_by_builder_id.setdefault(builder_id, []).append(t.name)
      if failed_tests_by_builder_id:
        test_summary_lines.append(
          'some of the failing tests are only run in CI,'
          ' add the following CL footers to enable them on try builders'
        )
        for builder_id, tests in failed_tests_by_builder_id.items():
          test_summary_lines.append(
            f'{steps.INCLUDE_CI_FOOTER}: {builder_id}|{",".join(tests)}'
          )

    current_size = 0
    for index, suite in enumerate(unrecoverable_test_suites):
      test_suite_header = '**%s** failed.' % suite.name

      is_valid, deterministic_failures = suite.failures_including_retry(suffix)
      if is_valid and self.m.tryserver.is_tryserver:
        is_valid, failures_to_ignore = suite.without_patch_failures_to_ignore()
        if is_valid:
          deterministic_failures = deterministic_failures - failures_to_ignore

      deterministic_failures = deterministic_failures or set()
      retry_suffix = self.m.test_utils.prepend_retry_shards(suffix)
      if not suite.did_complete(suffix) and not suite.did_complete(
        retry_suffix
      ):
        test_suite_header = (
          '**%s** did not complete, likely due to an infra bug.' % suite.name
        )
      elif deterministic_failures:
        test_suite_header = '**%s** failed because of:' % suite.name
      elif not is_valid:
        test_suite_header = (
          '**%s** failed with invalid results. '
          'Did a shard fail early?' % suite.name
        )

      current_size += len(test_suite_header)
      if current_size >= size_limit:
        hint = '#### ...%d more test(s)...' % (test_size - index)
        test_summary_lines.append(hint)
        return '\n\n'.join(test_summary_lines)

      test_summary_lines.append(test_suite_header)
      if not deterministic_failures:
        continue

      for idx, failure in enumerate(sorted(deterministic_failures)):
        if idx >= failure_limit or current_size >= size_limit:
          failure_size = len(deterministic_failures)
          hint = '- ...%d more failure(s) (%d total)...' % (
            failure_size - idx,
            failure_size,
          )
          test_summary_lines.append(hint)
          current_size += len(hint)
          break

        failure_line = '- %s' % failure
        test_summary_lines.append(failure_line)
        current_size += len(failure_line)

    return '\n\n'.join(test_summary_lines)

  def run_tests_for_flakiness(
    self,
    checkout_dir: Path,
    source_dir: Path,
    build_dir: Path,
    test_objects_by_suffix,
  ):
    """Runs tests for flake endorser.


    Args:
      checkout_dir: The directory where the checkout was performed.
      source_dir: The path to the top-level repo.
      test_objects_by_suffix: A dict mapping from test suffixes to lists of
        steps.AbstractTest objects.

    Returns:
      A RawResult object with the status of the build and failure message if
      there are flakiness. None if no flakiness.
    """
    general_suffix = self.m.flakiness.test_suffix
    flakiness_run_step_name = self.m.flakiness.RUN_TEST_STEP_NAME

    with self.m.step.nest(flakiness_run_step_name) as p:
      p.step_text = (
        'If you see failures unrelated with flaky new tests, '
        'please use "Validate-Test-Flakiness: skip" git footer to '
        'skip new test flakiness check and file a crbug to '
        'Infra>Test>Flakiness component.'
      )
      # |general_suffix| is always in |test_objects_by_suffix| dict and all
      # local tests are under this key.
      with self.wrap_chromium_tests(
        checkout_dir,
        source_dir,
        build_dir,
        tests=test_objects_by_suffix[general_suffix],
      ):
        self.m.test_utils.run_tests_for_flake_endorser(
          checkout_dir,
          source_dir,
          build_dir,
          test_objects_by_suffix,
          include_utr_instruction=True,
        )

    result = self.m.flakiness.check_run_results(test_objects_by_suffix)
    if result and result.status == common_pb.FAILURE:
      self.m.cv.set_do_not_retry_build()

    return result

  def determine_compilation_targets(
    self,
    builder_id: chromium_types.BuilderId,
    builder_config: ctbc.BuilderConfig,
    checkout_dir: Path,
    source_dir: Path,
    build_dir: Path,
    affected_files: Iterable[str],
    targets_config: targets_config_module.TargetsConfig,
    *,
    skip_analysis_reasons: Iterable[str] | None = None,
    turboci_analyze_check_id: str = '',
  ) -> tuple[list[str], list[str]]:
    """Determine the targets to build.

    Args:
      builder_id: A BuilderId for identifying a builder.
      builder_config: A BuilderConfig for accessing the static builder
        configuration.
      checkout_dir: The directory where the checkout was performed.
      source_dir: The path to the top level repo.
      build_dir: The path to the directory containing built outputs.
      affected_files: The paths to the files that are modified by the CL being
        tested, relative to source_dir.
      targets_config: The TargetsConfig providing the configured targets for the
        builder.
      skip_analysis_reasons: Reasons to skip analysis. If set to a non-empty
        value, then instead of executing analyze, all configured targets will be
        returned and an empty step will be emitted indicating why analyze wasn't
        executed.
      turboci_analyze_check_id: The check ID of the analyze check to create for
        the analyze. If empty, and the analyze is needed, a unique check ID will
        be used instead.

    Returns:
      A tuple of
      * Targets that should be built for tests that are to be run
      * Additional targets that should be built
    """
    tests = targets_config.all_tests
    compile_targets = targets_config.compile_targets
    test_targets = sorted(set(self._all_compile_targets(tests)))

    # There are work flows that call this method under a non-tryserver
    # environment. So return early in this case.
    if not self.m.tryserver.is_tryserver:
      return test_targets, compile_targets

    additional_compile_targets = sorted(
      set(compile_targets) - set(test_targets)
    )
    analyze_names = [
      'chromium',
      *builder_config.analyze_names,
      self.m.chromium.c.TARGET_PLATFORM,
    ]
    additional_exclusions = {
      exclusion: 'builder config additional exclusions'
      for exclusion in builder_config.additional_exclusions
    }

    turboci_analyze_check_id = self.m.chromium_turboci.ensure_check_id(
      turboci_analyze_check_id, f'{BUILD_CHECK_ID} analyze'
    )

    results_callback = self.m.chromium_turboci.prepare_analyze_check(
      turboci_analyze_check_id,
      test_targets,
      additional_compile_targets,
      analyze_names,
      additional_exclusions,
    )

    # Use analyze to determine the compile targets that are affected by the CL.
    # Use this to prune the relevant compile targets and test targets.
    skip_analysis_reasons = list(skip_analysis_reasons or [])
    skip_analysis_reasons.extend(
      self.m.chromium_bootstrap.skip_analysis_reasons
    )
    skip_analysis_logs = {}

    affected_spec_files = self._get_affected_spec_files(
      source_dir,
      affected_files,
      builder_config,
      self.get_targets_spec_dir(source_dir, builder_config),
    )
    # If any of the spec files that we used for determining the targets/tests
    # is affected, skip doing analysis, just build/test all of them
    if affected_spec_files:
      skip_analysis_reasons.append(
        'test specs that are consumed by the builder '
        'are also affected by the CL'
      )
      skip_analysis_logs['affected_spec_files'] = sorted(affected_spec_files)

    if skip_analysis_reasons:
      step_result = self.m.step('analyze', [])
      text = ['skipping analyze'] + skip_analysis_reasons
      step_result.presentation.step_text = '\n* '.join(text)
      for log, contents in sorted(skip_analysis_logs.items()):
        step_result.presentation.logs[log] = contents

      results_callback(
        '\n  '.join(['skipping analyze:', *skip_analysis_reasons]),
        sorted(compile_targets),
        sorted(test_targets),
      )
      return test_targets, compile_targets

    submodule_analyze = self.m.tryserver.is_tryserver and any(
      f.lower() == 'true'
      for f in self.m.tryserver.get_footer(steps.SUBMODULE_ANALYZE_FOOTER)
    )

    initial_test_targets = test_targets
    initial_compile_targets = compile_targets

    test_targets, compile_targets = self.m.filter.analyze(
      source_dir,
      build_dir,
      affected_files,
      test_targets,
      additional_compile_targets,
      builder_id=builder_id,
      additional_names=analyze_names,
      additional_exclusions=additional_exclusions,
      ignored_exclusion_patterns=['DEPS'] if submodule_analyze else None,
      results_callback=results_callback,
    )

    if submodule_analyze:
      step_result = self.m.step.active_result
      test_targets_removed = sorted(
        set(initial_test_targets) - set(test_targets)
      )
      compile_targets_removed = sorted(
        set(initial_compile_targets) - set(compile_targets)
      )

      if test_targets_removed:
        step_result.presentation.logs['test_targets_removed'] = (
          test_targets_removed
        )
      if compile_targets_removed:
        step_result.presentation.logs['compile_targets_removed'] = (
          compile_targets_removed
        )

      step_result.presentation.properties['submodule_analyze'] = {
        'test_targets_removed': test_targets_removed,
        'compile_targets_removed': compile_targets_removed,
      }

    return test_targets, compile_targets

  def configure_swarming(
    self,
    precommit: bool,
    task_output_stdout: str | None = None,
    **kwargs,
  ):
    """Configures default swarming dimensions and tags for chromium tests.

    This is a wrapper around `chromium_swarming.configure_swarming` that also
    allows configuring `task_output_stdout`.

    Args:
      precommit: Boolean flag to indicate whether the tests are running before
        the changes are committed.
      task_output_stdout: Optional path to redirect task stdout to.
      **kwargs: Keyword arguments to forward to
        `chromium_swarming.configure_swarming`.
    """
    self.m.chromium_swarming.configure_swarming(precommit=precommit, **kwargs)

    if task_output_stdout:
      self.m.chromium_swarming.task_output_stdout = task_output_stdout

  def build_affected_targets(
    self,
    builder_id: chromium_types.BuilderId,
    builder_config: ctbc.BuilderConfig,
    update_result: bot_update.Result,
    build_dir: Path,
    targets_config: targets_config_module.TargetsConfig,
    *,
    isolate_output_files_for_coverage: bool = False,
    additional_compile_targets: Iterable[str] | None = None,
    skip_analysis_reasons: Iterable[str] | None = None,
    files_relative_to: str | None = None,
    turboci_build_check_id: str = '',
    turboci_analyze_check_id: str = '',
  ) -> tuple[result_pb2.RawResult | None, Task]:
    """Builds targets affected by change.

    Args:
      builder_id: A BuilderId for identifying a builder.
      builder_config: A BuilderConfig for accessing the static builder
        configuration.
      isolate_output_files_for_coverage: Whether to also upload all test
        binaries and other required code coverage output files to one hash. If
        code_coverage.instrument sets skipping_coverage to True, then this
        kwarg will be overriden to be False.
      additional_compile_targets: Additional compile targets specified
        recipe-side. This field is intended for recipes to add targets needed
        for recipe functionality and not for configuring builder outputs (which
        should be specified src-side in waterfalls.pyl).
      skip_analysis_reasons: Reasons to skip analysis. If set to a non-empty
        value, then instead of executing analyze, all configured targets will be
        returned and an empty step will be emitted indicating why analyze wasn't
        executed.
      files_relative_to: Directory that files should be made relative to.
        Passed down to chromium_checkout.get_files_affected_by_patch. If
        omitted, the default value set by that function will be used.
      turboci_build_check_id: The check ID of the build check to create for the
        compile. If empty, a unique check ID will be used instead.
      turboci_analyze_check_id: The check ID of the analyze check to create for
        the analyze.

    Returns:
      A Tuple of
        RawResult object with the status of compile step
          and the failure message if it failed
        Configuration of the build/test.
    """

    skip_analysis_reasons = list(skip_analysis_reasons or [])

    submodule_analyze = self.m.tryserver.is_tryserver and any(
      f.lower() == 'true'
      for f in self.m.tryserver.get_footer(steps.SUBMODULE_ANALYZE_FOOTER)
    )

    if submodule_analyze:
      affected_files, skip_reasons = (
        self._get_submodule_affected_files_and_skip_reasons(
          files_relative_to=files_relative_to
        )
      )
      skip_analysis_reasons.extend(skip_reasons)
    else:
      affected_files = self.m.chromium_checkout.get_files_affected_by_patch(
        report_via_property=True, relative_to=files_relative_to
      )

    is_deps_only_change = affected_files == ["DEPS"] or submodule_analyze

    # Must happen before without patch steps.
    if self.m.code_coverage.using_coverage:
      self.m.code_coverage.instrument(
        affected_files, is_deps_only_change=is_deps_only_change
      )
      # Don't isolate output files if coverage is skipped anyway
      isolate_output_files_for_coverage = (
        isolate_output_files_for_coverage
        and not self.m.code_coverage.skipping_coverage
      )

    tests = []
    if not builder_config.is_compile_only:
      tests = targets_config.all_tests

    checkout_dir = update_result.checkout_dir
    source_dir = update_result.source_root.path

    turboci_build_check_id = self.m.chromium_turboci.ensure_check_id(
      turboci_build_check_id, BUILD_CHECK_ID
    )

    test_targets, compile_targets = self.determine_compilation_targets(
      builder_id,
      builder_config,
      checkout_dir,
      source_dir,
      build_dir,
      affected_files,
      targets_config,
      skip_analysis_reasons=skip_analysis_reasons,
      turboci_analyze_check_id=turboci_analyze_check_id,
    )

    expand_submodules = (
      'chromium_checkout.expand_submodules'
      in self.m.buildbucket.build.input.experiments
    )
    if expand_submodules and not submodule_analyze:
      self._experimental_submodule_analyze(
        builder_id,
        builder_config,
        source_dir,
        build_dir,
        files_relative_to,
        targets_config,
        test_targets,
        compile_targets,
      )

    self.m.chromium_turboci.update_build_check_compile_targets(
      turboci_build_check_id,
      compile_targets,
      'results from analyze',
    )

    # Compiles and isolates test suites.
    raw_result = result_pb2.RawResult(status=common_pb.SUCCESS)
    execution_info = None

    if compile_targets:
      if additional_compile_targets:
        compile_targets = list(compile_targets)
        compile_targets.extend(additional_compile_targets)
        self.m.chromium_turboci.update_build_check_compile_targets(
          turboci_build_check_id,
          compile_targets,
          'targets explicitly requested by recipe',
        )

      self.m.chromium_turboci.set_build_check_planned(
        turboci_build_check_id, 'compile targets determined'
      )

      tests = self.tests_in_compile_targets(test_targets, tests)
      compile_targets = sorted(set(compile_targets))
      raw_result, compile_output = self.compile_specific_targets(
        build_dir,
        builder_id,
        builder_config,
        update_result,
        targets_config,
        compile_targets,
        tests,
        override_execution_mode=ctbc.COMPILE_AND_TEST,
        include_utr_instruction=True,
        affected_files=affected_files,
      )

      self.m.chromium_turboci.finalize_build_check(
        turboci_build_check_id, 'executed compile', raw_result=raw_result
      )

      if compile_output:
        _, execution_info = self.isolate_test_targets(
          source_dir,
          build_dir,
          builder_config,
          update_result,
          compile_output,
          isolate_output_files_for_coverage=isolate_output_files_for_coverage,
        )

    else:
      self.m.chromium_turboci.set_build_check_planned(
        turboci_build_check_id, 'compile targets determined'
      )
      self.m.chromium_turboci.finalize_build_check(
        turboci_build_check_id, 'no compile necessary'
      )

      def is_source_file(filepath):
        # DEPS files embed include_rules, which we want to run the checkdeps
        # test for
        if self.m.path.basename(filepath) == 'DEPS':
          return True
        _, ext = self.m.path.splitext(filepath)
        return ext in ['.c', '.cc', '.cpp', '.h', '.java', '.mm']

      # Even though the patch doesn't require a compile on this platform,
      # we'd still like to run tests not depending on
      # compiled targets (that's obviously not covered by the
      # 'analyze' step) if any source files change.
      if any(is_source_file(f) for f in affected_files):
        tests = [t for t in tests if not t.compile_targets()]
      else:
        tests = []

    task = Task(
      builder_config=builder_config,
      test_suites=tests,
      update_result=update_result,
      build_dir=build_dir,
      affected_files=affected_files,
      swarming_execution_info=execution_info,
    )
    return raw_result, task

  def _get_submodule_affected_files_and_skip_reasons(
    self,
    files_relative_to: str | Path | None = None,
    step_name_prefix: str = '',
  ) -> tuple[list[str], list[str]]:
    """Gets affected files with submodule expansion and checks for skip reasons."""
    submodule_paths_result = (
      self.m.chromium_checkout.get_files_affected_by_patch_with_submodules(
        report_via_property=True,
        relative_to=files_relative_to,
        step_name_prefix=step_name_prefix,
      )
    )

    # Similar to nested submodules, sub-repo DEPS changes represent transitive
    # rolls (e.g. CIPD packages or dependencies not yet migrated to submodules)
    # that GN analyze cannot inspect. Skip analyze to safely run all tests.
    submodule_deps = [
      f
      for f in submodule_paths_result.affected_files
      if f != 'DEPS' and f.endswith('/DEPS')
    ]
    skip_reasons = []
    if submodule_paths_result.nested_submodules:
      skip_reasons.append(
        'nested submodules detected: '
        + ', '.join(submodule_paths_result.nested_submodules)
      )
    if submodule_paths_result.deleted_submodules:
      skip_reasons.append(
        'deleted submodules detected: '
        + ', '.join(submodule_paths_result.deleted_submodules)
      )
    if submodule_paths_result.unresolvable_submodules:
      skip_reasons.append(
        'unresolvable submodules detected: '
        + ', '.join(submodule_paths_result.unresolvable_submodules)
      )
    if submodule_deps:
      skip_reasons.append(
        'submodule DEPS modified: ' + ', '.join(submodule_deps)
      )

    return submodule_paths_result.affected_files, skip_reasons

  def _experimental_submodule_analyze(
    self,
    builder_id: chromium_types.BuilderId,
    builder_config: ctbc.BuilderConfig,
    source_dir: Path,
    build_dir: Path,
    files_relative_to: Path | None,
    targets_config: targets_config_module.TargetsConfig,
    test_targets: Collection[str],
    compile_targets: Collection[str],
  ):
    """Orchestrates the experimental submodule analyze step."""
    affected_files, skip_reasons = (
      self._get_submodule_affected_files_and_skip_reasons(
        files_relative_to=files_relative_to,
        step_name_prefix='[Experimental] ',
      )
    )

    with self.m.step.nest('[Experimental] analyze submodules') as presentation:
      if skip_reasons:
        presentation.step_text = 'skipping analyze:<br/>* ' + '<br/>* '.join(
          skip_reasons
        )
        return

      exp_test_targets, exp_compile_targets = (
        self._run_experimental_submodule_analyze(
          builder_id,
          builder_config,
          source_dir,
          build_dir,
          affected_files,
          targets_config,
        )
      )

      exp_test_targets_sorted = sorted(exp_test_targets)
      exp_compile_targets_sorted = sorted(exp_compile_targets)

      test_targets_added = sorted(
        set(exp_test_targets_sorted) - set(test_targets)
      )
      test_targets_removed = sorted(
        set(test_targets) - set(exp_test_targets_sorted)
      )
      compile_targets_added = sorted(
        set(exp_compile_targets_sorted) - set(compile_targets)
      )
      compile_targets_removed = sorted(
        set(compile_targets) - set(exp_compile_targets_sorted)
      )

      presentation.logs['test_targets'] = exp_test_targets_sorted
      presentation.logs['compile_targets'] = exp_compile_targets_sorted
      if test_targets_added:
        presentation.logs['test_targets_added'] = test_targets_added
      if test_targets_removed:
        presentation.logs['test_targets_removed'] = test_targets_removed
      if compile_targets_added:
        presentation.logs['compile_targets_added'] = compile_targets_added
      if compile_targets_removed:
        presentation.logs['compile_targets_removed'] = compile_targets_removed

      presentation.properties['experimental_submodule_analyze'] = {
        'diff': {
          'test_targets_added': test_targets_added,
          'test_targets_removed': test_targets_removed,
          'compile_targets_added': compile_targets_added,
          'compile_targets_removed': compile_targets_removed,
        },
      }

  def _run_experimental_submodule_analyze(
    self,
    builder_id: chromium_types.BuilderId,
    builder_config: ctbc.BuilderConfig,
    source_dir: Path,
    build_dir: Path,
    submodule_affected_files: Iterable[str],
    targets_config: targets_config_module.TargetsConfig,
  ) -> tuple[Collection[str], Collection[str]]:
    """Runs filter.analyze for experimental submodule expansion.

    This performs the same target and config setup that
    determine_compilation_targets does prior to invoking filter.analyze, but
    runs in isolation without updating production TurboCI check results or
    build targets.
    """
    tests = (
      targets_config.all_tests if not builder_config.is_compile_only else []
    )
    compile_targets = targets_config.compile_targets
    test_targets = sorted(set(self._all_compile_targets(tests)))
    additional_compile_targets = sorted(
      set(compile_targets) - set(test_targets)
    )
    analyze_names = [
      'chromium',
      *builder_config.analyze_names,
      self.m.chromium.c.TARGET_PLATFORM,
    ]
    additional_exclusions = {
      exclusion: 'builder config additional exclusions'
      for exclusion in builder_config.additional_exclusions
    }

    return self.m.filter.analyze(
      source_dir,
      build_dir,
      submodule_affected_files,
      test_targets,
      additional_compile_targets,
      builder_id=builder_id,
      additional_names=analyze_names,
      additional_exclusions=additional_exclusions,
      ignored_exclusion_patterns=['DEPS'],
    )

  def get_first_tag(self, key):
    '''Returns the first buildbucket tag value for a given key

    Buildbucket tags can have multiple values for a single key, but in most
    cases, we can assume just one value.

    Args:
      key: the key to look up
    Returns:
      the first value for this key or None
    '''
    for string_pair in self.m.buildbucket.build.tags:
      if string_pair.key == key:
        return string_pair.value

    return None

  def report_builders(self, builder_config, report_mirroring_builders=False):
    """Reports the builders being executed by the bot."""

    # Tester - returns (parent ID, builder ID)
    # Builder - returns (builder ID, )
    # This way testers sort after their triggering builder and before
    # other builders
    def details(builder_id):
      spec = builder_config.builder_db[builder_id]
      if not spec.parent_buildername:
        return (builder_id,)
      parent_builder_id = chromium_types.BuilderId.create_for_group(
        spec.parent_builder_group or builder_id.group, spec.parent_buildername
      )
      return (parent_builder_id, builder_id)

    builder_ids = (
      builder_config.builder_ids_in_scope_for_testing
      if self.m.tryserver.is_tryserver
      else builder_config.builder_ids
    )

    builder_details = sorted(details(b) for b in builder_ids)

    def present(details):
      if len(details) == 1:
        builder_id = details[0]
        return "running builder '{}' on group '{}'".format(
          builder_id.builder, builder_id.group
        )

      parent_builder_id, builder_id = details
      return (
        "running tester '{}' on group '{}' against builder '{}' on group '{}'"
      ).format(
        builder_id.builder,
        builder_id.group,
        parent_builder_id.builder,
        parent_builder_id.group,
      )

    lines = [''] + [present(d) for d in sorted(builder_details)]

    result = self.m.step.empty('report builders', step_text='\n'.join(lines))

    if report_mirroring_builders and builder_config.mirroring_try_builders:
      # TODO(gbeaty): This property is not well named, it suggests the opposite
      # relationship of what it is
      result.presentation.properties['mirrored_builders'] = sorted(
        [
          '{}:{}'.format(m.group, m.builder)
          for m in builder_config.mirroring_try_builders
        ]
      )

    # Links to upstreams help people figure out if upstreams are broken too
    def link(builder_id):
      bb_builder_id = builder_config.get_buildbucket_builder_id(builder_id)
      # For recipe-side builder configs, we don't have reliable information for
      # the project and bucket, so we assume the same project as the current
      # builder and the same bucket except with try replaced with ci
      if bb_builder_id is None:
        project = self.m.buildbucket.build.builder.project
        bucket = self.m.buildbucket.build.builder.bucket.replace('try', 'ci')
      else:
        project = bb_builder_id.project
        bucket = bb_builder_id.bucket
      builder = builder_id.builder
      return f'https://ci.chromium.org/p/{project}/builders/{bucket}/{builder}'

    for d in builder_details:
      for builder_id in d:
        result.presentation.links[builder_id.builder] = link(builder_id)

  def print_link_to_results(self):
    """Prints a step with a link to the 'test results' tab in Milo.

    Useful for led builds that are stuck on the old UI but are still able to
    render the results tab given the right URL.

    TODO(crbug.com/1264479): Remove this once led builds are fully on the new UI
    """
    if not self.m.led.launched_by_led:
      return
    server = self.m.swarming.current_server
    server = server.replace('http://', '')
    server = server.replace('https://', '')
    invocation_id = 'task-{server}-{task_id}'.format(
      server=server, task_id=self.m.swarming.task_id
    )
    result = self.m.step('test results link', cmd=None)
    result.presentation.links['results UI'] = (
      self.m.test_utils.luci_milo_test_results_url(invocation_id)
    )

  def _all_compile_targets(self, tests):
    """Returns the compile_targets for all the enabled Tests in |tests|."""
    return sorted(
      set(
        x for test in tests for x in test.compile_targets() if test.is_enabled
      )
    )

  def tests_in_compile_targets(self, compile_targets, tests):
    """Returns the tests in |tests| that have at least one of their compile
    targets in |compile_targets|."""
    result = []
    for test in tests:
      test_compile_targets = test.compile_targets()
      # Always return tests that don't require compile. Otherwise we'd never
      # run them. Also include tests that are disabled to reserve their steps
      if (
        (set(compile_targets) & set(test_compile_targets))
        or not test_compile_targets
        or not test.is_enabled
      ):
        result.append(test)
    return result

  def lookup_builder_gn_args(
    self,
    source_dir: Path,
    builder_id,
    builder_config,
    mb_config_path=None,
    mb_phase=None,
  ):
    # Lookup GN args for the associated builder
    parent_builder_id = chromium_types.BuilderId.create_for_group(
      builder_config.parent_builder_group or builder_id.group,
      builder_config.parent_buildername,
    )
    parent_builder_spec = builder_config.builder_db[parent_builder_id]

    # Make the chromium config that the parent would use
    parent_chromium_config = self.m.chromium.make_config(
      parent_builder_spec.chromium_config,
      # Set TEST_ONLY so that it doesn't validate the builder's
      # TARGET_PLATFORM against the tester's HOST_PLATFORM
      TEST_ONLY=True,
      **parent_builder_spec.chromium_config_kwargs,
    )
    for c in parent_builder_spec.chromium_apply_config:
      self.m.chromium.apply_config(c, parent_chromium_config)

    android_version_name, android_version_code = (
      self.get_android_version_details(
        source_dir, parent_builder_spec.android_version
      )
    )
    self.m.chromium.mb_lookup(
      source_dir,
      parent_builder_id,
      mb_config_path=mb_config_path,
      chromium_config=parent_chromium_config,
      phase=mb_phase,
      android_version_name=android_version_name,
      android_version_code=android_version_code,
      name='lookup builder GN args',
    )

  def _gen_runtime_dict_for_skylab(
    self,
    source_dir: Path,
    build_dir: Path,
    target,
  ):
    """Generate the rel path of runtime deps to src dir.

    Skylab DUT in CrOS does not support isolate. We reuse the isolate file
    generated at compile step to decide the runtime dependencies.

    Args:
      target: The ninja build target for the test, e.g. url_unittests.

    Returns:
      checkout_dir: The directory where the checkout was performed.
      source_dir: The path to the top-level repo.
      build_dir: The path to the directory containing built outputs.
      runtime_dict: Relative paths to the src dir of the runtime deps, with
          the key of its relative path to build dir, original form in the
          isolate file.
    """
    with self.m.step.nest('collect runtime deps for %s' % target) as step:
      abs_runtime_deps = build_dir.joinpath(target + '.isolate')
      if not self.m.path.exists(abs_runtime_deps):
        failure_msg = 'Failed to find the %s.isolate.' % target
        step.status = self.m.step.FAILURE
        raise self.m.step.StepFailure(failure_msg)

      content = self.m.file.read_text('read isolate file', abs_runtime_deps)
      try:
        isolate_dict = eval(content.strip())
      except Exception as e:
        failure_msg = 'Failed to parse the %s.isolate' % target
        step.status = self.m.step.FAILURE
        raise self.m.step.StepFailure(failure_msg) from e

      if len(isolate_dict.get('variables', {}).get('files', [])) == 0:
        failure_msg = 'No dependencies attached to target %s.' % target
        step.status = self.m.step.FAILURE
        raise self.m.step.StepFailure(failure_msg)

      runtime_dict = {}
      for f in isolate_dict['variables']['files']:
        abs_file_path = self.m.path.abspath(self.m.path.join(build_dir, f))
        rel_to_out_dir = self.m.path.relpath(abs_file_path, source_dir)
        runtime_dict[f] = str(rel_to_out_dir)

      return runtime_dict

  def _upload_runtime_deps_for_skylab(
    self,
    checkout_dir: Path,
    source_dir: Path,
    build_dir: Path,
    gcs_bucket,
    gcs_path,
    target,
    runtime_deps,
  ):

    def is_dir(rel_path):
      return self.m.path.isdir(source_dir / rel_path)

    def is_file(rel_path):
      return self.m.path.isfile(source_dir / rel_path)

    with self.m.step.nest('upload skylab runtime deps for %s' % target):
      # TODO(crbug/1276489): Remove below condition once we get rid of the
      # build target lacros_version_metadata in src.
      if not self.m.path.exists(build_dir / 'metadata.json'):
        version = self.m.chromium.get_version(source_dir)
        version_str = '%(MAJOR)s.%(MINOR)s.%(BUILD)s.%(PATCH)s' % version
        self.m.file.write_json(
          'write metadata.json',
          build_dir / 'metadata.json',
          dict(content={'version': version_str}, metadata_version=1),
        )

      # Lacros TLS provision requires a metadata.json containing the chrome
      # version along with the squashfs file. If user does not configure it
      # in the compile targets, we create one for the tests.
      out_dir = self.m.path.relpath(build_dir, checkout_dir)

      metadata_arch = arch_prop.ArchiveData(
        gcs_bucket=gcs_bucket,
        gcs_path='%s/%s' % (gcs_path, target),
        archive_type=arch_prop.ArchiveData.ARCHIVE_TYPE_FLATTEN_FILES,
        base_dir=str(out_dir),
        files=['metadata.json'],
      )
      browser_arch = arch_prop.ArchiveData(
        gcs_bucket=gcs_bucket,
        gcs_path='%s/%s/skylab_runtime_deps.tar.zst' % (gcs_path, target),
        archive_type=arch_prop.ArchiveData.ARCHIVE_TYPE_TAR_ZSTD,
        tar_zstd_params=arch_prop.TarZstdParams(
          compression_level=4,
        ),
        base_dir='src',
        files=[v for v in runtime_deps if is_file(v)],
        dirs=[v for v in runtime_deps if is_dir(v)],
        root_permission_override='755',
      )
      self.m.archive.generic_archive(
        checkout_dir,
        source_dir,
        build_dir=checkout_dir,
        update_properties={},
        config=arch_prop.InputProperties(
          archive_datas=[browser_arch, metadata_arch]
        ),
        use_hardlink=True,
      )
      return 'gs://{}{}/{}/{}'.format(
        gcs_bucket,
        '/experimental' if self.m.runtime.is_experimental else '',
        gcs_path,
        target,
      )

  def prepare_artifact_for_skylab(
    self,
    builder_config,
    checkout_dir: Path,
    source_dir: Path,
    build_dir: Path,
    tests,
    *,
    phase='with patch',
  ):
    if not (builder_config.skylab_gs_bucket and tests):
      raise self.m.step.InfraFailure(
        'Test was not scheduled because of absent lacros_gcs_path.'
      )
    gcs_path = ''
    if builder_config.skylab_gs_extra:
      gcs_path += '%s/' % builder_config.skylab_gs_extra
    _build_id = self.m.buildbucket.build.id
    # This is for `led launch`, where the build_id is always 0.
    if not _build_id:  # pragma: no cover
      _build_id = int(time.time())
    gcs_path += '%d_%s' % (_build_id, phase.replace(' ', '_'))
    with self.m.step.nest('prepare skylab tests'):
      tests_by_target = collections.defaultdict(list)
      for t in tests:
        tests_by_target[t.target_name].append(t)
      runtime_dict_by_target = {
        t: self._gen_runtime_dict_for_skylab(source_dir, build_dir, t)
        for t in sorted(tests_by_target)
      }
      runtime_deps = list(
        reduce(
          lambda a, b: a | b,
          [set(v.values()) for v in runtime_dict_by_target.values()],
        )
      )

      # When Siso build enables `without bytes` option, the RBE outputs
      # aren't fetched to the host machine by default.
      # It needs to download them explicitly by running `siso fs flush`.
      if self.m.siso.without_bytes:
        with self.m.context(cwd=build_dir):
          self.m.siso.fs_flush(
            'fetch RBE artifacts from CAS',
            source_dir,
            [
              self.m.path.relpath(
                self.m.path.abspath(self.m.path.join(source_dir, f)),
                self.m.path.abspath(build_dir),
              )
              for f in runtime_deps
            ],
          )

      runtime_deps_gcs_path = self._upload_runtime_deps_for_skylab(
        checkout_dir,
        source_dir,
        build_dir,
        builder_config.skylab_gs_bucket,
        gcs_path,
        UNIFIED_RUNTIME_DEPS_NAME,
        runtime_deps,
      )
      for target, tests_for_target in tests_by_target.items():
        for t in tests_for_target:
          exe = 'bin/run_%s' % t.target_name
          if t.is_GPU_test:  # pragma: nocover
            exe = './chrome'
          t.exe_rel_path = runtime_dict_by_target.get(target).get(exe)
          t.build_output_dir = self.m.path.relpath(build_dir, source_dir)
          t.lacros_gcs_path = runtime_deps_gcs_path

  def run_tests(
    self,
    checkout_dir: Path,
    source_dir: Path,
    build_dir: Path,
    builder_id,
    builder_config,
    tests,
    *,
    update_result: bot_update.api.Result | None = None,
    upload_results=None,
  ):
    if not tests:
      return

    self.configure_swarming(False, builder_group=builder_id.group)
    test_runner = self.create_test_runner(
      checkout_dir,
      source_dir,
      build_dir,
      tests,
      serialize_tests=builder_config.serialize_tests,
      retry_failed_shards=builder_config.retry_failed_shards,
      # If any tests export coverage data we want to retry invalid shards due
      # to an existing issue with occasional corruption of collected coverage
      # data.
      retry_invalid_shards=builder_config.retry_invalid_shards
      or any(
        t.runs_on_skylab or (t.runs_on_swarming and t.isolate_profile_data)
        for t in tests
      ),
      include_utr_instruction=True,
    )
    with self.wrap_chromium_tests(
      checkout_dir, source_dir, build_dir, tests=tests
    ):
      test_failure_summary = test_runner()

      if self.m.code_coverage.using_coverage:
        self.m.code_coverage.process_coverage_data(tests)

      if self.m.pgo.using_pgo:
        self.m.pgo.process_pgo_data(source_dir, tests)

      if self.m.orderfile.using_orderfile and self.m.orderfile.upload_orderfile:
        assert update_result is not None
        self.m.orderfile.process_orderfile_data(source_dir, update_result)

      if self.m.pinlist.upload_pinlist:
        assert update_result is not None
        self.m.pinlist.process_pinlist_data(update_result)

      test_success = True
      if test_failure_summary:
        test_success = False

      self.m.archive.generic_archive_after_tests(
        checkout_dir,
        source_dir,
        build_dir=build_dir,
        upload_results=upload_results,
        test_success=test_success,
      )
      self.m.test_utils.record_suite_statuses(tests, '')
      return test_failure_summary

  def find_suites_to_skip(self):
    """Returns a set of tests that has passed in the same patchset."""
    if not self.m.cv.active or not any(
      tag.key == 'cq_equivalent_cl_group_key'
      for tag in self.m.buildbucket.build.tags
    ):
      return set()

    with self.m.step.nest(
      'check previous builds for skippable test suites'
    ) as presentation:
      equivalent_key = self.m.cv.equivalent_cl_group_key
      bucket = self.m.buildbucket.build.builder.bucket
      predicate = builds_service_pb2.BuildPredicate(
        builder=self.m.buildbucket.build.builder,
        status=common_pb.ENDED_MASK,
        tags=self.m.buildbucket.tags(
          cq_equivalent_cl_group_key=str(equivalent_key)
        ),
        create_time=common_pb.TimeRange(
          start_time=timestamp_pb2.Timestamp(
            # Look back 1 day
            seconds=self.m.buildbucket.build.create_time.ToSeconds()
            - 60 * 60 * 24
          )
        ),
      )
      if bucket.endswith('.shadow'):
        predicate.builder.bucket = bucket[: -len('.shadow')]
      # Compilators should check their orchestrator which has the test statuses
      if 'orchestrator' in self.m.properties:
        predicate.builder.builder = self.m.properties['orchestrator'][
          'builder_name'
        ]

      builds = self.m.buildbucket.search(
        predicate, step_name='find equivalent patchset builds'
      )
      builds = [
        build for build in builds if build.id != self.m.buildbucket.build.id
      ]

      def successful_tests_from_build(build):
        build_props = build.output.properties
        successful_suites = set()
        for test_name, test_status in build_props['test_status'].items():
          if test_status != 'Success':
            continue
          # Tests can still fail if they were rejected by flake endorser in which
          # case we cannot skip them
          if 'flake_endorser_rejections' in build_props:
            if (
              'flaky_suites' in build_props['flake_endorser_rejections']
              and test_name
              in build_props['flake_endorser_rejections']['flaky_suites']
            ):
              continue
            if (
              'invalid_suites' in build_props['flake_endorser_rejections']
              and test_name
              in build_props['flake_endorser_rejections']['invalid_suites']
            ):
              continue
          successful_suites.add(test_name)
        return successful_suites

      # Only skip tests that have explicitly passed
      all_successful_suites = [
        successful_tests_from_build(build)
        for build in builds
        if 'test_status' in build.output.properties
      ]
      if all_successful_suites:
        tests_to_skip = set.union(*all_successful_suites)
        # Flakiness can cause an empty set
        if tests_to_skip:
          presentation.step_text = (
            'Skippable tests were found \n '
            + 'The following tests are skippable because they have passed in '
            + 'the last 24 hours with the same equivalent patchset: \n'
          ) + '\n'.join(tests_to_skip)
          presentation.properties['skippable_tests'] = list(tests_to_skip)
          return tests_to_skip
    return set()
