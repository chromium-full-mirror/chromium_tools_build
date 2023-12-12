# Copyright 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from google.protobuf.json_format import MessageToDict
from google.protobuf.json_format import ParseDict
from google.protobuf import timestamp_pb2
from recipe_engine import recipe_api

from PB.go.chromium.org.luci.buildbucket.proto import build as build_pb2
from PB.go.chromium.org.luci.buildbucket.proto \
  import builds_service as builds_service_pb2
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb
from PB.recipe_engine import result as result_pb2
from RECIPE_MODULES.build.attr_utils import attrib, attrs, mapping, sequence
from RECIPE_MODULES.build.chromium_tests.api import (
    ALL_TEST_BINARIES_ISOLATE_NAME)
from RECIPE_MODULES.build.code_coverage import constants
from RECIPE_MODULES.build.chromium_tests_builder_config import try_spec

COMPILATOR_SWARMING_TASK_COLLECT_STEP = (
    'wait for compilator swarming task cleanup overhead')

BUILD_CANCELED_SUMMARY = 'Build was canceled.'


@attrs()
class CompilatorOutputProps:
  """Contains output properties from the triggered Compilator build

  Attributes:
    swarming_props: Dict containing swarming information to trigger tests
    skylab_props: Dict containing skylab information to trigger skylab tests
    got_revisions: Dict containing revisions checked out for src and other deps
    override_deps: Dict containing deps to override for without patch runs, if
      any. This is only populated for CLs where the patch root is different
      from the root, like v8 CLs.
    affected_files: List containing paths (str) of files affected by the patch
    src_side_deps_digest: CAS digest hash (str) for downloading src-side deps
    src_side_test_spec_dir: Path (str) to downloaded src-side directory that
      contains test specs, relative to the root of the downloaded src-side deps
    skipping_coverage: Whether coverage is being skipped. The compilator
      determines this by checking the len of affected eligible files.
  """

  swarming_props = attrib(mapping[str, ...], default=None)
  skylab_props = attrib(mapping[str, ...], default=None)
  override_deps = attrib(mapping[str, str], default=None)
  got_revisions = attrib(mapping[str, str])
  affected_files = attrib(sequence[str], default=None)
  src_side_deps_digest = attrib(str, default=None)
  src_side_test_spec_dir = attrib(str, default=None)
  skipping_coverage = attrib(bool, default=None)


