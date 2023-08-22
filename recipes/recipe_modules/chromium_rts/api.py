# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from google.protobuf import timestamp_pb2
from recipe_engine import recipe_api

from PB.go.chromium.org.luci.buildbucket.proto \
  import builds_service as builds_service_pb2
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb
from RECIPE_MODULES.build.code_coverage import constants

_DISABLE_RTS_FOOTER = 'Disable-Rts'


class ChromiumRtsApi(recipe_api.RecipeApi):
  """A module for interacting with rts."""

  def is_rts_footer_disabled(self):
    disabled = False
    if self.m.tryserver.is_tryserver:
      footer_vals = self.m.tryserver.get_footer(_DISABLE_RTS_FOOTER)
      if footer_vals:
        disabled = footer_vals[-1].lower() == 'true'
    return disabled

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

  def get_compilator_from_build(self, quick_run_build, compilator):
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
    predicate.builder.builder = compilator

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
