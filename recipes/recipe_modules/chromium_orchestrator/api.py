# Copyright 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import re

from google.protobuf.json_format import MessageToDict
from recipe_engine import recipe_api
from recipe_engine.config_types import Path
from recipe_engine.engine_types import thaw

from PB.go.chromium.org.luci.buildbucket.proto import build as build_pb2
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb
from PB.recipe_engine import result as result_pb2
from RECIPE_MODULES.build.attr_utils import attrib, attrs, mapping, sequence
from RECIPE_MODULES.build.chromium_tests.api import (
    ALL_TEST_BINARIES_ISOLATE_NAME)

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
    skipping_coverage: Whether coverage is being skipped. The compilator
      determines this by checking the len of affected eligible files.
    build_dir: Name (str) of the build directory used by the compilator.
  """

  swarming_props = attrib(mapping[str, ...], default=None)
  skylab_props = attrib(mapping[str, ...], default=None)
  override_deps = attrib(mapping[str, str], default=None)
  got_revisions = attrib(mapping[str, str])
  affected_files = attrib(sequence[str], default=None)
  src_side_deps_digest = attrib(str, default=None)
  skipping_coverage = attrib(bool, default=None)
  build_dir = attrib(str, default=None)


class ChromiumOrchestratorApi(recipe_api.RecipeApi):

  def __init__(self, properties, **kwargs):
    super().__init__(**kwargs)
    self.compilator = properties.compilator
    self.compilator_watcher_git_revision = (
        properties.compilator_watcher_git_revision)
    self.compilator_watcher_polling_timeout_sec = (
        properties.compilator_watcher_polling_timeout_sec)

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

      task_id = comp_build.infra.swarming.task_id
      if not task_id:
        task_id = comp_build.infra.backend.task.id.id
      # crbug.com/1271287#c22
      # Wait for compilator task overhead to complete
      self.m.swarming.collect(
          name=COMPILATOR_SWARMING_TASK_COLLECT_STEP, tasks=[task_id], timeout="4m")

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
        },
        'skip_analysis_reasons':
            list(self.m.chromium_bootstrap.skip_analysis_reasons),
    }

    # Forward on any non-recipe_engine recipe module properties. The
    # recipe_engine module properties are often synthesized based on the current
    # build, so shouldn't be forwarded. For non-module properties, it is more
    # likely that the property would actually be consumed by something besides
    # the compilator recipe (led, bootstrapper), and therefore more likely to
    # have unexpected effects, so we should be more selective.

    # (?!recipe_engine) - negative lookahead of recipe_engine
    recipe_module_property_re = re.compile(r'\$(?!recipe_engine)[^/]+/[^/]+')
    should_forward_by_property = {
        # This property is used during compilation so should be forwarded
        'xcode_build_version': True,
        # This property is for the orchestrator and wouldn't be used by the
        # compilator
        '$build/chromium_orchestrator': False,
        # This property is set by the bootstrapper and intended only for the
        # build that it was set for
        '$build/chromium_bootstrap': False,
        # This is used for tests, the compilator runs any local tests
        'recipe_engine/resultdb/test_presentation': True,
    }
    for p, value in self.m.properties.items():
      should_forward = should_forward_by_property.get(p, None)
      if should_forward is None:
        should_forward = recipe_module_property_re.fullmatch(p)
      if should_forward:
        compilator_properties[p] = thaw(value)

    compilator_properties.update(self.m.cv.props_for_child_build)
    self.m.chromium_bootstrap.update_trigger_properties(compilator_properties)

    build = self._trigger_compilator('trigger compilator (with patch)',
                                     compilator_properties)

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

    # TODO(crbug.com/330782554) - chromium_checkout.checkout_dir should be
    # explicitly set as `None`, and api.path.checkout_dir should remain unset.
    self.m.chromium_checkout.set_paths(self.m.path.cleanup_dir,
                                       self.m.gclient.c.solutions[0].name)
    checkout_dir = self.m.chromium_checkout.checkout_dir
    source_dir = self.m.chromium_checkout.source_dir
    build_dir = self.m.chromium.default_build_dir(source_dir)
    self.m.code_coverage.source_dir = source_dir
    self.m.code_coverage.build_dir = build_dir
    self.m.profiles.source_dir = source_dir

    self.m.cas.download(
        'download src-side deps',
        comp_output.src_side_deps_digest,
        source_dir,
    )

    affected_files = comp_output.affected_files
    targets_config = self.m.chromium_tests.create_targets_config(
        builder_config,
        comp_output.got_revisions,
        source_dir,
        build_dir,
        targets_spec_dir=self.m.chromium_tests.get_targets_spec_dir(
            source_dir, builder_config),
        remote_tests_only=True)
    # This is used to set build properties on swarming tasks
    self.m.chromium.set_build_properties(comp_output.got_revisions)

    # Now let's get all the tests ready with the swarming/skylab trigger info
    # outputed by the compilator
    tests = []
    if comp_output.swarming_props:
      variant_digests = comp_output.swarming_props.get(
          'swarming_command_lines_variant_digests')
      if variant_digests:
        step_res = self.m.step.empty(
            'record swarming command lines variant digests')
        step_res.presentation.properties[
            'swarming_command_lines_variant_digests'] = thaw(variant_digests)

      tests = self.process_swarming_props(
          source_dir,
          build_dir,
          comp_output.swarming_props,
          builder_config,
          targets_config,
      )
    # Add any skylab tests
    if comp_output.skylab_props:
      tests.extend(
          self.process_skylab_props(comp_output.skylab_props, targets_config))

    self.m.chromium_tests.configure_swarming(
        self.m.tryserver.is_tryserver, builder_group=builder_id.group)

    # crbug/1346781
    # src/third_party/llvm-build/Release+Asserts/bin/llvm-profdata is needed
    # when running code_coverage merge scripts
    if self.m.code_coverage.use_clang_coverage:
      self.m.code_coverage.ensure_clang_coverage_tools()

    if (self.m.code_coverage.using_coverage and
        not comp_output.skipping_coverage):
      self.m.code_coverage.set_is_per_cl_coverage(True)
      self.m.code_coverage.filter_and_set_eligible_files(affected_files)

      self.m.file.ensure_directory('ensure output directory', build_dir)

      # Downloading these binaries can take up to 2 minutes, so start this
      # download in the background.
      all_test_binaries_future = self.m.futures.spawn_immediate(
          lambda: self.m.cas.download(
              'downloading cas digest {}'.format(ALL_TEST_BINARIES_ISOLATE_NAME
                                                ),
              self.m.isolate.isolated_tests[ALL_TEST_BINARIES_ISOLATE_NAME],
              build_dir,
          ))

    # Trigger and wait for the tests (and process coverage data, if enabled)!
    with self.m.chromium_tests.wrap_chromium_tests(
        checkout_dir, source_dir, build_dir, tests=tests):
      invalid_test_suites, failing_test_suites = (
          self.m.test_utils.run_tests_with_patch(
              checkout_dir,
              source_dir,
              build_dir,
              tests,
              retry_failed_shards=builder_config.retry_failed_shards,
              include_utr_instruction=True,
          ))

      if (tests and self.m.code_coverage.using_coverage and
          not comp_output.skipping_coverage):
        all_test_binaries_future.result()
        self._move_downloaded_binaries(build_dir)
        self.m.code_coverage.process_coverage_data(tests)

      self.m.chromium_rts.evaluate_rts(
          source_dir / 'out' / comp_output.build_dir, tests)

    # Let's check back on the compilator to see the results of the local
    # scripts/tests. The sub_build will only display steps relevant to those
    # local scripts/tests.
    local_tests_sub_build = self.launch_compilator_watcher(
        build, is_compile_phase=False, with_patch=True)
    build_to_process = local_tests_sub_build

    _, local_tests_raw_result = self.process_sub_build(
        build_to_process, is_compile_phase=False, with_patch=True)

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
              checkout_dir, source_dir, build_dir, new_tests)

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
        builder_config, self.m.chromium_checkout.source_dir, affected_files,
        self.m.chromium_tests.get_targets_spec_dir(
            self.m.chromium_checkout.source_dir, builder_config)):
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
    # Trigger another compilator build with the targets needed
    compilator_properties['test_targets'] = list(
        set(t.target_name for t in failing_test_suites))

    if comp_output.override_deps != None:
      compilator_properties['deps_revision_overrides'] = (
          dict(comp_output.override_deps))

    wo_build = self._trigger_compilator('trigger compilator (without patch)',
                                        compilator_properties)

    # Display steps of triggered (without patch) compilator until it outputs
    # swarming trigger props for the tests to retrigger without patch
    wo_build_to_process = self.launch_compilator_watcher(
        wo_build, is_compile_phase=True, with_patch=False)

    comp_output, maybe_raw_result = self.process_sub_build(
        wo_build_to_process, is_compile_phase=True, with_patch=False)

    # FAILURE/INFRA_FAILURE result
    if maybe_raw_result != None:
      self.handle_failed_with_patch_tests(tests, failing_test_suites)
      return maybe_raw_result

    if comp_output.swarming_props:
      self.process_swarming_props(
          source_dir,
          build_dir,
          comp_output.swarming_props,
          builder_config,
          targets_config,
          tests=tests)
    if comp_output.skylab_props:
      self.process_skylab_props(
          comp_output.skylab_props, targets_config, tests=failing_test_suites)

    # Trigger and wait for the (without patch) tests!
    with self.m.chromium_tests.wrap_chromium_tests(
        checkout_dir, source_dir, build_dir, tests=failing_test_suites):
      output_without_patch_property = self.m.step.empty(
          'record ran_tests_without_patch')
      output_without_patch_property.presentation.properties[
          'ran_tests_without_patch'] = True
      self.m.test_utils.run_tests(
          checkout_dir,
          source_dir,
          build_dir,
          failing_test_suites,
          'without patch',
          sort_by_shard=True,
          include_utr_instruction=True)

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

    self.m.chromium_tests.report_builders(builder_config)
    self.m.chromium_tests.print_link_to_results()

    self.m.chromium.apply_config('trybot_flavor')
    return builder_id, builder_config

  _EXPERIMENTS_TO_FORWARD = {
      'chromium.enable_cleandead',
      'luci.buildbucket.run_in_turboci',
      # TODO(crbug.com/40280175)
      'chromium_checkout.expand_submodules',
  }

  def _trigger_compilator(self,
                          step_name,
                          compilator_properties,
                          can_outlive_parent=None):
    experiments_to_forward = (
        self._EXPERIMENTS_TO_FORWARD
        | self.m.chromium_rts.get_experiment_names())
    experiments = {
        e: True
        for e in self.m.buildbucket.build.input.experiments
        if e in experiments_to_forward
    }

    if self.m.led.launched_by_led:
      build = self._trigger_compilator_led_build(step_name,
                                                 compilator_properties,
                                                 experiments)

    else:
      tags = [
          tag for tag in self.m.buildbucket.build.tags
          if tag.key.startswith('cq_')
      ]
      tags.append(common_pb.StringPair(key='hide-in-gerrit', value='pointless'))
      tags.append(
          common_pb.StringPair(key='skip-retry-in-gerrit', value='pointless'))
      request = self.m.buildbucket.schedule_request(
          builder=self.compilator,
          swarming_parent_run_id=self.m.swarming.task_id,
          properties=compilator_properties,
          tags=tags,
          can_outlive_parent=can_outlive_parent,
          experiments=experiments,
      )

      build = self.m.buildbucket.schedule([request], step_name=step_name)[0]

    self.current_compilator_buildbucket_id = build.id

    return build

  def trigger_led_recipe_bundled_build(self,
                                       priority,
                                       builder,
                                       experiments=None,
                                       properties=None,
                                       gerrit_change=None):
    """Launches the provided builder from the same project using led

    Args:
      priority: The swarming priority to use for the task
      builder: The name of the builder to launch
      experiments: A dictionary of experiments and their values
      properties: A dictionary of properties and their values to apply to the
        build
      gerrit_change: A buildbucket.common.GerritChange of the CL to test. Will
        use the current CL-under-test if not specified.
    Returns:
      The LedResult from launching the build
    """
    builder_name = 'luci.{project}.{bucket}:{builder}'.format(
        project=self.m.buildbucket.build.builder.project,
        bucket=self.m.led.shadowed_bucket,
        builder=builder)
    get_cmd = ['get-builder', '-adjust-priority', priority]
    if experiments:
      for e, val in experiments.items():
        get_cmd.extend(['-experiment', f'{e}={"true" if val else "false"}'])
    get_cmd.append(builder_name)
    led_result = self.m.led(*get_cmd)

    if not gerrit_change:
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

    if properties:
      properties_edit_args = []
      for prop, value in properties.items():
        properties_edit_args.extend(
            ['-p', prop + '=' + self.m.json.dumps(value)])
      led_result = led_result.then('edit', *properties_edit_args)

    if self.m.chromium_bootstrap.exe.HasField(
        'cas') or self.m.led.rbe_cas_input:
      digest_hash = (
          self.m.chromium_bootstrap.exe.cas.digest.hash
          if self.m.chromium_bootstrap.exe.HasField('cas') else
          self.m.led.rbe_cas_input.digest.hash)
      size_bytes = (
          self.m.chromium_bootstrap.exe.cas.digest.size_bytes
          if self.m.chromium_bootstrap.exe.HasField('cas') else
          self.m.led.rbe_cas_input.digest.size_bytes)
      led_result = led_result.then(
          'edit-payload', '-cas-ref', '{digest_hash}/{size_bytes}'.format(
              digest_hash=digest_hash,
              size_bytes=size_bytes,
          ))

    return led_result.then('launch', '-resultdb', 'on', '-bound-to-parent')

  def _trigger_compilator_led_build(
      self,
      step_name,
      compilator_properties,
      experiments,
  ):
    with self.m.step.nest(step_name):
      led_result = self.trigger_led_recipe_bundled_build(
          priority=0,
          builder=self.compilator,
          experiments=experiments,
          properties=compilator_properties)

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

    if self.compilator_watcher_polling_timeout_sec:
      cmd.extend([
          '-compilator-polling-timeout-sec',
          str(self.compilator_watcher_polling_timeout_sec),
      ])

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
    # Copy any instructions from the invocation since these steps are already
    # tagged from the sub build
    self.m.repro_instructions.process_sub_build(sub_build)

    # This condition should be rare as swarming only propagates
    # cancelations from parent -> child
    if sub_build.status == common_pb.CANCELED:
      if self.m.runtime.in_global_shutdown:
        return None, result_pb2.RawResult(
            status=common_pb.CANCELED, summary_markdown=BUILD_CANCELED_SUMMARY)
      raise self.m.step.InfraFailure(
          'Compilator was canceled before the parent orchestrator was canceled.'
      )

    # Update step instructions from the compilator
    self.m.repro_instructions.update_invocation_instructions()

    # Update the invocation so tests will inherit source position from the
    # orchestrator's invocation
    if sub_build.output.HasField('gitiles_commit'):
      self.m.chromium_checkout.update_rdb_source_spec_invocation(
          gitiles_commit=sub_build.output.gitiles_commit)

      # Set the output gitiles commit ASAP before any skylab tests are
      # triggered, so the source position can be uploaded with the skylab
      # test results (if any).
      self.m.buildbucket.set_output_gitiles_commit(
          sub_build.output.gitiles_commit)
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
      src_side_deps_digest = output_props.get('src_side_deps_digest')

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
          skipping_coverage=output_props.get('skipping_coverage'),
          build_dir=output_props.get('build_dir'),
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
                             source_dir: Path,
                             build_dir: Path,
                             swarming_props,
                             builder_config,
                             targets_config,
                             tests=None):
    """Read isolate hashes swarming_props content and download command lines

    Args:
      source_dir: The path to the top-level repo.
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
    variant_digests = swarming_props.get(
        'swarming_command_lines_variant_digests')
    swarming_cwd = swarming_props['swarming_command_lines_cwd']

    swarm_hashes = dict(swarming_props['swarm_hashes'])
    self.m.isolate.set_isolated_tests(swarm_hashes)

    if not tests:
      tests = [
          t for t in targets_config.all_tests
          if t.uses_isolate and t.target_name in self.m.isolate.isolated_tests
          # The compilator will not compile all tests that can be skipped but we
          # still want to include tests that are disabled for display
          or (t.isolate_target and not t.is_enabled)
      ]

    # CLs that update the test command lines are actually blocked from
    # running a without patch step, so the command lines aren't actually
    # updated to anything different.
    self.m.chromium_tests.download_command_lines_for_tests(
        source_dir,
        build_dir,
        tests,
        builder_config,
        swarming_command_lines_digest=swarming_digest,
        swarming_command_lines_cwd=swarming_cwd,
        swarming_command_lines_variant_digests=variant_digests)
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
          if t.runs_on_skylab and t.target_name in skylab_props.keys()
      ]

    for t in tests:
      target_properties = skylab_props[t.target_name]
      t.exe_rel_path = target_properties.get("exe_rel_path", '')
      t.lacros_gcs_path = target_properties.get("lacros_gcs_path", '')
      t.build_output_dir = target_properties.get('build_output_dir', '')
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

  def _move_downloaded_binaries(self, build_dir):
    compilator_out_dir = build_dir / 'out'
    if not self.m.path.exists(compilator_out_dir):
      return

    # In Siso without_bytes mode, compiled test binaries are isolated remotely in CAS
    # with paths relative to out/<compilator_build_dir>/...
    # Iterate through all subdirectories in compilator_out_dir and move their
    # contents up into build_dir so downstream coverage scripts can find them.
    with self.m.step.nest('relocate downloaded CAS test binaries'):
      subdirs = self.m.file.listdir('list out dir', compilator_out_dir)
      conflicting_binaries = []
      for comp_build_dir in subdirs:
        if not self.m.path.isdir(comp_build_dir):
          continue
        files_to_move = self.m.file.listdir('list comp build dir',
                                            comp_build_dir)
        for f in files_to_move:
          dest = build_dir / self.m.path.basename(f)
          if self.m.path.exists(dest):
            conflicting_binaries.append(self.m.path.basename(f))
            continue
          self.m.file.move('move %s' % self.m.path.basename(f), f, dest)
      self.m.file.rmtree('clean up out dir', compilator_out_dir)

      if conflicting_binaries:
        self.m.step.empty(
            'unexpected conflicting binaries %s from compilator' %
            ', '.join(sorted(conflicting_binaries)),
            status='FAILURE',
            step_text=(
                'Files %s already exist in %s; skipped moving from out/.' %
                (', '.join(sorted(conflicting_binaries)), build_dir)),
            raise_on_failure=False)
