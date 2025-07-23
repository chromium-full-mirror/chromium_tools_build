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
_SMART_TEST_SELECTION_MODEL = 'smart-test-selection'
_API_KEY_HOLDER_PROJECT = 'findit-for-me'
_API_KEY_SECRET = 'decisiongraph_api_key'

_DGI_SCRIPT_PATH = 'tools/test_selection/decisiongraph_invoker.py'

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
    """Generates .filter files for the given tests.
    """
    if self._rts_model == _SMART_TEST_SELECTION_MODEL:
      target_set = {test.canonical_name for test in tests if test.is_rts}
      if not target_set:
        self.m.step.empty('No candidate test targets for smart test selection')
        return
      sts_input_json_path = self._create_sts_input_json()
      filter_file_dir = (build_dir / 'gen' / 'rts')
      dgi_script_path = (src_dir / _DGI_SCRIPT_PATH)
      cmd = ['vpython3', self.resource(dgi_script_path)]
      cmd.append('--test-targets')
      cmd.extend(target_set)
      cmd.extend([
          '--sts-config-file', sts_input_json_path, '--test-selection-phase',
          'FETCH', '--filter-file-dir', filter_file_dir
      ])
      self.m.step('Fetch test selection results', cmd)

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
      self._rts_model = _SMART_TEST_SELECTION_MODEL
      step_result = self.m.step('rts options', [])
      step_result.presentation.step_text = 'RTS was enabled'
      step_result.presentation.properties['rts_model'] = self._rts_model

  def mb_args(self):
    """Returns the mb args to generate the RTS filter files"""
    mb_args = []
    if self._rts_model:
      rts_input_json = self._create_sts_input_json()
      step_result = self.m.step('adding rts to mb args', [])
      mb_args += ['--rts-model', self._rts_model]
      mb_args += ['--sts-config-file', rts_input_json, '--verbose']
      step_result.presentation.step_text = self._rts_model
    return mb_args

  def _create_sts_input_json(self):
    gerrit_change = self.m.buildbucket.build.input.gerrit_changes[0]
    with self.m.secret_manager.fetch(
        project=_API_KEY_HOLDER_PROJECT,
        secret=_API_KEY_SECRET,
        step_name='fetch decisiongraph api key') as api_key:
      rts_dict = {
          'build_id': str(self.test_executor_build_id),
          'change': gerrit_change.change,
          'patchset': gerrit_change.patchset,
          'builder': self.m.buildbucket.build.builder.builder,
          'api_key': api_key
      }
      rts_input_json_path = self.m.path.mkstemp('rts_input.json')
      # Write to json file
      self.m.file.write_json(
          name='create test selection input json',
          dest=rts_input_json_path,
          data=rts_dict)
      return rts_input_json_path
