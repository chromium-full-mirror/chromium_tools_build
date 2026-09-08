# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import recipe_api
from recipe_engine.config_types import Path

from RECIPE_MODULES.build.chromium_tests.steps import Test

from . import evaluation

TEST_RTS_MODEL_EXPERIMENT = 'chromium_rts.filter_file_analysis'
_RTS_MODEL_CIPD_PREFIX = 'chromium/rts/model/'
_DEFAULT_TARGET_CHANGE_RECALL = 0.95


class ChromiumRtsApi(recipe_api.RecipeApi):
  """An interaction module with Regression Test Selection (RTS) for Chromium."""

  def __init__(self, **kwargs):
    super().__init__(**kwargs)
    self._evaluation_future = None
    self._banned_suites = None
    self._overwritten_tests: set[str] = set()

  def get_experiment_names(self) -> set[str]:
    """Returns the set of RTS experiment names."""
    return {TEST_RTS_MODEL_EXPERIMENT}

  def _should_generate_filters(self, tests: list[Test] | None = None) -> bool:
    """Whether RTS filter file generation should run."""
    experiments = self.m.buildbucket.build.input.experiments
    return (TEST_RTS_MODEL_EXPERIMENT
            in experiments) or (tests is not None and
                                any(t.enable_rts_filtering for t in tests))

  def filter_file_dir(self, build_dir: Path) -> Path:
    """Returns the path to the directory containing the RTS filter files."""
    return build_dir / 'gen' / 'rts'

  def get_filter_file_path(
      self,
      build_dir: Path,
      test: Test,
  ) -> Path | None:
    """Returns the path to the RTS filter file for a test, if applicable."""
    target_name = test.isolate_target or test.target_name
    # Derivative suites sharing a target/isolate with a parent suite should not
    # look for or associate filter files unless RTS filtering is enabled.
    if test.canonical_name != target_name and not test.enable_rts_filtering:
      return None
    return self.filter_file_dir(build_dir) / f'{test.canonical_name}.filter'

  def generate_filter_files(
      self,
      src_dir: Path,
      build_dir: Path,
      affected_files: list[str],
      tests: list[Test] | None = None,
  ) -> None:
    """Generates RTS filter files if RTS is enabled."""
    if not self._should_generate_filters(tests):
      return

    filter_file_dir = self.filter_file_dir(build_dir)
    model_dir = self._fetch_chromium_rts_model()

    exe_name = 'rts-chromium'
    if self.m.platform.is_win:
      exe_name += '.exe'
    exec_path = model_dir / exe_name

    cmd = [
        exec_path,
        'select',
        '-checkout',
        src_dir,
        '-model-dir',
        model_dir,
        '-out',
        filter_file_dir,
        '-changed-files-path',
        self.m.raw_io.input_text('\n'.join(affected_files)),
        '-target-change-recall',
        str(_DEFAULT_TARGET_CHANGE_RECALL),
        '-change-ref',
        'HEAD~',
        '-gen-inverse',
    ]

    self.m.step('generate chromium-rts filter files', cmd)

  def _get_banned_suites(self) -> set[str]:
    """Loads and resolves the banned suites for the current builder."""
    if self._banned_suites is not None:
      return self._banned_suites

    resource_path = self.resource('rts_banned_suites.json')
    banned_suites_dict = self.m.file.read_json(
        'read rts_banned_suites.json',
        resource_path,
        test_data={
            '*': ['blink_python_tests'],
        })
    builder = self.m.buildbucket.builder_name
    banned = set(banned_suites_dict.get('*', []))
    banned.update(banned_suites_dict.get(builder, []))
    self._banned_suites = banned
    return self._banned_suites

  def _fetch_chromium_rts_model(self) -> Path:
    """Fetches the RTS model from CIPD."""
    install_dir = self.m.path.cleanup_dir / 'rts-model'
    platform = self.m.cipd.platform
    package_name = _RTS_MODEL_CIPD_PREFIX + platform
    ensure_file = self.m.cipd.EnsureFile()
    ensure_file.add_package(package_name, 'latest')
    self.m.cipd.ensure(install_dir, ensure_file, name='install RTS model')
    return install_dir

  def evaluate_rts(
      self,
      build_dir: Path,
      tests: list[Test],
  ) -> None:
    """RTS safety evaluation logic.

    Evaluates the model's safety performance (test and builder recall)
    by comparing filter files and RDB results.

    Args:
      build_dir: Path to the root build directory containing generated RTS
        filter files.
      tests: List of Test objects to evaluate against their ResultDB test
        results.
    """
    if TEST_RTS_MODEL_EXPERIMENT in self.m.buildbucket.build.input.experiments:
      evaluation.evaluate_rts(self, build_dir, tests, self._get_banned_suites(),
                              self._overwritten_tests)

  def start_evaluation(
      self,
      build_dir: Path,
      tests: list[Test],
  ) -> None:
    """Starts RTS safety evaluation in a background future."""
    if self._evaluation_future is not None:
      return
    self._evaluation_future = self.m.futures.spawn_immediate(
        self.evaluate_rts,
        build_dir,
        tests,
    )

  def wait_for_evaluation(self) -> None:
    """Waits for background RTS safety evaluation to complete."""
    if self._evaluation_future is not None:
      self.m.futures.wait([self._evaluation_future])
      self._evaluation_future = None

  def isolate_filter_files(self,
                           build_dir: Path,
                           targets: list[str],
                           tests: list[Test] | None = None) -> None:
    """Adds generated RTS filter files to the corresponding isolate files."""
    if not self._should_generate_filters(tests):
      return

    with self.m.step.nest('add RTS filter files to isolates'):
      missing_isolates = []
      for target in targets:
        if target in self._get_banned_suites():
          continue
        filter_files_to_add = []
        filter_file = self.filter_file_dir(build_dir) / f'{target}.filter'
        if self.m.path.exists(filter_file):
          filter_files_to_add.append(
              self.m.path.relpath(filter_file, build_dir))
        comp_filter_file = self.filter_file_dir(
            build_dir) / f'{target}_inverted.filter'
        if self.m.path.exists(comp_filter_file):
          filter_files_to_add.append(
              self.m.path.relpath(comp_filter_file, build_dir))

        if filter_files_to_add:
          isolate_file = build_dir / f'{target}.isolate'
          if self.m.path.exists(isolate_file):
            self.m.isolate.add_files_to_isolate_file(isolate_file,
                                                     filter_files_to_add)
          else:
            missing_isolates.append(target)

      if missing_isolates:
        step_result = self.m.step.empty('missing isolate files')
        step_result.presentation.step_text = (
            'The following targets had RTS filter files generated but were '
            'missing .isolate files: %s' % ', '.join(missing_isolates))

  def get_command_line_variants(
      self,
      build_dir: Path,
      command_lines: dict[str, list[str]],
      tests: list[Test] | None = None,
  ) -> dict[str, dict[str, list[str]]]:
    """Constructs command line variants for the given targets."""
    variants = {}
    rts_command_lines = self._get_rts_command_lines(build_dir, command_lines,
                                                    tests)
    if rts_command_lines:
      variants['rts'] = rts_command_lines

    rts_complement_command_lines = self._get_rts_complement_command_lines(
        build_dir, command_lines, tests)
    if rts_complement_command_lines:
      variants['rts_complement'] = rts_complement_command_lines

    return variants

  def _get_rts_command_lines(
      self,
      build_dir: Path,
      command_lines: dict[str, list[str]],
      tests: list[Test] | None = None,
  ) -> dict[str, list[str]]:
    """Constructs RTS-modified command lines for the given targets. """
    rts_command_lines = {}
    if not self._should_generate_filters(tests):
      return rts_command_lines

    for target, cmd in command_lines.items():
      if target in self._get_banned_suites():
        continue
      filter_file = self.filter_file_dir(build_dir) / f'{target}.filter'
      if self.m.path.exists(filter_file):
        rts_cmd = list(cmd)
        rts_cmd.append(f'--test-launcher-filter-file=gen/rts/{target}.filter')
        rts_command_lines[target] = rts_cmd

    return rts_command_lines

  def _get_rts_complement_command_lines(
      self,
      build_dir: Path,
      command_lines: dict[str, list[str]],
      tests: list[Test] | None = None,
  ) -> dict[str, list[str]]:
    """Constructs RTS complement command lines for the given targets. """
    rts_complement_command_lines = {}
    if not self._should_generate_filters(tests):
      return rts_complement_command_lines

    for target, cmd in command_lines.items():
      if target in self._get_banned_suites():
        continue
      filter_file = self.filter_file_dir(
          build_dir) / f'{target}_inverted.filter'
      if self.m.path.exists(filter_file):
        rts_cmd = list(cmd)
        rel_path = self.m.path.relpath(filter_file, build_dir)
        rts_cmd.append(f'--test-launcher-filter-file={rel_path}')
        rts_complement_command_lines[target] = rts_cmd

    return rts_complement_command_lines

  def set_swarming_test_execution_info(
      self,
      test: Test,
      command_line_variants: dict[str, dict[str, list[str]]] | None,
  ) -> None:
    """Sets RTS command line on the given test if available."""
    if not command_line_variants or 'rts' not in command_line_variants:
      return

    if not test.enable_rts_filtering:
      return

    rts_command_line = command_line_variants['rts'].get(test.target_name, [])
    if rts_command_line:
      test.raw_cmd = rts_command_line
      self._overwritten_tests.add(test.name)

  def append_test_step_text(
      self,
      test: Test,
      messages: list[str],
  ) -> None:
    """Appends RTS info message if the test's command was overwritten by RTS."""
    if test.name in self._overwritten_tests:
      messages.append(
          'Ran tests selected by Regression Test Selection (RTS).\n')
