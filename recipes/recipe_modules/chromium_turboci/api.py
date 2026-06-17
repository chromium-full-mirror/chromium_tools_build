# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping
import contextlib
import hashlib

from google.protobuf import json_format
from recipe_engine import recipe_api, turboci

from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb
from PB.recipe_engine import result as result_pb2
from PB.turboci.data.build.v1.build_check_options import (
    BuildCheckOptions,
    Product,
)
from PB.turboci.data.build.v1.build_check_results import BuildCheckResults
from PB.turboci.data.common.v1.display_message import DisplayMessage
from PB.turboci.data.chrome.build.v1.analyze_options import AnalyzeOptions
from PB.turboci.data.chrome.build.v1.analyze_results import AnalyzeResults
from PB.turboci.data.chrome.build.v1.compile_targets_options import (
    CompileTargetsOptions,)
from PB.turboci.data.test.v1.test_check_description_option import (
    TestCheckDescriptionOption,)
from PB.turboci.data.test.v1.test_check_summary_result import (
    TestCheckSummaryResult,)
from PB.turboci.graph.orchestrator.v1.query import Query

BUILD_CANCELED_SUMMARY = 'Build was canceled.'


class ChromiumTurbociApi(recipe_api.RecipeApi):

  @contextlib.contextmanager
  def display_turboci_checks(self):
    try:
      yield
    finally:
      if (not self.m.runtime.in_global_shutdown and
          'luci.buildbucket.run_in_turboci'
          in self.m.buildbucket.build.input.experiments):
        # Output all checks from the workplan when running in turboci, mainly
        # for inspection purpose.
        # TODO(https://crbug.com/493256377): Remove this step when we have a
        # better place to show checks
        step_result = self.m.step.empty('TurboCI Checks')
        # Use the same turboci query as that in
        # recipes-py/recipe_engine/internal/test/magic_check_fn.py
        workplan = turboci.query_nodes(
            turboci.make_query(
                Query.SelectChecks(),
                Query.CollectChecks(
                    options=True,
                    result_data=True,
                ),
            ),
            types=('*',)).workplans[0]
        step_result.presentation.logs['checks'] = json_format.MessageToJson(
            workplan)

  def ensure_check_id(
      self,
      check_id: str,
      default_check_id_prefix: str,
  ) -> str:
    """Ensure a non-empty check ID.

    chromium_tests module code is shared by multiple recipes, some of which are
    in scope for migrating to TurboCI and some of which are not. The recipe
    engine provides a fake for TurboCI that works regardless of whether the
    caller is in scope for migration. However, valid check ID values are still
    necessary because the fake enforces the TurboCI constraints. This provides
    the means to get a unique non-empty ID so that non-TurboCI-aware callers
    don't need to worry about providing non-empty, non-colliding IDs so that
    chromium_tests doesn't need to litter the code with conditionals.

    Args:
      check_id: The check ID provided to the module.
      default_check_id_prefix: The prefix to use when generating a check ID if
        check_id is empty.

    Returns:
      check_id if it is non-empty, otherwise a unique check ID beginning with
      default_check_id_prefix.
    """
    if check_id:
      return check_id
    return f'{default_check_id_prefix}-{self.m.uuid.random()}'

  def create_build_check(
      self,
      build_check_id: str,
      source_check_id: str,
      *,
      create_analyze_check: bool = False,
      builder_full_name: str = '',
  ) -> str:
    """Create a build check.

    Args:
      build_check_id: The check ID for the build check to create. The check will
        be created in the PLANNING state and have a BuildCheckOptions attached.
      source_check_id: The check ID for an existing source check. The build
        check will have a dependency on the source check.
      create_analyze_check: If true, an analyze check will be created. The check
        will be created in the PLANNING state. The build check will have a
        dependency on the analyze check.
      builder_full_name: The fully-qualified builder name to report. If a
        non-empty value is not provided, the name of the currently running
        builder be used.

    Returns:
      The check ID of the analyze check if create_analyze_check is True, an
      empty string otherwise.
    """
    assert build_check_id, 'build_check_id'
    assert source_check_id, 'source_id'
    checks = []
    analyze_check_id = ''
    build_check_deps = [source_check_id]

    if create_analyze_check:
      analyze_check_id = f'{build_check_id} analyze'
      checks.append(
          turboci.check(
              analyze_check_id,
              kind='CHECK_KIND_ANALYSIS',
              # TODO (crbug.com/513249469#comment2): Remove realm after move to
              # new python helpers.
              realm='$from_container',
          ))
      build_check_deps.append(analyze_check_id)

    # When the workflows are decomposed, this may need to change to some
    # identity that identifies the configuration across stages rather than a
    # builder name
    builder_full_name = (
        builder_full_name or self.m.buildbucket.builder_full_name)
    checks.append(
        turboci.check(
            build_check_id,
            kind='CHECK_KIND_BUILD',
            # TODO (crbug.com/513249469#comment2): Remove realm after move to
            # new python helpers.
            realm='$from_container',
            realm_options=[(
                '$from_container',
                BuildCheckOptions(
                    target=BuildCheckOptions.BuildTarget(
                        name=builder_full_name,
                        product=Product.PRODUCT_BROWSER,
                    )),
            )],
            deps=turboci.dep_group(*build_check_deps),
        ))

    turboci.write_nodes(turboci.reason('initializing build check'), *checks)

    return analyze_check_id

  def prepare_analyze_check(
      self,
      analyze_check_id: str,
      test_targets: Collection[str],
      additional_compile_targets: Collection[str],
      analyze_config_names: Collection[str],
      additional_exclusions: Mapping[str, str],
  ) -> recipe_api.ResultsCallback:
    """Make the analyze check ready to execute analyze.

    Args:
      analyze_check_id: The check ID for the analyze check. The check may or may
        not already exist. The check will be left in the PLANNED state and have
        an AnalyzeOptions attached.
      test_targets: Targets corresponding to the tests to be executed.
      additional_compile_targets: Targets explicitly requested for compilation.
      analyze_config_names: Analyze config names to use in addition to the
        always used "base".
      additional_exclusions: Additional file paths to skip analysis for. Keys
        are the file path relative to the root of the top-level repo with the
        corresponding value describing why the file is excluded.

    Returns:
      A results callback that can be used to write the results into the analyze
      check and set its state to FINAL.
    """
    assert analyze_check_id, 'analyze_check_id'
    turboci.write_nodes(
        turboci.reason('performing analyze to reduce targets'),
        turboci.check(
            analyze_check_id,
            kind='CHECK_KIND_ANALYSIS',
            # TODO (crbug.com/513249469#comment2): Remove realm after move to
            # new python helpers.
            realm='$from_container',
            realm_options=[(
                '$from_container',
                AnalyzeOptions(
                    compile_targets=sorted(additional_compile_targets),
                    test_targets=sorted(test_targets),
                    analyze_config_path=(self.m.chromium.c.analyze_config_path),
                    analyze_config_names=analyze_config_names,
                    additional_exclusions=additional_exclusions,
                ),
            )],
            state='CHECK_STATE_PLANNED',
        ),
    )

    def results_callback(
        reason: str,
        compile_targets: Iterable[str],
        test_targets: Iterable[str],
    ) -> None:
      """Results callback that will update the analyze check.

      After execution, the analyze check will have an AnalyzeResults attached
      and be in state FINAL.
      """
      turboci.write_nodes(
          turboci.reason(reason),
          turboci.check(
              analyze_check_id,
              # TODO (crbug.com/513249469#comment2): Remove realm after move to
              # new python helpers.
              realm='$from_container',
              realm_results=[(
                  '$from_container',
                  AnalyzeResults(
                      compile_targets=sorted(compile_targets),
                      test_targets=sorted(test_targets)),
              )],
              state='CHECK_STATE_FINAL',
          ),
      )

    return results_callback

  def update_build_check_compile_targets(
      self,
      build_check_id: str,
      compile_targets: Iterable[str],
      reason: str,
  ) -> None:
    """Update the compile targets on a build check.

    Args:
      build_check_id: The check ID to update the targets for. The check may or
        may not already exist. The check will have a CompileTargetsOptions
        attached.
      compile_targets: The set of compile targets to be passed to the build
        system.
      reason: A description of the reason the compile targets are being updated.
    """
    assert build_check_id, 'build_check_id'
    turboci.write_nodes(
        turboci.reason(reason),
        turboci.check(
            build_check_id,
            kind='CHECK_KIND_BUILD',
            # TODO (crbug.com/513249469#comment2): Remove realm after move to
            # new python helpers.
            realm='$from_container',
            realm_options=[(
                '$from_container',
                CompileTargetsOptions(compile_targets=sorted(compile_targets)),
            )],
        ),
    )

  def set_build_check_planned(
      self,
      build_check_id: str,
      reason: str,
  ) -> None:
    """Set the build check's state to PLANNED.

    This indicates that planning for the check is done; a check can't be moved
    from the PLANNING state directly to FINAL state and the options can't be
    updated in the PLANNED state, so this must be called before calling
    finalize_build_check for the build check.

    Args:
      build_check_id: The check ID of an existing build check in the PLANNING
        state. The check should have BuildCheckOptions and CompileTargetOptions
        attached.
      reason: A description of the reason to update the check's state.
    """
    assert build_check_id, 'build_check_id'
    turboci.write_nodes(
        turboci.reason(reason),
        turboci.check(
            build_check_id,
            # TODO (crbug.com/513249469#comment2): Remove realm after move to
            # new python helpers.
            realm='$from_container',
            state='CHECK_STATE_PLANNED',
        ),
    )

  def finalize_build_check(
      self,
      build_check_id: str,
      reason: str,
      *,
      raw_result: result_pb2.RawResult | None = None,
  ) -> None:
    """Record the results of a compile.

    Args:
      build_check_id: The check ID of an existing build check in the PLANNED
        state.
      reason: A description of the reason to finalize the check.
      raw_result: The result of the compile operation. If provided, a
        BuildCheckResults will be attached to the build check containing the
        summary markdown if present.
    """
    assert build_check_id, 'build_check_id'
    realm_results = []
    if raw_result is not None:
      display_message = None
      if raw_result.summary_markdown:
        display_message = DisplayMessage(
            message=raw_result.summary_markdown,
            message_format=DisplayMessage.MessageFormat.MESSAGE_FORMAT_MARKDOWN,
        )
      realm_results.append((
          '$from_container',
          BuildCheckResults(
              success=raw_result.status == common_pb.SUCCESS,
              display_message=display_message,
          ),
      ))
    turboci.write_nodes(
        turboci.reason(reason),
        turboci.check(
            build_check_id,
            # TODO (crbug.com/513249469#comment2): Remove realm after move to
            # new python helpers.
            realm='$from_container',
            state='CHECK_STATE_FINAL',
            realm_results=realm_results,
        ),
    )

  @staticmethod
  def get_test_check_id(test_step_name: str) -> str:
    """Create a valid turboci test check_id for the given test step name."""
    # TODO(crbug.com/493256377): Consolidate with "ensure_check_id" method.
    return hashlib.sha256(test_step_name.encode('utf-8')).hexdigest()

  def create_test_check(self, test_step_name: str) -> None:
    """Create a TurboCI test check for the test suite on a given phase."""
    turboci.write_nodes(
        turboci.reason(f'Create test {test_step_name}'),
        turboci.check(
            self.get_test_check_id(test_step_name),
            kind='CHECK_KIND_TEST',
            # TODO (crbug.com/513249469#comment2): Remove realm after move to
            # new python helpers.
            realm='$from_container',
            realm_options=[(
                '$from_container',
                TestCheckDescriptionOption(title=test_step_name),
            )],
        ))

  def set_test_check_planned(self, test_step_name: str) -> None:
    """Set test check's state to PLANNED for a test suite on a given phase."""
    turboci.write_nodes(
        turboci.reason(f'Trigger test {test_step_name}'),
        turboci.check(
            self.get_test_check_id(test_step_name),
            # TODO (crbug.com/513249469#comment2): Remove realm after move to
            # new python helpers.
            realm='$from_container',
            state='CHECK_STATE_PLANNED',
        ))

  def finalize_test_check(
      self,
      test_step_name: str,
      is_success: bool,
      display_text: str,
  ) -> None:
    """Mark test check as FINAL and record the test results."""
    result = TestCheckSummaryResult(
        success=is_success,
        display_message=DisplayMessage(
            message=display_text,
            message_format=DisplayMessage.MessageFormat.MESSAGE_FORMAT_HTML,
        ))
    turboci.write_nodes(
        turboci.reason(f'Collect results for test {test_step_name}'),
        turboci.check(
            self.get_test_check_id(test_step_name),
            # TODO (crbug.com/513249469#comment2): Remove realm after move to
            # new python helpers.
            realm='$from_container',
            state='CHECK_STATE_FINAL',
            realm_results=[('$from_container', result)],
        ))
