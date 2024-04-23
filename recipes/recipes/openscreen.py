# Copyright 2018 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Recipe for building and running tests for Open Screen stand-alone."""

DEPS = [
    'code_coverage',
    'depot_tools/bot_update',
    'depot_tools/depot_tools',
    'depot_tools/gclient',
    'depot_tools/git',
    'depot_tools/gsutil',
    'depot_tools/osx_sdk',
    'depot_tools/tryserver',
    'profiles',
    'recipe_engine/buildbucket',
    'recipe_engine/context',
    'recipe_engine/file',
    'recipe_engine/cas',
    'recipe_engine/path',
    'recipe_engine/platform',
    'recipe_engine/properties',
    'recipe_engine/step',
    'recipe_engine/swarming',
]

# Open Screen specific paths and repository information.
BUILD_CONFIG = 'Default'
UNIT_TEST_BINARY_NAME = 'openscreen_unittests'
E2E_TEST_BINARY_NAME = 'e2e_tests'
CAST_E2E_TEST_SCRIPT_NAME = 'standalone_e2e.py'
CAST_SENDER_BINARY_NAME = 'cast_sender'
CAST_RECEIVER_BINARY_NAME = 'cast_receiver'
BUILD_TARGETS = [
    'gn_all', UNIT_TEST_BINARY_NAME, E2E_TEST_BINARY_NAME, 'fuzzer_tests_all',
    CAST_SENDER_BINARY_NAME, CAST_RECEIVER_BINARY_NAME
]
OPENSCREEN_REPO = 'https://chromium.googlesource.com/openscreen'

GN_PROPERTIES = [
    'cast_allow_developer_certificate', 'have_ffmpeg', 'have_libsdl2',
    'have_libopus', 'have_libvpx', 'is_component_build', 'is_debug', 'is_asan',
    'is_tsan', 'is_gcc', 'target_cpu', 'sysroot', 'use_coverage'
]

# List of dimensions used for starting swarming on ARM64.
SWARMING_DIMENSIONS = {'cpu': 'arm64', 'os': 'Ubuntu-22'}

# LUCI pool information.
FLEX_TRY_POOL = 'luci.flex.try'
FLEX_CI_POOL = 'luci.flex.ci'
POOL_DIMENSION = 'pool'


class RepositoryPaths:
  """Container for checkout_path dependent repository paths, such as
     unit test binary location.
  """

  def __init__(self, api):
    """Initializer for RepositoryPaths.

    Args:
        api (recipe_api.RecipeApi): API generated from recipe dependencies.
    """
    self.api = api
    self.checkout_path = api.path.checkout_dir
    self.output_path = self.checkout_path.joinpath('out', BUILD_CONFIG)
    self.unit_test_binary_path = self.output_path / UNIT_TEST_BINARY_NAME
    self.e2e_test_binary_path = self.output_path / E2E_TEST_BINARY_NAME
    self.cast_e2e_test_script_path = self.checkout_path.joinpath(
        'cast', CAST_E2E_TEST_SCRIPT_NAME)
    self.cast_sender_binary_path = self.output_path.joinpath(
        CAST_SENDER_BINARY_NAME)
    self.cast_receiver_binary_path = self.output_path.joinpath(
        CAST_RECEIVER_BINARY_NAME)
    self.test_data_path = self.checkout_path.joinpath('test', 'data')
    self.ninja_path = self.checkout_path.joinpath('third_party', 'ninja',
                                                  'ninja')


  def SwarmingBinaryPath(self, binary_name):
    """Returns a relative path in the CAS archive to binary_name."""
    return self.api.path.join('.', 'out', BUILD_CONFIG, binary_name)


def GetSwarmingDimensions(is_ci):
  """Get a list of swarming dimensions to pass to swarming API.

  Args:
      is_ci (bool): Whether this is the CI pool (True) or the TRY pool (False).

  Returns:
      dict of str=>str: dimensions to use for swarming.
  """
  dimensions = SWARMING_DIMENSIONS
  dimensions[POOL_DIMENSION] = FLEX_CI_POOL if is_ci else FLEX_TRY_POOL
  return dimensions


