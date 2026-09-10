# Copyright 2024 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import itertools

from recipe_engine import recipe_api

from RECIPE_MODULES.build.chromium_tests import steps

from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb


class ChromiumReviverApi(recipe_api.RecipeApi):
  def run(self):
    builder_id, builder_config = (
      self.m.chromium_polymorphic.lookup_builder_config()
    )

    # Set the ResultDB variants so that tests for the target builder can be
    # queried
    target_builder = self.m.chromium_polymorphic.target_builder_id
    self.m.chromium_tests.base_variant_getter = lambda spec: {
      'builder': self.m.buildbucket.builder_name,
      'reviver_project': target_builder.project,
      'reviver_bucket': target_builder.bucket,
      'reviver_builder': spec.waterfall_buildername,
    }

    self.m.chromium_tests.configure_build(builder_config)

    with self.m.chromium.chromium_layout():
      update_result, build_dir, targets_config = (
        self.m.chromium_tests.prepare_checkout(
          builder_config, report_cache_state=False
        )
      )
      checkout_dir = update_result.checkout_dir
      source_dir = update_result.source_root.path
      self.m.chromium_swarming.configure_swarming(
        precommit=False, builder_group=builder_id.group
      )

      # Set the test options so that the tests run disabled test cases, skipping
      # tests that don't support it
      test_options = steps.TestOptions(run_disabled=True)
      tests = []
      for t in targets_config.all_tests:
        if t.option_flags.run_disabled_flag:
          t.test_options = test_options
          tests.append(t)

      # Only compile targets for the tests that will be run
      compile_targets = sorted(
        set(itertools.chain.from_iterable(t.compile_targets() for t in tests))
      )

      compile_result, compile_output = (
        self.m.chromium_tests.compile_specific_targets(
          build_dir,
          builder_id,
          builder_config,
          update_result,
          targets_config,
          compile_targets,
          tests,
        )
      )
      if compile_result and compile_result.status != common_pb.SUCCESS:
        return compile_result

      if compile_output:
        self.m.chromium_tests.isolate_test_targets(
          source_dir, build_dir, builder_config, update_result, compile_output
        )

      # There will pretty much always be test failures since it is unlikely that
      # all disabled tests can be re-enabled, so don't fail the build due to
      # failed tests
      test_runner = self.m.chromium_tests.create_test_runner(
        checkout_dir,
        source_dir,
        build_dir,
        tests,
        serialize_tests=builder_config.serialize_tests,
        surface_invalid_results_as_infra_failure=True,
      )
      with self.m.chromium_tests.wrap_chromium_tests(
        checkout_dir, source_dir, build_dir, tests=tests
      ):
        test_result = test_runner()
        if self.m.code_coverage.using_coverage:
          self.m.code_coverage.process_coverage_data(
            tests, override_builder_name=target_builder.builder
          )
        if test_result and test_result.status not in (
          common_pb.SUCCESS,
          common_pb.FAILURE,
        ):
          return test_result
