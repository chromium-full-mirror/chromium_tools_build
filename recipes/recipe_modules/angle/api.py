# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import recipe_api

from PB.recipe_engine import result as result_pb2
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb


class ANGLEApi(recipe_api.RecipeApi):
  def __init__(self, properties, **kwargs):
    super().__init__(**kwargs)

  def _get_builder_id_and_config(self):
    trybots = None
    builders = None

    if self._test_data.enabled:
      if 'builders' in self._test_data:
        builders = self._test_data['builders']
      if 'trybots' in self._test_data:
        trybots = self._test_data['trybots']

    builder_id, builder_config = (
      self.m.chromium_tests_builder_config.lookup_builder(
        builder_db=builders, try_db=trybots
      )
    )
    return builder_id, builder_config

  def ci_steps(self):
    builder_id, builder_config = self._get_builder_id_and_config()
    test_result, update_result = self.m.chromium_tests.main_waterfall_steps(
      builder_id, builder_config
    )

    # Skip trace tests if the rest of the build already failed.
    if not _raw_result_was_successful(test_result):
      # This is not run unconditionally in order to not interfere with the
      # hacky logic of _assert_no_regular_tests_run().
      self._kill_mspdbsrv()
      return test_result, update_result
    self._maybe_run_trace_tests()
    self._kill_mspdbsrv()
    return test_result, update_result

  def try_steps(self):
    self.m.tryserver.require_is_tryserver()

    builder_id, builder_config = self._get_builder_id_and_config()

    if self.m.properties.get('no_extra_traces'):
      orig_configure_build = self.m.chromium_tests.configure_build

      def patched_configure_build(builder_config):
        orig_configure_build(builder_config)
        self.m.gclient.apply_config('angle_no_extra_traces')

      self.m.chromium_tests.configure_build = patched_configure_build

    try:
      chromium_results = self.m.chromium_tests.trybot_steps(
        builder_id, builder_config, files_relative_to='angle/'
      )
    finally:
      if self.m.properties.get('no_extra_traces'):
        self.m.chromium_tests.configure_build = orig_configure_build

    # Skip trace tests if the rest of the build already failed.
    if not _raw_result_was_successful(chromium_results):
      # This is not run unconditionally in order to not interfere with the
      # hacky logic of _assert_no_regular_tests_run().
      self._kill_mspdbsrv()
      return chromium_results
    self._maybe_run_trace_tests()
    self._kill_mspdbsrv()
    return chromium_results

  def _kill_mspdbsrv(self):
    """Attempts to kill mspdbsrv.exe.

    This is only expected to happen when building with MSVC, but is safe to do
    on all Windows builds. If this is not done, the process can prevent
    Swarming from moving the builder directory, which in turn prevents a cache
    from being created.
    """
    if not self.m.platform.is_win:
      return
    self.m.step(
      'Kill mspdbsrv.exe (if running)',
      ['taskkill.exe', '/f', '/t', '/im', 'mspdbsrv.exe'],
      raise_on_failure=False,
      ok_ret='any',
    )

  def _maybe_run_trace_tests(self):
    """Runs all trace tests if specified to via the recipe module properties.

    These tests are run directly in the recipe instead of as normal tests
    because they require compiling targets during execution, which is not
    possible with isolated tests.
    """
    if not self.m.properties.get('run_trace_tests'):
      return
    self._assert_no_regular_tests_run()
    source_dir = self.m.chromium_checkout.source_dir
    with self.m.context(cwd=source_dir):
      for gtest_filter, step_name in [
        ('*/ES2_Vulkan_SwiftShader', 'GLES 2.0 trace tests'),
        ('*/ES3_Vulkan_SwiftShader', 'GLES 3.0 trace tests'),
        ('*/ES3_1_Vulkan_SwiftShader', 'GLES 3.1 trace tests'),
        ('*/ES1_Vulkan_SwiftShader', 'GLES 1.0 trace tests'),
      ]:
        self._run_trace_tests_for(gtest_filter, step_name, source_dir)

  def _assert_no_regular_tests_run(self):
    """Asserts that the builder is not configured to run regular tests.

    While regular and trace tests are capable of running together, doing so is
    indicative of the builder being misconfigured.
    """
    # Information about which tests were run (if any) is not accessible outside
    # of main_waterfall_steps()/trybot_steps(). So, look at the last run step
    # and use that as a proxy for tests being run or child build being
    # triggered.
    if self.m.step.active_result.name.startswith(
      ('record test suite statuses', 'Test statistics', 'trigger')
    ):
      raise self.m.step.StepFailure(
        'Regular tests and trace tests are mutually exclusive.'
      )

  def _run_trace_tests_for(self, gtest_filter, step_name, source_dir):
    """Runs ANGLE trace tests for a given filter.

    Args:
      gtest_filter: A string to use as a gtest filter when running the tests.
      step_name: The name to use for the build step when running the tests.
      source_dir: The path to the ANGLE source directory/root.
    """
    cmd = [
      'vpython3',
      'src/tests/capture_replay_tests.py',
      '--log',
      'debug',
      '--gtest_filter=%s' % gtest_filter,
      '--out-dir=%s' % source_dir.joinpath('out_CaptureReplayTest'),
      '--use-remoteexec',
      '--use-siso',
    ]
    if self.m.platform.is_linux:
      cmd.append('--xvfb')
    self.m.step(step_name, cmd)


def _raw_result_was_successful(raw_result):
  """Checks if a result_pb2.RawResult was successful.

  Args:
    raw_result: The result_pb2.RawResult to check.

  Returns:
    True if |raw_result| indicates success, otherwise False.
  """
  return raw_result is None or raw_result.status == common_pb.SUCCESS