def GetHostToolLabel(platform):
  """Determines what the platform label is, e.g. 'mac' or 'linux64'.

  Args:
      platform (api platform): Platform information from recipes API

  Returns:
      str: platform host tool label.
  """
  if platform.is_linux and platform.bits == 64:
    return 'linux64'
  raise ValueError('unknown or unsupported platform')  # pragma: no cover


def GenerateCoverageTestConstants(api, paths):
  """Generates fake file paths used for validation in code coverage tests.

  Args:
      api (recipe_api.RecipeApi): API generated from recipe dependencies.
      paths (RepositoryPaths): Checkout-dependent repository files.
  """
  # Add fakes for the paths that are validated in SetCodeCoverageConstants()
  if api.properties.get('is_valid_coverage_test', False):
    llvm_dir = paths.checkout_path.joinpath('third_party', 'llvm-build',
                                            'Release+Asserts', 'bin')
    api.path.mock_add_paths(
        paths.checkout_path.joinpath('build', 'code_coverage'))
    api.path.mock_add_paths(llvm_dir / 'llvm-profdata')
    api.path.mock_add_paths(llvm_dir / 'llvm-cov')
    api.path.mock_add_paths(paths.output_path / 'default.profraw')

  # Add fake .profraw data and destination path
  if api.properties.get('generate_test_profraw', False):
    api.path.mock_add_paths(paths.checkout_path / 'default.profraw')

  # Generate fake .profdata
  if api.properties.get('generate_test_profdata', False):
    api.path.mock_add_paths(paths.output_path / 'default.profdata')


def GetChangedFiles(api, checkout_path):
  """Returns list of POSIX paths of files affected by patch.

  Args:
      api (recipe_api.RecipeApi): API generated from recipe dependencies.
      checkout_path (api.path): Location of open screen checkout.

  Returns:
      list: api.path: list of changed files.
  """
  files = []
  if api.tryserver.gerrit_change:
    patch_root = api.gclient.get_gerrit_patch_root()
    assert patch_root, ('local path is not configured for {}'.format(
        api.tryserver.gerrit_change_repo_url))
    with api.context(cwd=checkout_path):
      files = api.tryserver.get_files_affected_by_patch(patch_root)
    for i, path in enumerate(files):
      files[i] = api.path.relpath(str(path), checkout_path)
  return files


def FormatGnArg(arg, value):
  """Takes a specific arg and value and formats them as a valid gn argument.

  Args:
      arg (str): GN arg name.
      value (any): GN arg value.

  Returns:
      str: Formatted key value pair as GN argument
  """
  format_string = '{}="{}"' if isinstance(value, str) else '{}={}'
  return format_string.format(arg, value).lower()


def FormatGnArgs(properties):
  """Takes a list of properties and maps them to string gn arguments.

  Args:
      properties (dict of str=>str): Recipe API properties to combine for GN.

  Returns:
      str: formatted GN arguments.
  """
  return ' '.join([
      FormatGnArg(arg, properties[arg])
      for arg in [p for p in GN_PROPERTIES if properties.get(p, None) != None]
  ])

def UploadOpenscreenTestFilesToCas(api, paths):
  """Pushes files up to RBE-CAS server storage.

  Args:
      api (recipe_api.RecipeApi): API generated from recipe dependencies.
      paths (RepositoryPaths): Checkout-dependent repository files.

  Returns:
      str: CAS digest of file archive.
  """
  return api.cas.archive('upload files to cas', paths.checkout_path,
                         paths.unit_test_binary_path,
                         paths.e2e_test_binary_path,
                         paths.cast_e2e_test_script_path,
                         paths.cast_sender_binary_path,
                         paths.cast_receiver_binary_path, paths.test_data_path)


