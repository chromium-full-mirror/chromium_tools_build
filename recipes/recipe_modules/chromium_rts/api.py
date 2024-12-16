# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from google.protobuf import timestamp_pb2
from recipe_engine import recipe_api

from PB.go.chromium.org.luci.buildbucket.proto \
  import builds_service as builds_service_pb2
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb
from RECIPE_MODULES.build.chromium_tests_builder_config import try_spec
from RECIPE_MODULES.build.code_coverage import constants

_DISABLE_RTS_FOOTER = 'Disable-Rts'


class ChromiumRtsApi(recipe_api.RecipeApi):
  """A module for interacting with rts."""

  def __init__(self, *args, **kwargs):
    super().__init__(*args, **kwargs)

    # A string indicating which RTS model to use regression test selection.
    self._rts_model = None

  @property
  def rts_model(self) -> str:
    return self._rts_model

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

      compatible_run_modes = ('chromium_rts.dry_run_rts'
                              in self.m.buildbucket.build.input.experiments)
      if compatible_run_modes:
        self.m.cv.allow_reuse_for(self.m.cv.DRY_RUN)
    return tests

  def init_rts_options(self, builder_config):
    """Initialize the rts module settings for the builder config."""
    run_mode = None
    props = self.m.properties.get('$recipe_engine/cv', None)
    if props:
      run_mode = props.get('run_mode', props.get('runMode'))

    dry_run = False
    if run_mode == self.m.cv.DRY_RUN:
      dry_run = ('chromium_rts.dry_run_rts'
                 in self.m.buildbucket.build.input.experiments)
      step_result = self.m.step('rts dry run', [])

    self._rts_model = None
    use_rts = (
        dry_run or builder_config.regression_test_selection == try_spec.ALWAYS)

    if use_rts and not self._is_rts_footer_disabled():
      self._rts_model = 'smart-test-selection'
      step_result = self.m.step('rts options', [])
      if dry_run:
        step_result.presentation.step_text = 'RTS was enabled in a dry run'

      step_result.presentation.properties['rts_model'] = self._rts_model

  def mb_args(self):
    """Returns the mb args to generate the RTS filter files"""
    mb_args = []
    if self._rts_model:
      step_result = self.m.step('adding rts to mb args', [])
      mb_args += ['--rts-model', self._rts_model]
      step_result.presentation.step_text = self._rts_model

    return mb_args
