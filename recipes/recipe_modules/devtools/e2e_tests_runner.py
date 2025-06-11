# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from .commons import Results
from .test_runner_base import ExonerableTests
from functools import cached_property
import re

FLAKE_DETECTION_OPTION = '--repeat=10'

class E2ETests(ExonerableTests):

  @property
  def test_type_tag(self):
    prefix = 'shuffled_' if self.divider.shuffled else ''
    return prefix + 'e2e_tests'

  def __init__(self, api, trigger, builder_config, step_name, divider):
    super().__init__(api, trigger, builder_config, False, step_name)
    self.divider = divider
    if self.divider.shuffled:
      self.extra_args = ['--bail']

  def skip(self):
    return self.api.devtools.is_debug(self.builder_config)

  def commands(self):
    is_exoneration_attempt = self.extra_args and not self.divider.shuffled
    if is_exoneration_attempt:
      return [self.run_tests_command('test/e2e')]
    return [
        self.run_tests_command(*test_list)
        for test_list in self.divider.commands('e2e')
    ]

  @property
  def grep_filter_pattern(self):
    """This pattern is used to remove the prefix from the test name. Used by
    exoneration logic.
    """
    return r'^e2e/[^:]*: '

  def test_name_to_grep_string(self, name):
    name = re.sub(self.grep_filter_pattern, '', name)
    return super().test_name_to_grep_string(name)

  def trigger_exoneration(self, test_names):
    self.divider = E2ETestDivider(self.api, self.builder_config, shard_count=1)
    return super().trigger_exoneration(test_names)


class E2ENonHostedTests(E2ETests):

  def __init__(self, api, trigger, builder_config, step_name, divider):
    super().__init__(api, trigger, builder_config, step_name, divider)
    self.skip_deflaking_result = None
    # TODO(liviurau): Needed only for where this cannot be passed in grep
    # pattern. To be removed once we have a ResultDB based solution.
    self.owned_new_tests = []

  def commands(self):
    is_flake_detection_attempt = FLAKE_DETECTION_OPTION in self.extra_args
    if is_flake_detection_attempt:
      return [self.run_tests_command(*self.owned_new_tests)]
    return [
        self.run_tests_command(*test_list)
        for test_list in self.divider.commands('e2e_non_hosted')
    ]

  @property
  def grep_filter_pattern(self):
    return r'^e2e_non_hosted/[^:]*: '

  @property
  def test_type_tag(self):
    return 'e2e_non_hosted_tests'

  def trigger_flake_detection(self, test_names):
    self.owned_new_tests = [
        test for test in test_names if test.startswith('test/e2e_non_hosted')
    ]
    if not self.owned_new_tests:
      self.skip_deflaking_result = Results()
      return
    # TODO(liviurau): Should also take care of  large swiping changes that
    # touche a lot of tests, e.g. some refactoring. Add a maximum limit
    # TODO(liviurau): The divider needs rework.  E.g. the rerun count could be
    # used to divide. Instead of running 10 times on one shard you could run
    # the same 5 times on 2 shares, etc.
    self.divider = E2ETestDivider(self.api, self.builder_config, shard_count=1)
    self.step_name += ' (flake detection)'
    # TODO(liviurau): There must be a better way to prepare a limited run.
    # Maybe pass the command function to the trigger function and have
    # discrete commands for normal and limited runs.
    self.extra_args = [FLAKE_DETECTION_OPTION]
    self.trigger('flake detection')

  def process_flake_detection_results(self, test_names):
    if self.skip_deflaking_result:
      self.results += self.skip_deflaking_result
      # Rerun was not triggered; nothing to preocess
      return
    self.process_results()


class RepeatE2EShuffledTests(E2ETests):

  def trigger_exoneration(self, test_names):
    pass

  def process_exoneration_results(self, test_names):
    pass

  @property
  def test_type_tag(self):
    return 'shuffled_repeat_e2e_tests'


class E2ETestDivider:

  def __init__(self,
               api,
               builder_config,
               shard_count=4,
               shuffled=False):
    self.api = api
    self.builder_config = builder_config
    self.shard_count = shard_count
    self.shuffled = shuffled

  # It returns a list of lists of test paths
  def commands(self, test_type):
    contents = read_test_list(self.api, self.builder_config, test_type)
    all_tests = contents.splitlines()
    all_test_paths = [
        self.api.path.join('test', test_type, t) for t in all_tests
    ]
    if self.shuffled:
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


def read_test_list(api, builder_config, test_type):
  gen_root = api.devtools.source_dir / 'out' / builder_config / 'gen'
  test_root = gen_root / 'test' / test_type
  test_list_file_path = test_root / 'tests.txt'
  return api.file.read_text('Read test list', test_list_file_path)


def write_test_list(api, builder_config, test_type, test_list):
  gen_root = api.devtools.source_dir / 'out' / builder_config / 'gen'
  test_root = gen_root / 'test' / test_type
  api.step('Create E2E test root', ['mkdir', '-p', test_root])
  test_list_file_path = test_root / 'tests.txt'
  api.file.write_text('Write E2E test list', test_list_file_path, test_list)
