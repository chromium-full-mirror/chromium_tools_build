#!/usr/bin/env vpython3
# Copyright 2025 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
# /// script
# requires-python = '>=3.11,<3.12'
# dependencies = [
#   'google-cloud-bigquery==3.23.1',
#   'google-api-core==2.24.2',
#   'requests==2.31.0',
#   'google-auth==2.16.2',
#   'googleapis-common-protos==1.69.2',
#   'proto-plus==1.26.1',
#   'protobuf==6.30.2',
#   'pyasn1-modules==0.2.4',
#   'rsa==3.4.2',
#   'cachetools==5.3.3',
#   'six==1.15.0',
#   'python-dateutil==2.8.1',
#   'packaging==21.3',
#   'google-cloud-core==2.3.3',
#   'google-resumable-media==2.3.0',
#   'google-crc32c==1.5.0+chromium.1',
#   'pyparsing==2.4.7',
#   'pyasn1==0.4.5',
#   'charset-normalizer==2.0.4',
#   'idna==2.8',
#   'urllib3==1.26.6',
#   'certifi==2020.11.8',
#   'grpcio==1.57.0',
#   'grpcio-status==1.57.0'
# ]
# ///

"""Tests for autosharder queries.

These tests should not be automated because they are too slow and work
on live tables. The live tables in particular can cause unexpected
results if multiple people are running them at the same time. They exist
for manual triggering when the queries are changed. If these need to
run at the same time in the future, the tests can be reworked to use a
uuid in their dataset names. This, however, will also require the
teardown to delete the dataset which will make debugging the tests more
difficult without artifacts from the last run.
"""

import datetime
import os
import json
import subprocess
import time
import unittest

# vpython-provided modules.
# pylint: disable=import-error
from google.cloud import bigquery
from google.api_core import exceptions
from google.api_core.retry import Retry
from zoneinfo import ZoneInfo
# pylint: enable=import-error

# Protected access is allowed for unittests.
# pylint: disable=protected-access

_CLOUD_PROJECT_ID = 'chrome-trooper-analytics'


class Task:
  task_id = 0

  def __init__(
    self,
    duration: float,
    start_time: datetime.datetime,
    create_time: datetime.datetime,
    end_time: datetime.datetime | None = None,
  ):
    self.duration = duration
    self.start_time = start_time
    self.create_time = create_time
    self.end_time = end_time or (start_time + datetime.timedelta(0, duration))
    self.task_id = Task.task_id
    Task.task_id += 1
    self.tags = []
    self.parent_build = None

  def get_row(self):
    return {
      'task_id': self.task_id,
      'request': {
        'name': 'foo-task-name',
        'parent_task_id': self.parent_build.build_task_id,
        'tags': self.tags,
      },
      'duration': self.duration,
      'start_time': str(self.start_time),
      'create_time': str(self.create_time),
      'end_time': str(self.end_time),
      'state': 'SUCCESS',
    }


class Suite:
  def __init__(
    self,
    name: str,
    tasks: list[Task],
    normally_assigned_shard_count: int | None = None,
    experimental_shard_count: int | None = None,
  ):
    self.name = name
    self.tasks = tasks
    for t in tasks:
      t.tags.append(f'test_suite:{name}')
      t.tags.append('test_phase:with patch')
      if normally_assigned_shard_count:
        t.tags.append(
          f'normally_assigned_shard_count:{normally_assigned_shard_count}'
        )
      if experimental_shard_count:
        t.tags.append(f'experimental_shard_count:{experimental_shard_count}')


class Build:
  task_id = 0

  def __init__(
    self,
    builder: str,
    waterfall_builder_group: str,
    waterfall_buildername: str,
    start_time: datetime,
    suites: list[Suite],
  ):
    self.builder = builder
    self.start_time = start_time
    for suite in suites:
      for task in suite.tasks:
        task.tags.extend(
          [
            f'waterfall_builder_group:{waterfall_builder_group}',
            f'waterfall_buildername:{waterfall_buildername}',
          ]
        )
        task.parent_build = self
    self.suites = suites

    self.build_task_id = Build.task_id
    Build.task_id += 1

  def get_row(self):
    return {
      'builder': {
        'builder': self.builder,
        'bucket': 'try',
        'project': 'chromium',
      },
      'start_time': str(self.start_time),
      'infra': {
        'backend': {
          'task': {
            'id': {'id': self.build_task_id},
          },
        },
      },
      'input': {
        'properties': json.dumps({'cq': 'required'}),
      },
      'output': {
        'properties': None,
      },
      'status': 'SUCCESS',
      'tags': [
        {
          'key': 'cq_cl_owner',
          'value': 'foo_user@bar.com',
        }
      ],
    }