def CheckSwarmingResults(api, name, results):
  """Called after swarming.collect() to produce a proper step result.

  Args:
      api (recipe_api.RecipeApi): API generated from recipe dependencies.
      name (str): Name of step to use for failures.
      results (list: TaskResult): Collected swarming results.

  Raises:
      api.step.StepFailure: swarming completed and failed, or timed out.
  """
  for result in results:
    if result.state in (api.swarming.TaskState.COMPLETED,
                        api.swarming.TaskState.TIMED_OUT):
      if not result.success:
        fail_text = '{} failure'.format(name)
        step = api.step(fail_text, None)
        step.presentation.status = 'FAILURE'
        raise api.step.StepFailure(fail_text)
    else:
      api.step.active_result.presentation.status = 'EXCEPTION'
      result.analyze()


class SwarmRequest:
  """A class to represent the data necessary to generate a swarming request.

  Attributes:
    cas_digest: the digest used to download the executables for this request
        from CAS, the test storage server.
    binary_path (string): the path to the binary to execute.
    task_name (string): the name to be used for the task steps associated with
        this request.
  """

  def __init__(self, cas_digest, binary_path, task_name):
    self.cas_digest = cas_digest
    self.binary_path = binary_path
    self.task_name = task_name


def TriggerTest(api, dimensions, swarm_request):
  """Triggers a swarming test request.

  Args:
      api (recipe_api.RecipeApi): API generated from recipe dependencies.
      dimensions (dict of str=>str): Dimensions to be used to generate a
          request. Must be valid swarming selection dimensions to be
          unpacked as **kwargs, such as pool or os.
      swarm_request(SwarmRequest): the information used to start the swarming
          request.
  """
  request = api.swarming.task_request().with_name(swarm_request.task_name)

  # Quick note about the swarming API. Swarming Tasks are composed of "slices"
  # made of individual TaskRequest objects. The task_request() getter returns
  # a basic request with one mostly empty TaskRequest that can be used as a
  # template to generate slices.
  # For more information, see the swarming guide:
  # https://chromium.googlesource.com/infra/luci/luci-py/+/main/appengine/swarming/doc/User-Guide.md#task #pylint: disable=line-too-long
  task_slice = request[0].with_command([
      swarm_request.binary_path
  ]).with_dimensions(**dimensions).with_cas_input_root(swarm_request.cas_digest)

  request = request.with_slice(0, task_slice)
  return api.swarming.trigger(
      f'trigger {swarm_request.task_name}', requests=[request])


def CollectTest(api, paths, dimensions, swarm_request, metadata):
  """Collects a swarming test request. We collect each swarm request individually
    to enable better reporting of failures.

  Args:
      api (recipe_api.RecipeApi): API generated from recipe dependencies.
      paths (RepositoryPaths): Checkout-dependent repository files.
      dimensions (dict of str=>str): Dimensions to be used to generate a
          request. Must be valid swarming selection dimensions to be
          unpacked as **kwargs, such as pool or os.
      swarm_request(SwarmRequest): the information used to start the swarming
          request.
      metadata(TaskRequestMetadata): the information associated with the test trigger.
  """
  output_directory = api.path.mkdtemp(
      f'{swarm_request.task_name}-swarming-output')
  results = api.swarming.collect(
      f'collect {swarm_request.task_name}',
      metadata,
      output_dir=output_directory,
      timeout='30m')
  CheckSwarmingResults(api, swarm_request.task_name, results)


