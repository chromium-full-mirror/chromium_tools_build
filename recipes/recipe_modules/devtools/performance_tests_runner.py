# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from .test_runner_base import DevToolsTests


class PerformanceTests(DevToolsTests):

  def __init__(self,
               api,
               trigger,
               builder_config,
               step_name,
               target_os='unknown'):
    super().__init__(api, trigger, builder_config, False, step_name)
    self.target_os = target_os.lower()

  @property
  def test_type_tag(self):
    return 'perf_tests'

  def skip(self):
    if self.api.properties.get("perf_benchmarks", False):
      return False

    is_ci = not self.api.tryserver.is_tryserver
    is_debug_build = self.api.devtools.is_debug(self.builder_config)
    is_linux = self.target_os.startswith('ubuntu')
    return is_ci or is_debug_build or (not is_linux)

  def commands(self):
    return [self.run_tests_command(['test/perf'])]


  def _post_collect(self):
    if self.api.properties.get('perf_benchmarks', False):
      self.copy_perf_benchmarks_data()

  def copy_perf_benchmarks_data(self):
    shard_output_dir = self.tasks[0].get_task_shard_output_dirs()[0]
    perf_data_dir = (self.output_dir / shard_output_dir / 'perf-data')
    self.api.file.rmtree(
        'remove perf data file if it exists',
        self.api.path.join(self.api.devtools.source_dir, 'perf-data'))
    self.api.file.copytree(
        'copy perf tests data', perf_data_dir,
        self.api.path.join(self.api.devtools.source_dir, 'perf-data'))
