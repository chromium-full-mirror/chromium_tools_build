#!/usr/bin/env vpython3
# Copyright 2025 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
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

# [VPYTHON:BEGIN]
# python_version: "3.11"
#
# wheel: <
#   name: "infra/python/wheels/google-cloud-bigquery-py3"
#   version: "version:3.23.1"
# >
# wheel: <
#   name: "infra/python/wheels/google-api-core-py3"
#   version: "version:2.24.2"
# >
# wheel: <
#   name: "infra/python/wheels/requests-py3"
#   version: "version:2.31.0"
# >
# wheel: <
#   name: "infra/python/wheels/google-auth-py3"
#   version: "version:2.16.2"
# >
# wheel: <
#   name: "infra/python/wheels/google-auth-py3"
#   version: "version:2.16.2"
# >
# wheel: <
#   name: "infra/python/wheels/googleapis-common-protos-py2_py3"
#   version: "version:1.69.2"
# >
# wheel: <
#   name: "infra/python/wheels/proto-plus-py3"
#   version: "version:1.26.1"
# >
# wheel: <
#   name: "infra/python/wheels/protobuf-py3"
#   version: "version:6.30.2"
# >
# wheel: <
#   name: "infra/python/wheels/pyasn1_modules-py2_py3"
#   version: "version:0.2.4"
# >
# wheel: <
#   name: "infra/python/wheels/rsa-py2_py3"
#   version: "version:3.4.2"
# >
# wheel: <
#   name: "infra/python/wheels/cachetools-py3"
#   version: "version:5.3.3"
# >
# wheel: <
#   name: "infra/python/wheels/six-py2_py3"
#   version: "version:1.15.0"
# >
# wheel: <
#   name: "infra/python/wheels/python-dateutil-py2_py3"
#   version: "version:2.8.1"
# >
# wheel: <
#   name: "infra/python/wheels/packaging-py3"
#   version: "version:21.3"
# >
# wheel: <
#   name: "infra/python/wheels/google-cloud-core-py3"
#   version: "version:2.3.3"
# >
# wheel: <
#   name: "infra/python/wheels/google-resumable-media-py3"
#   version: "version:2.3.0"
# >
# wheel: <
#   name: "infra/python/wheels/google-crc32c/${vpython_platform}"
#   version: "version:1.5.0.chromium.1"
# >
# wheel: <
#   name: "infra/python/wheels/pyparsing-py2_py3"
#   version: "version:2.4.7"
# >
# wheel: <
#   name: "infra/python/wheels/pyasn1-py2_py3"
#   version: "version:0.4.5"
# >
# wheel: <
#   name: "infra/python/wheels/charset_normalizer-py3"
#   version: "version:2.0.4"
# >
# wheel: <
#   name: "infra/python/wheels/idna-py2_py3"
#   version: "version:2.8"
# >
# wheel: <
#   name: "infra/python/wheels/urllib3-py2_py3"
#   version: "version:1.26.6"
# >
# wheel: <
#   name: "infra/python/wheels/certifi-py2_py3"
#   version: "version:2020.11.8"
# >
# wheel: <
#   name: "infra/python/wheels/grpcio/${vpython_platform}"
#   version: "version:1.57.0"
# >
# wheel: <
#   name: "infra/python/wheels/grpcio-status-py3"
#   version: "version:1.57.0"
# >
# [VPYTHON:END]

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


def is_retryable(exc):
  # If the dataset isn't found it's most likely because it was just recreated
  is_404 = isinstance(exc, exceptions.NotFound)
  if is_404:
    print('\nWaiting for cleanup...')
  return is_404


class QueryIntegrationTests(unittest.TestCase):

  def setUp(self):
    self._retry = Retry(
        predicate=is_retryable, initial=10, maximum=240, timeout=1200)

    self._client = bigquery.Client(project=_CLOUD_PROJECT_ID)
    # Sets references to the mock tables, creating them if they don't exist.
    dataset = bigquery.Dataset(f'{_CLOUD_PROJECT_ID}.autosharder_test')

    # Double check we are deleting the correct dataset
    assert 'autosharder_test' in dataset.dataset_id
    self._client.delete_dataset(
        dataset, delete_contents=True, not_found_ok=True)
    time.sleep(10)
    self._dataset = self._client.create_dataset(dataset)

    # Setup cq_builders using the live schema
    cq_builders_schema = self._client.get_table(
        'chrome-trooper-analytics.metrics.cq_builders').schema
    cq_builders_table_ref = bigquery.table.Table(
        f'{_CLOUD_PROJECT_ID}.{self._dataset.dataset_id}.cq_builders',
        schema=cq_builders_schema,
    )
    self._cq_builders_table = self._client.create_table(
        cq_builders_table_ref, exists_ok=True)

    # Setup builds table with the live schema
    builds_schema = self._client.get_table(
        'cr-buildbucket.chromium.builds').schema
    builds_table_ref = bigquery.table.Table(
        f'{_CLOUD_PROJECT_ID}.{self._dataset.dataset_id}.builds',
        schema=builds_schema,
    )
    self._builds_table = self._client.create_table(
        builds_table_ref, exists_ok=True)

    # Setup the tasks tables with the live schema
    tasks_schema = self._client.get_table(
        'chromium-swarm.swarming.task_results_summary').schema
    tasks_table_ref = bigquery.table.Table(
        f'{_CLOUD_PROJECT_ID}.{self._dataset.dataset_id}'
        '.task_results_summary',
        schema=tasks_schema,
    )
    self._tasks_table = self._client.create_table(
        tasks_table_ref, exists_ok=True)

  def _create_cq_builders_table(self, cq_builders: list[tuple[str, bool]]):
    self._client.insert_rows(
        self._cq_builders_table, cq_builders, timeout=120, retry=self._retry)

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
        os.path.dirname(__file__), 'query_cq_builders.sql.tmpl')
    with open(query_file, 'r', encoding='utf-8') as f:
      query = f.read().format(
          builders_project=_CLOUD_PROJECT_ID,
          builders_dataset=self._dataset.dataset_id,
      )

    rows = self._run_query([
        'bq', 'query', '--project_id=' + _CLOUD_PROJECT_ID, '--format=json',
        '--max_rows=100000', '--nouse_legacy_sql', query
    ])
    self.assertEqual(
        set(r['builder'] for r in rows), set(i[0] for i in cq_builders))


if __name__ == '__main__':
  unittest.main(verbosity=2)