def SwarmTests(api, paths, dimensions):
  """Runs specific types of tests on a separate swarming bot.

  Args:
      api (recipe_api.RecipeApi): API generated from recipe dependencies.
      paths (RepositoryPaths): Checkout-dependent repository files.
      dimensions (dict of str=>str): Dimensions to be used to generate a
          request. Must be valid swarming selection dimensions to be
          unpacked as **kwargs, such as pool or os.
  """

  cas_digest = UploadOpenscreenTestFilesToCas(api, paths)
  unit_tests_request = SwarmRequest(
      cas_digest, paths.SwarmingBinaryPath(UNIT_TEST_BINARY_NAME), 'unit tests')
  e2e_tests_request = SwarmRequest(
      cas_digest, paths.SwarmingBinaryPath(E2E_TEST_BINARY_NAME), 'e2e tests')
  unit_test_metadata = TriggerTest(api, dimensions, unit_tests_request)
  e2e_test_metadata = TriggerTest(api, dimensions, e2e_tests_request)

  # Generate, trigger, and collect results for a request to run the cast
  # standalone end to end tests, if certificate support is configured for this
  # build.
  cast_certificate_enabled = api.properties.get(
      'cast_allow_developer_certificate')
  if (cast_certificate_enabled):
    cast_request = SwarmRequest(
        cas_digest, api.path.join('.', 'cast', CAST_E2E_TEST_SCRIPT_NAME),
        'cast streaming e2e tests')
    cast_metadata = TriggerTest(api, dimensions, cast_request)
    CollectTest(api, paths, dimensions, cast_request, cast_metadata)

  CollectTest(api, paths, dimensions, unit_tests_request, unit_test_metadata)
  CollectTest(api, paths, dimensions, e2e_tests_request, e2e_test_metadata)



def SetCodeCoverageConstants(api, checkout_path, host_tool_label):
  """Performs dependency injection on internal constants of the code_coverage
     module needed to allow for the code coverage module to function correctly.

  Args:
      api (recipe_api.RecipeApi): API generated from recipe dependencies.
      checkout_path (api.path): Location of openscreen checkout.
      host_tool_label (str): Host label from GetHostToolLabel.
  """
  llvm_dir = checkout_path.joinpath('third_party', 'llvm-build',
                                    'Release+Asserts', 'bin')
  merge_libs_dir = checkout_path.joinpath('build', 'code_coverage')

  api.profiles._merge_scripts_dir = merge_libs_dir
  api.profiles._llvm_base_path = llvm_dir
  api.code_coverage._platform = host_tool_label
  api.code_coverage._use_clang_coverage = True
  api.code_coverage._include_component_mapping = False
  api.code_coverage.src_dir = checkout_path

  exists = {
      str(p): api.path.exists(p) for p in [
          api.profiles.llvm_profdata_exec, api.code_coverage.cov_executable,
          merge_libs_dir
      ]
  }
  if not all(exists.values()):
    result = ''.join('\nhas {}: {}'.format(p, e) for (p, e) in exists.items())
    api.step.empty(
        'code coverage executable dependencies missing!',
        status=api.step.INFRA_FAILURE,
        step_text=result)


def CalculateCodeCoverage(api, paths):
  """Calculates code coverage from raw coverage data generated by prior test
       runs.

  Args:
      api (recipe_api.RecipeApi): API generated from recipe dependencies.
      paths (RepositoryPaths): Checkout-dependent repository files.
  """
  # Configure a temporary directory where the code_coverage module knows
  # to look for .profdata files.
  temp_dir = api.profiles.profile_dir('profdata')

  # Process the raw code coverage data.
  api.step('process raw coverage data', [
      api.profiles.merge_results_script,
      '--task-output-dir',
      paths.checkout_path,
      '--profdata-dir',
      temp_dir,
      '--llvm-profdata',
      api.profiles.llvm_profdata_exec,
      '--per-cl-coverage',
  ])

  # Validate that the script worked as expected, then bubble this up to
  # the trybot UI.
  source = paths.output_path / 'default.profdata'
  dest = temp_dir / 'default.profdata'
  if api.path.exists(source) or api.path.exists(dest):
    api.step.empty('coverage data successfully processed')
  else:
    api.step.empty('failed to process coverage data', status=api.step.FAILURE)

  # Copy the generated profdata file to a temp directory where the
  # code_coverage tool knows to look for it.
  # NOTE: This is needed because the merge_lib.py chromium script does
  # not correctly respect the --profdata-dir parameter. This fix has
  # been added here rather than in merge_lib.py for two reasons:
  # - To keep openscreen's merge_lib.py in sync with that of chromium.
  # - So that if Chromium's merge_lib.py fixes this issue, the
  #   dependency can be updated without breaking our build
  if api.path.exists(source) and not api.path.exists(dest):
    api.file.copy('copy processed coverage data', source, dest)

  # Upload the coverage results to the code_coverage cloud storge
  # account, so that Gerrit can find it.
  # For more information, see:
  # https://source.chromium.org/chromium/chromium/tools/build/+/main:recipes/recipe_modules/code_coverage/api.py;drc=70ad29c01fa623ddad8bd1d6db2009e6472504e8;l=666  #pylint: disable=line-too-long
  api.code_coverage.process_clang_coverage_data(
      binaries={paths.unit_test_binary_path}, upload_metadata=True)


