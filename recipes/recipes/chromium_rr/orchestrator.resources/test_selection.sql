-- Copyright 2024 The Chromium Authors
-- Use of this source code is governed by a BSD-style license that can be
-- found in the LICENSE file.
WITH
  sheriff_rotations_ci_builds AS (
    SELECT DISTINCT builder.builder,
    FROM
      `cr-buildbucket.chromium.builds`
    WHERE
      input.properties LIKE '%sheriff_rotations%'
      AND JSON_VALUE_ARRAY(input.properties, '$.sheriff_rotations')[OFFSET(0)]
        = "chromium"
      AND start_time > TIMESTAMP_SUB(CURRENT_TIMESTAMP(),
                                     INTERVAL {sample_day} DAY)
  ),
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
  ),
  test_failure_data AS (
    SELECT
      bug_id,
      test_id,
      test_suite,
      builder,
      test_name,
      ARRAY_AGG(STRUCT(
        invocation_id,
        sources,
        test_result_id
      ))[0] AS test_data,
      COUNT(DISTINCT invocation_id) AS flaky_invocation_count
    FROM failures_tests
    WHERE builder IN (SELECT builder FROM sheriff_rotations_ci_builds)
    GROUP BY bug_id, test_id, test_suite, builder, test_name
  )
SELECT * EXCEPT (test_data), test_data.* FROM test_failure_data;