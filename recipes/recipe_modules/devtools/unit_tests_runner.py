# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from .test_runner_base import ExonerableTests, use_legacy_test_runner


class UnitTests(ExonerableTests):

  @property
  def test_type_tag(self):
    return 'unit_tests'

  def construct_commands(self):
    return [self.run_tests_command('front_end')]

  def legacy_construct_commands(self):
    command = [
        self.api.path.join('scripts', 'test', 'run_unittests.py'),
        '--target=' + self.builder_config,
        '--swarming-output-file',
        '${ISOLATED_OUTDIR}',
    ]
    if self.coverage:
      command.append('--coverage')
    return [command]

  def _post_collect(self):
    if self.coverage:
      self.copy_coverage_data()

  def copy_coverage_data(self):
    shard_output_dir = self.tasks[0].get_task_shard_output_dirs()[0]
    coverage_data_dir = self.output_dir / shard_output_dir / 'karma-coverage'
    self.api.file.rmtree(
        'remove coverage files if they exist',
        self.api.path.join(self.api.path.checkout_dir, 'karma-coverage'))
    self.api.file.copytree(
        'copy unit tests coverage data', coverage_data_dir,
        self.api.path.join(self.api.path.checkout_dir, 'karma-coverage'))

  def prepare_filtered_rerun(self, test_names):
    if use_legacy_test_runner(self.api):
      self.env = {
          'MOCHA_FGREP': self.test_names_to_grep_string(test_names),
      }
    else:
      super().prepare_filtered_rerun(test_names)