def RunTestsLocally(api, paths):
  """Runs unit tests and e2e tests locally.

  Args:
      api (recipe_api.RecipeApi): API generated from recipe dependencies.
      paths (RepositoryPaths): Checkout-dependent repository files.
  """
  api.step('run unit tests', [paths.unit_test_binary_path])
  api.step('run e2e tests', [paths.e2e_test_binary_path])


def RunTestsAndCoverageLocally(api, paths):
  """Runs unit tests and e2e tests locally and calculates code coverage.

  Args:
      api (recipe_api.RecipeApi): API generated from recipe dependencies.
      paths (RepositoryPaths): Checkout-dependent repository files.
  """
  # Proceed with remaining tests for non-ARM bots.
  with api.step.nest('run tests'):
    # Clean up invalid coverage data generated by build.
    #
    # TODO(crbug/1086998): Stop generating this invalid data.
    with api.step.nest('perform pre-test cleanup'):
      files = api.file.glob_paths(
          'get files',
          paths.checkout_path,
          '**/*.profraw',
          test_data=[paths.output_path / 'default.profraw'])
      for path in files:
        api.file.remove('remove ' + str(path), path)

    # Run the Unit Tests, generating the .profraw data
    api.step('run unit tests', [paths.unit_test_binary_path])

    # Ensure tests correctly generated coverage data.
    profraw_path = paths.checkout_path / 'default.profraw'
    if not api.path.exists(profraw_path):
      api.step.empty(
          'skip coverage calculations because no data was generated',
          status=api.step.FAILURE)

    # Use data generated from tests to calculate code coverage.
    else:
      with api.step.nest('calculate code coverage'):
        CalculateCodeCoverage(api, paths)

    # TODO(issuetracker.google.com/155643967): Include E2E test coverage
    # with unit test coverage.
    api.step('run e2e tests', [paths.e2e_test_binary_path])

  # Set output properties on the buildbot as required for code coverage
  # to function.
  # NOTE: This cannot be done in a nested step due to buildbot weirdness.
  # If it is, the output properties will not be reflected correctly for
  # the build.
  api.code_coverage._set_builder_output_properties_for_uploads()


