# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from .test_runner_base import ExonerableTests, use_legacy_test_runner
from functools import cached_property
import re


class E2ETests(ExonerableTests):

  @property
  def test_type_tag(self):
    prefix = 'shuffled_' if self.api.devtools.is_shuffled_run() else ''
    return prefix + 'e2e_tests'

  def __init__(self, api, source_dir, cas_digest, builder_config, coverage,
               step_name, divider):
    super().__init__(api, source_dir, cas_digest, builder_config, coverage,
                     step_name)
    self.divider = divider

  def skip(self):
    return self.api.devtools.is_debug(self.builder_config)

  def legacy_construct_commands(self):
    return [cmd + self.extra_args for cmd in self.divider.commands]

  def construct_commands(self):
    is_exoneration_attempt = self.extra_args
    if is_exoneration_attempt:
      return [self.run_tests_command('test/e2e')]
    return [
        self.run_tests_command(*test_list)
        for test_list in self.divider.commands
    ]

  def test_name_to_grep_string(self, name):
    name = re.sub(r'^e2e/.*: ', '', name)
    return super().test_name_to_grep_string(name)

  def trigger_exoneration(self, test_names):
    self.divider = E2ETestDivider(
        self.api, self.source_dir, self.builder_config, shard_count=1)
    return super().trigger_exoneration(test_names)


class RepeatE2EShuffledTests(E2ETests):

  def trigger_exoneration(self, test_names):
    pass

  def process_exoneration_results(self, test_names):
    pass

  def skip(self):
    return super().skip() or (not self.api.devtools.is_shuffled_run())

  @property
  def test_type_tag(self):
    return 'shuffled_repeat_e2e_tests'


class E2ETestDivider:

  def __init__(self, api, source_dir, builder_config, shard_count=4):
    self.api = api
    self.source_dir = source_dir
    self.builder_config = builder_config
    self.shard_count = shard_count

  def legacy_commands(self):
    return self.api.devtools.divided_e2e_commands(
        self.source_dir,
        builder_config=self.builder_config,
        shards=self.shard_count,
    )

  # TODO(liviurau) Rename function after legacy branch is no longer supported
  # It returns a list of lists of test paths
  @cached_property
  def commands(self):
    if use_legacy_test_runner(self.api):
      return self.legacy_commands()
    gen_root = self.source_dir / 'out' / self.builder_config / 'gen'
    test_relative_path = 'test/e2e'
    test_root = gen_root / test_relative_path
    test_list_file_path = test_root / 'tests.txt'
    contents = self.api.file.read_text('Read test list', test_list_file_path)
    all_tests = contents.splitlines()
    all_test_paths = [
        self.api.path.join(test_relative_path, t) for t in all_tests
    ]
    if self.api.devtools.is_shuffled_run():
      # TODO(liviurau) make this pseudo-random with a seed based e.g. on
      # the revision of the commit
      self.api.random.shuffle(all_test_paths)
    return divide_list(all_test_paths, self.shard_count)


def divide_list(lst, split_count):
  chunk_size, remainder_size = divmod(len(lst), split_count)
  chunk_start = 0
  result = []
  for i in range(split_count):
    current_size = chunk_size + (1 if i < remainder_size else 0)
    result.append(lst[chunk_start:chunk_start + current_size])
    chunk_start += current_size
  return result
