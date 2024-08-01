-- Copyright 2024 The Chromium Authors
-- Use of this source code is governed by a BSD-style license that can be
-- found in the LICENSE file.
WITH
  test_bugs AS (
    SELECT
      bug.id,
      rule_id
    FROM `luci-analysis.chromium.failure_association_rules`
    WHERE project = "chromium" AND
      is_active = TRUE AND
    DATE(create_time) > DATE_SUB(CURRENT_DATE(), INTERVAL {sample_day} DAY)
  ),
  failures_tests AS (
    SELECT
      test_bugs.id AS bug_id,
      failures_table.test_id,
      ARRAY_AGG(STRUCT(
        failures_table.test_result_id,
        failures_table.ingested_invocation_id as invocation_id,
        failures_table.sources,
        (SELECT value FROM UNNEST(failures_table.variant)
            WHERE key = "test_suite") AS test_suite,
        (SELECT value FROM UNNEST(failures_table.variant)
            WHERE key = "builder") AS builder,
        (SELECT value FROM UNNEST(failures_table.tags)
            WHERE key = "target_platform") AS test_platform,
        (SELECT value FROM UNNEST(failures_table.tags)
            WHERE key = "test_name") AS test_name
      ))[0] AS test_data
    FROM test_bugs LEFT JOIN `luci-analysis.chromium.clustered_failures`
        AS failures_table
    ON failures_table.cluster_id = test_bugs.rule_id
    -- Select only Linux platform tests because the rr tool is only compatible
    -- with Linux.
    -- Additionally, to obtain traces that can provide a clearer understanding
    -- of the root cause of the bug, only select test failures on chromium:ci.
    WHERE (SELECT value FROM UNNEST(failures_table.tags)
        WHERE key = "target_platform") = "linux" AND
      realm = "chromium:ci"  AND
      DATE(failures_table.partition_time) > DATE_SUB(
            CURRENT_DATE(), INTERVAL {sample_day} DAY)
  GROUP BY test_bugs.id, failures_table.test_id)
SELECT * EXCEPT (test_data), test_data.* FROM failures_tests;