def RunSteps(api):
  """Main function body for execution on the current bot.

  Args:
      api (recipe_api.RecipeApi): API generated from recipe dependencies.
  """
  openscreen_config = api.gclient.make_config()
  solution = openscreen_config.solutions.add()
  solution.name = 'openscreen'
  solution.url = OPENSCREEN_REPO
  solution.deps_file = 'DEPS'
  # We need to set the target CPU for gclient to pull down its related sysroot.
  target_cpu = api.properties.get('target_cpu')
  if target_cpu:
    openscreen_config.target_cpu.add(target_cpu)
  api.gclient.c = openscreen_config

  api.bot_update.ensure_checkout()
  api.gclient.runhooks()

  paths = RepositoryPaths(api)
  GenerateCoverageTestConstants(api, paths)

  env = {}
  api.profiles.src_dir = paths.checkout_path
  if api.properties.get('is_asan', False):
    env['ASAN_SYMBOLIZER_PATH'] = str(
        api.profiles.llvm_exec_path('llvm-symbolizer'))

  is_ci = api.properties.get('is_ci', False)
  with api.context(cwd=paths.checkout_path, env=env):
    # Populate Code coverage tool with the set of files that changed.
    use_coverage = api.properties.get('use_coverage', False)
    if use_coverage:
      with api.step.nest('initialize code coverage') as coverage_step:
        coverage_step.status = api.step.SUCCESS
        try:
          host_tool_label = GetHostToolLabel(api.platform)
          SetCodeCoverageConstants(api, paths.checkout_path, host_tool_label)

          # Only continuous integration bots run full-repo coverage--trybots
          # only run per-cl coverage.
          if is_ci:
            # NOTE: By skipping the below instrument() call, the
            # code_coverage  module is set to perform full-repo coverage
            # calculations.
            api.step.empty('instrumentation skipped for full repo coverage')
          else:
            changed_files = GetChangedFiles(api, paths.checkout_path)

            # Initialize code coverage.
            # For more details, see: https://goto.google.com/dmbjf
            #
            # NOTE: This step configures the code_coverage module to perform
            # per-CL coverage calculations.
            api.code_coverage.instrument(
                changed_files, output_dir=paths.output_path)

            api.step.empty(
                'coverage calculations will proceed for {} files'.format(
                    len(changed_files)),
                step_text=''.join(
                    '\n{}'.format(str(path)) for path in changed_files))
        except:  # pylint: disable=bare-except
          coverage_step.status = api.step.FAILURE
          use_coverage = False

    # NOTE: the api.osx_sdk statement is a no-op on non-macOS platforms.
    with api.osx_sdk('mac'):
      api.step('gn gen', [
          api.depot_tools.gn_py_path,
          'gen',
          paths.output_path,
          '--check',
          '--args=' + FormatGnArgs(api.properties),
      ])

      ninja_cmd = [paths.ninja_path, '-C', paths.output_path]
      ninja_cmd.extend(BUILD_TARGETS)

      api.step('compile with ninja', ninja_cmd)

    # ARM64 tests cannot be run on the building bot, since they must be
    # cross-compiled from x86-64.
    is_arm64 = api.properties.get('target_cpu') == 'arm64'
    if is_arm64:
      assert not use_coverage, "coverage is not supported on ARM64 builds."
      SwarmTests(api, paths, GetSwarmingDimensions(is_ci))
    elif use_coverage:
      RunTestsAndCoverageLocally(api, paths)
    else:
      RunTestsLocally(api, paths)


