# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import traceback

from recipe_engine import recipe_api, step_data
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
    self._rts_disabled: bool = False

  def get_experiment_names(self) -> set[str]:
    """Returns the set of RTS experiment names."""
    return {TEST_RTS_MODEL_EXPERIMENT}

  def _should_generate_filters(self, tests: list[Test] | None = None) -> bool:
    """Whether RTS filter file generation should run."""
    if self._rts_disabled:
      return False
    experiments = self.m.buildbucket.build.input.experiments
    return (TEST_RTS_MODEL_EXPERIMENT in experiments) or (
      tests is not None and any(t.enable_rts_filtering for t in tests)
    )

  def _should_analyze(self) -> bool:
    """Whether RTS safety analysis should run."""
    if self._rts_disabled:
      return False
    return (
      TEST_RTS_MODEL_EXPERIMENT in self.m.buildbucket.build.input.experiments
    )

  def filter_file_dir(self, build_dir: Path) -> Path:
    """Returns the path to the directory containing the RTS filter files."""
    return build_dir / 'gen' / 'rts'

  def _get_isolate_target(self, test: Test) -> str:
    """Returns the isolate target name for a test."""
    return test.isolate_target or test.target_name

  def get_filter_file_path(
    self,
    build_dir: Path,
    test: Test,
    inverted: bool = False,
  ) -> Path | None:
    """Returns the path to the RTS filter file for a test."""
    target_name = self._get_isolate_target(test)
    # Derivative suites sharing a target/isolate with a parent suite should not
    # look for or associate filter files unless RTS filtering is enabled.
    if test.canonical_name != target_name and not test.enable_rts_filtering:
      return None
    suffix = '_inverted' if inverted else ''
    return (
      self.filter_file_dir(build_dir) / f'{test.canonical_name}{suffix}.filter'
    )

  def _disable_rts_with_reason(
    self,
    error: Exception,
    step_text_prefix: str,
    presentation: step_data.StepPresentation,
  ) -> None:
    """Disables RTS, records the error reason, and sets output properties."""
    self._rts_disabled = True
    presentation.status = self.m.step.FAILURE
    presentation.step_text = f'{step_text_prefix}: {error}'
    presentation.logs['exception'] = traceback.format_exc().splitlines()
    presentation.properties['rts_failed'] = True
    presentation.properties['rts_failure_reason'] = str(error)

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

    try:
      filter_file_dir = self.filter_file_dir(build_dir)
      model_dir = self._fetch_chromium_rts_model()
      if not model_dir or self._rts_disabled:
        return

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
    except Exception as e:
      self._disable_rts_with_reason(
        e,
        'RTS filter generation failed, falling back to all tests',
        self.m.step.active_result.presentation,
      )

  def _get_banned_suites(self) -> set[str] | None:
    """Loads and resolves the banned suites for the current builder."""
    if self._banned_suites is not None:
      return self._banned_suites

    try:
      resource_path = self.resource('rts_banned_suites.json')
      banned_suites_dict = self.m.file.read_json(
        'read rts_banned_suites.json',
        resource_path,
        test_data={
          '*': ['blink_python_tests'],
        },
      )
      builder = self.m.buildbucket.builder_name
      banned = set(banned_suites_dict.get('*', []))
      banned.update(banned_suites_dict.get(builder, []))
      self._banned_suites = banned
      return self._banned_suites
    except Exception as e:
      self._disable_rts_with_reason(
        e,
        'Failed to read banned suites, disabling RTS',
        self.m.step.active_result.presentation,
      )
      return None

  def _fetch_chromium_rts_model(self) -> Path | None:
    """Fetches the RTS model from CIPD."""
    try:
      install_dir = self.m.path.cleanup_dir / 'rts-model'
      platform = self.m.cipd.platform
      package_name = _RTS_MODEL_CIPD_PREFIX + platform
      ensure_file = self.m.cipd.EnsureFile()
      ensure_file.add_package(package_name, 'latest')
      self.m.cipd.ensure(install_dir, ensure_file, name='install RTS model')
      return install_dir
    except Exception as e:
      self._disable_rts_with_reason(
        e,
        'Failed to install RTS model, falling back to all tests',
        self.m.step.active_result.presentation,
      )
      return None

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
    if not self._should_analyze():
      return
    banned_suites = self._get_banned_suites()
    if banned_suites is None:
      return
    evaluation.evaluate_rts(
      self,
      build_dir,
      tests,
      banned_suites,
      self._overwritten_tests,
    )

  def start_evaluation(
    self,
    build_dir: Path,
    tests: list[Test],
  ) -> None:
    """Starts RTS safety evaluation in a background future."""
    if not self._should_analyze() or self._evaluation_future is not None:
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

  def isolate_filter_files(self, build_dir: Path, tests: list[Test]) -> None:
    """Adds generated RTS filter files to the corresponding isolate files."""
    if not self._should_generate_filters(tests):
      return

    with self.m.step.nest(
      'add RTS filter files to isolates'
    ) as nest_presentation:
      try:
        banned_suites = self._get_banned_suites()
        if banned_suites is None:
          nest_presentation.status = self.m.step.FAILURE
          nest_presentation.step_text = 'RTS failed during banned suites lookup'
          return

        filter_files_by_target = {}
        for test in tests:
          target = self._get_isolate_target(test)
          if target in banned_suites or test.canonical_name in banned_suites:
            continue
          filter_file = self.get_filter_file_path(build_dir, test)
          if filter_file and self.m.path.exists(filter_file):
            filter_files_by_target.setdefault(target, set()).add(
              self.m.path.relpath(filter_file, build_dir)
            )
          inverted_filter_file = self.get_filter_file_path(
            build_dir, test, inverted=True
          )
          if inverted_filter_file and self.m.path.exists(inverted_filter_file):
            filter_files_by_target.setdefault(target, set()).add(
              self.m.path.relpath(inverted_filter_file, build_dir)
            )

        missing_isolates = []
        for target, filter_files in sorted(filter_files_by_target.items()):
          isolate_file = build_dir / f'{target}.isolate'
          if self.m.path.exists(isolate_file):
            self.m.isolate.add_files_to_isolate_file(
              isolate_file, sorted(filter_files)
            )
          else:
            missing_isolates.append(target)

        if missing_isolates:
          step_result = self.m.step.empty('missing isolate files')
          step_result.presentation.step_text = (
            'The following targets had RTS filter files generated but were '
            'missing .isolate files: %s' % ', '.join(missing_isolates)
          )
      except Exception as e:
        self._disable_rts_with_reason(
          e,
          'Adding RTS filter files to isolates failed, '
          'falling back to all tests',
          presentation=nest_presentation,
        )

  def get_command_line_variants(
    self,
    build_dir: Path,
    command_lines: dict[str, list[str]],
    tests: list[Test],
  ) -> dict[str, dict[str, list[str]]]:
    """Constructs command line variants for the given targets."""
    if not self._should_generate_filters(tests):
      return {}

    banned_suites = self._get_banned_suites()
    if banned_suites is None:
      return {}

    variants = {}
    rts_command_lines = self._get_rts_command_lines(
      build_dir, command_lines, tests, banned_suites
    )
    if rts_command_lines:
      variants['rts'] = rts_command_lines

    rts_complement_command_lines = self._get_rts_command_lines(
      build_dir, command_lines, tests, banned_suites, inverted=True
    )
    if rts_complement_command_lines:
      variants['rts_complement'] = rts_complement_command_lines

    return variants

  def _get_rts_command_lines(
    self,
    build_dir: Path,
    command_lines: dict[str, list[str]],
    tests: list[Test],
    banned_suites: set[str],
    inverted: bool = False,
  ) -> dict[str, list[str]]:
    """Constructs RTS-modified command lines for the given targets."""
    rts_command_lines = {}
    for test in tests:
      target = self._get_isolate_target(test)
      if target in banned_suites or test.canonical_name in banned_suites:
        continue
      cmd = command_lines.get(target)
      if not cmd:
        continue
      filter_file = self.get_filter_file_path(
        build_dir, test, inverted=inverted
      )
      if filter_file and self.m.path.exists(filter_file):
        rts_cmd = list(cmd)
        rel_path = self.m.path.relpath(filter_file, build_dir)
        rts_cmd.append(f'--test-launcher-filter-file={rel_path}')
        rts_command_lines[target] = rts_cmd

    return rts_command_lines

  def set_swarming_test_execution_info(
    self,
    test: Test,
    command_line_variants: dict[str, dict[str, list[str]]] | None,
  ) -> None:
    """Sets RTS command line on the given test if available."""
    if not test.enable_rts_filtering or self._rts_disabled:
      return
    if not command_line_variants or 'rts' not in command_line_variants:
      in_experiment = (
        TEST_RTS_MODEL_EXPERIMENT in self.m.buildbucket.build.input.experiments
      )
      if in_experiment:
        self._rts_disabled = True
      return

    target = self._get_isolate_target(test)
    rts_command_line = command_line_variants['rts'].get(target, [])
    if rts_command_line:
      test.raw_cmd = rts_command_line
      self._overwritten_tests.add(test.name)

  def append_test_step_text(
    self,
    test: Test,
    messages: list[str],
  ) -> None:
    """Appends RTS info message if the test's command was overwritten by RTS."""
    if not self._rts_disabled and test.name in self._overwritten_tests:
      messages.append(
        'Ran tests selected by Regression Test Selection (RTS).\n'
      )
