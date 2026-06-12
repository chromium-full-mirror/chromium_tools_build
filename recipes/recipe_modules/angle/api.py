# Copyright 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import recipe_api
from recipe_engine.config_types import Path

from PB.recipe_engine import result as result_pb2
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb

from RECIPE_MODULES.depot_tools import bot_update

from . import builders as builders_module
from . import trybots as trybots_module


class ANGLEApi(recipe_api.RecipeApi):

  def __init__(self, properties, **kwargs):
    super().__init__(**kwargs)
    self._trybots = None
    self._builders = None
    self._builder_id = None
    self._builder_config = None

  def _apply_builder_config(self, platform, toolchain, test_mode):
    self.set_config('angle')

    self._trybots = trybots_module.TRYBOTS
    self._builders = builders_module.BUILDERS

    if self._test_data.enabled:
      if 'builders' in self._test_data:
        self._builders = self._test_data['builders']
      if 'trybots' in self._test_data:
        self._trybots = self._test_data['trybots']

    # contains build/test settings for the bot
    self._builder_id, self._builder_config = (
        self.m.chromium_tests_builder_config.lookup_builder(
            builder_db=self._builders, try_db=self._trybots, use_try_db=True))
    self.m.chromium_tests.report_builders(self._builder_config)
    self.m.chromium_tests.configure_build(self._builder_config)

  def _get_angle_commit_pos(self, source_dir: Path):
    stepdata = self.m.step(
        'get commit position', [
            'python3',
            source_dir / 'src/commit_id.py',
            'position',
        ],
        stdout=self.m.raw_io.output_text(add_output_log=True))
    commit_pos = int(stepdata.stdout.strip())
    stepdata.presentation.step_text = '<br/>commit position: %d' % commit_pos
    return commit_pos

  def _checkout(self) -> tuple[bot_update.Result, Path]:
    # Checkout angle and its dependencies (specified in DEPS) using gclient.
    solution_path = self.m.path.cache_dir / 'builder'
    self.m.file.ensure_directory('init cache if not exists', solution_path)
    with self.m.context(cwd=solution_path):
      if self.m.siso.enabled:
        self.m.siso.enable_download_remoteexec_cfg_hook()
      update_result = self.m.bot_update.ensure_checkout()
      # Many methods on the chromium_tests API require the chromium_checkout
      # paths set
      self.m.chromium_checkout.set_paths_from_update_result(update_result)

    source_dir = update_result.source_root.path
    build_dir = self.m.chromium.default_build_dir(source_dir)

    # Add an ANGLE commit position to the build properties.
    build_properties = update_result.properties
    build_properties['angle_commit_pos'] = self._get_angle_commit_pos(
        source_dir)

    self.m.chromium.set_build_properties(update_result.properties)
    self.m.chromium.runhooks(source_dir, build_dir)
    return update_result, build_dir

  def _compile(self, source_dir: Path, build_dir: Path, isolated_targets):
    raw_result = self.m.chromium_tests.run_mb_and_compile(
        source_dir, build_dir, self._builder_id, ['all'], isolated_targets, '')
    return raw_result

  def _run_trace_tests(self, checkout, gtest_filter, step_name):
    cmd = [
        'vpython3',
        'src/tests/capture_replay_tests.py',
        '--log',
        'debug',
        '--gtest_filter=%s' % gtest_filter,
        '--out-dir=%s' % checkout.joinpath('out_CaptureReplayTest'),
        '--use-remoteexec',
    ]
    if self.m.siso.enabled:
      cmd += ['--use-siso']
    if self.m.platform.is_linux:
      cmd += ['--xvfb']
    self.m.step(step_name, cmd)

  def _trace_tests(self, source_dir: Path):
    with self.m.context(cwd=source_dir):
      self._run_trace_tests(source_dir, '*/ES2_Vulkan_SwiftShader',
                            'GLES 2.0 trace tests')
      self._run_trace_tests(source_dir, '*/ES3_Vulkan_SwiftShader',
                            'GLES 3.0 trace tests')
      self._run_trace_tests(source_dir, '*/ES3_1_Vulkan_SwiftShader',
                            'GLES 3.1 trace tests')
      self._run_trace_tests(source_dir, '*/ES1_Vulkan_SwiftShader',
                            'GLES 1.0 trace tests')

  def steps(self):
    toolchain = self.m.properties.get('toolchain', 'clang')
    platform = self.m.properties.get('platform', self.m.platform.name)
    test_mode = self.m.properties.get('test_mode')
    self._apply_builder_config(platform, toolchain, test_mode)
    update_result, build_dir = self._checkout()
    checkout_dir = update_result.checkout_dir
    source_dir = update_result.source_root.path
    if test_mode == 'checkout_only':
      pass
    elif test_mode == 'trace_tests':
      self._trace_tests(source_dir)
    elif test_mode == 'compile_only':
      raw_result = self._compile(source_dir, build_dir, None)
      if raw_result.status != common_pb.SUCCESS:
        return raw_result
    else:
      assert (test_mode == 'compile_and_test')
      script_dir = source_dir / 'testing/merge_scripts'
      self.m.chromium_swarming.configure_swarming(
          'angle',
          self.m.tryserver.is_tryserver,
          path_to_merge_scripts=script_dir)
      targets_config = self.m.chromium_tests.create_targets_config(
          self._builder_config,
          update_result.properties,
          source_dir,
          build_dir,
          checkout_dir=checkout_dir)

      if self.m.tryserver.is_tryserver:  # pragma: no cover
        affected_files = self.m.chromium_checkout.get_files_affected_by_patch(
            relative_to='angle/', cwd=source_dir, report_via_property=True)
        test_targets, compile_targets = (
            self.m.chromium_tests.determine_compilation_targets(
                self._builder_id,
                self._builder_config,
                checkout_dir,
                source_dir,
                build_dir,
                affected_files,
                targets_config,
            ))

        compile_targets = sorted(list(set(test_targets)))
        tests = self.m.chromium_tests.tests_in_compile_targets(
            compile_targets, targets_config.all_tests)
      else:
        tests = targets_config.all_tests
        test_targets = [t.isolate_target for t in tests if t.uses_isolate]
        compile_targets = sorted(list(set(test_targets)))

      compile_step = self._compile(source_dir, build_dir, compile_targets)
      if compile_step.status != common_pb.SUCCESS:
        return compile_step

      self.m.isolate.isolate_tests(
          build_dir,
          targets=compile_targets,
          verbose=True,
      )
      self.m.chromium_tests.set_swarming_test_execution_info(
          source_dir,
          build_dir,
          tests,
          self.m.chromium_tests.find_swarming_command_lines("", build_dir),
          self.m.path.relpath(build_dir, source_dir),
      )
      # ANGLE marks entire failing shards as invalid. We retry them here.
      invalid_test_suites, failing_test_suites = (
          self.m.test_utils.run_tests(
              checkout_dir,
              source_dir,
              build_dir,
              tests,
              "",
              retry_invalid_shards=True))

      self.m.chromium_swarming.report_stats()

      if invalid_test_suites:
        return result_pb2.RawResult(
            summary_markdown=self.m.chromium_tests
            .format_unrecoverable_failures(invalid_test_suites, ''),
            status=common_pb.FAILURE)

      if failing_test_suites:
        return result_pb2.RawResult(
            summary_markdown=self.m.chromium_tests
            .format_unrecoverable_failures(failing_test_suites, ''),
            status=common_pb.FAILURE)
