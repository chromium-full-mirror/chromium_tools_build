# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from google.protobuf import timestamp_pb2
from recipe_engine import recipe_api

from PB.go.chromium.org.luci.buildbucket.proto \
  import builds_service as builds_service_pb2
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb
from RECIPE_MODULES.build.chromium_tests_builder_config import try_spec
from RECIPE_MODULES.build.code_coverage import constants

_DISABLE_RTS_FOOTER = 'Disable-Rts'


class ChromiumRtsApi(recipe_api.RecipeApi):
  """A module for interacting with rts."""

  def __init__(self, *args, **kwargs):
    super().__init__(*args, **kwargs)

    # A string indicating which RTS model to use regression test selection.
    # None will disable RTS (bit.ly/chromium-rts)
    self._rts_setting = None

    # A float from (0 to 1] indicating what change recall rts should aim for, 0
    # being the fastest and 1 being the safest, and typically between .9 and 1
    self._rts_recall = None

    # A bool indicated the inverse of a previous RTS run is being run
    self._inverted_rts = False

    self._reuseable_compilator_build = None
    self._reuseable_rts_build = None

  @property
  def rts_setting(self) -> str:
    return self._rts_setting

  @rts_setting.setter
  def rts_setting(self, value: str) -> None:
    self._rts_setting = value

  @property
  def rts_recall(self) -> float:
    return self._rts_recall

  @rts_recall.setter
  def rts_recall(self, value: float) -> None:
    self._rts_recall = value

  @property
  def inverted_rts(self) -> bool:
    return self._inverted_rts

  @inverted_rts.setter
  def inverted_rts(self, value: bool) -> None:
    self._inverted_rts = value

  def get_reuseable_compilator_build(self, compilator):
    """Searches for a compatible reuseable RTS enabled build for inverse RTS

    Args:
      compilator (str): The name of the orchestrator builder's compilator

    Returns:
      A reuseable compilator build to use for an inverted rts build.
    """
    inverted_rts_experiment = ('chromium_rts.inverted_rts'
                               in self.m.buildbucket.build.input.experiments)
    inverted_rts_bail_early_experiment = (
        'chromium_rts.inverted_rts_bail_early'
        in self.m.buildbucket.build.input.experiments)

    if self._is_rts_footer_disabled():
      log_step = self.m.step.empty('log rts disabled by footer')
      log_step.presentation.properties['rts_footer_disabled'] = True

    # The chromium_rts.inverted_rts attempts to run only the tests that were
    # skipped as part of a previous compatible Quick Run build
    self._reuseable_rts_build = None
    self._reuseable_compilator_build = None
    if (inverted_rts_bail_early_experiment and self.m.cq.active and
        self.m.cq.run_mode == self.m.cq.QUICK_DRY_RUN):
      # Prevent this build from getting reused for a later DRY_RUN or FULL_RUN
      self.m.cq.allow_reuse_for(self.m.cq.QUICK_DRY_RUN)
      return

    # Allow the bail early experiment to run inverted since this is only used
    # on experimental builders. Otherwise we only want FULL_RUN to run the
    # inverted
    if inverted_rts_bail_early_experiment or (
        inverted_rts_experiment and self.m.cq.active and self.m.cq.run_mode
        == self.m.cq.FULL_RUN) and not self._is_rts_footer_disabled():
      self._reuseable_rts_build = self._find_compatible_rts_build()

      if self._reuseable_rts_build:
        self._reuseable_compilator_build = self._get_compilator_from_build(
            self._reuseable_rts_build, compilator)
        if self._reuseable_compilator_build:
          log_step = self.m.step.empty('log reused builds')
          log_step.presentation.properties['reused_quick_run_build'] = str(
              self._reuseable_rts_build.id)
          log_step.presentation.properties['reused_compilator_build'] = str(
              self._reuseable_compilator_build.id)

    if inverted_rts_bail_early_experiment and not self._reuseable_compilator_build:
      # Prevent this build from getting reused for a later DRY_RUN or FULL_RUN
      self.m.cq.allow_reuse_for(self.m.cq.QUICK_DRY_RUN)

    self._inverted_rts = bool(self._reuseable_compilator_build)

    return self._reuseable_compilator_build

  def _is_rts_footer_disabled(self):
    disabled = False
    if self.m.tryserver.is_tryserver:
      footer_vals = self.m.tryserver.get_footer(_DISABLE_RTS_FOOTER)
      if footer_vals:
        disabled = footer_vals[-1].lower() == 'true'
    return disabled

  def _find_compatible_rts_build(self):
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

  def _get_compilator_from_build(self, rts_build, compilator):
    """Finds the compilator build from a given Quick Run build

    Args:
      quick_run_build (Build): The compatible Quick Run build to get the
        compilator build from
      compilator (str): The name of the orchestrator builder's compilator

    Returns:
      The compilator build to be reused
    """
    predicate = builds_service_pb2.BuildPredicate(
        child_of=rts_build.id,
        status=common_pb.SUCCESS,
        builder=self.m.buildbucket.build.builder,
    )
    predicate.builder.builder = compilator

    builds = self.m.buildbucket.search(
        predicate, step_name='get compilator build')

    if not builds:
      return None

    # If more than one compilator is found the earliest would be '(with patch)'
    compilator_build = min(builds, key=lambda b: b.start_time.ToSeconds())
    return compilator_build

  def download_previous_code_coverage(self):
    """Downloads the code coverage for the previously run RTS build"""
    if ('coverage_gs_bucket' not in self._reuseable_rts_build.output.properties
        or 'merged_profdata_gs_paths'
        not in self._reuseable_rts_build.output.properties):
      return
    bucket = self._reuseable_rts_build.output.properties['coverage_gs_bucket']
    for path in self._reuseable_rts_build.output.properties[
        'merged_profdata_gs_paths']:
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

  def setup_tests(self, tests):
    """Sets the given tests up to be run with RTS

    The tests will be setup to run RTS when both the test and the current
    run mode support RTS. This also marks the reuseability of the current build
    for CQ to prevent tests from getting skipped before submission

    Args:
      tests (Test[]): The test objects to have RTS conditionally enabled
    """
    # If we only need to run the tests that were skipped in the last build
    if self._inverted_rts:
      tests = [t for t in tests if t.supports_inverted_rts]
      for test in tests:
        test.is_rts = False
        test.is_inverted_rts = True
    elif self._rts_setting:
      for test in tests:
        test.is_inverted_rts = False
        if test.supports_rts:
          test.is_rts = True

    if any(test.is_rts for test in tests):
      # RTS-enabled builds can't be reused for non-RTS because they are slightly
      # less safe than normal builds
      log_step = self.m.step.empty('RTS was used')
      log_step.presentation.properties['rts_was_used'] = True

      compatible_run_modes = ('chromium_rts.dry_run_rts'
                              in self.m.buildbucket.build.input.experiments)
      if compatible_run_modes:
        self.m.cq.allow_reuse_for(self.m.cq.DRY_RUN, self.m.cq.QUICK_DRY_RUN)
      else:
        self.m.cq.allow_reuse_for(self.m.cq.QUICK_DRY_RUN)
    return tests

  def init_rts_options(self, builder_config):
    """Initialize the rts module settings for the builder config
    """
    self._log_rts_heuristics()
    # TODO(sshrimp): cq.active/cq.run_mode no longer works from the compilator
    # this should go back to using that module when gerrit no longer skips
    # copying tags on reruns and cq.active no longer checks created_by
    run_mode = None
    props = self.m.properties.get('$recipe_engine/cq', None)
    if props:
      run_mode = props.get('run_mode', props.get('runMode'))
    experiment_active = False
    if run_mode == self.m.cq.DRY_RUN:
      experiment_active = ('chromium_rts.dry_run_rts'
                           in self.m.buildbucket.build.input.experiments)

    self._rts_setting = None
    self._rts_recall = None
    use_rts = (
        ((experiment_active or run_mode == self.m.cq.QUICK_DRY_RUN) and
         builder_config.regression_test_selection == try_spec.QUICK_RUN_ONLY) or
        builder_config.regression_test_selection == try_spec.ALWAYS)

    if (use_rts or self._inverted_rts) and not self._is_rts_footer_disabled():
      if ('chromium_rts.experimental_model'
          in self.m.buildbucket.build.input.experiments):
        self._rts_setting = 'rts-ml-chromium'
      else:
        self._rts_setting = 'rts-chromium'

      self._rts_recall = builder_config.regression_test_selection_recall

      step_result = self.m.step('quick run options', [])
      if experiment_active:
        step_result.presentation.step_text = 'RTS was enabled by an experiment'

      step_result.presentation.properties['rts_setting'] = self._rts_setting
      step_result.presentation.links[
          'use_rts: true'] = 'https://bit.ly/chromium-rts'
      step_result.presentation.links['file a bug'] = (
          'https://bugs.chromium.org/p/chromium/issues/entry?'
          'template=Quick%20Run%20Issue')

  def _log_rts_heuristics(self):
    # TODO(https://crbug.com/1445185): Remove this after we have enough data to
    # evaluate these heuristics
    try:
      with self.m.step.nest('log rts heuristics') as presentation:
        gerrit_change = self.m.buildbucket.build.input.gerrit_changes[0]
        reviewers = self.m.gerrit.call_raw_api(
            f'https://{gerrit_change.host}',
            f'/changes/{gerrit_change.change}/reviewers/',
            method='GET',
            name='get reviewer status')
        reviewers = [
            reviewer for reviewer in reviewers
            if reviewer.get('approvals', {}).get('Code-Review') and
            not reviewer.get('approvals', {}).get('Auto-Submit')
        ]
        presentation.logs['reviewers'] = self.m.json.dumps(reviewers, indent=4)
        presentation.properties['reviewers'] = len(reviewers)

        change = self.m.gerrit.get_changes(
            f'https://{gerrit_change.host}',
            query_params=[
                ('change', gerrit_change.change),
            ],
            limit=1)
        has_review_started = False
        if len(change) > 0:
          has_review_started = change[0].get('has_review_started', False)
        presentation.properties['has_review_started'] = has_review_started
    except Exception:
      # This is purely informational, we don't want to fail the build and the
      # abscense of output properties can be used to identify if the heuristic
      # is not available
      pass

  def configure_build(self):
    """Sets the gclient variables to checkout the appropriate RTS model"""
    if self._rts_setting == 'rts-chromium':
      self.m.gclient.c.solutions[0].custom_vars['checkout_rts_model'] = 'True'
    elif self._rts_setting == 'rts-ml-chromium':
      self.m.gclient.c.solutions[0].custom_vars[
          'checkout_rts_experimental_model'] = 'True'

  def mb_args(self):
    """Returns the mb args to generate the RTS filter files"""
    mb_args = []
    if self._rts_setting:
      mb_args += ['--rts', self._rts_setting]

      if self._rts_recall:
        mb_args += ['--rts-target-change-recall', str(self._rts_recall)]
    return mb_args