def is_retryable(exc):
  # If the dataset isn't found it's most likely because it was just recreated
  is_404 = isinstance(exc, exceptions.NotFound)
  if is_404:
    print('\nWaiting for cleanup...')
  return is_404


class QueryIntegrationTests(unittest.TestCase):
  def setUp(self):
    self._retry = Retry(
      predicate=is_retryable, initial=10, maximum=240, timeout=1200
    )

    self._client = bigquery.Client(project=_CLOUD_PROJECT_ID)
    # Sets references to the mock tables, creating them if they don't exist.
    dataset = bigquery.Dataset(f'{_CLOUD_PROJECT_ID}.autosharder_test')

    # Double check we are deleting the correct dataset
    assert 'autosharder_test' in dataset.dataset_id
    self._client.delete_dataset(
      dataset, delete_contents=True, not_found_ok=True
    )
    time.sleep(10)
    self._dataset = self._client.create_dataset(dataset)

    # Setup cq_builders using the live schema
    cq_builders_schema = self._client.get_table(
      'chrome-trooper-analytics.metrics.cq_builders'
    ).schema
    cq_builders_table_ref = bigquery.table.Table(
      f'{_CLOUD_PROJECT_ID}.{self._dataset.dataset_id}.cq_builders',
      schema=cq_builders_schema,
    )
    self._cq_builders_table = self._client.create_table(
      cq_builders_table_ref, exists_ok=True
    )

    # Setup builds table with the live schema
    builds_schema = self._client.get_table(
      'cr-buildbucket.chromium.builds'
    ).schema
    builds_table_ref = bigquery.table.Table(
      f'{_CLOUD_PROJECT_ID}.{self._dataset.dataset_id}.builds',
      schema=builds_schema,
    )
    self._builds_table = self._client.create_table(
      builds_table_ref, exists_ok=True
    )

    # Setup the tasks tables with the live schema
    tasks_schema = self._client.get_table(
      'chromium-swarm.swarming.task_results_summary'
    ).schema
    tasks_table_ref = bigquery.table.Table(
      f'{_CLOUD_PROJECT_ID}.{self._dataset.dataset_id}.task_results_summary',
      schema=tasks_schema,
    )
    self._tasks_table = self._client.create_table(
      tasks_table_ref, exists_ok=True
    )

  def _create_cq_builders_table(self, cq_builders: list[tuple[str, bool]]):
    self._client.insert_rows(
      self._cq_builders_table, cq_builders, timeout=120, retry=self._retry
    )

  def _create_builds_table(self, builds: list[Build]):
    self._client.insert_rows_json(
      self._builds_table,
      [b.get_row() for b in builds],
      timeout=120,
      retry=self._retry,
    )
    task_rows = [t.get_row() for b in builds for s in b.suites for t in s.tasks]
    if task_rows:
      self._client.insert_rows_json(
        self._tasks_table, task_rows, timeout=120, retry=self._retry
      )

  def _run_query(self, args):
    try:
      output = subprocess.check_output(args)
    except subprocess.CalledProcessError as e:
      print(e.output)
      raise Exception(f'Query failed: {e.output}') from e
    return json.loads(output)

  def testCqBuilders(self):
    cq_builders = [
      # builder name, is_experimental
      ('foo-rel', False),
      ('bar-x64-dbg', False),
    ]
    self._create_cq_builders_table(cq_builders)
    query_file = os.path.join(
      os.path.dirname(__file__), 'query_cq_builders.sql.tmpl'
    )
    with open(query_file, 'r', encoding='utf-8') as f:
      query = f.read().format(
        builders_project=_CLOUD_PROJECT_ID,
        builders_dataset=self._dataset.dataset_id,
      )

    rows = self._run_query(
      [
        'bq',
        'query',
        '--project_id=' + _CLOUD_PROJECT_ID,
        '--format=json',
        '--max_rows=100000',
        '--nouse_legacy_sql',
        query,
      ]
    )
    self.assertEqual(
      set(r['builder'] for r in rows), set(i[0] for i in cq_builders)
    )

  def testQuerySuiteDurations(self):
    start_date = datetime.datetime(2025, 1, 6)
    end_date = datetime.datetime(2025, 1, 13)

    min_sample_size = 11
    # Simple builder with 2 suites (sharded with 1 and 2 shard counts)
    # that run consistently for the minimum sample count
    builds = [
      Build(
        'foo-try-builder',
        'foo-group',
        'foo-ci-builder',
        start_date + datetime.timedelta(0, 1),
        [
          Suite(
            name='foo-suite-with-1-shard',
            tasks=[
              Task(
                duration=10.0,
                create_time=start_date + datetime.timedelta(0, 10),
                start_time=start_date + datetime.timedelta(0, 20),
              ),
            ],
          ),
          Suite(
            name='foo-suite-with-2-shards',
            tasks=[
              Task(
                duration=20.0,
                create_time=start_date + datetime.timedelta(0, 20),
                start_time=start_date + datetime.timedelta(0, 40),
              ),
              Task(
                duration=10.0,
                create_time=start_date + datetime.timedelta(0, 10),
                start_time=start_date + datetime.timedelta(0, 20),
              ),
            ],
          ),
        ],
      )
      for _ in range(min_sample_size)
    ]

    # Add a builder that has different performances half the time it runs
    builds.extend(
      [
        Build(
          'changing-runtimes-between-builds',
          'foo-group',
          'foo-ci-builder',
          start_date + datetime.timedelta(0, 1),
          [
            Suite(
              name='foo-suite',
              tasks=[
                Task(
                  duration=10.0,
                  # Pending of 20
                  create_time=start_date + datetime.timedelta(0, 10),
                  start_time=start_date + datetime.timedelta(0, 30),
                ),
              ],
            )
          ],
        )
        for _ in range(6)
      ]
      + [
        Build(
          'changing-runtimes-between-builds',
          'foo-group',
          'foo-ci-builder',
          start_date + datetime.timedelta(0, 1),
          [
            Suite(
              name='foo-suite',
              tasks=[
                Task(
                  duration=20.0,
                  # Pending of 40
                  create_time=start_date + datetime.timedelta(0, 10),
                  start_time=start_date + datetime.timedelta(0, 50),
                ),
              ],
            )
          ],
        )
        for _ in range(6)
      ]
    )

    # Add a builder that doesn't run enough to get sharded
    builds.extend(
      [
        Build(
          'infrequently-run-builder',
          'foo-group',
          'foo-ci-builder',
          start_date + datetime.timedelta(0, 1),
          [
            Suite(
              name='foo-suite',
              tasks=[
                Task(
                  duration=10.0,
                  create_time=start_date + datetime.timedelta(0, 10),
                  start_time=start_date + datetime.timedelta(0, 20),
                ),
              ],
            )
          ],
        )
        for _ in range(min_sample_size - 1)
      ]
    )

    self._create_builds_table(builds)

    query_file = os.path.join(
      os.path.dirname(__file__), 'query_suite_durations.sql.tmpl'
    )
    with open(query_file, 'r', encoding='utf-8') as f:
      query = f.read().format(
        builds_project=_CLOUD_PROJECT_ID,
        builds_dataset=self._dataset.dataset_id,
        tasks_project=_CLOUD_PROJECT_ID,
        tasks_dataset=self._dataset.dataset_id,
        lookback_start_date=start_date,
        lookback_end_date=end_date,
        percentile=80,  # default
        min_sample_size=10,
        target_runtime=15.0,
        ignore_cl_owner='""',
        exclude_test_suites='""',
        exclude_builders='""',
        exclude_builder_suites='""',
      )

    rows = self._run_query(
      [
        'bq',
        'query',
        '--project_id=' + _CLOUD_PROJECT_ID,
        '--format=json',
        '--max_rows=100000',
        '--nouse_legacy_sql',
        query,
      ]
    )

    def get_builder_row(builder, suite=None):
      for row in rows:
        if row['try_builder'] == builder and (
          not suite or row['test_suite'] == suite
        ):
          return row
      return None

    # Single shard suite
    row = get_builder_row('foo-try-builder', 'foo-suite-with-1-shard')
    self.assertEqual(int(row['sample_size']), min_sample_size)
    self.assertEqual(row['test_suite'], 'foo-suite-with-1-shard')
    self.assertEqual(row['try_builder'], 'foo-try-builder')
    self.assertEqual(row['waterfall_builder_group'], 'foo-group')
    self.assertEqual(row['waterfall_builder_name'], 'foo-ci-builder')
    self.assertEqual(int(row['shard_count']), 1)
    self.assertAlmostEqual(
      float(row['percentile_duration_minutes']), round(10 / 60, 2)
    )

    # 2 shard suite
    row = get_builder_row('foo-try-builder', 'foo-suite-with-2-shards')
    self.assertEqual(int(row['sample_size']), min_sample_size)
    self.assertEqual(row['test_suite'], 'foo-suite-with-2-shards')
    self.assertEqual(row['try_builder'], 'foo-try-builder')
    self.assertEqual(row['waterfall_builder_group'], 'foo-group')
    self.assertEqual(row['waterfall_builder_name'], 'foo-ci-builder')
    # Based on the max pending shard per build (making this a percentile
    # over a single number)
    self.assertEqual(int(row['shard_count']), 2)
    self.assertAlmostEqual(float(row['percentile_duration_minutes']), 0.33)

    # 2 different runtimes/pendings profiles for the same suite
    row = get_builder_row('changing-runtimes-between-builds')
    self.assertEqual(int(row['sample_size']), 12)
    self.assertEqual(row['test_suite'], 'foo-suite')
    self.assertEqual(row['try_builder'], 'changing-runtimes-between-builds')
    self.assertEqual(row['waterfall_builder_group'], 'foo-group')
    self.assertEqual(row['waterfall_builder_name'], 'foo-ci-builder')
    self.assertEqual(int(row['shard_count']), 1)
    self.assertAlmostEqual(float(row['percentile_duration_minutes']), 0.33)

    # Infrequently run builders don't get sharded
    self.assertIsNone(get_builder_row('infrequently-run-builder'))

  def testAvgBuildsPerHour(self):
    start_date = datetime.datetime(
      2025, 1, 5, tzinfo=ZoneInfo('America/Los_Angeles')
    )
    end_date = datetime.datetime(
      2025, 1, 12, tzinfo=ZoneInfo('America/Los_Angeles')
    )

    # Add a builder that runs 1 Monday-Friday and 5 on the weekends (to ignore)
    builds = (
      [
        Build('foo-try-builder', 'foo-group', 'foo-ci-builder', start_date, [])
        for _ in range(5)
      ]
      + [
        Build(
          'foo-try-builder',
          'foo-group',
          'foo-ci-builder',
          start_date + datetime.timedelta(1),
          [],
        ),
        Build(
          'foo-try-builder',
          'foo-group',
          'foo-ci-builder',
          start_date + datetime.timedelta(2),
          [],
        ),
        Build(
          'foo-try-builder',
          'foo-group',
          'foo-ci-builder',
          start_date + datetime.timedelta(3),
          [],
        ),
        Build(
          'foo-try-builder',
          'foo-group',
          'foo-ci-builder',
          start_date + datetime.timedelta(4),
          [],
        ),
        Build(
          'foo-try-builder',
          'foo-group',
          'foo-ci-builder',
          start_date + datetime.timedelta(5),
          [],
        ),
      ]
      + [
        Build(
          'foo-try-builder',
          'foo-group',
          'foo-ci-builder',
          start_date + datetime.timedelta(6),
          [],
        )
        for _ in range(5)
      ]
    )

    # Add a builder that run 5 on Monday
    builds.extend(
      [
        Build(
          'monday-heavy',
          'foo-group',
          'foo-ci-builder',
          start_date + datetime.timedelta(1),
          [],
        )
        for _ in range(5)
      ]
    )

    self._create_builds_table(builds)

    query_file = os.path.join(
      os.path.dirname(__file__), 'query_average_number_builds_per_hour.sql.tmpl'
    )
    with open(query_file, 'r', encoding='utf-8') as f:
      query_str = f.read()
    query = query_str.format(
      builds_project=_CLOUD_PROJECT_ID,
      builds_dataset=self._dataset.dataset_id,
      lookback_start_date=start_date,
      lookback_end_date=end_date,
    )
    rows = self._run_query(
      [
        'bq',
        'query',
        '--project_id=' + _CLOUD_PROJECT_ID,
        '--format=json',
        '--max_rows=100000',
        '--nouse_legacy_sql',
        query,
      ]
    )

    def get_builder_row(builder):
      for row in rows:
        if row['try_builder'] == builder:
          return row
      return None

    row = get_builder_row('foo-try-builder')
    self.assertEqual(float(row['avg_count']), 1)

    row = get_builder_row('monday-heavy')
    self.assertEqual(float(row['avg_count']), 5)

  def testOverhead(self):
    start_date = datetime.datetime(2025, 1, 5)
    end_date = datetime.datetime(2025, 1, 12)

    # A build with perfect, 0 overhead
    builds = [
      # Normal build
      Build(
        'perfect-overhead-try-builder',
        'foo-group',
        'foo-ci-builder',
        start_date + datetime.timedelta(0, 1),
        [
          Suite(
            name='foo-suite',
            tasks=[
              Task(
                duration=10.0,
                create_time=start_date + datetime.timedelta(0, 10),
                start_time=start_date + datetime.timedelta(0, 20),
              ),
            ],
          )
        ],
      ),
      # n+1 experimental build
      Build(
        'perfect-overhead-try-builder',
        'foo-group',
        'foo-ci-builder',
        start_date + datetime.timedelta(0, 1),
        [
          Suite(
            name='foo-suite',
            tasks=[
              Task(
                duration=5.0,
                create_time=start_date + datetime.timedelta(0, 1),
                start_time=start_date + datetime.timedelta(0, 2),
              ),
              Task(
                duration=5.0,
                create_time=start_date + datetime.timedelta(0, 1),
                start_time=start_date + datetime.timedelta(0, 2),
              ),
            ],
            normally_assigned_shard_count=1,
            experimental_shard_count=2,
          ),
        ],
      ),
    ]

    # Build with 1 overhead
    builds.extend(
      [
        # Normal build
        Build(
          'one-overhead-try-builder',
          'foo-group',
          'foo-ci-builder',
          start_date + datetime.timedelta(0, 1),
          [
            Suite(
              name='foo-suite',
              tasks=[
                # 9 minutes of testing, 1 min of overhead per shard
                Task(
                  duration=5.5,
                  create_time=start_date + datetime.timedelta(0, 1),
                  start_time=start_date + datetime.timedelta(0, 2),
                ),
                Task(
                  duration=5.5,
                  create_time=start_date + datetime.timedelta(0, 1),
                  start_time=start_date + datetime.timedelta(0, 2),
                ),
              ],
            )
          ],
        ),
        # n+1 experimental build
        Build(
          'one-overhead-try-builder',
          'foo-group',
          'foo-ci-builder',
          start_date + datetime.timedelta(0, 1),
          [
            Suite(
              name='foo-suite',
              # 1 min overhead per shard still means 9 mins of
              # testing total
              tasks=[
                Task(
                  duration=4.0,
                  create_time=start_date + datetime.timedelta(0, 1),
                  start_time=start_date + datetime.timedelta(0, 2),
                ),
                Task(
                  duration=4.0,
                  create_time=start_date + datetime.timedelta(0, 1),
                  start_time=start_date + datetime.timedelta(0, 2),
                ),
                Task(
                  duration=4.0,
                  create_time=start_date + datetime.timedelta(0, 1),
                  start_time=start_date + datetime.timedelta(0, 2),
                ),
              ],
              normally_assigned_shard_count=2,
              experimental_shard_count=3,
            ),
          ],
        ),
      ]
    )

    self._create_builds_table(builds)

    query_file = os.path.join(
      os.path.dirname(__file__), 'query_test_overheads.sql.tmpl'
    )
    with open(query_file, 'r', encoding='utf-8') as f:
      query_str = f.read()
    query = query_str.format(
      builds_project=_CLOUD_PROJECT_ID,
      builds_dataset=self._dataset.dataset_id,
      tasks_project=_CLOUD_PROJECT_ID,
      tasks_dataset=self._dataset.dataset_id,
      lookback_start_date=start_date,
      lookback_end_date=end_date,
    )
    rows = self._run_query(
      [
        'bq',
        'query',
        '--project_id=' + _CLOUD_PROJECT_ID,
        '--format=json',
        '--max_rows=100000',
        '--nouse_legacy_sql',
        query,
      ]
    )

    def get_builder_row(builder):
      for row in rows:
        if row['try_builder'] == builder:
          return row
      return None

    row = get_builder_row('perfect-overhead-try-builder')
    self.assertEqual(float(row['p50_task_setup_duration_sec']), 0)
    self.assertEqual(float(row['p50_test_harness_overhead_sec']), 0)

    row = get_builder_row('one-overhead-try-builder')
    self.assertEqual(float(row['p50_task_setup_duration_sec']), 0)
    self.assertEqual(float(row['p50_test_harness_overhead_sec']), 1)


if __name__ == '__main__':
  unittest.main(verbosity=2)
