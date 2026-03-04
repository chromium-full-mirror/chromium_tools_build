# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import recipe_api
from recipe_engine.config_types import Path

from RECIPE_MODULES.build import chromium_types
from RECIPE_MODULES.build import chromium_tests_builder_config as ctbc
from RECIPE_MODULES.build.chromium_tests import api as chromium_tests
from RECIPE_MODULES.build.code_coverage.api import MAX_CANDIDATE_FILES

from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb
from PB.recipe_engine import result as result_pb2

ORCHESTRATOR_ALL_TARGET_NAME = 'infra/orchestrator:orchestrator_all'
ORCHESTRATOR_RUNTIME_DEPS_FILE = 'orchestrator_all.runtime_deps'


class ChromiumCompilatorApi(recipe_api.RecipeApi):

  def __init__(self, properties, **kwargs):
    super().__init__(**kwargs)
    self._properties = properties

  def compilator_steps(self, properties=None):
    properties = properties or self._properties
    try:
      return self._compilator_steps_impl(properties)
    finally:
      if self.m.runtime.in_global_shutdown:
        # pylint: disable=lost-exception
        # Compilator builds can experience a variety of exceptions when its
        # swarming tasks are killed, depending on where in the recipe it was
        # killed.
        # The important part is that the build status is CANCELED.
        return result_pb2.RawResult(
            status=common_pb.CANCELED,
            summary_markdown=self._global_shutdown_summary_markdown(
                self._create_orchestrator_milo_link(
                    self.m.buildbucket.swarming_parent_run_id,
                    self.m.buildbucket.backend_hostname)))

  def _compilator_steps_impl(self, properties):
    self.m.tryserver.require_is_tryserver()

    with self.m.chromium.chromium_layout():
      orchestrator = properties.orchestrator.builder_name
      builder_group = properties.orchestrator.builder_group
      if not orchestrator or not builder_group:
        raise self.m.step.InfraFailure(
            'this builder is intended to be triggered by its corresponding'
            ' orchestrator builder, it is an error to trigger it directly')
      orch_builder_id = chromium_types.BuilderId.create_for_group(
          builder_group, orchestrator)

      _, orch_builder_config = (
          self.m.chromium_tests_builder_config.lookup_builder(
              builder_id=orch_builder_id))

      self.m.chromium_tests.report_builders(orch_builder_config)

      # This should eventually be replaced with a property that controls compile
      # with RTS independent of compilator
      self.m.chromium_rts.rts_model = properties.rts_model

      self.m.chromium_tests.configure_build(orch_builder_config)
      self.m.chromium.apply_config('trybot_flavor')

      # Assumes that the compilator is in the same project and bucket as the
      # orchestrator
      bb_id = self.m.buildbucket.build.builder
      builder_full_name = (
          f'{bb_id.project}/{bb_id.bucket}/{orch_builder_id.builder}')

      # test_targets implies that this compilator build must be compiled
      # without a patch so that the orchestrator can retry these tests
      # without patch
      if properties.test_targets:
        update_result, build_dir, targets_config = self.m.chromium_tests.prepare_checkout(
            orch_builder_config,
            timeout=3600,
            no_fetch_tags=True,
            enforce_fetch=True,
            patch=False,
            runhooks_suffix='without patch',
            turboci_source_check_id=chromium_tests.WITHOUT_PATCH_SOURCE_CHECK_ID
        )

        # code coverage is ignored for without patch steps, but compile will
        # error if there is no files_to_instrument.txt file
        if self.m.code_coverage.using_coverage:
          self.m.code_coverage.instrument([])

        # properties.test_targets should only be targets required for
        # isolated swarming tests or skylab tests
        test_suites = [
            t for t in targets_config.all_tests
            if t.target_name in properties.test_targets and
            (t.runs_on_swarming or t.runs_on_skylab)
        ]

        self.m.chromium_tests.turboci.create_build_check(
            chromium_tests.WITHOUT_PATCH_BUILD_CHECK_ID,
            chromium_tests.WITHOUT_PATCH_SOURCE_CHECK_ID,
            builder_full_name=builder_full_name)

        raw_result, execution_info = (
            self.m.chromium_tests.build_and_isolate_failing_tests(
                build_dir,
                orch_builder_id,
                orch_builder_config,
                test_suites,
                update_result,
                'without patch',
                additional_compile_targets=[ORCHESTRATOR_ALL_TARGET_NAME],
                turboci_build_check_id=chromium_tests
                .WITHOUT_PATCH_BUILD_CHECK_ID))
      else:
        update_result, build_dir, targets_config = (
            self.m.chromium_tests.prepare_checkout(
                orch_builder_config,
                timeout=3600,
                no_fetch_tags=True,
                turboci_source_check_id=chromium_tests.SOURCE_CHECK_ID))

        turboci_analyze_check_id = self.m.chromium_tests.turboci.create_build_check(
            chromium_tests.BUILD_CHECK_ID,
            chromium_tests.SOURCE_CHECK_ID,
            create_analyze_check=True,
            builder_full_name=builder_full_name)

        raw_result, task = self.m.chromium_tests.build_affected_targets(
            orch_builder_id,
            orch_builder_config,
            update_result,
            build_dir,
            targets_config,
            isolate_output_files_for_coverage=True,
            additional_compile_targets=[ORCHESTRATOR_ALL_TARGET_NAME],
            skip_analysis_reasons=properties.skip_analysis_reasons,
            turboci_build_check_id=chromium_tests.BUILD_CHECK_ID,
            turboci_analyze_check_id=turboci_analyze_check_id,
        )
        execution_info = task.swarming_execution_info
        test_suites = task.test_suites
        update_result = task.update_result
        build_dir = task.build_dir

        # In case a without patch build is needed later, output the needed
        # deps override
        if update_result.patch_root != update_result.source_root:
          patch_root = update_result.patch_root.name
          deps_overrides = {}
          deps_revision_dict = update_result.manifest[patch_root]
          deps_overrides[patch_root] = deps_revision_dict['revision']
          output_deps_override = self.m.step.empty('output override_deps')
          output_deps_override.presentation.properties['override_deps'] = (
              deps_overrides)

      if raw_result and raw_result.status != common_pb.SUCCESS:
        return raw_result

      checkout_dir = update_result.checkout_dir
      source_dir = update_result.source_root.path
      if any((t.runs_on_swarming or t.runs_on_skylab) and t.is_enabled
             for t in test_suites):
        affected_files_to_archive = []
        # If properties.test_targets exist, it means this build is doing a
        # "without patch" so there's no affected files to archive
        if (not properties.test_targets and
            not self.m.code_coverage.skipping_coverage):
          deleted_files = self._get_deleted_files(source_dir,
                                                  task.affected_files)
          affected_files_to_archive = [
              # In case this is a windows compilator
              str(source_dir / f).replace('/', self.m.path.sep)
              for f in task.affected_files
              # If the affected file is deleted, don't attempt to archive it or
              # else you'll get a file not found error
              if f not in deleted_files
          ]
        self._archive_src_side_deps(
            orch_builder_config,
            checkout_dir,
            source_dir,
            build_dir,
            affected_files_to_archive,
        )

        if any(t.runs_on_swarming and t.is_enabled for t in test_suites):
          # Isolate the tests first so the Orchestrator can trigger them asap
          trigger_properties = execution_info.ensure_command_lines_archived(
              self.m.chromium_tests).as_trigger_prop()

          properties_step = self.m.step('swarming trigger properties', [])
          properties_step.presentation.properties[
              'swarming_trigger_properties'] = trigger_properties
          properties_step.presentation.logs[
              'swarming_trigger_properties'] = self.m.json.dumps(
                  trigger_properties, indent=2)

        if any(t.runs_on_skylab and t.is_enabled for t in test_suites):
          skylab_tests = [t for t in test_suites if t.runs_on_skylab]
          skylab_trigger_properties = (
              self.m.chromium_tests._get_skylab_trigger_properties(skylab_tests)
          )
          properties_step = self.m.step('skylab trigger properties', [])
          properties_step.presentation.properties[
              'skylab_trigger_properties'] = skylab_trigger_properties
          properties_step.presentation.logs[
              'skylab_trigger_properties'] = self.m.json.dumps(
                  skylab_trigger_properties, indent=2)

        post_step = self.m.step('post output trigger properties', [])
        post_step.presentation.tags['chromium.outputted_trigger_properties'] = (
            'outputted swarming and/or skylab trigger properties')

      local_tests = [t for t in test_suites if t.runs_locally]
      if local_tests:
        test_runner = self.m.chromium_tests.create_test_runner(
            checkout_dir,
            source_dir,
            build_dir,
            local_tests,
            suffix='with patch',
        )
        with self.m.chromium_tests.wrap_chromium_tests(
            checkout_dir, source_dir, build_dir, tests=local_tests):
          raw_result = test_runner()
          if raw_result and raw_result.status != common_pb.SUCCESS:
            return raw_result

        # check for new flaky tests on successful run w/ patch
        if self.m.flakiness.check_for_flakiness:
          new_tests = self.m.flakiness.find_tests_for_flakiness(
              local_tests, affected_files=task.affected_files)
          if new_tests:
            return self.m.chromium_tests.run_tests_for_flakiness(
                checkout_dir, source_dir, build_dir, new_tests)

      return raw_result

  def _archive_src_side_deps(
      self,
      orch_builder_config: ctbc.BuilderConfig,
      checkout_dir: Path,
      source_dir: Path,
      build_dir: Path,
      affected_files,
  ):
    """Archives src-side deps that the Orchestrator needs to run tests/coverage.

    Affected files is also needed by the orchestrator to run code coverage

    Args:
      checkout_dir: The directory where the checkout was performed.
      source_dir: The path to the top-level repo.
      affected_files (list): List of absolute string paths
    """
    with self.m.step.nest('archive src-side dep paths') as nested_step:
      dep_paths = set(self._get_src_side_dep_paths(source_dir, build_dir))
      # Affected files are needed in when coverage is collected
      dep_paths.update(affected_files)
      # Include the targets spec files so that the orchestrator can create the
      # test objects for running remote tests
      targets_spec_dir = self.m.chromium_tests.get_targets_spec_dir(
          source_dir, orch_builder_config)
      dep_paths.update(
          str(targets_spec_dir / f)
          for f in orch_builder_config.targets_spec_files.values())
      dep_paths = sorted(dep_paths)

      # We need the files relative to the checkout dir so they can get download
      # correctly on the orchestrator. And the .isolate file inherits the cwd of
      # the file itself, so create the file using a tmp name that should be
      # sufficiently unique to this build.
      isolate_file = self.m.path.join(
          source_dir, '%s_archive_deps.isolate' % self.m.swarming.task_id)
      rel_dep_paths = []
      for p in dep_paths:
        rel_dep_paths.append(self.m.path.relpath(p, source_dir))
      self.m.isolate.write_isolate_file(isolate_file, rel_dep_paths)
      digest = self.m.isolate.isolate('archive src-side deps', isolate_file)
      self.m.file.remove('rm %s' % isolate_file, isolate_file)

      targets_spec_dir = self.m.chromium_tests.get_targets_spec_dir(
          source_dir, orch_builder_config)
      relative_test_spec_dir = self.m.path.relpath(targets_spec_dir, source_dir)
      # On windows compilators, this would use a `\\` path separator instead of
      # a `/` that the linux orchestrators need to construct Paths
      relative_test_spec_dir = relative_test_spec_dir.replace(
          self.m.path.sep, '/')

      nested_step.properties['src_side_test_spec_dir'] = (
          relative_test_spec_dir)
      nested_step.properties['src_side_deps_digest'] = digest
      nested_step.logs['dep paths'] = self.m.json.dumps(dep_paths, indent=2)

  def _get_src_side_dep_paths(self, source_dir: Path, build_dir: Path):
    """Get src-side paths to archive.

    The chromium compile step writes which src-side deps to archive.
    The orchestrator build will use the CAS hash to download these deps to run
    tests and code coverage.

    Returns:
      List of string paths
    """
    dep_paths = set()
    runtime_deps_file = build_dir / ORCHESTRATOR_RUNTIME_DEPS_FILE
    paths = (
        self.m.file.read_text('read orchestrator_all.runtime_deps',
                              runtime_deps_file).rstrip().split('\n'))
    for path in paths:
      # Paths written in these files look like '../../testing/X.py' relative
      # to the output dir
      file_path = self.m.path.relpath(build_dir / path, source_dir)
      file_path = source_dir / file_path

      # Path can be a regex pattern
      if "*" in str(file_path):
        paths = self.m.file.glob_paths('get files that match pattern',
                                       source_dir, str(file_path))
        dep_paths.update([str(p) for p in paths])
      else:
        dep_paths.add(str(file_path))
    return list(dep_paths)

  def _get_deleted_files(self, source_dir, affected_files):
    deleted_files = []
    for f in affected_files:
      path = source_dir / f
      # In case this is a windows compilator
      path = str(path).replace('/', self.m.path.sep)

      if not self.m.path.exists(path):
        deleted_files.append(f)
    return deleted_files

  def _create_orchestrator_milo_link(self, swarming_task_id, host):
    return 'https://luci-milo.appspot.com/swarming/task/{}?server={}'.format(
        swarming_task_id, host)

  def _global_shutdown_summary_markdown(self, parent_build_url):
    message = (
        'Parent orchestrator [build]({}) ended, causing this build to be '
        'canceled.')
    return message.format(parent_build_url)
