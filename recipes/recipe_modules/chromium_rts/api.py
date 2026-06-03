# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import recipe_api
from RECIPE_MODULES.build.chromium_tests_builder_config import try_spec

_DISABLE_RTS_FOOTER = 'Disable-Rts'

class ChromiumRtsApi(recipe_api.RecipeApi):
  """A module for interacting with rts."""

  def __init__(self, *args, **kwargs):
    super().__init__(*args, **kwargs)
    # A string indicating which RTS model to use regression test selection.
    self._rts_model = None

  @property
  def test_executor_build_id(self):
    # If the current build is a compilator build, the tests will be executed
    # by orchestrator
    if ('orchestrator' in self.m.properties and
        self.m.buildbucket.build.ancestor_ids):
      parent_id = self.m.buildbucket.build.ancestor_ids[-1]
      return parent_id
    return self.m.buildbucket.build.id

  @property
  def rts_model(self) -> str:
    return self._rts_model

  @property
  def enabled(self) -> bool:
    return bool(self.rts_model)

  @rts_model.setter
  def rts_model(self, value: str) -> None:
    self._rts_model = value

  def _is_rts_footer_disabled(self):
    disabled = False
    if self.m.tryserver.is_tryserver:
      footer_vals = self.m.tryserver.get_footer(_DISABLE_RTS_FOOTER)
      if footer_vals:
        disabled = footer_vals[-1].lower() == 'true'
    return disabled

  def generate_filter_files(self, src_dir, build_dir, tests):
    """Generates .filter files for the given tests."""

  def setup_tests(self, tests):
    """Sets the given tests up to be run with RTS

    The tests will be setup to run RTS when both the test and the current
    run mode support RTS. This also marks the reuseability of the current build
    for CV to prevent tests from getting skipped before submission

    Args:
      tests (Test[]): The test objects to have RTS conditionally enabled
    """
    # If we only need to run the tests that were skipped in the last build
    if self._rts_model:
      for test in tests:
        if test.supports_rts:
          test.is_rts = True

    if any(test.is_rts for test in tests):
      # RTS-enabled builds can't be reused for non-RTS because they are slightly
      # less safe than normal builds
      log_step = self.m.step.empty('RTS was used')
      log_step.presentation.properties['rts_was_used'] = True

      compatible_run_modes = ('chromium_rts.rts'
                              in self.m.buildbucket.build.input.experiments)
      if compatible_run_modes:
        self.m.cv.allow_reuse_for(self.m.cv.DRY_RUN)
    return tests

  def init_rts_options(self, builder_config):
    """Initialize the rts module settings for the builder config."""
    self._rts_model = None
    use_rts = ('chromium_rts.rts' in self.m.buildbucket.build.input.experiments
               or builder_config.regression_test_selection == try_spec.ALWAYS)

    if use_rts and not self._is_rts_footer_disabled():
      self._rts_model = 'chromium-rts'
      step_result = self.m.step('rts options', [])
      step_result.presentation.step_text = 'RTS was enabled'
      step_result.presentation.properties['rts_model'] = self._rts_model

  def mb_args(self):
    """Returns the mb args to generate the RTS filter files"""
    mb_args = []
    if self._rts_model:
      step_result = self.m.step('adding rts to mb args', [])
      mb_args += ['--rts-model', self._rts_model]
      step_result.presentation.step_text = self._rts_model
    return mb_args