def GenTests(api):
  """Generates tests used to verify there are no python usage errors.

  Args:
      api (recipe_api.RecipeApi): API generated from recipe dependencies.

  Yields:
      tests: Generated API tests to be used to verify usage.
  """
  yield api.test(
      'linux_x64_coverage',
      api.platform('linux', 64),
      api.buildbucket.try_build('openscreen', 'try'),
      api.properties(
          is_debug=True,
          is_asan=True,
          use_coverage=True,
          is_valid_coverage_test=True,
          generate_test_profraw=True,
          generate_test_profdata=True),
      api.step_data(
          'run tests.calculate code coverage.process raw coverage data',
          retcode=0),
  )
  yield api.test(
      'linux_x64_coverage_no_profdata_does_fail_bot',
      api.platform('linux', 64),
      api.buildbucket.try_build('openscreen', 'try'),
      api.properties(
          is_debug=True,
          is_asan=True,
          use_coverage=True,
          is_valid_coverage_test=True,
          generate_test_profraw=True,
      ),
      api.step_data(
          'run tests.calculate code coverage.process raw coverage data',
          retcode=0),
      api.expect_status('FAILURE'),
  )
  yield api.test(
      'linux_x64_coverage_no_profraw_does_fail_bot',
      api.platform('linux', 64),
      api.buildbucket.try_build('openscreen', 'try'),
      api.properties(
          is_debug=True,
          is_asan=True,
          use_coverage=True,
          is_valid_coverage_test=True),
      api.expect_status('FAILURE'),
  )
  yield api.test(
      'linux_x64_coverage_failed_coverage_init',
      api.platform('linux', 64),
      api.buildbucket.try_build('openscreen', 'try'),
      api.properties(is_debug=True, is_asan=True, use_coverage=True),
  )
  yield api.test(
      'linux_x64_coverage_full_repo_coverage',
      api.platform('linux', 64),
      api.buildbucket.try_build('openscreen', 'ci'),
      api.properties(
          is_debug=True,
          is_asan=True,
          is_ci=True,
          use_coverage=True,
          is_valid_coverage_test=True,
          generate_test_profraw=True,
          generate_test_profdata=True),
      api.step_data(
          'run tests.calculate code coverage.process raw coverage data',
          retcode=0),
  )
  yield api.test(
      'linux_x64',
      api.platform('linux', 64),
      api.buildbucket.try_build('openscreen', 'try'),
      api.properties(is_debug=True, is_asan=True),
  )
  yield api.test(
      'linux_x64_tsan_rel',
      api.platform('linux', 64),
      api.buildbucket.try_build('openscreen', 'try'),
      api.properties(is_tsan=True),
  )
  yield api.test(
      'linux_x64_msan_rel',
      api.platform('linux', 64),
      api.buildbucket.try_build('openscreen', 'try'),
      api.properties(is_msan=True),
  )
  yield api.test(
      'linux_x64_gcc',
      api.platform('linux', 64),
      api.buildbucket.try_build('openscreen', 'try'),
      api.properties(is_debug=True, is_gcc=True),
  )
  yield api.test(
      'linux_arm64', api.platform('linux', 64),
      api.buildbucket.try_build('openscreen', 'try'),
      api.properties(
          is_debug=True, is_component_build=False, target_cpu='arm64'))
  yield api.test(
      'linux_arm64_ci', api.platform('linux', 64),
      api.buildbucket.try_build('openscreen', 'ci'),
      api.properties(
          is_debug=True,
          is_component_build=False,
          target_cpu='arm64',
          is_ci=True))
  yield api.test(
      'linux_arm64_cast_receiver', api.platform('linux', 64),
      api.buildbucket.try_build('openscreen', 'try'),
      api.properties(
          is_debug=True,
          is_component_build=False,
          target_cpu='arm64',
          cast_allow_developer_certificate=True,
          have_ffmpeg=True,
          have_libsdl2=True,
          have_libopus=True,
          have_libvpx=True))
  failed_result = api.swarming.task_result(
      id='0',
      name=UNIT_TEST_BINARY_NAME,
      state=api.swarming.TaskState.COMPLETED,
      failure=True)
  yield api.test(
      'linux_arm64_with_collect_COMPLETED_and_failed',
      api.platform('linux', 64),
      api.buildbucket.try_build('openscreen', 'try'),
      api.properties(
          is_debug=True, is_component_build=False, target_cpu='arm64'),
      api.override_step_data('collect unit tests',
                             api.swarming.collect([failed_result])),
      api.expect_status('FAILURE'),
  )

  timeout_result = api.swarming.task_result(
      id='0',
      name=UNIT_TEST_BINARY_NAME,
      state=api.swarming.TaskState.TIMED_OUT)
  yield api.test(
      'linux_arm64_with_collect_TIMED_OUT',
      api.platform('linux', 64),
      api.buildbucket.try_build('openscreen', 'try'),
      api.properties(
          is_debug=True, is_component_build=False, target_cpu='arm64'),
      api.override_step_data('collect unit tests',
                             api.swarming.collect([timeout_result])),
      api.expect_status('FAILURE'),
  )

  died_result = api.swarming.task_result(
      id='0', name=UNIT_TEST_BINARY_NAME, state=api.swarming.TaskState.BOT_DIED)
  yield api.test(
      'linux_arm64_with_collect_BOT_DIED',
      api.platform('linux', 64),
      api.buildbucket.try_build('openscreen', 'try'),
      api.properties(
          is_debug=True, is_component_build=False, target_cpu='arm64'),
      api.override_step_data('collect unit tests',
                             api.swarming.collect([died_result])),
      api.expect_status('INFRA_FAILURE'),
  )
  yield api.test(
      'mac_x64',
      api.platform('mac', 64),
      api.buildbucket.try_build('openscreen', 'try'),
      api.properties(is_debug=True),
  )
  yield api.test(
      'win_x64',
      api.platform('win', 64),
      api.buildbucket.try_build('openscreen', 'try'),
      api.properties(is_debug=True),
  )