class ChromiumOrchestratorApi(recipe_api.RecipeApi):

  def __init__(self, properties, **kwargs):
    super().__init__(**kwargs)
    self.compilator = properties.compilator
    self.compilator_watcher_git_revision = (
        properties.compilator_watcher_git_revision)

    self.compilator_watcher_pkg = None

    # Buildbucket ID of most recently triggered compilator build
    # If a without patch compilator build is triggered, this will be updated
    # to the without patch compilator build's ID
    self.current_compilator_buildbucket_id = None
    # By default the compilators are canceled automatially when the parent
    # orchestrator is canceled. For other experimental situations, this
    # automatic cancelation is disabled and `can_outlive_parent` is set to
    # True when the compilator is triggered.
    self.disable_auto_compilator_cancels = False

    # Build proto to be assigned when triggering a without patch compilator
    # build. This needs to be defined here so that the
    # trigger_without_patch_compile_callback function can assign the variable.
    self.without_patch_build = None
    # Without patch build was triggered early, before the retry shards
    # are triggered.
    self.triggered_without_patch_build_early = False

  def trybot_steps(self):
    if self.m.led.launched_by_led and not self.m.led.led_build:
      return result_pb2.RawResult(
          status=common_pb.INFRA_FAILURE,
          summary_markdown=(
              'using led swarming tasks is not supported for this recipe,'
              ' please pass the -real-build flag to led get-* and led launch'))

    raw_result = self.test_patch()

    # If the orchestrator build is canceled or infra failed, the exception
    # should bubble up during test_patch() and the code below will not be
    # executed
    if (self.current_compilator_buildbucket_id and
        not self.m.runtime.in_global_shutdown):
      comp_build = self.m.buildbucket.get(
          step_name='fetch compilator build proto',
          build_id=self.current_compilator_buildbucket_id)

      # The compilator build's gitiles_commit contains the commit position too,
      # so use this instead of the gitiles_commit fed in by the bootstrapper.
      if comp_build.output.HasField('gitiles_commit'):
        self.m.buildbucket.set_output_gitiles_commit(
            comp_build.output.gitiles_commit)

      # When the without_patch_build was triggered early in the build
      # (during the retry shards step) but no longer needed by the
      # orchestrator, we don't need to wait around for it to finish.
      # By this point the without_patch_build has already been cancelled through
      # buildbucket.cancel() and the compilator swarming task will finish
      # shutting down on its own time.
      if (self.triggered_without_patch_build_early or
          self.disable_auto_compilator_cancels):
        return raw_result
      # crbug.com/1271287#c22
      # Wait for compilator task overhead to complete
      self.m.swarming.collect(
          name=COMPILATOR_SWARMING_TASK_COLLECT_STEP,
          tasks=[comp_build.infra.swarming.task_id],
          timeout="4m")

    return raw_result

  # TODO (kimstephanie): break up test_patch into separate methods for each
  # phase of the build
  def test_patch(self):
    """Runs steps to test CL changes

    Returns:
      RawResult object or None
    """
    if not self.compilator:
      raise self.m.step.InfraFailure('Missing compilator input')

    self.m.chromium_tests.raise_failure_if_cq_depends_footer_exists()

    builder_id, builder_config = self.configure_build()

    # Trigger compilator to compile and build targets with patch
    # Scheduled build inherits current build's project and bucket
    compilator_properties = {
        'orchestrator': {
            'builder_name': self.m.buildbucket.builder_name,
            'builder_group': self.m.builder_group.for_current,
        }
    }
    # Find any test suites that have passed in a previous patchset to skip
    skip_tests_active = ('chromium.skip_successful_tests'
                         in self.m.buildbucket.build.input.experiments)
    test_names_to_skip = self.m.chromium_tests.find_suites_to_skip()
    if skip_tests_active and test_names_to_skip:
      compilator_properties['skip_tests'] = list(test_names_to_skip)

    gitiles_commit = None
    # When this enabled, triggered compilators will not be automatically
    # canceled when the parent orchestrators are canceled.
    self.disable_auto_compilator_cancels = (
        'chromium.compilator_can_outlive_parent'
        in self.m.buildbucket.build.input.experiments)

    # Pass in any input props
    compilator_properties.update(self.m.cq.props_for_child_build)
    self.m.chromium_bootstrap.update_trigger_properties(compilator_properties)
    build = self._trigger_compilator(
        'trigger compilator (with patch)',
        compilator_properties,
        gitiles_commit,
        can_outlive_parent=self.disable_auto_compilator_cancels)

    # Now that we've finished the Orchestrator's bot_update and analyze,
    # let's check on the triggered compilator and display its steps (until
    # it outputs the swarming trigger props).
    build_to_process = self.launch_compilator_watcher(
        build, is_compile_phase=True, with_patch=True)

    comp_output, maybe_raw_result = self.process_sub_build(
        build_to_process, is_compile_phase=True, with_patch=True)

    # Can be either SUCCESS or FAILURE/INFRA_FAILURE result
    # SUCCESS means that there's no swarming tests to trigger
    if maybe_raw_result != None:
      return maybe_raw_result

    self.m.chromium_checkout.checkout_dir = self.m.path['cleanup']

    self.m.cas.download(
        'download src-side deps',
        comp_output.src_side_deps_digest,
        self.m.chromium_checkout.src_dir,
    )

    affected_files = comp_output.affected_files
    targets_config = self.m.chromium_tests.create_targets_config(
        builder_config,
        comp_output.got_revisions,
        self.m.chromium_checkout.src_dir,
        targets_spec_dir=self.m.chromium_checkout.src_dir.join(
            comp_output.src_side_test_spec_dir),
        remote_tests_only=True)
    # This is used to set build properties on swarming tasks
    self.m.chromium.set_build_properties(comp_output.got_revisions)

    # Now let's get all the tests ready with the swarming/skylab trigger info
    # outputed by the compilator
    tests = []
    if comp_output.swarming_props:
      tests = self.process_swarming_props(comp_output.swarming_props,
                                          builder_config, targets_config)
    # Add any skylab tests
    if comp_output.skylab_props:
      tests.extend(
          self.process_skylab_props(comp_output.skylab_props, targets_config))
    self.m.chromium_tests.configure_swarming(
        self.m.tryserver.is_tryserver, builder_group=builder_id.group)

    if skip_tests_active and test_names_to_skip:
      tests = [test for test in tests if test.name not in test_names_to_skip]
      # Give the swarming shards for retries priority
      self.m.chromium_swarming.default_priority = 20

    # crbug/1346781
    # src/third_party/llvm-build/Release+Asserts/bin/llvm-profdata is needed
    # when running code_coverage merge scripts
    if self.m.code_coverage.use_clang_coverage:
      self.m.code_coverage.ensure_clang_coverage_tools()

    if (self.m.code_coverage.using_coverage and
        not comp_output.skipping_coverage):
      self.m.code_coverage.set_is_per_cl_coverage(True)
      self.m.code_coverage.filter_and_set_eligible_files(affected_files)

      output_dir = self.m.chromium_checkout.src_dir.join(
          'out', self.m.chromium.c.build_config_fs)
      self.m.code_coverage.build_dir = output_dir

      self.m.file.ensure_directory('ensure output directory', output_dir)

      # Downloading these binaries can take up to 2 minutes, so start this
      # download in the background.
      all_test_binaries_future = self.m.futures.spawn_immediate(
          lambda: self.m.cas.download(
              'downloading cas digest {}'.format(ALL_TEST_BINARIES_ISOLATE_NAME
                                                ),
              self.m.isolate.isolated_tests[ALL_TEST_BINARIES_ISOLATE_NAME],
              output_dir,
          ))

    trigger_without_patch_compile_early = False
    if ('chromium.pre_retry_shards_without_patch_compile'
        in self.m.buildbucket.build.input.experiments):
      trigger_without_patch_compile_early = True

    def trigger_without_patch_compile_callback(test_suites):
      if self.m.chromium_tests.should_skip_without_patch(
          builder_config, affected_files,
          self.m.chromium_checkout.src_dir.join(
              comp_output.src_side_test_spec_dir)):
        return

      # Trigger another compilator build with the targets needed
      compilator_properties['test_targets'] = list(
          set(t.target_name for t in test_suites))

      if comp_output.override_deps != None:
        compilator_properties['deps_revision_overrides'] = (
            dict(comp_output.override_deps))

      # With can_outlive_parent=True, this means that when the orchestrator
      # is cancelled or infra failed, the triggered compilator won't
      # automatically get cancelled. This is so that orchestrator builds
      # don't have to wait up to 4m waiting for the compilator swarming task
      # to finish cleanly.
      self.without_patch_build = self._trigger_compilator(
          'trigger compilator (without patch)',
          compilator_properties,
          gitiles_commit,
          can_outlive_parent=True)
      self.triggered_without_patch_build_early = True

    if trigger_without_patch_compile_early:
      pre_retry_shards_callback = trigger_without_patch_compile_callback
    else:
      pre_retry_shards_callback = None
    # Trigger and wait for the tests (and process coverage data, if enabled)!
    with self.m.chromium_tests.wrap_chromium_tests(builder_config, tests):
      invalid_test_suites, failing_test_suites = (
          self.m.test_utils.run_tests_with_patch(
              tests,
              retry_failed_shards=builder_config.retry_failed_shards,
              pre_retry_shards_callback=pre_retry_shards_callback,
          ))

      if (tests and self.m.code_coverage.using_coverage and
          not comp_output.skipping_coverage):
        all_test_binaries_future.result()
        self.m.code_coverage.process_coverage_data(tests)

    # Let's check back on the compilator to see the results of the local
    # scripts/tests. The sub_build will only display steps relevant to those
    # local scripts/tests.
    local_tests_sub_build = self.launch_compilator_watcher(
        build, is_compile_phase=False, with_patch=True)
    build_to_process = local_tests_sub_build

    _, local_tests_raw_result = self.process_sub_build(
        build_to_process, is_compile_phase=False, with_patch=True)

    def cancel_without_patch_build(wo_build_id):
      self.m.buildbucket.cancel_build(
          wo_build_id,
          ('Canceling because the parent builder does not need to retry '
           'shards without patch.'),
      )

    if not failing_test_suites:
      if (self.without_patch_build and trigger_without_patch_compile_early and
          not self.disable_auto_compilator_cancels):
        cancel_without_patch_build(self.without_patch_build.id)
      self.m.chromium_swarming.report_stats()
      # There could be exonerated failed tests from FindIt flakes
      self.m.chromium_tests.summarize_test_failures(tests)

      # Checks for new flaky tests are run on both the orchestrator and
      # compilator for CQ builders that have flaky checks enabled.
      if self.m.flakiness.check_for_flakiness:
        new_tests = self.m.flakiness.find_tests_for_flakiness(
            tests, affected_files=affected_files)
        if new_tests:
          result = self.m.chromium_tests.run_tests_for_flakiness(
              builder_config, new_tests)

          # If the swarming checks for flakiness succeed, we'll only need to
          # check for the compilator's failures. On success, None is returned by
          # run_tests_for_flakiness(). Otherwise, aggregate the summaries s.t.
          # all flaky results can be presented to the user.
          if not result:
            return local_tests_raw_result

          if (local_tests_raw_result and
              local_tests_raw_result.status != common_pb.SUCCESS):
            summary = result.summary_markdown
            summary += '\n\n From compilator:\n {}'.format(
                local_tests_raw_result.summary_markdown)
            result.summary_markdown = summary
          return result

      # All of the swarming tests passed, so the final status of the tryjob
      # depends on whether the local tests run by the compilator passed or not.
      return local_tests_raw_result

    # Exit without retry without patch if there were invalid tests or
    # (without patch) should be skipped
    if invalid_test_suites or self.m.chromium_tests.should_skip_without_patch(
        builder_config, affected_files,
        self.m.chromium_checkout.src_dir.join(
            comp_output.src_side_test_spec_dir)):
      if (self.without_patch_build and trigger_without_patch_compile_early and
          not self.disable_auto_compilator_cancels):
        cancel_without_patch_build(self.without_patch_build.id)
      self.handle_failed_with_patch_tests(tests, failing_test_suites)

      summary_markdown = self.m.chromium_tests.format_unrecoverable_failures(
          failing_test_suites, 'with patch')
      if (local_tests_raw_result and
          local_tests_raw_result.status != common_pb.SUCCESS):
        summary_markdown += '\n\n From compilator:\n{}'.format(
            local_tests_raw_result.summary_markdown)

      status = self.m.chromium_tests.determine_build_status_from_tests(
          failing_test_suites, 'with patch')
      return result_pb2.RawResult(
          summary_markdown=summary_markdown, status=status)

    # =====================================================================
    # Now we're going into the without patch phase
    # =====================================================================
    if trigger_without_patch_compile_early:
      # self.without_patch_build should have been set via the callback function
      assert self.without_patch_build is not None
    else:
      # Trigger another compilator build with the targets needed
      compilator_properties['test_targets'] = list(
          set(t.target_name for t in failing_test_suites))

      if comp_output.override_deps != None:
        compilator_properties['deps_revision_overrides'] = (
            dict(comp_output.override_deps))

      self.without_patch_build = self._trigger_compilator(
          'trigger compilator (without patch)',
          compilator_properties,
          gitiles_commit,
          can_outlive_parent=self.disable_auto_compilator_cancels)

    # Display steps of triggered (without patch) compilator until it outputs
    # swarming trigger props for the tests to retrigger without patch
    wo_build_to_process = self.launch_compilator_watcher(
        self.without_patch_build, is_compile_phase=True, with_patch=False)

    comp_output, maybe_raw_result = self.process_sub_build(
        wo_build_to_process, is_compile_phase=True, with_patch=False)

    # FAILURE/INFRA_FAILURE result
    if maybe_raw_result != None:
      self.handle_failed_with_patch_tests(tests, failing_test_suites)
      return maybe_raw_result

    if comp_output.swarming_props:
      self.process_swarming_props(
          comp_output.swarming_props,
          builder_config,
          targets_config,
          tests=tests)
    if comp_output.skylab_props:
      self.process_skylab_props(
          comp_output.skylab_props, targets_config, tests=failing_test_suites)

    # Trigger and wait for the (without patch) tests!
    with self.m.chromium_tests.wrap_chromium_tests(builder_config,
                                                   failing_test_suites):
      self.m.test_utils.run_tests(
          failing_test_suites,
          'without patch',
          sort_by_shard=True)

    # unrecoverable_test_suites are those that passed without a patch, so the
    # failures must be due to the CL
    unrecoverable_test_suites = self.m.chromium_tests.summarize_test_failures(
        tests, retried_without_patch_suites=failing_test_suites)

    self.m.chromium_swarming.report_stats()

    summary_markdown = ''
    final_status = common_pb.SUCCESS

    if unrecoverable_test_suites:
      self.m.chromium_tests.handle_invalid_test_suites(
          unrecoverable_test_suites)
      summary_markdown += self.m.chromium_tests.format_unrecoverable_failures(
          unrecoverable_test_suites, 'with patch')
      final_status = self.m.chromium_tests.determine_build_status_from_tests(
          unrecoverable_test_suites, 'with patch')

    if (local_tests_raw_result and
        local_tests_raw_result.status != common_pb.SUCCESS):
      summary_markdown += '\n\n From compilator:\n{}'.format(
          local_tests_raw_result.summary_markdown)
      if final_status == common_pb.SUCCESS:
        final_status = local_tests_raw_result.status

    return result_pb2.RawResult(
        summary_markdown=summary_markdown, status=final_status)

  def configure_build(self):
    builder_id, builder_config = (
        self.m.chromium_tests_builder_config.lookup_builder())

    self.m.chromium_tests.configure_build(builder_config, test_only=True)

    # Set self.m.chromium.c.compile_py.compiler to empty string so that
    # prepare_checkout() does not attempt to run ensure_goma()
    self.m.chromium.c.compile_py.compiler = ''
    self.m.chromium_tests.report_builders(builder_config)
    self.m.chromium_tests.print_link_to_results()

    self.m.chromium.apply_config('trybot_flavor')
    return builder_id, builder_config

  def _trigger_compilator(self,
                          step_name,
                          compilator_properties,
                          gitiles_commit,
                          can_outlive_parent=False):
    if self.m.led.launched_by_led:
      build = self._trigger_compilator_led_build(step_name,
                                                 compilator_properties)

    else:
      request = self.m.buildbucket.schedule_request(
          builder=self.compilator,
          swarming_parent_run_id=self.m.swarming.task_id,
          properties=compilator_properties,
          gitiles_commit=gitiles_commit,
          tags=self.m.buildbucket.tags(**{
              'hide-in-gerrit': 'pointless',
              'skip-rety-in-gerrit': 'pointless',
          }),
          can_outlive_parent=can_outlive_parent,
      )

      build = self.m.buildbucket.schedule([request], step_name=step_name)[0]

    self.current_compilator_buildbucket_id = build.id

    return build

  def _trigger_compilator_led_build(self, step_name, compilator_properties):
    with self.m.step.nest(step_name):
      builder_name = 'luci.{project}.{bucket}:{builder}'.format(
          project=self.m.buildbucket.build.builder.project,
          bucket=self.m.led.shadowed_bucket,
          builder=self.compilator)
      # By default, the priority of the tasks will be increased by 10, but
      # since this builder runs as part of CQ for the recipe repos, we want
      # the builds to run at regular priority
      led_result = self.m.led('get-builder', '-adjust-priority', '0',
                              builder_name)

      gerrit_change = self.m.tryserver.gerrit_change
      gerrit_cl_url = (
          'https://{gerrit_host}/c/{project}/+/{change}/{patchset}'.format(
              gerrit_host=gerrit_change.host,
              project=gerrit_change.project,
              change=gerrit_change.change,
              patchset=gerrit_change.patchset,
          ))

      led_result = led_result.then('edit-cr-cl', gerrit_cl_url)
      # We used to set `is_experimental` to true, but the chromium recipe
      # currently uses that to deprioritize swarming tasks, which results in
      # very slow runtimes for the led task. Because this recipe blocks the
      # build.git CQ, we decided the tradeoff to run these edited recipes in
      # production mode instead would be better.
      led_result = led_result.then('edit', '-exp', 'false')

      properties_edit_args = []
      for prop, value in compilator_properties.items():
        properties_edit_args.extend(
            ['-p', prop + '=' + self.m.json.dumps(value)])
      led_result = led_result.then('edit', *properties_edit_args)

      if self.m.chromium_bootstrap.exe.HasField('cas'):
        led_result = led_result.then(
            'edit-payload', '-cas-ref', '{digest_hash}/{size_bytes}'.format(
                digest_hash=self.m.chromium_bootstrap.exe.cas.digest.hash,
                size_bytes=self.m.chromium_bootstrap.exe.cas.digest.size_bytes,
            ))

      led_result = led_result.then('launch', '-resultdb', 'on',
                                   '-bound-to-parent')

      return self.m.buildbucket.get(led_result.launch_result.build_id)

  def launch_compilator_watcher(self, build, is_compile_phase, with_patch):
    """Launches a sub_build displaying a subset of the Compilator's steps

    Args:
      build (Build): buildbucket Build of triggered Compilator
      is_compile_phase (bool): whether the Orchestrator is currently waiting
        for remote test props or not
      with_patch (bool): whether the Orchestrator is currently using a patch or
        not

    Returns:
      Build proto of sub_build
    """
    if not self.compilator_watcher_pkg:
      git_revision = self.compilator_watcher_git_revision
      cipd_pkg = 'infra/chromium/compilator_watcher/${platform}'
      if git_revision:
        version = 'git_revision:{}'.format(git_revision)
      else:
        version = 'latest'
      self.compilator_watcher_pkg = self.m.cipd.ensure_tool(cipd_pkg, version)

    sub_build = build_pb2.Build()
    sub_build.CopyFrom(build)
    cmd = [
        self.compilator_watcher_pkg,
        '--',
        '-compilator-id',
        build.id,
    ]
    outputted_trigger_tag = 'chromium.outputted_trigger_properties'
    if is_compile_phase:
      cmd.extend(['-end-step-tag', outputted_trigger_tag])
    else:
      cmd.extend(['-start-step-tag', outputted_trigger_tag])

    if with_patch:
      name = 'compilator steps (with patch)'
    else:
      name = 'compilator steps (without patch)'
    build_url = self.m.buildbucket.build_url(build_id=build.id)
    try:
      ret = self.m.step.sub_build(name, cmd, sub_build)
      ret.presentation.links['compilator build: ' + str(build.id)] = build_url
      return ret.step.sub_build
    except self.m.step.StepFailure as e:
      ret = self.m.step.active_result
      ret.presentation.links['compilator build: ' + str(build.id)] = build_url
      # The step_text will inherit the markdown of the sub_build, including
      # raw html tags. But we also mirror it in this build's summary, so no
      # need to display it twice.
      ret.presentation.step_text = None
      sub_build = ret.step.sub_build
      if not sub_build:
        raise self.m.step.InfraFailure('sub_build missing from step') from e
      return sub_build

  def process_sub_build(self, sub_build, is_compile_phase, with_patch):
    """Processes the sub_build's status and output properties

    Args:
      sub_build (Build): completed sub_build that displayed Compilator steps
      is_compile_phase (bool): whether the Orchestrator is currently waiting
        for remote test props or not
      with_patch (bool): whether the Orchestrator is currently using a patch or
        not

    Returns tuple of:
      CompilatorOutputProps or None
      RawResult object or None
    """
    # This condition should be rare as swarming only propagates
    # cancelations from parent -> child
    if sub_build.status == common_pb.CANCELED:
      if self.m.runtime.in_global_shutdown:
        return None, result_pb2.RawResult(
            status=common_pb.CANCELED, summary_markdown=BUILD_CANCELED_SUMMARY)
      raise self.m.step.InfraFailure(
          'Compilator was canceled before the parent orchestrator was canceled.'
      )

    # Update the invocation so tests will inherit source position from the
    # orchestrator's invocation
    if sub_build.output.HasField('gitiles_commit'):
      self.m.chromium_checkout.update_rdb_invocation(
          gitiles_commit=sub_build.output.gitiles_commit)
    else:
      # If the compilator didn't have a commit position we want to know about
      # it but not fail the build
      self.m.step.empty(
          'compilator gitiles_commit missing',
          status='FAILURE',
          raise_on_failure=False)

    swarming_prop_key = 'swarming_trigger_properties'
    skylab_prop_key = 'skylab_trigger_properties'
    if is_compile_phase and (swarming_prop_key in sub_build.output.properties or
                             skylab_prop_key in sub_build.output.properties):
      output_props = MessageToDict(sub_build.output.properties)
      got_revisions = {k: v for k, v in output_props.items() if 'got_' in k}
      # TODO (kimstephanie): Replace src_side_.* with
      # output_props.get() in a separate CL
      src_side_deps_digest = None
      src_side_test_spec_dir = None
      if 'src_side_deps_digest' in output_props:
        src_side_deps_digest = output_props['src_side_deps_digest']
        src_side_test_spec_dir = output_props['src_side_test_spec_dir']

      affected_files = None
      if 'affected_files' in output_props:
        affected_files = self.m.chromium_checkout.format_affected_file_paths(
            output_props['affected_files']['first_100'])

      comp_output = CompilatorOutputProps(
          swarming_props=output_props.get(swarming_prop_key),
          skylab_props=output_props.get(skylab_prop_key),
          override_deps=output_props.get('override_deps'),
          got_revisions=got_revisions,
          affected_files=affected_files,
          src_side_deps_digest=src_side_deps_digest,
          src_side_test_spec_dir=src_side_test_spec_dir,
          skipping_coverage=output_props.get('skipping_coverage'),
      )
      return comp_output, None

    if not with_patch and sub_build.status == common_pb.SUCCESS:
      raise self.m.step.InfraFailure(
          'Missing swarming_trigger_properties and skylab_trigger_properties '
          'from without patch compilator')

    # Could be a "No analyze required" success, compilator compile failure,
    # compilator local tests failure, or some other infra failure
    return None, result_pb2.RawResult(
        status=sub_build.status, summary_markdown=sub_build.summary_markdown)

  def process_swarming_props(self,
                             swarming_props,
                             builder_config,
                             targets_config,
                             tests=None):
    """Read isolate hashes swarming_props content and download command lines

    Args:
      swarming_props (dict): contains information about swarming tests to
        trigger
      builder_config (BuilderConfig): configuration for the Orchestrator
        builder
      targets_config (TargetsConfig): configuration for the tests' targets
      tests (list(Test)): Test objects to update with swarming info. If None,
        new Test objects will be created.
    Returns:
      List of Test objects with swarming info
    """
    swarming_digest = swarming_props['swarming_command_lines_digest']
    swarming_cwd = swarming_props['swarming_command_lines_cwd']

    swarm_hashes = dict(swarming_props['swarm_hashes'])
    self.m.isolate.set_isolated_tests(swarm_hashes)

    if not tests:
      tests = [
          t for t in targets_config.all_tests
          if t.uses_isolate and t.target_name in self.m.isolate.isolated_tests
      ]

    # CLs that update the test command lines are actually blocked from
    # running a without patch step, so the command lines aren't actually
    # updated to anything different.
    self.m.chromium_tests.download_command_lines_for_tests(
        tests,
        builder_config,
        swarming_command_lines_digest=swarming_digest,
        swarming_command_lines_cwd=swarming_cwd)
    return tests

  def process_skylab_props(
      self,
      skylab_props,
      targets_config,
      tests=None,
  ):
    if not tests:
      tests = [
          t for t in targets_config.all_tests
          if t.is_skylabtest and t.target_name in skylab_props.keys()
      ]

    for t in tests:
      target_properties = skylab_props[t.target_name]
      t.exe_rel_path = target_properties.get("exe_rel_path", '')
      t.lacros_gcs_path = target_properties.get("lacros_gcs_path", '')
      t.tast_expr_file = target_properties.get("tast_expr_file", '')
    return tests

  def handle_failed_with_patch_tests(self, tests, failing_test_suites):
    """Summarizes test stats, flakiness, and test failures

    Args:
      tests: Test objects of completed tests
      failing_test_suites: Test objects of failed tests. Subset of 'tests' arg
    """
    self.m.chromium_swarming.report_stats()
    self.m.chromium_tests.summarize_test_failures(tests)
    self.m.chromium_tests.handle_invalid_test_suites(failing_test_suites)
