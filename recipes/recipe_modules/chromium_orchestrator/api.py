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

RTS_SUMMARY = '''

Tests were run with RTS. If failures are suspected to be caused by RTS skipped
tests this can be disabled by adding this footer to your CL message:

    Disable-Rts: True

See https://bit.ly/regression-test-selection for more information on RTS
'''


@attrs()
class CompilatorOutputProps:
  """Contains output properties from the triggered Compilator build

  Attributes:
    swarming_props: Dict containing swarming information to trigger tests
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

  swarming_props = attrib(mapping[str, ...])
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

    inverted_rts_experiment = ('chromium_rts.inverted_rts' in
                               self.m.buildbucket.build.input.experiments)
    inverted_rts_bail_early_experiment = (
        'chromium_rts.inverted_rts_bail_early' in
        self.m.buildbucket.build.input.experiments)

    if self.m.chromium_rts.is_rts_footer_disabled():
      log_step = self.m.step.empty('log rts disabled by footer')
      log_step.presentation.properties['rts_footer_disabled'] = True

    # The chromium_rts.inverted_rts attempts to run only the tests that were
    # skipped as part of a previous compatible Quick Run build
    reuseable_quick_run_build = None
    reuseable_compilator_build = None
    if (inverted_rts_bail_early_experiment and self.m.cq.active and
        self.m.cq.run_mode == self.m.cq.QUICK_DRY_RUN):
      # Prevent this build from getting reused for a later DRY_RUN or FULL_RUN
      self.m.cq.allow_reuse_for(self.m.cq.QUICK_DRY_RUN)
      return

    # Allow the bail early experiment to run inverted since this is only used
    # on experimental builders. Otherwise we only want FULL_RUN to run the
    # inverted
    if inverted_rts_bail_early_experiment or (
        inverted_rts_experiment and self.m.cq.active and
        self.m.cq.run_mode == self.m.cq.FULL_RUN
    ) and not self.m.chromium_rts.is_rts_footer_disabled():
      reuseable_quick_run_build = self.find_compatible_quick_run_build()

      if reuseable_quick_run_build:
        reuseable_compilator_build = self.get_compilator_from_build(
            reuseable_quick_run_build)
        if reuseable_compilator_build:
          log_step = self.m.step.empty('log reused builds')
          log_step.presentation.properties['reused_quick_run_build'] = str(
              reuseable_quick_run_build.id)
          log_step.presentation.properties['reused_compilator_build'] = str(
              reuseable_compilator_build.id)

    if inverted_rts_bail_early_experiment and not reuseable_compilator_build:
      # Prevent this build from getting reused for a later DRY_RUN or FULL_RUN
      self.m.cq.allow_reuse_for(self.m.cq.QUICK_DRY_RUN)
      return None

    builder_id, builder_config, rts_setting = self.configure_build(
        inverted_rts=bool(reuseable_compilator_build))

    # Trigger compilator to compile and build targets with patch
    # Scheduled build inherits current build's project and bucket
    compilator_properties = {
        'orchestrator': {
            'builder_name': self.m.buildbucket.builder_name,
            'builder_group': self.m.builder_group.for_current,
        }
    }
    if rts_setting:
      compilator_properties['rts_setting'] = rts_setting

    gitiles_commit = None

    if reuseable_compilator_build:
      build_to_process = reuseable_compilator_build
      if build_to_process.output.HasField('gitiles_commit'):
        self.m.buildbucket.set_output_gitiles_commit(
            build_to_process.output.gitiles_commit)
      else:
        # If the compilator didn't have a commit position we want to know about
        # it but not fail the build
        self.m.step.empty(
            'compilator gitiles_commit missing',
            status='FAILURE',
            raise_on_failure=False)
    else:
      # Pass in any RTS mode input props
      compilator_properties.update(self.m.cq.props_for_child_build)
      self.m.chromium_bootstrap.update_trigger_properties(compilator_properties)
      build = self._trigger_compilator('trigger compilator (with patch)',
                                       compilator_properties, gitiles_commit)

      # Now that we've finished the Orchestrator's bot_update and analyze,
      # let's check on the triggered compilator and display its steps (until
      # it outputs the swarming trigger props).
      build_to_process = self.launch_compilator_watcher(
          build, is_swarming_phase=True, with_patch=True)

    comp_output, maybe_raw_result = self.process_sub_build(
        build_to_process, is_swarming_phase=True, with_patch=True)

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
        isolated_tests_only=True)
    self.check_for_non_swarmed_isolated_tests(targets_config.all_tests)
    # This is used to set build properties on swarming tasks
    self.m.chromium.set_build_properties(comp_output.got_revisions)

    # Now let's get all the tests ready with the swarming trigger info
    # outputed by the compilator
    tests = self.process_swarming_props(
        comp_output.swarming_props,
        builder_config,
        targets_config,
        include_inverted_rts=bool(reuseable_compilator_build))

    tests = self.m.chromium_tests.setup_quickrun_tests(
        tests, rts_setting, bool(reuseable_compilator_build))

    if reuseable_compilator_build and not tests:
      # No invertible tests were found and we have a successful build
      return result_pb2.RawResult(status=common_pb.SUCCESS)

    self.m.chromium_tests.configure_swarming(
        self.m.tryserver.is_tryserver, builder_group=builder_id.group)

    # crbug/1346781
    # src/third_party/llvm-build/Release+Asserts/bin/llvm-profdata is needed
    # when running code_coverage merge scripts
    if self.m.code_coverage.use_clang_coverage:
      clang_update_script = self.m.chromium_checkout.src_dir.join(
          'tools', 'clang', 'scripts', 'update.py')
      args = ['python3', clang_update_script, '--package', 'coverage_tools']
      self.m.step(
          'run tools/clang/scripts/update.py',
          args,
      )

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

    # Trigger and wait for the tests (and process coverage data, if enabled)!
    with self.m.chromium_tests.wrap_chromium_tests(builder_config, tests):
      invalid_test_suites, failing_test_suites = (
          self.m.test_utils.run_tests_with_patch(
              tests,
              retry_failed_shards=builder_config.retry_failed_shards))

      if (self.m.code_coverage.using_coverage and
          not comp_output.skipping_coverage):
        # Grab the coverage from the reused build
        if reuseable_compilator_build:
          self.download_previous_code_coverage(reuseable_quick_run_build)

        all_test_binaries_future.result()
        self.m.code_coverage.process_coverage_data(tests)

    # Led compilator build has already been collected with the local tests
    # finished
    if not reuseable_compilator_build:
      # Let's check back on the compilator to see the results of the local
      # scripts/tests. The sub_build will only display steps relevant to those
      # local scripts/tests.
      local_tests_sub_build = self.launch_compilator_watcher(
          build, is_swarming_phase=False, with_patch=True)
      build_to_process = local_tests_sub_build

    _, local_tests_raw_result = self.process_sub_build(
        build_to_process, is_swarming_phase=False, with_patch=True)

    if not failing_test_suites:
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
      self.handle_failed_with_patch_tests(tests, failing_test_suites)

      summary_markdown = self.m.chromium_tests._format_unrecoverable_failures(
          failing_test_suites, 'with patch')
      if (local_tests_raw_result and
          local_tests_raw_result.status != common_pb.SUCCESS):
        summary_markdown += '\n\n From compilator:\n{}'.format(
            local_tests_raw_result.summary_markdown)
      if any(
          test.is_rts or test.is_inverted_rts for test in invalid_test_suites):
        summary_markdown += RTS_SUMMARY

      return result_pb2.RawResult(
          summary_markdown=summary_markdown, status=common_pb.FAILURE)

    # =====================================================================
    # Now we're going into the without patch phase
    # =====================================================================

    # Trigger another compilator build with the targets needed
    compilator_properties['swarming_targets'] = list(
        set(t.target_name for t in failing_test_suites))

    if comp_output.override_deps != None:
      compilator_properties['deps_revision_overrides'] = (
          dict(comp_output.override_deps))

    wo_build = self._trigger_compilator('trigger compilator (without patch)',
                                        compilator_properties, gitiles_commit)

    # Display steps of triggered (without patch) compilator until it outputs
    # swarming trigger props for the tests to retrigger without patch
    wo_build_to_process = self.launch_compilator_watcher(
        wo_build, is_swarming_phase=True, with_patch=False)

    comp_output, maybe_raw_result = self.process_sub_build(
        wo_build_to_process, is_swarming_phase=True, with_patch=False)

    # FAILURE/INFRA_FAILURE result
    if maybe_raw_result != None:
      self.handle_failed_with_patch_tests(tests, failing_test_suites)
      return maybe_raw_result

    self.process_swarming_props(
        comp_output.swarming_props, builder_config, targets_config, tests=tests)

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
      summary_markdown += self.m.chromium_tests._format_unrecoverable_failures(
          unrecoverable_test_suites, 'with patch')
      final_status = common_pb.FAILURE

    if (local_tests_raw_result and
        local_tests_raw_result.status != common_pb.SUCCESS):
      summary_markdown += '\n\n From compilator:\n{}'.format(
          local_tests_raw_result.summary_markdown)
      if final_status == common_pb.SUCCESS:
        final_status = local_tests_raw_result.status

    if unrecoverable_test_suites and any(test.is_rts or test.is_inverted_rts
                                         for test in unrecoverable_test_suites):
      summary_markdown += RTS_SUMMARY

    return result_pb2.RawResult(
        summary_markdown=summary_markdown, status=final_status)

  def configure_build(self, inverted_rts=False):
    builder_id, builder_config = (
        self.m.chromium_tests_builder_config.lookup_builder())

    rts_setting = self.m.chromium_tests.get_quickrun_options(
        builder_config, inverted_rts=inverted_rts)
    self.m.chromium_tests.configure_build(
        builder_config, rts_setting, test_only=True)

    # Set self.m.chromium.c.compile_py.compiler to empty string so that
    # prepare_checkout() does not attempt to run ensure_goma()
    self.m.chromium.c.compile_py.compiler = ''
    self.m.chromium_tests.report_builders(builder_config)
    self.m.chromium_tests.print_link_to_results()

    self.m.chromium.apply_config('trybot_flavor')
    return builder_id, builder_config, rts_setting

  def _trigger_compilator(self, step_name, compilator_properties,
                          gitiles_commit):
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

  def launch_compilator_watcher(self, build, is_swarming_phase, with_patch):
    """Launches a sub_build displaying a subset of the Compilator's steps

    Args:
      build (Build): buildbucket Build of triggered Compilator
      is_swarming_phase (bool): whether the Orchestrator is currently waiting
        for swarming props or not
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
    if is_swarming_phase:
      cmd.append('-get-swarming-trigger-props')
    else:
      cmd.append('-get-local-tests')

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
      sub_build = ret.step.sub_build
      if not sub_build:
        raise self.m.step.InfraFailure('sub_build missing from step') from e
      return sub_build

  def process_sub_build(self, sub_build, is_swarming_phase, with_patch):
    """Processes the sub_build's status and output properties

    Args:
      sub_build (Build): completed sub_build that displayed Compilator steps
      is_swarming_phase (bool): whether the Orchestrator is currently waiting
        for swarming props or not
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
    if is_swarming_phase and swarming_prop_key in sub_build.output.properties:
      output_props = MessageToDict(sub_build.output.properties)

      got_revisions = {k: v for k, v in output_props.items() if 'got_' in k}

      affected_files = None
      if 'affected_files' in output_props:
        affected_files = self.m.chromium_checkout.format_affected_file_paths(
            output_props['affected_files']['first_100'])

      # TODO (kimstephanie): Replace src_side_.* with
      # output_props.get() in a separate CL
      src_side_deps_digest = None
      src_side_test_spec_dir = None
      if 'src_side_deps_digest' in output_props:
        src_side_deps_digest = output_props['src_side_deps_digest']
        src_side_test_spec_dir = output_props['src_side_test_spec_dir']

      comp_output = CompilatorOutputProps(
          swarming_props=output_props[swarming_prop_key],
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
          'Missing swarming_trigger_properties from without patch '
          'compilator')

    # Could be a "No analyze required" success, compilator compile failure,
    # compilator local tests failure, or some other infra failure
    return None, result_pb2.RawResult(
        status=sub_build.status, summary_markdown=sub_build.summary_markdown)

  def process_swarming_props(self,
                             swarming_props,
                             builder_config,
                             targets_config,
                             tests=None,
                             include_inverted_rts=False):
    """Read isolate hashes swarming_props content and download command lines

    Args:
      swarming_props (dict): contains information about swarming tests to
        trigger
      builder_config (BuilderConfig): configuration for the Orchestrator
        builder
      targets_config (TargetsConfig): configuration for the tests' targets
      tests (list(Test)): Test objects to update with swarming info. If None,
        new Test objects will be created.
      include_inverted_rts (bool): attempts to retrieve the inverted rts
        command lines with the non-inverted commands
    Returns:
      List of Test objects with swarming info
    """
    swarming_digest = swarming_props['swarming_command_lines_digest']
    swarming_rts_command_digest = swarming_props.get(
        'swarming_rts_command_lines_digest')
    swarming_inverted_rts_command_digest = swarming_props.get(
        'swarming_inverted_rts_command_lines_digest'
    ) if include_inverted_rts else None
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
        swarming_rts_command_digest=swarming_rts_command_digest,
        swarming_inverted_rts_command_digest=(
            swarming_inverted_rts_command_digest),
        swarming_command_lines_cwd=swarming_cwd)
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

  def check_for_non_swarmed_isolated_tests(self, tests):
    for t in tests:
      if t.uses_isolate and not t.runs_on_swarming:
        raise self.m.step.StepFailure(
            '{} is an isolated test but is not swarmed.'.format(t.target_name))

  def find_compatible_quick_run_build(self):
    """Finds a Quick Run build that can be reused for the current build

    Returns:
      A single build who's compilator should be reuseable
    """
    equivalent_key = self.m.cq.equivalent_cl_group_key
    predicate = builds_service_pb2.BuildPredicate(
        builder=self.m.buildbucket.build.builder,
        tags=self.m.buildbucket.tags(
            cq_equivalent_cl_group_key=str(equivalent_key)),
        create_time=common_pb.TimeRange(
            start_time=timestamp_pb2.Timestamp(
                # Look back 1 day
                seconds=self.m.buildbucket.build.create_time.ToSeconds() -
                60 * 60 * 24)),
        status=common_pb.SUCCESS,
    )
    if predicate.builder.builder.endswith('-inverse-fyi'):
      predicate.builder.builder = predicate.builder.builder[:-len('-inverse-fyi'
                                                                 )]
    builds = self.m.buildbucket.search(
        predicate, step_name='find successful Quick Runs')

    builds = [
        build for build in builds
        if 'rts_was_used' in build.output.properties and
        build.output.properties['rts_was_used']
    ]

    return builds[0] if builds else None

  def get_compilator_from_build(self, quick_run_build):
    """Finds the compilator build from a given Quick Run build

    Args:
      quick_run_build (Build): The compatible Quick Run build to get the
        compilator build from

    Returns:
      The compilator build to be reused
    """
    predicate = builds_service_pb2.BuildPredicate(
        child_of=quick_run_build.id,
        status=common_pb.SUCCESS,
        builder=self.m.buildbucket.build.builder,
    )
    predicate.builder.builder = self.compilator

    builds = self.m.buildbucket.search(
        predicate, step_name='get compilator build')

    if not builds:
      return None

    # If more than one compilator is found the earliest would be '(with patch)'
    compilator_build = min(builds, key=lambda b: b.start_time.ToSeconds())
    return compilator_build

  def download_previous_code_coverage(self, quick_run_build):
    if ('coverage_gs_bucket' not in quick_run_build.output.properties or
        'merged_profdata_gs_paths' not in quick_run_build.output.properties):
      return
    bucket = quick_run_build.output.properties['coverage_gs_bucket']
    for path in quick_run_build.output.properties['merged_profdata_gs_paths']:
      # Make the _unit coverage data match the unit regex used in code coverage
      if '/%s_unit/' % self.m.buildbucket.builder_name in path:
        dest = self.m.profiles.profile_dir().join(
            constants.QUICK_RUN_UNIT_PROFDATA)
        step_name = 'download Quick Run unit coverage from GS'
      else:
        dest = self.m.profiles.profile_dir().join(
            constants.QUICK_RUN_OVERALL_PROFDATA)
        step_name = 'download Quick Run overall coverage from GS'
      self.m.gsutil.download(bucket, path, dest, name=step_name)
