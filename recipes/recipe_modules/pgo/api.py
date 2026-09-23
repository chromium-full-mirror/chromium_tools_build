# Copyright (c) 2020 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import hashlib

from recipe_engine import recipe_api
from recipe_engine.config_types import Path


class PgoApi(recipe_api.RecipeApi):
  # This is used by main_waterfall_steps.py and trybot_steps.py.
  TEMP_PROFDATA_FILENAME = 'pgo_final_aggregate.profdata'

  # These are private to this file.
  _GS_BUCKET = 'chromium-optimization-profiles'
  _GS_BUCKET_PATH = 'pgo_profiles'
  _SUPPORTED_CROS_ARCH = ['amd64-generic', 'arm-generic', 'arm64-generic']

  def __init__(self, properties, *args, **kwargs):
    super().__init__(*args, **kwargs)
    self._use_pgo = properties.use_pgo
    self._skip_profile_upload = properties.skip_profile_upload
    self._gs_bucket_override = properties.gs_bucket_override
    self._gs_bucket_path_override = properties.gs_bucket_path_override
    self._profdata_platform_override = properties.profdata_platform_override
    self._last_uploaded_pgo_filename = None

  @property
  def using_pgo(self):
    """Indicates this run uses PGO, set in bot's *.star configuration files.

    Used by chromium_tests.run_tests() to process profile data when True.
    """
    return self._use_pgo

  @property
  def skip_profile_upload(self):
    """Flag to skip profile uploads to GS.

    Bypass uploading the generated profile to GS such that it's not rolled
    into src.
    """
    return self._skip_profile_upload

  @property
  def gs_bucket(self):
    """The GS bucket name to use.

    Can be overridden by the gs_bucket_override property.
    """
    return self._gs_bucket_override or self._GS_BUCKET

  @property
  def gs_bucket_path(self):
    """The GS bucket path to use.

    Can be overridden by the gs_bucket_path_override property.
    """
    return self._gs_bucket_path_override or self._GS_BUCKET_PATH

  @property
  def last_uploaded_pgo_filename(self):
    """The filename of the most recently uploaded profile."""
    return self._last_uploaded_pgo_filename

  @property
  def branch(self):
    """Parse the branch name from ref."""
    ref = self.m.buildbucket.gitiles_commit.ref
    if not ref:
      raise self.m.step.StepFailure(
        f'Missing ref: {self.m.buildbucket.gitiles_commit}'
      )
    # Release ref: refs/branch-heads/4103
    # Main ref: refs/heads/main
    return ref.split('/', 2)[2]

  def _get_platform(self):
    """Return the platform name used for the profdata artifact."""
    if self._profdata_platform_override:
      return self._profdata_platform_override

    # android and chromeos are undefined through platform API,
    # so we use the chromium config
    target_platform = self.m.chromium.c.TARGET_PLATFORM
    if target_platform in ['android', 'chromeos']:
      return target_platform
    return self.m.platform.name

  def configure_llvm_tooling_path(self, source_dir: Path, builder_id, is_cros):
    """Configure llvm tooling path

    This will set profiles recipe module to use llvm tooling to the same one
    used by CrOS. This workflow is specific to PGO.

    Args:
      builder_id: Builder ID used for identifying GN args
      is_cros: Flag to ensure that this logic only runs for CrOS. CrOS
               is usually identified from chromium.c.TARGET_PLATFORM = "cros"
    """
    # for chromeos, ensure we use the same llvm version as cros toolchain so
    # that we avoid any incompatibilities.
    if not is_cros:
      return

    with self.m.step.nest('searching cros llvm toolchain') as p:
      gn_args = self.m.chromium.mb_lookup(
        source_dir, builder_id, recursive=True
      )
      dict_gn_args = self.m.gn.parse_gn_args(self.m.gn.reformat_args(gn_args))
      # crbug/1417071 - We use cros_target_cxx, but this should be its own
      # llvm-profdata arg at some point instead of deducing it.
      if 'cros_target_cxx' in dict_gn_args:
        val = dict_gn_args['cros_target_cxx'].strip('"')
        target_path = self.m.path.join(
          self.m.path.dirname(val), 'llvm-profdata'
        )
        if self.m.path.exists(target_path):
          self.m.profiles.llvm_profdata_exec = target_path
          p.logs['override llvm-profdata-path'] = (
            'Detected cros. Overriding llvm-profdata path to %s' % target_path
          )

  def _get_commit(self, test_data=None):
    """Return the hash of the current commit.

    Args:
      * test_data (bytes): used as the return value for testing.

    Return:
        (string) commit hash.
    """
    step_test_data = None
    if test_data is not None:
      step_test_data = lambda: self.m.raw_io.test_api.stream_output(test_data)
    return (
      self.m.git(
        'rev-parse',
        'HEAD',
        stdout=self.m.raw_io.output(),
        step_test_data=step_test_data,
      )
      .stdout.strip()
      .decode('utf-8')
    )

  def _profdata_artifact_name(self, source_dir: Path, sha1: str) -> str:
    """Generate profdata artifact name.

    Args:
      source_dir: The path to the top-level repo.
      sha1: sha1 hash of the profdata content.

    Template:
    chrome-{platform}-{branch number}-{timestamp}-{profile_hash}-{commit_hash}.profdata
    * {platform} refers to the platform, which is one of
        [android32, android64, linux, win32, win64 and mac].
    * {branch number} refers to the branch number, such as 4103.
    * {timestamp} refers to the timestamp of the commit at HEAD.
    * {profile_hash} refers to the sha1 hash of the profdata content.
    * {commit_hash} refers to the Chromium commit where the profile was generated.

    Return:
      filename of the resulting .profdata.
    """
    # TODO(crbug.com/1077004) - Prefix should be chromium for profiles generated
    # without internal sources. Update this prefix when support is introduced.
    profdata_template = 'chrome-%s-%s-%s-%s-%s.profdata'

    platform = self._get_platform()

    # if is_win, we support [32,64,arm64].
    if self.m.platform.is_win:
      if self.m.chromium.c.TARGET_ARCH == 'arm':
        platform += '-arm'
      platform += str(self.m.chromium.c.TARGET_BITS)

    # if android, should append bits [32,64]
    if platform == 'android':
      platform += str(self.m.chromium.c.TARGET_BITS)

    # only supporting -arm for mac for now.
    arch = self.m.chromium.c.TARGET_ARCH
    if platform == 'mac' and self.m.chromium.c.TARGET_ARCH == 'arm':
      platform += '-' + arch

    # listify and split by colon.
    target_cros_boards = self.m.chromium.c.TARGET_CROS_BOARDS
    if platform == 'chromeos' and target_cros_boards:
      target_cros_boards = target_cros_boards.split(':')
      # lacros will specify both arch and board in TARGET_CROS_BOARDS.
      # we don't include board as part of the name because amd64 can run across
      # all the boards, but we can only train with one. we may look into
      # training across multiple boards and merging into one, or train for the
      # most popular board to maximize perf.
      for arch_def in self._SUPPORTED_CROS_ARCH:
        if arch_def in target_cros_boards:
          platform += '-' + arch_def

    commit = 'none'
    with self.m.context(cwd=source_dir):
      # timestamp from git commit HEAD. under the hood invokes
      # `git show --format=%at -s`, where %at=author date, UNIX timestamp
      timestamp = str(self.m.git.get_timestamp(test_data='1587876258'))
      commit = self._get_commit(
        test_data=b'abcdeabcdeabcdeabcdeabcdeabcdeabcdeabcde'
      )

    return profdata_template % (platform, self.branch, timestamp, sha1, commit)

  def ensure_profdata_files(self, tests):
    """Ensure there is a profdata file generated for each test.

    Each test run should have a subfolder path in profiles.profile_subdirs
    with a profdata file. Fails the current run if the number of tests don't
    match the number of profdata files in the subdir

    Args:
      tests: list of step.Test objects.
    """
    failed_benchmarks = []
    missing_files = {}
    with self.m.step.nest(
      'validate benchmark results and profile data'
    ) as presentation:
      files = set(
        self.m.file.listdir(
          'searching for profdata files',
          self.m.profiles.profile_dir(),
          recursive=True,
        )
      )

      tests_failing_verification = []
      for test in tests:
        test_verification_success = False
        # crbug.com/1113316 - Ensure that the test is passed before any form
        # of handling. Failed benchmarks may produce bad profdata files, so
        # we want to fail this run if any of them fail.
        for suffix in test._rdb_results:
          # Key in profile_subdirs is set as test.step_name with suffix
          subdir_identifier = test.step_name(suffix)
          path = self.m.profiles.profile_subdirs.get(subdir_identifier)
          profdata_filename = test.target_name + '.profdata'
          profdata_path = path / profdata_filename
          # Check that each suffix for this test is valid and has no failures
          if not test.has_valid_results(suffix) or test.deterministic_failures(
            suffix
          ):
            # Remove the profile from failed runs so it's never included in
            # the final generated profile.
            self.m.file.remove('Removing %s' % profdata_path, profdata_path)
            failed_benchmarks.append(f'{test.name} ({suffix})')
            continue
          # In this path, there should be a profdata file named after the test
          if profdata_path not in files:
            missing_files[profdata_filename] = subdir_identifier
            continue

          # Consider success if any suffix of the test (including the original
          # run and possibly a retry) is successful.
          test_verification_success = True

        if not test_verification_success:
          tests_failing_verification.append(test.name)

      # Empty failed_benchmarks and missing_files is logged irrespective of
      # step status.
      if failed_benchmarks or missing_files:
        presentation.logs['failed_benchmarks'] = failed_benchmarks
        presentation.logs['missing_files'] = self.m.json.dumps(
          missing_files, indent=2
        )

      if tests_failing_verification:
        failure_msgs = []
        failure_msgs.append(
          'The following tests failed all runs: **%s**.'
          % ', '.join(tests_failing_verification)
        )
        if failed_benchmarks:
          failure_msgs.append(f'{len(failed_benchmarks)} benchmark(s) failed.')
          for failed_benchmark in failed_benchmarks:
            failure_msgs.append(f'**{failed_benchmark}** failed.')
        if missing_files:
          failure_msgs.append(f'{len(missing_files)} missing file(s).')
          for name, subdir_identifier in missing_files.items():
            failure_msgs.append(f'**{name}** missing in {subdir_identifier}.')

        presentation.status = self.m.step.FAILURE
        raise self.m.step.StepFailure('\n'.join(failure_msgs))

  def process_pgo_data(self, source_dir: Path, tests):
    """Processes the pgo profraw files generated by benchmark tests.

    The implementation is similar to self.m.code_coverage.process_coverage_data,
    but leaves out the cruft and focuses only on merging and uploading.

    Args:
      source_dir: The path to the top-level repo.
      tests: (list) of step.Test objects
    """
    # Ensure a profdata was generated per test, before even starting to process
    self.ensure_profdata_files(tests)

    with self.m.step.nest('Processing PGO .profraw data'):
      # Invoke the merge script
      profdata_artifact = self.m.profiles.profile_dir().joinpath(
        self.TEMP_PROFDATA_FILENAME
      )
      # We want to run llvm-profdata without the --sparse argument.
      # https://llvm.org/docs/CommandGuide/llvm-profdata.html#profdata-merge
      self.m.profiles.merge_profdata(profdata_artifact)

      if not self.m.path.exists(profdata_artifact):
        self.m.step.empty(
          'No profdata was generated.',
          status=self.m.step.FAILURE,
          step_text=(
            'Verify that the Swarming tasks have '
            'completed successfully, and have output .profraw files'
          ),
        )

      # Check for any merge errors
      self.m.profiles.find_merge_errors()
      result = self.m.step.active_result
      if result.stdout and result.stdout['total'] > 0:
        result.presentation.text = 'Found invalid profraw files'
        result.presentation.properties['merge errors'] = result.stdout
        self.m.step.empty(
          'Failing due to merge errors found alongside invalid profile data.',
          status=self.m.step.FAILURE,
          step_text='Please see logs of failed step for details.',
        )

      # SHA1 hash content of the profdata is used as part of the naming to
      # make it content-addressed.
      contents = self.m.file.read_raw(
        'Read profdata content',
        profdata_artifact,
        test_data='some_profdata_content',
      )
      sha1 = hashlib.sha1(contents).hexdigest()

      if self.skip_profile_upload:
        return self.m.step.empty(
          'Skipping upload to GS for this generated profile as '
          'skip_profile_upload property is enabled.'
        )

      # The final profdata artifact name uses the sha1 hash of the contents,
      # so the profdata file is generated first, and then renamed.
      new_filename = self._profdata_artifact_name(source_dir, sha1)
      new_filepath = self.m.profiles.profile_dir().joinpath(new_filename)
      self.m.file.move(
        'Rename the profdata artifact', profdata_artifact, new_filepath
      )

      self._last_uploaded_pgo_filename = new_filename

      # Reset profdata_artifact to the updated naming
      self.m.profiles.upload(
        self.gs_bucket,
        '%s/%s' % (self.gs_bucket_path, new_filename),
        new_filepath,
        args=[
          '-Z',
        ],
        link_name=new_filename,
      )
