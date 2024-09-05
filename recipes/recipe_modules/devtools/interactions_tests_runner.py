# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from .test_runner_base import ExonerableTests, Results
from contextlib import contextmanager
import re


class InteractionsTests(ExonerableTests):

  @property
  def test_type_tag(self):
    return 'interactions_tests'

  def __init__(self,
               api,
               source_dir,
               trigger,
               builder_config,
               coverage,
               step_name,
               bucket='devtools-frontend-screenshots'):
    self.bucket = bucket
    super().__init__(api, source_dir, trigger, builder_config, coverage,
                     step_name)

  def collect(self):
    if self.api.tryserver.is_tryserver:
      with self.api.step.nest(f'{self.step_name} shards results') \
        as presentation:
        self.api.chromium_swarming.collect_task(self.tasks[0])
      if presentation.status != self.api.step.SUCCESS:
        if presentation.status == self.api.step.EXCEPTION:
          return Results(infra_failures=[f'Infra Failure in {self.step_name}'])
        return Results(task_failures=[f'Failure in {self.step_name}'])
      return Results()

    return super().collect()

  def legacy_construct_commands(self):
    command = [
        self.api.path.join('third_party', 'node', 'node.py'),
        "--output",
        self.api.path.join('scripts', 'test', 'run_test_suite.js'),
        "--test-suite-path=gen/test/interactions",
        "--test-suite-source-dir=test/interactions",
        "--test-server-type='component-docs'",
        "--target=" + self.builder_config,
        '--swarming-output-file',
        '${ISOLATED_OUTDIR}',
    ] + self.extra_args
    if self.coverage:
      command.append('--coverage')
    return [command]

  def construct_commands(self):
    return [self.run_tests_command('test/interactions')]

  def construct_env(self):
    env = {
        "FORCE_UPDATE_ALL_GOLDENS":
            'True',
        "THROW_AFTER_GOLDENS_UPDATE":
            'True',
        "HTML_OUTPUT_FILE":
            self.api.path.join('${ISOLATED_OUTDIR}',
                               'interactions_failure_screenshots.html'),
    }
    env.update(self.env)
    return env

  @contextmanager
  def _collection_context(self):
    with self.api.devtools.collect_screenshots_on_trybot(self.bucket):
      yield

  def _post_collect(self):
    self.copy_golden_snapshots()

  def copy_golden_snapshots(self):
    # TODO:(liviurau) Remove this after fast build gets fixed for the new runner
    goldens_collector_builders = [
        "devtools_frontend_linux_rel", "devtools_frontend_mac_rel",
        "devtools_frontend_win_rel"
    ]
    if self.api.buildbucket.builder_name not in goldens_collector_builders:
      return
    shard_output_dir = self.tasks[0].get_task_shard_output_dirs()[0]
    golden_snapshots_dir = self.output_dir / shard_output_dir / 'goldens'
    self.api.file.rmtree(
        'remove previous goldens',
        self.api.path.join(self.source_dir, 'test', 'interactions', 'goldens'))
    self.api.file.copytree(
        'copy golden snapshots', golden_snapshots_dir,
        self.api.path.join(self.source_dir, 'test', 'interactions', 'goldens'))

  def test_name_to_grep_string(self, name):
    name = re.sub(r'^interactions/.*: ', '', name)
    return super().test_name_to_grep_string(name)
