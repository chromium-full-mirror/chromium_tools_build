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

  def _should_generate_filters(self) -> bool:
    """Whether RTS filter file generation and evaluation should run."""
    return (TEST_RTS_MODEL_EXPERIMENT
            in self.m.buildbucket.build.input.experiments)

  def filter_file_dir(self, build_dir: Path) -> Path:
    """Returns the path to the directory containing the RTS filter files."""
    return build_dir / 'gen' / 'rts'

  def generate_filter_files(
      self,
      src_dir: Path,
      build_dir: Path,
  ) -> None:
    """Generates RTS filter files if RTS is enabled."""

    if not self._should_generate_filters():
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
        '-target-change-recall',
        str(_DEFAULT_TARGET_CHANGE_RECALL),
        '-change-ref',
        'HEAD~',
    ]
    self.m.step('generate chromium-rts filter files', cmd)

  def banned_suites(self) -> set[str]:
    """Loads and resolves the banned suites for the current builder."""
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
    return banned

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
    if self._should_generate_filters():
      banned_suites = self.banned_suites()
      evaluation.evaluate_rts(self, build_dir, tests, banned_suites)
