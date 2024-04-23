# Copyright 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import recipe_api

from PB.recipe_engine import result as result_pb2
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb

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

  def _get_angle_commit_pos(self):
    stepdata = self.m.step(
        'get commit position', [
            'python3',
            self.m.path.join(self.m.path.checkout_dir, 'src', 'commit_id.py'),
            'position',
        ],
        stdout=self.m.raw_io.output_text(add_output_log=True))
    commit_pos = int(stepdata.stdout.strip())
    stepdata.presentation.step_text = '<br/>commit position: %d' % commit_pos
    return commit_pos

  def _checkout(self):
    # Checkout angle and its dependencies (specified in DEPS) using gclient.
    solution_path = self.m.path.cache_dir / 'builder'
    self.m.file.ensure_directory('init cache if not exists', solution_path)
    with self.m.context(cwd=solution_path):
      if self.m.reclient.instance:
        self.m.reclient.use_download_remoteexec_cfg_hook(
            self.m.gclient.c.solutions[0])
      update_step = self.m.bot_update.ensure_checkout()

    assert update_step.json.output['did_run']

    # Add an ANGLE commit position to the build properties.
    build_properties = update_step.json.output['properties']
    build_properties['angle_commit_pos'] = self._get_angle_commit_pos()

    self.m.chromium.set_build_properties(update_step.json.output['properties'])
    self.m.chromium.runhooks()
    return update_step

  def _compile(self, isolated_targets):
    raw_result = self.m.chromium_tests.run_mb_and_compile(
        self._builder_id, ['all'], isolated_targets, '')
    return raw_result

  def _run_trace_tests(self, checkout, gtest_filter, step_name):
    cmd = [
        'vpython3',
        'src/tests/capture_replay_tests.py',
        '--log',
        'debug',
        '--gtest_filter=%s' % gtest_filter,
        '--out-dir=%s' % checkout.join('out', 'CaptureReplayTest'),
        '--use-reclient',
    ]
    if self.m.platform.is_linux:
      cmd += ['--xvfb']
    self.m.step(step_name, cmd)

  def _trace_tests(self):
    checkout = self.m.path.checkout_dir
    with self.m.context(cwd=checkout):
      self._run_trace_tests(checkout, '*/ES2_Vulkan_SwiftShader',
                            'GLES 2.0 trace tests')
      self._run_trace_tests(checkout, '*/ES3_Vulkan_SwiftShader',
                            'GLES 3.0 trace tests')
      self._run_trace_tests(checkout, '*/ES3_1_Vulkan_SwiftShader',
                            'GLES 3.1 trace tests')
      self._run_trace_tests(checkout, '*/ES1_Vulkan_SwiftShader',
                            'GLES 1.0 trace tests')

  def steps(self):
    toolchain = self.m.properties.get('toolchain', 'clang')
    platform = self.m.properties.get('platform', self.m.platform.name)
    test_mode = self.m.properties.get('test_mode')
    self._apply_builder_config(platform, toolchain, test_mode)
    update_step = self._checkout()
    if test_mode == 'checkout_only':
      pass
    elif test_mode == 'trace_tests':
      self._trace_tests()
    elif test_mode == 'compile_only':
      raw_result = self._compile(None)
      if raw_result.status != common_pb.SUCCESS:
        return raw_result
    else:
      assert (test_mode == 'compile_and_test')
      script_dir = self.m.path.join(self.m.path.checkout_dir, 'testing',
                                    'merge_scripts')
      self.m.chromium_swarming.configure_swarming(
          'angle',
          self.m.tryserver.is_tryserver,
          path_to_merge_scripts=script_dir)
      targets_config = self.m.chromium_tests.create_targets_config(
          self._builder_config, update_step.presentation.properties,
          self.m.path.checkout_dir)

      if self.m.tryserver.is_tryserver:
        affected_files = self.m.chromium_checkout.get_files_affected_by_patch(
            relative_to='angle/',
            cwd=self.m.path.checkout_dir,
            report_via_property=True)
        test_targets, compile_targets = (
            self.m.chromium_tests.determine_compilation_targets(
                self._builder_id, self._builder_config,
                self.m.path.checkout_dir, affected_files, targets_config))

        compile_targets = sorted(list(set(test_targets)))
        tests = self.m.chromium_tests.tests_in_compile_targets(
            compile_targets, targets_config.all_tests)
      else:
        tests = targets_config.all_tests
        test_targets = [t.isolate_target for t in tests if t.uses_isolate]
        compile_targets = sorted(list(set(test_targets)))

      compile_step = self._compile(compile_targets)
      if compile_step.status != common_pb.SUCCESS:
        return compile_step

      self.m.isolate.isolate_tests(
          self.m.chromium.output_dir,
          targets=compile_targets,
          verbose=True,
      )
      self.m.chromium_tests.set_swarming_test_execution_info(
          tests, self.m.chromium_tests.find_swarming_command_lines(""),
          self.m.path.relpath(self.m.chromium.output_dir,
                              self.m.path.checkout_dir))
      # ANGLE marks entire failing shards as invalid. We retry them here.
      invalid_test_suites, failing_test_suites = (
          self.m.test_utils.run_tests(tests, "", retry_invalid_shards=True))

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
