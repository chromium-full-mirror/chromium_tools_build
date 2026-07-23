# Copyright 2018 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import collections
import json
import logging
import os
import re
import sys

from recipe_engine import config_types
from recipe_engine import recipe_api

from RECIPE_MODULES.build import chromium_swarming

from . import constants

MAX_CANDIDATE_FILES = 200

# This should be same as toolchain side token at bit.ly/3F3IIMC
INSTRUMENT_ALL_JACOCO_OVERRIDE_TOKEN = 'INSTRUMENT_ALL_JACOCO'

PROBLEMATIC_JACOCO_PATHS = []


class CodeCoverageApi(recipe_api.RecipeApi):
  """This module contains apis to generate code coverage data."""

  def __init__(self, properties, *args, **kwargs):
    super().__init__(*args, **kwargs)
    # Temp dir for report.
    self._report_dir = None
    # Temp dir for metadata
    self._metadata_root_dir = None
    # Path to checked out repo
    self._source_dir = None
    # Path to director containing the build artifacts e.g. <root>/out/coverage
    self._build_dir = None
    # When set, subset of source files to include in the coverage report for
    # each tool.
    self._eligible_files_by_tool = collections.defaultdict(list)
    # When set, indicates that current context is per-cl coverage for try jobs.
    self._is_per_cl_coverage = False
    # The list of profdata gs paths to be uploaded.
    self._merged_profdata_gs_paths = []
    # The list of coverage metadata gs paths to be uploaded.
    self._coverage_metadata_gs_paths = []
    # When set, the base builder name (without test type suffix) to use instead
    # of the real builder name to compose mimic builder names
    self._override_builder_name = None
    # The list of mimic builder names to be uploaded.
    self._mimic_builder_names = []
    # The bucket to which code coverage data should be uploaded.
    self._gs_bucket = (
        properties.coverage_gs_bucket or constants.DEFAULT_BUCKET_NAME)
    # List of test types to run in a builder. By default, it runs overall
    # coverage. This is used in Clang and JaCoCo coverage at present.
    self._test_types = properties.coverage_test_types or [
        constants.test_types.OVERALL
    ]
    # The key in constants.EXCLUDE_SOURCES to get corresponding excluded file
    # pattern.
    self._exclude_sources_key = properties.coverage_exclude_sources
    # Internal nested dict to store paths to metadata folder lists, indexed by
    # test type and tool type. e.g.
    # {'overall': {'clang': 'path1', 'jacoco': 'path2'}}
    self._metadata_dir_by_tool_type_by_test_type = collections.defaultdict(dict)
    # When set True, Clang coverage is enabled.
    self._use_clang_coverage = properties.use_clang_coverage
    # When set True, Java coverage is enabled.
    self._use_java_coverage = properties.use_java_coverage
    # When set True, JavaScript coverage is enabled.
    self._use_javascript_coverage = properties.use_javascript_coverage
    # Platform for which this build is running.
    self._platform = None
    # Determines whether a component mapping should be used for non-per-cl
    # coverage runs.
    self._include_component_mapping = True
    # Absolute path to the file containing line number mappings from bot
    # version of files to gerrit version
    self._bot_to_gerrit_mapping_file = None
    # Determines if the coverage data be exported to zoss or not
    self._export_coverage_to_zoss = properties.export_coverage_to_zoss or False
    # Determines if blame list data should be generated for files or not
    self._generate_blame_list = properties.generate_blame_list or False
    # Whether to skip coverage
    self._skipping_coverage = False

  @property
  def use_clang_coverage(self):
    return self._use_clang_coverage

  @property
  def use_java_coverage(self):
    return self._use_java_coverage

  @property
  def use_javascript_coverage(self):
    return self._use_javascript_coverage

  @property
  def platform(self):
    if not self._platform:
      self._platform = self.m.chromium.c.TARGET_PLATFORM
    return self._platform

  @property
  def build_id(self):
    build = self.m.buildbucket.build
    build_id = build.id
    if self.m.led.launched_by_led:
      build_id = self.m.swarming.task_id
    return build_id

  @property
  def skipping_coverage(self):
    return self._skipping_coverage

  @property
  def cov_executable(self):
    """Returns the path to the llvm-cov executable."""
    return self.m.profiles.llvm_exec_path('llvm-cov')

  @property
  def report_dir(self):
    """A temporary directory to save a report to. Created on first access."""
    if not self._report_dir:
      self._report_dir = self.m.path.mkdtemp()
    return self._report_dir

  def _all_eligible_files(self):
    """Generates the list of eligible files of all tools."""
    all_eligible_files = []
    for files in self._eligible_files_by_tool.values():
      all_eligible_files.extend(files)
    return all_eligible_files

  def _has_eligible_files_for_tool(self, tool):
    """Checks as a step and returns if any eligible files for a given tool"""
    if not self._eligible_files_by_tool.get(tool):
      self.m.step.empty('skip processing %s coverage data because '
                        'no related source file changed' % tool)
      return False
    return True

  def _ensure_metadata_dir(self, test_type, tool_type):
    """Returns a temporary directory for the metadata.

    It's a temporary directory with nested directory structure for test type
    and coverage tool type. Temp dir is created on first call to this method.
    Subdirs are created on first access when processing each test type / tool
    type. Also updates |self._metadata_dir_by_tool_type_by_test_type|.
    """
    if not self._metadata_root_dir:
      self._metadata_root_dir = self.m.path.mkdtemp()
    metadata_dir = self._metadata_root_dir.joinpath(test_type).joinpath(
        tool_type)
    self.m.file.ensure_directory(
        f'ensure metadata dir for {tool_type} coverage', metadata_dir)
    self._metadata_dir_by_tool_type_by_test_type[test_type][tool_type] = (
        metadata_dir)
    return metadata_dir

  @property
  def build_dir(self):
    assert self._build_dir, 'build_dir must be set for this recipe module'
    return self._build_dir

  @build_dir.setter
  def build_dir(self, value):
    assert self._build_dir is None, 'build_dir cannot be set multiple times'
    self._build_dir = value

  @property
  def source_dir(self):
    assert self._source_dir, 'source_dir must be set for this recipe_module'
    return self._source_dir

  @source_dir.setter
  def source_dir(self, value):
    assert self._source_dir is None, 'source_dir cannot be set multiple times'
    self._source_dir = value

  @property
  def bot_to_gerrit_mapping_file(self):
    """Generates the line number mapping from bot to Gerrit.

    In order to correctly display the (un)covered line numbers on Gerrit.
    Per-cl metadata's line numbers need to be rebased because the base
    revision of the change in this build is different from the one on Gerrit.

    The mapping get's generated and stored on the first access to the property.
    """
    if not self._bot_to_gerrit_mapping_file:
      output_dir = self.m.path.mkdtemp()
      gerrit_change = self.m.buildbucket.build.input.gerrit_changes[0]
      local_to_gerrit_diff_mapping_file = output_dir.joinpath(
          constants.BOT_TO_GERRIT_LINE_NUM_MAPPING_FILE_NAME)
      timeout_in_minutes = 15
      maybe_auth_args = []
      if gerrit_change.host in constants.NEEDS_AUTH_HOSTS:
        token = self.m.service_account.default().get_access_token(
            scopes=['https://www.googleapis.com/auth/gerritcodereview'])
        maybe_auth_args = [
            '--token-path',
            self.m.raw_io.input_text(token),
        ]
      project_checkout_path = self.m.gclient.get_gerrit_patch_root()
      base_checkout_path = self.m.gclient.c.solutions[
          0].name if self.m.gclient.c else 'src'
      args = [
          'python3',
          self.resource('rebase_line_number_from_bot_to_gerrit.py'),
          '--host',
          gerrit_change.host,
          '--project',
          gerrit_change.project,
          '--base-checkout-path',
          base_checkout_path,
          '--change',
          gerrit_change.change,
          '--patchset',
          gerrit_change.patchset,
          '--src-path',
          self.source_dir,
          '--output-file',
          local_to_gerrit_diff_mapping_file,
      ]
      if project_checkout_path:
        args += [
            '--project-checkout-path',
            project_checkout_path,
        ]
      self.m.step(
          'generate line number mapping from bot to Gerrit',
          args + maybe_auth_args + self._all_eligible_files(),
          timeout=timeout_in_minutes * 60,
          stdout=self.m.json.output())
      self._bot_to_gerrit_mapping_file = local_to_gerrit_diff_mapping_file
    return self._bot_to_gerrit_mapping_file

  def _compose_mimic_builder_name(self, test_type):
    """Mimic builder name composed from the test type.

    If test_type is overall coverage, return real builder name. Otherwise,
    return {real_builder_name}_{test_type}.

    Use override_builder_name instead of real builder name if provided.
    """
    builder_name = (
        self._override_builder_name or self.m.buildbucket.build.builder.builder)
    suffix = '' if test_type == constants.test_types.OVERALL else ('_' +
                                                                   test_type)
    return builder_name + suffix

  def ensure_clang_coverage_tools(self):
    """Runs a clang script to ensure llvm-profdata tool exists.

    The tool is needed when merging clang coverage data for each test step and
    processing clang coverage data afterwards. The other way to set up the tool
    in chromium is to use `checkout_clang_coverage_tools` gclient config:
    bit.ly/45OU2rA
    """
    clang_update_script = self.m.chromium_checkout.source_dir.joinpath(
        'tools', 'clang', 'scripts', 'update.py')
    args = ['python3', clang_update_script, '--package', 'coverage_tools']
    self.m.step(
        'ensure clang coverage tools',
        args,
    )

  @property
  def using_coverage(self):
    """Checks if the current build is running coverage-instrumented targets."""
    return (self.use_clang_coverage or self.use_java_coverage or
            self.use_javascript_coverage)

  def set_is_per_cl_coverage(self, value):
    self._is_per_cl_coverage = value

  def get_required_build_output_files(self, tests):
    """Get required build output files necessary to run code coverage

    Args:
      tests (list(Test)): List of Test objects

    Returns:
      List of Paths to build output files
    """
    # Pass |may_use_binaries_list_file| as False to make it figure out the
    # binaries from scratch. It's needed here because at this time the file
    # may already exist in the build output cache.
    binary_paths = self.get_binaries(tests, may_use_binaries_list_file=False)
    if self.m.siso.enabled and self.m.siso.without_bytes:
      files = binary_paths
    else:
      files = [
          binary_path for binary_path in binary_paths
          if self.m.path.exists(binary_path)
      ]

    # Write the list of used binary paths to a json file. In
    # orchestrator/compilator structure, this file is passed from compilator
    # to orchestrator, so the orchestrator build can directly use the binary
    # paths as relevant binaries for clang coverage, insteading figuring that
    # out from tests again.
    binary_relative_paths = ([
        self.m.path.relpath(path, self.build_dir) for path in files
    ])
    binary_relative_paths_json_file_path = (
        self.build_dir / constants.BINARY_RELATIVE_PATHS_JSON_FILE_NAME)
    self.m.file.write_json(
        name='create binary relative path list file',
        dest=binary_relative_paths_json_file_path,
        data=binary_relative_paths)
    files.append(binary_relative_paths_json_file_path)

    if self.platform == 'android':
      step_result = self.m.step('Get jacoco and jar files for java coverage', [
          'python3',
          self.resource('get_jacoco_and_jar_files_for_java.py'),
          '--sources-json-dir',
          self.build_dir,
          '--output-json',
          self.m.json.output(),
      ])
      paths = step_result.json.output

      files.extend([self.m.path.abs_to_path(f) for f in paths])

    return files

  def _get_android_native_lib_for_target(self, target):
    """Finds native lib paths for a isolate target."""
    step_result = self.m.step(
        'Get native libs for %s' % target,
        [
            'python3',
            self.resource('get_native_libraries_for_android_target.py'),
            '--chromium-output-dir',
            self.build_dir,
            '--output-json',
            self.m.json.output(),
            '--isolate-target',
            target,
        ],
        step_test_data=lambda: self.m.json.test_api.output([]),
    )
    return step_result.json.output

  def _is_gtest(self, t):
    """Returns True if the test object represents a GTest executable."""
    if 'GTest' in type(t).__name__:
      return True
    module_scheme = getattr(
        getattr(t.spec, 'resultdb', None), 'module_scheme', None)
    return module_scheme == 'gtest'

  def get_binaries(self,
                   tests,
                   may_use_binaries_list_file,
                   test_type=constants.test_types.OVERALL):
    """Returns paths to the binary for the given test objects.

    By default, use the name of the target as the binary.

    Args:
      tests (list(Test)): List of Test objects
      may_use_binaries_list_file (bool): Whether the method can use the
        list of stored binary paths read from a file (if it exists). In
        orchestrator/compilator structure, this file is passed from
        compilator to orchestrator. It includes the binary list relevant
        to |tests|, discovered by |get_binaries| API invoked on compilator.
      test_type (string): Test type being processed.

    Returns:
      List of Paths to test binaries
    """
    if may_use_binaries_list_file:
      binary_relative_paths_json_file_path = (
          self.build_dir / constants.BINARY_RELATIVE_PATHS_JSON_FILE_NAME)

      if self.m.path.exists(binary_relative_paths_json_file_path):
        rel_paths = self.m.file.read_json(
            'read binary relative path list file',
            self.build_dir / constants.BINARY_RELATIVE_PATHS_JSON_FILE_NAME,
            test_data=["some_library"])
        return {self.build_dir / path for path in rel_paths}

    # TODO(crbug.com/899974): Implement a sturdier approach that also works in
    # separate builder-tester setup.
    binaries = set()

    # Android and Fuchsia platform needs to use unstripped files for llvm-cov.
    # The unstripped artifacts will be generated under lib.unstripped/ or
    # exe.unstripped/.
    if self.platform in ('android', 'fuchsia'):
      step_result = self.m.step('Get all unstripped artifacts paths', [
          'python3',
          self.resource('get_unstripped_paths.py'),
          '--chromium-output-dir',
          self.build_dir,
          '--output-json',
          self.m.json.output(),
      ])
      unstripped_paths = step_result.json.output

    for t in tests:
      # There are a number of local isolated scripts such as
      # check_static_initializers, checkdeps, checkperms etc. that introduce
      # extra complexitites, meawhile, it's still unclear if there is any value
      # in collecting code coverage data for them. So, for simplicity, skip
      # tests that don't run on swarming for now.
      if not t.runs_on_swarming:
        continue

      target = t.isolate_target

      # Do not get the test binary if it does not correspond to test type.
      if (self.platform in constants.PLATFORM_TO_TARGET_NAME_PATTERN_MAP and
          not re.search(
              constants.PLATFORM_TO_TARGET_NAME_PATTERN_MAP[self.platform]
              [test_type], target)):
        continue

      patterns = [
          # Following are scripts based tests that don't build any binaries.
          ['blink_python_tests', None],
          ['cronet_sizes', None],
          ['extension_docserver_python_unittests', None],
          ['fuchsia_pytype', None],
          ['grit_python_unittests', None],
          ['metrics_python_tests', None],
          ['mojo_python_unittests', None],
          ['python3_smoketest', None],
          ['telemetry_gpu_unittests', None],

          # Following are mappings from isolate target names to binary names.
          ['telemetry_gpu_integration_test', 'chrome'],
          ['telemetry_unittests', 'chrome'],
          ['telemetry_perf_unittests', 'chrome'],
          ['chromedriver_py_tests', 'chrome'],
          ['chromedriver_replay_unittests', 'chrome'],
          ['chrome_all_tast_tests', 'chrome'],
          ['cros_browser_sanity_test', 'chrome'],
          ['mini_installer_tests', 'setup'],
          ['webdriver_wpt_tests', 'chrome'],
          ['xr_browser_tests', 'xr_browser_tests_binary'],
          ['content_shell_crash_test', 'content_shell'],
          ['.*blink_web_tests', 'content_shell'],
          ['.*blink_wpt_tests', 'content_shell'],
          ['chrome_wpt_tests', 'chrome'],
          ['headless_shell_wpt', 'headless_shell'],
          ['.*_ozone', target[:-len('_ozone')]],
          ['.*_eg2tests_module', 'ios_chrome_eg2tests'],
          [
              'ios_web_view_inttests',
              'ios_web_view_inttests' if self.platform == 'ios' else None
          ],
          ['.*', target if self._is_gtest(t) else None],
      ]
      for pattern, binary in patterns:
        if not re.match(pattern, target):
          continue
        if binary is None:
          break

        if self.platform == 'android':
          so_library_name = 'lib' + binary + '__library.so'
          for unstripped_path in unstripped_paths:
            if unstripped_path.endswith(binary) or unstripped_path.endswith(
                so_library_name):
              binaries.add(self.m.path.abs_to_path(unstripped_path))
              break
          else:
            # Java instrumentation tests' native library names don't include
            # target name. Find these through {target}.isolate files.
            lib_paths = self._get_android_native_lib_for_target(target)
            binaries.update(
                [self.m.path.abs_to_path(path) for path in lib_paths])
        elif self.platform == 'fuchsia':
          exec_name = binary + '__exec'
          for unstripped_path in unstripped_paths:
            if unstripped_path.endswith(exec_name):
              binaries.add(self.m.path.abs_to_path(unstripped_path))
              break
        elif self.platform == 'ios':
          if binary == 'ios_web_view_inttests':
            binaries.add(
                self.build_dir.joinpath('ChromeWebView.framework',
                                        'ChromeWebView'))
            break
          # Actual binary file is at {binary}.app/{binary} for iOS.
          binaries.add(self.build_dir.joinpath(binary + '.app', binary))
        elif self.platform == 'win':
          binaries.add(self.build_dir.joinpath(binary + '.exe'))
        else:
          binaries.add(self.build_dir / binary)

        break

    return sorted(binaries, key=str)

  def filter_and_set_eligible_files(self, candidate_files):
    """Filter candidate_files and assigns them to self._eligible_files_by_tool

    Args:
      candidate_files: A list of string file paths relative to the checkout path

    Returns: None
    """

    def _filter_source_file(file_paths, extensions):
      """Filters source files with valid extensions.

      Args:
        file_paths: A list of string file paths relative to the checkout path.
        extensions: A list of extensions to filter source files.

      Returns:
        A sub-list of the input with valid extensions.
      """
      source_files = []
      for file_path in file_paths:
        if any(file_path.endswith(extension) for extension in extensions):
          source_files.append(file_path)

      return source_files

    if self.use_clang_coverage:
      self._eligible_files_by_tool[constants.tools.CLANG] = (
          _filter_source_file(
              candidate_files,
              constants.TOOLS_TO_EXTENSIONS_MAP[constants.tools.CLANG]))
    if self.use_java_coverage:
      candidate_files = [
          x for x in candidate_files if x not in PROBLEMATIC_JACOCO_PATHS
      ]
      self._eligible_files_by_tool[constants.tools.JACOCO] = (
          _filter_source_file(
              candidate_files,
              constants.TOOLS_TO_EXTENSIONS_MAP[constants.tools.JACOCO]))
    if self.use_javascript_coverage:
      self._eligible_files_by_tool[constants.tools.V8] = (
          _filter_source_file(
              candidate_files,
              constants.TOOLS_TO_EXTENSIONS_MAP[constants.tools.V8]))

  def _validate_test_types(self):
    """Validates that test type to process in build is supported."""
    for test_type in self._test_types:
      if test_type not in constants.SUPPORTED_TEST_TYPES:
        raise Exception('Unsupported test type %s.' % test_type)
    assert not self.use_javascript_coverage or len(self._test_types) == 1, (
        'JS coverage only supports one test type, current types: %s' %
        self._test_types)

  def _set_builder_output_properties_for_uploads(self):
    """Sets the output property of the builder."""
    result = self.m.step.empty('Set builder output properties')
    result.presentation.properties['coverage_metadata_gs_paths'] = (
        self._coverage_metadata_gs_paths)
    result.presentation.properties['mimic_builder_names'] = (
        self._mimic_builder_names)
    result.presentation.properties['merged_profdata_gs_paths'] = (
        self._merged_profdata_gs_paths)
    result.presentation.properties['coverage_gs_bucket'] = (self._gs_bucket)

  def instrument(self,
                 candidate_files,
                 output_dir=None,
                 is_deps_only_change=False):
    """Saves source paths to generate coverage instrumentation for to a file.

    Args:
      candidate_files (list of str): paths to the files we want to instrument,
          relative to the checkout path.
    """
    self.set_is_per_cl_coverage(True)
    contains_jacoco_change = False
    if self.use_java_coverage:
      for candidate_file in candidate_files:
        if candidate_file.startswith('third_party/jacoco'):
          contains_jacoco_change = True
          break
    if contains_jacoco_change:
      # In case of a jacoco change, we write a special token to output file,
      # which indicates toolchain to instrument everything. We do so to surface
      # any compatability issues between jacoco and chromium/src.
      # See crbug/1412466#c47
      files_to_instrument = [INSTRUMENT_ALL_JACOCO_OVERRIDE_TOKEN]
      # Because we are instrumenting everything, there would be large amount
      # of raw coverage data, which we do not care for(because it is mostly for
      # the files outside the CL). Therefore we do not process it either.
      candidate_files = []
      self.m.step.empty('Jacoco change detected. Instrumenting everything!' +
                        ' Generated coverage data will not be processed')
    else:
      skip_step = None
      if len(candidate_files) > MAX_CANDIDATE_FILES:
        # Skip instrumentation if there are too many files because:
        # 1. They cause problems such as crash due to too many cmd line
        #    arguments.
        # 2. These CLs typically does mechanial refactorings, and coverage
        #    information is useless.
        # 3. Has non-trivial performance implications in terms of CQ cycle time.
        candidate_files = []
        skipping_coverage_message = (
            'skip instrumenting code coverage because >{} files are modified')
        skip_step = self.m.step.empty(
            skipping_coverage_message.format(MAX_CANDIDATE_FILES))
      if is_deps_only_change:
        # Skip instrumentation if current change is a DEPS only change.
        # This is because code_coverage recipe module expects candidate_files to
        # belong to a chromium checkout, and in case of DEPS only change
        # candidate_files belong to third party code.
        candidate_files = []
        skip_step = self.m.step.empty(
            'Skip instrumentating code coverage because DEPS only change')
      # This will let other builders (like orchestrator) know that coverage is
      # being skipped so coverage data need not be processed.
      if skip_step:
        skip_step.presentation.properties['skipping_coverage'] = True
        self._skipping_coverage = True

      self.filter_and_set_eligible_files(candidate_files)
      files_to_instrument = self._all_eligible_files()

    if not output_dir:
      output_dir = self.build_dir
    self.m.file.ensure_directory('create .code-coverage',
                                 self.source_dir / '.code-coverage')
    self.m.step(
        'save paths of affected files',
        [
            'python3',
            self.resource('write_paths_to_instrument.py'),
            '--write-to',
            self.source_dir / '.code-coverage/files_to_instrument.txt',
            '--src-path',
            self.source_dir,
            '--build-path',
            output_dir,
        ] + files_to_instrument,
        stdout=self.m.raw_io.output_text(add_output_log=True))

  def process_coverage_data(self, tests, override_builder_name=None):
    """Processes the coverage data for html report or metadata.

    Args:
      tests (list of steps.Test): A list of test objects
          whose binaries we are to create a coverage report for.
    """
    try:
      self._validate_test_types()
    except Exception as e:
      if self._is_per_cl_coverage:
        self.m.step.empty('skip processing because of an exception '
                          'when validating test types to process: %s' % e)
      else:
        self.m.step.empty(
            'Exception when validating test types to process: %s' % e,
            status=self.m.step.FAILURE)
      return

    if self._is_per_cl_coverage:
      unsupported_projects = self._get_unsupported_projects()
      if unsupported_projects:
        self.m.step.empty(
            'skip processing coverage data, project(s) %s is(are) unsupported' %
            unsupported_projects)
        return

    merge_from_multiple_tools = ((self.use_clang_coverage +
                                  self.use_java_coverage +
                                  self.use_javascript_coverage) > 1)
    if merge_from_multiple_tools and not self._is_per_cl_coverage:
      raise self.m.step.StepFailure(
          'CI coverage supports only 1 coverage tool type.')

    if override_builder_name:
      self._override_builder_name = override_builder_name

    try:
      for test_type in self._test_types:
        any_success = False
        if self.use_clang_coverage:
          success = self.process_clang_coverage_data(tests, test_type=test_type)
          any_success = success or any_success

        if self.use_java_coverage:
          success = self.process_java_coverage_data(test_type)
          any_success = success or any_success

        if self.use_javascript_coverage:
          # JS coverage processing only supports 'overall' test type. This is
          # ensured in |_validate_test_types|.
          success = self.process_javascript_coverage_data(test_type)
          any_success = success or any_success

        # Do not merge or upload data if no successful processing step for the
        # test type.
        if not any_success:
          continue

        metadata_dir = (
            list(self._metadata_dir_by_tool_type_by_test_type[test_type].values(
            ))[0])
        if merge_from_multiple_tools:
          metadata_dir = self._merge_metadata_from_multiple_tools(test_type)

        self._persist_coverage_artifacts(
            source_dir=metadata_dir, test_type=test_type)

      self._set_builder_output_properties_for_uploads()
    finally:
      if self.use_java_coverage:
        self.m.step('Clean up Java coverage files', [
            'python3',
            self.resource('clean_up_java_coverage_files.py'),
            '--sources-json-dir',
            self.build_dir,
            '--java-coverage-dir',
            self.build_dir / constants.JAVA_COVERAGE_DIR,
        ])

  def _get_unsupported_projects(self):
    """If the build input has changes in unsupported projects, return them."""
    result = []
    for change in self.m.buildbucket.build.input.gerrit_changes:
      if (change.host,
          change.project) not in constants.SUPPORTED_PATCH_PROJECTS:
        result.append((change.host, change.project))
    return ', '.join('/'.join(p) for p in result)

  def _persist_coverage_artifacts(self, source_dir, test_type, **kwargs):
    """Uploads coverage artifacts to GCS bucket.

    Uploads coverage artifacts to google cloud storage. Also adds the gs_path
    and mimic_builder_name corresponding to the uploaded file to
    self._coverage_metadata_gs_paths and self._mimic_builder_names,
    which are later to be exposed as step properties.

    Args:
      source_dir: Absolute location to dir containing coverage artifacts
      test_type (str): Test type to process. e.g. 'overall', 'unit'.
    """
    mimic_builder_name = self._compose_mimic_builder_name(test_type)
    gs_path = self._compose_gs_path_for_coverage_data(
        data_type='metadata', mimic_builder_name=mimic_builder_name)
    self.m.gsutil.upload(
        source=source_dir,
        bucket=self._gs_bucket,
        dest=gs_path,
        name='Upload coverage artifacts',
        link_name='Coverage Artifacts',
        args=['-r'],
        multithreaded=True,
        **kwargs)
    self._coverage_metadata_gs_paths.append(gs_path)
    self._mimic_builder_names.append(mimic_builder_name)

  # TODO(crbug.com/1484092): Get ride of |upload_metadata| and potentially make
  # this method internal as it doesn't upload metadata by default.
  def process_clang_coverage_data(self,
                                  tests=None,
                                  binaries=None,
                                  test_type=constants.test_types.OVERALL,
                                  upload_metadata=False):
    """Processes the clang coverage data for html report or metadata.

    Returns True if all processing steps run and succeed.

    Args:
      tests (list of steps.Test): A list of test objects
          whose binaries we are to create a coverage report for.
      binaries: A list of binaries for which coverage reports should be
          created.
      test_type (str): Test type to process. e.g. 'overall', 'unit'.
      upload_metadata (bool): Whether to upload metadata in this method.

      NOTE: Only one of the two above should be present.
    """
    assert (tests and not binaries) or (not tests and binaries), \
        'One of tests or binaries must be provided for clang coverage'

    if (self._is_per_cl_coverage and
        not self._has_eligible_files_for_tool(constants.tools.CLANG)):
      return
    if not self.m.profiles.profile_subdirs:  # pragma: no cover.
      self.m.step.empty('skip processing clang coverage data because '
                        'no profile data collected')
      return

    with self.m.step.nest(
        'process clang code coverage data for %s test coverage' %
        test_type) as processing_step:
      try:
        merged_profdata = self._merge_and_upload_profdata(test_type)
        if not merged_profdata:
          self.m.step.empty('skip processing because no profdata was generated')
          return

        try:
          self.m.profiles.find_merge_errors()
        finally:
          result = self.m.step.active_result
          if result.stdout and result.stdout['total'] > 0:
            result.presentation.text = 'Found invalid profraw files'
            result.presentation.properties['merge errors'] = result.stdout
        if not binaries:
          binaries = self.get_binaries(
              tests, may_use_binaries_list_file=True, test_type=test_type)
          binaries = self._get_binaries_with_valid_coverage_data_on_trybot(
              binaries, merged_profdata)

          if not binaries:
            self.m.step.empty('skip processing because no data is found')
            return
        self._generate_clang_metadata(binaries, merged_profdata, test_type,
                                      upload_metadata)
        self._generate_and_upload_html_report_on_trybot(binaries,
                                                        merged_profdata,
                                                        test_type)
        return True
      except:  # pylint: disable=bare-except
        self.m.step.active_result.presentation.properties[
            'process_coverage_data_failure'] = True

        if self._is_per_cl_coverage:
          # Do not raise coverage steps exception for per-cl coverage because
          # per-cl coverage is integrated into Chromium try jobs, coverage steps
          # are expected to be non-fatal.
          processing_step.logs['error'] = '\n'.join(
              str(x) for x in sys.exc_info())
        else:
          raise

  def process_java_coverage_data(self,
                                 test_type=constants.test_types.OVERALL,
                                 **kwargs):
    """Generates metadata and JaCoCo HTML report to upload to storage bucket.

    Generates Java coverage metadata and JaCoCo HTML report by scripts.
    Returns True if all processing steps run and succeed. Does not upload
    metadata.

    Args:
      test_type (str): Test type to process. e.g. 'overall', 'unit'.
      **kwargs: Kwargs for python and gsutil steps.
    """
    if (self._is_per_cl_coverage and
        not self._has_eligible_files_for_tool(constants.tools.JACOCO)):
      return
    with self.m.step.nest('process java coverage (%s)' % test_type):
      try:
        coverage_dir = self.build_dir / constants.JAVA_COVERAGE_DIR
        output_dir = self._ensure_metadata_dir(test_type,
                                               constants.tools.JACOCO)
        cmd = [
            'python3',
            self.resource('generate_coverage_metadata_for_java.py'),
            '--src-path',
            self.source_dir,
            '--output-dir',
            output_dir,
            '--coverage-dir',
            coverage_dir,
            '--sources-json-dir',
            self.build_dir,
        ]

        if self._is_per_cl_coverage:
          cmd.append('--source-files')
          cmd.extend(self._eligible_files_by_tool[constants.tools.JACOCO])
          cmd.extend(['--diff-mapping-path', self.bot_to_gerrit_mapping_file])
        else:
          dir_metadata_path = self._generate_dir_metadata()
          cmd.extend([
              '--dir-metadata-path',
              dir_metadata_path,
          ])
        cmd.extend([
            '--exec-filename-pattern',
            ("%s\.exec" % constants.PLATFORM_TO_TARGET_NAME_PATTERN_MAP[
                self.platform][test_type])
        ])
        cmd.extend(['--exclusion-pattern', constants.EXCLUDED_FILE_REGEX])
        cmd.append('--third-party-inclusion-subdirs')
        cmd.extend(constants.INCLUDED_THIRD_PARTY_SUBDIRS)
        if self._generate_blame_list:
          cmd.append('--generate-blame-list')
        self.m.step('Generate Java coverage metadata', cmd, **kwargs)

        # Upload data to zoss to show it on code search
        if self._export_coverage_to_zoss:
          for repo in constants.COVERAGE_REPOS:
            self.m.gsutil.upload(
                source=output_dir / 'coverage.xml',
                bucket=constants.ZOSS_BUCKET_NAME,
                dest='%s/coverage.xml' % self._compose_gs_path_for_zoss_upload(
                    builder=self._compose_mimic_builder_name(test_type),
                    build_id=self.build_id,
                    zoss_host=repo['host']),
                link_name=None,
                multithreaded=True,
                name='export coverage data to zoss for host %s' % repo['host'])
            self.m.file.write_json(
                name='create zoss metadata json for host %s' % repo['host'],
                dest=output_dir / 'zoss_metadata.json',
                data=self._get_zoss_metadata(
                    zoss_host=repo['host'],
                    zoss_project=repo['project'],
                    coverage_format='JACOCO_XML',
                    coverage_type=test_type))
            self.m.gsutil.upload(
                source=output_dir / 'zoss_metadata.json',
                bucket=constants.ZOSS_BUCKET_NAME,
                dest='%s/metadata.json' % self._compose_gs_path_for_zoss_upload(
                    builder=self._compose_mimic_builder_name(test_type),
                    build_id=self.build_id,
                    zoss_host=repo['host']),
                link_name=None,
                multithreaded=True,
                name='export metadata to zoss for host %s' % repo['host'])

        metadata_path = output_dir / 'all.json.gz'
        if not self.m.path.exists(metadata_path):
          self.m.step.empty(
              'skip processing because %s tests metadata was missing' %
              test_type)
          return
        return True
      except self.m.step.StepFailure:
        self.m.step.active_result.presentation.properties[
            'process_coverage_data_failure'] = True
        if not self._is_per_cl_coverage:
          # Do not raise coverage steps exception for per-cl coverage.
          raise

  def process_javascript_coverage_data(self,
                                       test_type=constants.test_types.OVERALL,
                                       **kwargs):
    """Returns True if all processing steps run and succeed.

    Args:
      test_type (str): Test type to process. e.g. 'overall', 'unit'.
    Does not upload metadata.
    """
    if (self._is_per_cl_coverage and
        not self._has_eligible_files_for_tool(constants.tools.V8)):
      return
    with self.m.step.nest('process javascript coverage (%s)' % test_type):
      try:
        coverage_dir = self.build_dir / constants.JS_COVERAGE_DIR
        if not self.m.path.exists('%s/lcov.info' % coverage_dir):
          raise self.m.step.StepFailure("Required lcov.info is missing at %s" %
                                        coverage_dir)
        lcov_data = self.m.file.read_text('read lcov.info',
                                          '%s/lcov.info' % coverage_dir)
        any_source_file_cov_available = False
        eligible_files = self._eligible_files_by_tool[constants.tools.V8]
        for path in eligible_files:
          if path in lcov_data:
            any_source_file_cov_available = True
            break
        if self._is_per_cl_coverage and not any_source_file_cov_available:
          self.m.step.empty(
              'skip processing because lcov.info does not have data '
              'for eligible files')
          return
        output_dir = self._ensure_metadata_dir(test_type, constants.tools.V8)
        cmd = [
            'python3',
            self.resource('generate_coverage_metadata_for_javascript.py'),
            '--src-path',
            self.source_dir,
            '--output-dir',
            output_dir,
            '--coverage-dir',
            coverage_dir,
        ]
        if self._is_per_cl_coverage:
          cmd.append('--source-files')
          cmd.extend(eligible_files)
          cmd.extend(['--diff-mapping-path', self.bot_to_gerrit_mapping_file])
        else:
          dir_metadata_path = self._generate_dir_metadata()
          cmd.extend([
              '--dir-metadata-path',
              dir_metadata_path,
          ])
        self.m.step('Generate JavaScript coverage metadata', cmd)
        # Upload data to zoss to show it on code search
        if self._export_coverage_to_zoss:
          for repo in constants.COVERAGE_REPOS:
            self.m.gsutil.upload(
                source=coverage_dir / 'lcov.info',
                bucket=constants.ZOSS_BUCKET_NAME,
                dest='%s/lcov.info' % self._compose_gs_path_for_zoss_upload(
                    builder=self._compose_mimic_builder_name(test_type),
                    build_id=self.build_id,
                    zoss_host=repo['host']),
                link_name='lcov_info',
                multithreaded=True,
                name='export coverage data to zoss for host %s' % repo['host'])
            self.m.file.write_json(
                name='create zoss metadata json for host %s' % repo['host'],
                dest=output_dir / 'zoss_metadata.json',
                data=self._get_zoss_metadata(
                    zoss_host=repo['host'],
                    zoss_project=repo['project'],
                    coverage_format='LCOV',
                    coverage_type=test_type))
            self.m.gsutil.upload(
                source=output_dir / 'zoss_metadata.json',
                bucket=constants.ZOSS_BUCKET_NAME,
                dest='%s/metadata.json' % self._compose_gs_path_for_zoss_upload(
                    builder=self._compose_mimic_builder_name(test_type),
                    build_id=self.build_id,
                    zoss_host=repo['host']),
                link_name='Zoss Metadata',
                multithreaded=True,
                name='export metadata to zoss for host %s' % repo['host'])
        return True
      except self.m.step.StepFailure:
        self.m.step.active_result.presentation.properties[
            'process_coverage_data_failure'] = True
        if not self._is_per_cl_coverage:
          # Do not raise coverage steps exception for per-cl coverage.
          raise

  def _merge_metadata_from_multiple_tools(self, test_type):
    """Merges metadata from multiple tools processings."""
    with self.m.step.nest('merge data from multiple coverage tools (%s)' %
                          test_type):
      output_dir = self._ensure_metadata_dir(test_type, 'merged')
      cmd = [
          'python3',
          self.resource('merge_metadata_files.py'),
          '--input-dirs',
      ]
      cmd.extend(
          list(
              self._metadata_dir_by_tool_type_by_test_type[test_type].values()))
      cmd.extend([
          '--output-dir',
          output_dir,
      ])

      self.m.step('Merge metadata', cmd)

    return output_dir

  def _merge_and_upload_profdata(self, test_type):
    """Merges the profdata generated by each step to a single profdata.

    Returns:
      A path to the {test_type}-merged.profdata file if it exists, otherwise,
      None. One possible reason that profdata doesn't exist is that there might
      be no .profraw files to merge at all.
    """
    merged_profdata = self.m.profiles.profile_dir().joinpath(
        '%s-merged.profdata' % test_type)

    # Input profdata in this step was named as {target_name}.profdata. This is
    # used for filtering profdata file of current test type.
    if self.platform in constants.PLATFORM_TO_TARGET_NAME_PATTERN_MAP:
      input_profdata_pattern = ("%s\.profdata" %
                                constants.PLATFORM_TO_TARGET_NAME_PATTERN_MAP[
                                    self.platform][test_type])
    else:
      # do not filter anything
      input_profdata_pattern = ".+\.profdata"

    self.m.profiles.merge_profdata(
        merged_profdata,
        profdata_filename_pattern=input_profdata_pattern,
        sparse=True)

    if not self.m.path.exists(merged_profdata):
      return None

    # The uploaded profdata file is named "merged.profdata" regardless of test
    # type, since test types are already distinguished in builder part of gs
    # path.
    gs_path = self._compose_gs_path_for_coverage_data(
        data_type='merged.profdata',
        mimic_builder_name=self._compose_mimic_builder_name(test_type))
    upload_step = self.m.profiles.upload(
        self._gs_bucket, gs_path, merged_profdata, link_name='Merged profdata')
    upload_step.presentation.links['merged.profdata'] = (
        'https://storage.cloud.google.com/%s/%s' % (self._gs_bucket, gs_path))
    self._merged_profdata_gs_paths.append(gs_path)
    return merged_profdata

  def _get_arch_flag(self):
    """Returns architecture flag for coverage based on builder config."""
    arch = {
        ('arm', 64): 'arm64',
        ('intel', 64): 'x86_64',
        ('arm', 32): 'armv7',
        ('intel', 32): 'i386',
    }.get((self.m.chromium.c.TARGET_ARCH, self.m.chromium.c.TARGET_BITS))
    return ['--arch', arch] if arch else []

  # TODO(crbug.com/929769): Remove this method when the fix is landed upstream.
  def _get_binaries_with_valid_coverage_data_on_trybot(self, binaries,
                                                       profdata_path):
    """Gets binaries with valid coverage data.

    llvm-cov bails out with error message "No coverage data found" if an
    included binary does not exercise any instrumented file. The long-term
    solution should be making llvm-cov being able to proceed by ignoring the
    binaries without coverage data, however, for short-term, this method
    implements a solution to filter out binaries without coverage data by trying
    to invoke llvm-cov on each binary and decide if there is coverage data based
    on the return code and error message.

    This method is expected to run fast for per-cl coverage because only a small
    number of files are instrumented.

    Args:
      binaries (list): A list of absolute paths to binaries.
      profdata_path (str): Path to the merged profdata file.

    Returns:
      A list of absolute paths to the binaries with valid coverage data.
    """
    if not (self._is_per_cl_coverage and
            self._eligible_files_by_tool[constants.tools.CLANG]):
      # Only gets binaries with valid coverage data for per-cl coverage.
      return binaries

    cmd = [
        'python3',
        self.resource('get_binaries_with_valid_coverage_data.py'),
        '--profdata-path',
        profdata_path,
        '--llvm-cov',
        self.cov_executable,
        '--output-json',
        self.m.json.output(),
    ]

    cmd.extend(binaries)

    cmd.extend(self._get_arch_flag())

    step_result = self.m.step(
        'filter binaries with valid data for %s binaries' % len(binaries),
        cmd,
        step_test_data=lambda: self.m.json.test_api.output(
            ['/path/to/base_unittests', '/path/to/content_shell']),
    )
    return step_result.json.output

  def _generate_and_upload_html_report_on_trybot(self, binaries, profdata_path,
                                                 test_type):
    """Generate html coverage report for the given binaries.

    Produce a coverage report for the instrumented test targets and upload to
    the appropriate bucket.
    """
    if not (self._is_per_cl_coverage and
            self._eligible_files_by_tool[constants.tools.CLANG]):
      # Only upload html report for CQ coverage bots.
      return

    cmd = [
        'python3',
        self.resource('make_report.py'),
        '--report-directory',
        self.report_dir,
        '--profdata-path',
        profdata_path,
        '--llvm-cov',
        self.cov_executable,
        '--compilation-directory',
        self.build_dir,
        '--binaries',
    ]
    cmd.extend(binaries)
    cmd.append('--sources')
    cmd.extend([
        self.source_dir / s
        for s in self._eligible_files_by_tool[constants.tools.CLANG]
    ])

    cmd.extend(self._get_arch_flag())

    self.m.step(
        ('generate html report for %s test coverage in %d tests' %
         (test_type, len(self.m.profiles.profile_subdirs))),
        cmd,
    )

    html_report_gs_path = self._compose_gs_path_for_coverage_data(
        data_type='html_report',
        mimic_builder_name=self._compose_mimic_builder_name(test_type))
    upload_step = self.m.gsutil.upload(
        self.report_dir,
        self._gs_bucket,
        html_report_gs_path,
        link_name='html report',
        args=['-r'],
        multithreaded=True,
        name='upload html report')
    upload_step.presentation.links['html report'] = (
        'https://storage.cloud.google.com/%s/%s/index.html' %
        (self._gs_bucket, html_report_gs_path))

  def shard_merge(self,
                  step_name,
                  target_name,
                  additional_merge=None,
                  skip_validation=False,
                  sparse=False):
    """Returns a merge object understood by the swarming module.

    See the docstring for the `merge` parameter of api.chromium_swarming.task.

    |additional_merge| is an additional merge script. This will be invoked from
    the clang coverage merge script.
    """
    args = [
        '--profdata-dir',
        self.m.profiles.profile_dir(step_name),
        '--llvm-profdata',
        self.m.profiles.llvm_profdata_exec,
        '--test-target-name',
        target_name,
    ]
    if skip_validation:
      args += [
          '--skip-validation',
      ]
    if sparse:
      args += [
          '--sparse',
      ]

    if self.use_java_coverage:
      jacococli_jar = (
          self.source_dir / 'third_party/jacoco/cipd/lib/jacococli.jar')
      # TODO: Remove the fallback once no builders are building old versions of
      # chromium/src.
      if not self.m.path.exists(jacococli_jar):
        jacococli_jar = self.source_dir / 'third_party/jacoco/lib/jacococli.jar'

      args.extend([
          '--java-coverage-dir',
          self.build_dir / constants.JAVA_COVERAGE_DIR,
          '--jacococli-path',
          jacococli_jar,
          '--merged-jacoco-filename',
          self.m.profiles.normalize(step_name),
      ])
    if self.use_javascript_coverage:
      args.extend([
          '--javascript-coverage-dir',
          self.build_dir / constants.JS_COVERAGE_DIR,
          '--chromium-src-dir',
          self.source_dir,
          '--build-dir',
          self.build_dir,
      ])
    if self._is_per_cl_coverage:
      args.append('--per-cl-coverage')
    if additional_merge:
      args.extend([
          '--additional-merge-script',
          additional_merge.script,
      ])
      if additional_merge.args:
        args.extend([
            '--additional-merge-script-args',
            self.m.json.dumps(additional_merge.args)
        ])

    return chromium_swarming.MergeScript(
        script=self.m.profiles.merge_results_script, args=args)

  def get_chromium_fuzz_coverage(self,
                                 source_dir,
                                 build_dir,
                                 llvm_raw_data,
                                 targets: set[str],
                                 overall: bool = True,
                                 use_cache: bool = False):
    """Generate fuzz coverage information.

    Arguments:
        targets: A set of fuzzers in GN output file form (relative paths from
            the build directory). These are not GN target labels. This set of
            fuzzers must have exactly one member when computing per-target
            coverage with `overall=False`.
        overall: Whether we're computing overall merged coverage.
        use_cache: Whether to use cached GN results.
    """
    llvm_cov = (
        source_dir / 'third_party/llvm-build/Release+Asserts/bin/llvm-cov')
    if not targets:
      self.m.step.empty('no fuzz targets to generate coverage for')
      return
    if overall:
      test_type = 'overall'
    else:
      (test_type,) = targets
    with self.m.step.nest(f'process fuzz coverage ({test_type})'):
      self.m.file.chmod('chmod llvm file', llvm_cov, '777')
      output_dir = self._ensure_metadata_dir(test_type, constants.tools.CLANG)
      binaries = self._resolve_binaries_for_fuzzing(
          build_dir, targets, use_cache=use_cache)
      cmd = [
          'vpython3',
          self.resource('generate_coverage_metadata.py'),
          '--output-dir',
          output_dir,
          '--build-dir',
          build_dir,
          '--llvm-cov',
          llvm_cov,
          '--src-path',
          source_dir,
          '--profdata-path',
          llvm_raw_data,
          '--fuzz',
          '--exclusion-pattern',
          constants.EXCLUDED_FILE_REGEX,
          '--binaries',
          *sorted(binaries),
      ]
      self.m.step('generate coverage metadata', cmd)
      self._persist_coverage_artifacts(
          source_dir=output_dir, test_type=test_type)
      self._set_builder_output_properties_for_uploads()

  def _resolve_binaries_for_fuzzing(
      self,
      build_dir: config_types.Path,
      targets: set[str],
      use_cache: bool = False,
  ) -> set[config_types.Path]:
    """Get a list of relevant ELF files to extract coverage data for.

    ELF is the executable format for Linux and macOS.

    Some fuzzers invoke multiple binaries (notably fuzztest, where a thin
    wrapper binary invokes a more substantial unit test binary). For each
    fuzzer in `targets`, query `gn desc` to deduce runtime dependencies among
    executables or shared objects.
    """
    # Extract coverage for the fuzzers themselves.
    binaries = {build_dir / target for target in targets}
    # Convert fuzzers back into GN target labels for consumption by `gn desc`
    # (e.g., `base64_encode_fuzzer` -> `//base:base64_encode_fuzzer`). Do not
    # get indirect references, which may not be fuzzers.
    fuzzer_labels = self.m.gn.refs(
        build_dir, sorted(binaries), all_deps=False, output_type='executable')
    for fuzzer_label in fuzzer_labels:
      # `elf_paths` contains all executables and shared objects that
      # `fuzzer_label` transitively builds. These can include executables only
      # used at build time (e.g., `protoc`).
      elf_paths = self.m.gn.desc(
          build_dir,
          fuzzer_label,
          'deps',
          '--all',
          '--as=output',
          '--type=executable',
          step_name='gn desc (deps --type=executable)',
          use_cache=use_cache)
      elf_paths += self.m.gn.desc(
          build_dir,
          fuzzer_label,
          'deps',
          '--all',
          '--as=output',
          '--type=shared_library',
          step_name='gn desc (deps --type=shared_library)',
          use_cache=use_cache)
      # `runtime_paths` contains all runtime dependencies of `fuzzer_label`,
      # including non-binary files (e.g., resource files, scripts).
      runtime_paths = self.m.gn.desc(
          build_dir,
          fuzzer_label,
          'runtime_deps',
          step_name='gn desc (runtime_deps)',
          use_cache=use_cache)
      # Normalize as structured absolute paths.
      elf_paths = {build_dir / path for path in elf_paths}
      runtime_paths = {build_dir / path for path in runtime_paths}
      # We want only binaries or shared libraries that the target depends on
      # at runtime. That is the set of binaries to extract coverage from.
      binaries |= elf_paths & runtime_paths
    return binaries

  def _compose_gs_path_for_coverage_data(self, data_type, mimic_builder_name):
    build = self.m.buildbucket.build
    if build.input.gerrit_changes:
      # Assume that there is only one gerrit patchset which is true for
      # Chromium CQ in practice.
      gerrit_change = build.input.gerrit_changes[0]
      return 'presubmit/%s/%s/%s/%s/%s/%s/%s' % (
          gerrit_change.host,
          gerrit_change.change,  # Change id is unique in a Gerrit host.
          gerrit_change.patchset,
          build.builder.bucket,
          mimic_builder_name,
          self.build_id,
          data_type,
      )

    commit = build.input.gitiles_commit
    assert commit is not None, 'No gitiles commit'
    return 'postsubmit/%s/%s/%s/%s/%s/%s/%s' % (
        commit.host,
        commit.project,
        commit.id,  # A commit HEX SHA1 is unique in a Gitiles project.
        build.builder.bucket,
        mimic_builder_name,
        self.build_id,
        data_type,
    )

  def _generate_dir_metadata(self):
    """Extracts directory metadata, e.g. mapping to monorail component."""
    dir_metadata = self.m.path.mkdtemp() / constants.DIR_METADATA_FILE_NAME
    with self.m.context(cwd=self.source_dir):
      self.m.step('Extract directory metadata', [
          self.source_dir / 'third_party/depot_tools/dirmd',
          'export',
          '-out',
          dir_metadata,
      ])
    return dir_metadata

  def _generate_clang_metadata(self, binaries, profdata_path, test_type,
                               upload_metadata, **kwargs):
    """Generates the coverage info in metadata format."""
    output_dir = self._ensure_metadata_dir(test_type, constants.tools.CLANG)
    cmd = [
        'vpython3',
        self.resource('generate_coverage_metadata.py'),
        '--build-dir',
        self.build_dir,
        '--src-path',
        self.source_dir,
        '--output-dir',
        output_dir,
        '--profdata-path',
        profdata_path,
        '--llvm-cov',
        self.cov_executable,
        '--binaries',
    ]
    cmd.extend(binaries)
    if self._is_per_cl_coverage:
      cmd.append('--sources')
      cmd.extend(self._eligible_files_by_tool[constants.tools.CLANG])
      cmd.extend(['--diff-mapping-path', self.bot_to_gerrit_mapping_file])
    else:
      cmd.extend(['--exclusion-pattern', constants.EXCLUDED_FILE_REGEX])
      cmd.append('--third-party-inclusion-subdirs')
      cmd.extend(constants.INCLUDED_THIRD_PARTY_SUBDIRS)
      if self._include_component_mapping:
        cmd.extend(['--dir-metadata-path', self._generate_dir_metadata()])

    cmd.extend(self._get_arch_flag())

    try:
      self.m.step(
          'generate metadata for %s test coverage in %d tests' %
          (test_type, len(self.m.profiles.profile_subdirs)), cmd)
    finally:
      # Upload data to zoss to show it on code search
      if self._export_coverage_to_zoss:
        for repo in constants.COVERAGE_REPOS:
          self.m.gsutil.upload(
              source=output_dir / 'coverage.json',
              bucket=constants.ZOSS_BUCKET_NAME,
              dest='%s/coverage.json' % self._compose_gs_path_for_zoss_upload(
                  builder=self._compose_mimic_builder_name(test_type),
                  build_id=self.build_id,
                  zoss_host=repo['host']),
              link_name='coverage_json',
              multithreaded=True,
              name='export coverage data to zoss for host %s' % repo['host'])
          self.m.file.write_json(
              name='create zoss metadata json for host %s' % repo['host'],
              dest=output_dir / 'zoss_metadata.json',
              data=self._get_zoss_metadata(
                  zoss_host=repo['host'],
                  zoss_project=repo['project'],
                  coverage_format='LLVM',
                  coverage_type=test_type))
          self.m.gsutil.upload(
              source=output_dir / 'zoss_metadata.json',
              bucket=constants.ZOSS_BUCKET_NAME,
              dest='%s/metadata.json' % self._compose_gs_path_for_zoss_upload(
                  builder=self._compose_mimic_builder_name(test_type),
                  build_id=self.build_id,
                  zoss_host=repo['host']),
              link_name='Zoss Metadata',
              multithreaded=True,
              name='export metadata to zoss for host %s' % repo['host'])

      if upload_metadata:
        self._persist_coverage_artifacts(
            source_dir=output_dir, test_type=test_type, **kwargs)

  def _compose_gs_path_for_zoss_upload(self, builder, build_id, zoss_host):
    commit = self.m.buildbucket.build.input.gitiles_commit
    assert commit is not None, 'No gitiles commit'
    return "ng3-chrome-coverage/absolute/%s/%s/%s/%s/%s/%s" % (
        commit.host, commit.project, commit.id, builder, build_id, zoss_host)

  def _get_zoss_metadata(self, zoss_host, zoss_project, coverage_format,
                         coverage_type):
    """Returns a dict which has to be uploaded along with coverage data to zoss.

    Args:
      coverage_format (str): Coverage format supported by Zoss. Valid values
                              are LCOV, LLVM, GO_COV and JACOCO_XML.
    """
    commit = self.m.buildbucket.build.input.gitiles_commit
    branch = 'main'
    category = ('DEFAULT' if coverage_type == constants.test_types.OVERALL else
                'CHROME_UNIT_TEST')
    return {
        # Maps to https://source.corp.google.com/h/chrome-internal/codesearch/chrome/src
        # which is a view of https://chromium.googlesource.com/chromium/src/
        # but with git submodules instead of DEPS for codesearch to index
        'host': zoss_host,
        'project': zoss_project,
        'trace_type': coverage_format,
        'git_project': commit.project,
        'commit_id': commit.id,
        'category': category,
        'ref': 'refs/heads/main',
        # e.g. source = chromium/src:main
        'source': '%s:%s' % (commit.project, branch),
        'owner': 'chrome-code-coverage@google.com',
        # buganizer component
        'bug_component': '992692'
    }
