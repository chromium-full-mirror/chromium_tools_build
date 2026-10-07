# Copyright 2020 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Binary size analysis for patchsets."""

from __future__ import annotations

import os
import re

from recipe_engine import recipe_api
from recipe_engine.config_types import Path

from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb

from . import constants


def _linkify_filenames(url, filename_map):
  """Replaces placeholders in |url| with values from |filename_map|.

  Args:
    url: String URL with placeholders like '{{foo.bar}}'.
    filename_map: Dict mapping placeholder names to desired values.

  Returns:
    String url with any placeholders present in |filename_map| populated.
  """
  for filename, archived_url in filename_map.items():
    url = url.replace('{{' + filename + '}}', archived_url)
  return url


def _normalize_name(v):
  """Normalizes a string to a logdog stream name.

  The real normalization function is in the infra/infra repo in
  //luci/client/libs/logdog/streamname.py. This is a not so close
  approximation that only works if you don't look too hard (eg: does not
  handle case where first character is illegal).

  Args:
    v: An arbitrary string.

  Returns:
    A string suitable for use as a logdog stream name.
  """
  return re.sub(r'[^0-9A-Za-z:\-\./]', '_', v)


def _parse_gs_zip_path(gs_zip_path):
  """Get (timestamp, revision sha) from gs zip path.

  Args:
    String path like the following:
      'android-binary-size/commit_size_analysis/' +
      '1592001045_551be50f2e3dae7dd1b31522fce7a91374c0efab.zip'

  Returns:
    tuple of (timestamp, revision sha)
  """
  m = re.search(r'.*\/(.*)_(.*)\.zip', gs_zip_path)
  return int(m.group(1)), m.group(2)


class BinarySizeApi(recipe_api.RecipeApi):
  def __init__(self, properties, **kwargs):
    super().__init__(**kwargs)
    self._analyze_targets = list(
      properties.analyze_targets or constants.DEFAULT_ANALYZE_TARGETS
    )
    self.compile_targets = list(
      properties.compile_targets or constants.DEFAULT_COMPILE_TARGETS
    )
    self.results_bucket = (
      properties.results_bucket or constants.RESULTS_GS_BUCKET
    )

    # Path relative to Chromium output directory. Empty when not set via
    # properties, in which case _get_size_config_json() picks a default based
    # on the milestone of the checkout.
    self._size_config_json = properties.size_config_json

    self.arm64_size_config_json = properties.arm64_size_config_json

  def _get_size_config_json(self, source_dir: Path) -> str:
    """Returns the size config JSON path, relative to the output directory.

    Args:
      source_dir: The path to the top-level repo.
    """
    if self._size_config_json:
      return self._size_config_json
    # TODO(crbug.com/532501271): Remove the milestone gating once no release
    # branch older than M157 still runs android-binary-size. See
    # constants.LEGACY_SIZE_CONFIG_JSON.
    milestone = int(self.m.chromium.get_version(source_dir)['MAJOR'])
    if milestone < constants.DEFAULT_SIZE_CONFIG_MILESTONE:
      return constants.LEGACY_SIZE_CONFIG_JSON
    return constants.DEFAULT_SIZE_CONFIG_JSON

  def get_first_committed_ancestor_position(
    self, url, revision, step_name_suffix=None, ancestor_index=1
  ):
    """Gets the commit position of a committed ancestor of a patch.

    Args:
      url: URL of the repo, e.g. https://chromium.googlesource.com/v8/v8
      revision: The git SHA1 of the revision.
      step_name_suffix: Suffix to use in the step name.
      ancestor_index: The number of ancestors we've traversed.

    Returns:
      An integer representing the commit position, or None if not found.
    """
    # Limit to 10 links up the chain, if we still haven't found a committed CL,
    # there must be something wrong.
    if ancestor_index > 10:
      return None
    if not step_name_suffix:
      step_name_suffix = f'patch\'s parent(x{ancestor_index}) revision'
    commit_details = self.m.gitiles.commit_log(
      url, revision, step_name='Commit log for {}'.format(step_name_suffix)
    )
    cp_footer = self.m.tryserver.get_footer(
      constants.COMMIT_POSITION_FOOTER_KEY, patch_text=commit_details['message']
    )
    # A patch's parent may be another CL that hasn't landed yet, so there's
    # no commit position footer yet. If so, go one more link up the chain.
    if not cp_footer:
      return self.get_first_committed_ancestor_position(
        url, commit_details['parents'][0], ancestor_index=ancestor_index + 1
      )
    return int(self.m.commit_position.parse(cp_footer[0])[1])

  def android_binary_size(self, **kwargs):
    """Checks the binary size of Android targets.

    Args:
      **kwargs: Passed through to compare_size().
    """
    gclient_apply_configs = list(kwargs.get('gclient_apply_configs', []))
    if 'checkout_pgo_profiles' not in gclient_apply_configs:
      gclient_apply_configs += ['checkout_pgo_profiles']
    kwargs['gclient_apply_configs'] = gclient_apply_configs
    return self.compare_size(
      binary_size_footer=constants.ANDROID_BINARY_SIZE_FOOTER_KEY,
      diff_func=self._create_diffs_android,
      analysis_func=self.android_size_analysis,
      analysis_warning_statuses={},
      **kwargs,
    )

  def fuchsia_binary_size(self):
    """Checks the binary size of Fuchsia targets."""
    return self.compare_size(
      chromium_config='chromium',
      chromium_apply_configs=['mb'],
      gclient_config='chromium',
      gclient_apply_configs=['fuchsia_arm64'],
      binary_size_footer=constants.FUCHSIA_BINARY_SIZE_FOOTER_KEY,
      diff_func=self._create_diffs_fuchsia,
      analysis_func=self.fuchsia_size_analysis,
      # Fuchsia ignores roller failures, but these should be indicated anyway.
      # See crbug.com/1355914
      analysis_warning_statuses={
        constants.FUCHSIA_ROLLER_WARNING: 'Ignore roller errors for Fuchsia.'
      },
    )

  def compare_size(
    self,
    *,
    chromium_config,
    chromium_apply_configs=(),
    gclient_config,
    gclient_apply_configs=(),
    binary_size_footer,
    diff_func,
    analysis_func,
    analysis_warning_statuses,
    try_gs_analysis=False,
  ):
    """Determines the increase in size caused by the patch under test.

    To do so, this function:
     - syncs with the patch
     - exits early if none of the configured analyze targets were affected.
     - builds the configured compile targets with the patch
     - measures the size of the configured targets with the patch
     - syncs without the patch
     - builds the same targets without the patch
     - measures the size of the configured targets without the patch
     - reapplies the patch and compares the results

    In general, this recipe is responsible only for driving the execution of
    these steps and failing when necessary. The analysis and measurement logic
    for Android is largely in //tools/binary_size in chromium/src, for Fuchsia
    in //build/fuchsia, and for compile size it's in //tools/clang/scripts.

    See http://bit.ly/2up0mcA for more information.

    Args:
      chromium_config: A string containing the name of the chromium
        recipe_module config to use.
      chromium_apply_configs: An optional list of strings containing the names
        of additional chromium recipe_module configs to apply.
      gclient_config: A string containing the name of the gclient
        recipe_module config to use.
      gclient_apply_configs: An optional list of strings containing the names
        of additional gclient recipe_module configs to apply.
      binary_size_footer: A string with the gerrit footer to allow for size
        regressions.
      diff_func: Function that takes (author, review_subject, review_url,
        before_dir, after_dir, results_path, staging_dir) and generates
        diffs in results_path.
      analysis_func: Function that takes a staging_dir and performs a size
        analysis.
      analysis_warning_statuses: Dict of {status_code: int -> message: string}
        items for diff analysis statuses that should only be warnings.
      try_gs_analysis: bool, whether to try to use previously computed size
        results from tip of tree to skip building without patch.
    """
    assert self.m.tryserver.is_tryserver

    # Don't want milestone try builds to use gs analysis. The 'project' field
    # looks like 'chromium-m86'
    is_trunk_builder = (
      self.m.buildbucket.build.builder.project == 'chromium'
      and self.m.buildbucket.build.builder.bucket in ('try', 'try.shadow')
    )

    # Subrepos like V8 may use call builders that use this recipe. Since
    # a CI builder is only set up for chromium/src, if the project is
    # not chromium/src try_gs_analysis should be False.
    gerrit_change_project = self.m.tryserver.gerrit_change.project

    try_gs_analysis = (
      try_gs_analysis
      and gclient_config == 'chromium'
      and is_trunk_builder
      and gerrit_change_project == 'chromium/src'
    )

    with self.m.chromium.chromium_layout():
      self.m.gclient.set_config(gclient_config)
      for gclient_apply_config in gclient_apply_configs:
        self.m.gclient.apply_config(gclient_apply_config)
      self.m.chromium.set_config(chromium_config)
      for chromium_apply_config in chromium_apply_configs:
        self.m.chromium.apply_config(chromium_apply_config)
      self.m.chromium_android.set_config('base_config')

      revision_info = self.m.gerrit.get_revision_info(
        'https://%s' % self.m.tryserver.gerrit_change.host,
        self.m.tryserver.gerrit_change.change,
        self.m.tryserver.gerrit_change.patchset,
      )
      author = revision_info['commit']['author']['email']
      commit_message = revision_info['commit']['message']
      review_subject = revision_info['commit']['subject']
      review_url = self.m.tryserver.gerrit_change_review_url
      is_revert = review_subject.startswith('Revert')
      commit_footers = self.m.tryserver.get_footers(patch_text=commit_message)
      # get_footer returns a dict of footer keys and values.
      has_size_footer = bool(commit_footers.get(binary_size_footer))
      allow_size_regressions = is_revert or has_size_footer

      has_expectations_footer = bool(
        commit_footers.get(constants.SKIP_EXPECTATIONS_FOOTER_KEY)
      )
      allow_expectations_regressions = is_revert or has_expectations_footer

      self.m.chromium_tests.check_builder_cache(
        self.m.chromium_checkout.default_checkout_dir
      )
      gs_zip_path = None
      checkout_kwargs = {'set_output_commit': True}
      if not try_gs_analysis:
        update_result = self.m.chromium_checkout.ensure_checkout(
          **checkout_kwargs
        )
      else:
        patch_parent_revision = revision_info['commit']['parents'][0]['commit']
        gs_zip_path, recent_upload_revision = (
          self._get_recent_tot_analysis_path(patch_parent_revision)
        )
        if gs_zip_path:
          self.m.gclient.c.solutions[0].revision = recent_upload_revision

        try:
          update_result = self.m.chromium_checkout.ensure_checkout(
            **checkout_kwargs,
            # Make sure that the git cache is refreshed with another origin
            # fetch to get a correct diff of the patch
            enforce_fetch=True,
          )
        except self.m.step.StepFailure:
          # CL patch is incompatible with revision used in recently uploaded
          # analysis. Use the most recent trunk commit instead.
          self.m.gclient.c.solutions[0].revision = None
          gs_zip_path = None
          update_result = self.m.chromium_checkout.ensure_checkout(
            **checkout_kwargs
          )

      checkout_dir = update_result.checkout_dir
      source_dir = update_result.source_root.path
      build_dir = self.m.chromium.default_build_dir(source_dir)

      suffix = ' (with patch)'
      self.m.chromium.runhooks(source_dir, build_dir, name='runhooks' + suffix)

      self._clear_failed_expectation_files(build_dir)

      affected_files = self.m.chromium_checkout.get_files_affected_by_patch()
      # README.chromium files are implicit inputs to about:credits which affects binary size.
      # Ensure binary size analysis runs whenever any README.chromium is modified.
      additional_exclusions = {
        f: 'binary_size recipe'
        for f in affected_files
        if f.endswith('README.chromium')
      }
      affected_test_targets, _ = self.m.filter.analyze(
        source_dir,
        build_dir,
        affected_files,
        self._analyze_targets,
        None,
        additional_exclusions=additional_exclusions,
      )
      if not affected_test_targets:
        step_result = self.m.step.active_result
        step_result.presentation.properties[
          constants.PLUGIN_OUTPUT_PROPERTY_NAME
        ] = {
          'listings': [],
          'extras': [],
        }
        return

      staging_dir = self.m.path.mkdtemp('binary-size-trybot')

      # expectations_without_patch_json is never set when using cached reference
      # builds (via use_gs_analysis). This is fine since we expect
      # expectations_without_patch_json to exist only when using base
      # expectation files in different repositiories (e.g. //clank), and in this
      # case use_gs_analysis == False.
      expectations_without_patch_json = None
      with_results_dir, raw_result = self._build_and_measure(
        'with_patch', source_dir, build_dir, staging_dir, analysis_func
      )

      if raw_result and raw_result.status != common_pb.SUCCESS:
        return raw_result

      expectations_with_patch_json = self._get_failed_expectations(
        build_dir, suffix
      )

      if gs_zip_path:
        without_results_dir = self._download_recent_tot_analysis(
          gs_zip_path, staging_dir
        )
      else:
        with self.m.context(cwd=checkout_dir):
          self.m.bot_update.deapply_patch(update_result)

        with self.m.context(cwd=source_dir):
          suffix = ' (without patch)'

          self.m.chromium.runhooks(
            source_dir, build_dir, name='runhooks' + suffix
          )
          without_results_dir, raw_result = self._build_and_measure(
            'without_patch', source_dir, build_dir, staging_dir, analysis_func
          )

          if raw_result and raw_result.status != common_pb.SUCCESS:
            self.m.step.empty(constants.PATCH_FIXED_BUILD_STEP_NAME)
            return None

        expectations_without_patch_json = self._get_failed_expectations(
          build_dir, suffix
        )

        # Re-apply patch so that the diff scripts can be tested via tryjobs.
        # We could build without-patch first to avoid having to apply the patch
        # twice, but it's nicer to fail fast when the patch does not compile.
        suffix = ' (with patch again)'
        with self.m.context(cwd=checkout_dir):
          update_result = self.m.bot_update.ensure_checkout(
            suffix=suffix, patch=True
          )
        self.m.chromium.runhooks(
          source_dir, build_dir, name='runhooks' + suffix
        )

      with self.m.context(cwd=source_dir):
        size_results_path = staging_dir / 'size_results.json'

        diff_func(
          author,
          review_subject,
          review_url,
          source_dir,
          os.path.basename(self._get_size_config_json(source_dir)),
          without_results_dir,
          with_results_dir,
          size_results_path,
          staging_dir,
          affected_files=affected_files,
        )
        expectation_success = self._check_expectations(
          expectations_with_patch_json,
          expectations_without_patch_json,
          allow_expectations_regressions,
        )

        binary_size_result, gerrit_plugin_details = (
          self._check_for_undocumented_increase(
            size_results_path,
            staging_dir,
            allow_size_regressions,
            analysis_warning_statuses,
          )
        )

        try:
          measure_arm64 = False
          if self.arm64_size_config_json and gerrit_plugin_details:
            for listing in gerrit_plugin_details['listings']:
              if listing.get('log_name') == 'resource_sizes_64_log':
                measure_arm64 = (
                  not listing['allowed'] or listing['large_improvement']
                )
                break
            measure_arm64 = (
              measure_arm64 or 'CreateArm64SizeReport' in commit_footers
            )
          if measure_arm64:
            # Create a supersize report for arm64.
            # If gs_zip_path was used:
            #   The output directory contains the with-patch files.
            #   1) Create the with-patch arm64 .size file
            #   2) Perform the diff
            # If gs_zip_path was not used:
            #   The output directory contains the without-patch files (but src in sync'ed to "with patch")
            #   1) Create the without-patch arm64 .size file
            #   2) Re-build the with-patch artifacts
            #   3) Create the with-patch arm64 .size file
            #   4) Perform the diff
            if gs_zip_path:
              with_results_dir = staging_dir / 'with_patch_arm64'
              self.m.file.ensure_directory(
                'mkdir with_patch_arm64', with_results_dir
              )
              self.android_size_analysis_arm64(
                source_dir, build_dir, with_results_dir
              )
            else:
              without_results_dir = staging_dir / 'without_patch_arm64'
              self.m.file.ensure_directory(
                'mkdir without_patch_arm64', without_results_dir
              )
              self.android_size_analysis_arm64(
                source_dir, build_dir, without_results_dir
              )

              with_results_dir, raw_result = self._build_and_measure(
                'with_patch_arm64',
                source_dir,
                build_dir,
                staging_dir,
                self.android_size_analysis_arm64,
              )

              if raw_result and raw_result.status != common_pb.SUCCESS:
                return raw_result

            size_results_path = staging_dir / 'size_results64.json'

            diff_func(
              author,
              review_subject,
              review_url,
              source_dir,
              os.path.basename(self.arm64_size_config_json),
              without_results_dir,
              with_results_dir,
              size_results_path,
              staging_dir,
              affected_files=affected_files,
            )

            _, gerrit_plugin_details_arm64 = (
              self._check_for_undocumented_increase(
                size_results_path,
                staging_dir,
                allow_size_regressions,
                analysis_warning_statuses,
              )
            )
            extras = gerrit_plugin_details_arm64['extras']
            # Prevent extras with the same name as non-arm64 ones.
            # E.g. "APK Breakdown" -> "APK Breakdown (arm64)"
            for extra in extras:
              extra['text'] += ' (arm64)'

            gerrit_plugin_details['extras'] += extras
            gerrit_plugin_details['listings'] += gerrit_plugin_details_arm64[
              'listings'
            ]
        finally:
          if gerrit_plugin_details:
            self.m.step.active_result.presentation.properties[
              constants.PLUGIN_OUTPUT_PROPERTY_NAME
            ] = gerrit_plugin_details

        if not expectation_success:
          raise self.m.step.StepFailure(constants.FAILED_CHECK_MESSAGE)

        if binary_size_result.presentation.status == self.m.step.FAILURE:
          raise self.m.step.StepFailure(
            binary_size_result.presentation.step_text
          )

  def android_size_analysis(
    self,
    source_dir: Path,
    build_dir: Path,
    staging_dir,
  ):
    """Returns the Android command to compute size analysis files.

    Args:
      staging_dir: Staging directory to pass input files and retrieve output
        size analysis files (e.g., .size and size JSON files).
    """
    generator_script = (
      source_dir / 'tools/binary_size/generate_commit_size_analysis.py'
    )
    cmd = [generator_script]
    cmd += [
      '--size-config-json',
      build_dir / self._get_size_config_json(source_dir),
    ]
    cmd += ['--staging-dir', staging_dir]
    cmd += ['--chromium-output-directory', build_dir]
    self.m.step(name='Generate commit size analysis files', cmd=cmd)

  def android_size_analysis_arm64(
    self,
    source_dir: Path,
    build_dir: Path,
    staging_dir,
  ):
    """Runs supersize on the arm64 artifacts."""
    generator_script = (
      source_dir / 'tools/binary_size/generate_commit_size_analysis.py'
    )
    cmd = [generator_script]
    cmd += ['--size-config-json', build_dir / self.arm64_size_config_json]
    cmd += ['--staging-dir', staging_dir]
    cmd += ['--chromium-output-directory', build_dir]
    self.m.step(name='Generate commit size analysis files (arm64)', cmd=cmd)

  def fuchsia_size_analysis(
    self,
    source_dir: Path,
    build_dir: Path,
    staging_dir,
  ):
    """Returns the Fuchsia command to compute size analysis files.

    Args:
      staging_dir: Staging directory to pass input files and retrieve output
        size analysis files (e.g., .size and size JSON files).
    """
    generator_script = source_dir / 'build/fuchsia/binary_sizes.py'
    cmd = [generator_script]
    cmd += ['--build-out-dir', build_dir]

    size_path = source_dir / 'tools/fuchsia/size_tests/fyi_sizes.json'
    cmd += ['--sizes-path', size_path]

    output_file = build_dir / 'plugin.json'
    cmd += ['--size-plugin-json-path', output_file]
    cmd += ['--isolated-script-test-output', staging_dir / 'size_results.json']
    self.m.step(name='Generate commit size analysis files', cmd=cmd)

  def _get_recent_tot_analysis_path(self, patch_parent_revision):
    """Get recent size analysis results path and latest revision.

    Args:
      patch_parent_revision: String, parent revision of patch.

    Returns:
      (results_path: string, latest_revision: string) or (None, None) if no
        recent size analysis results are valid.
    """

    gs_directory = 'android-binary-size/commit_size_analysis/'

    test_data = (
      'android-binary-size/commit_size_analysis/'
      '{}_551be50f2e3dae7dd1b31522fce7a91374c0efab.zip'.format(
        constants.TEST_TIME
      )
    )

    lines = self.m.gsutil.cat(
      'gs://{bucket}/{source}'.format(
        bucket=self.results_bucket, source=gs_directory + 'LATEST'
      ),
      stdout=self.m.raw_io.output_text(add_output_log=True),
      step_test_data=lambda: self.m.raw_io.test_api.stream_output_text(
        test_data
      ),
      name='cat LATEST',
    ).stdout.splitlines()

    # If the LATEST file has blank data, it's likely to have been manually
    # cleared to invalidate the latest gs:// results to indicate that
    # significant binary package restructure has taken place.
    if not lines or not lines[0].strip():
      return None, None

    gs_zip_path = lines[0]
    latest_upload_timestamp, latest_upload_revision = _parse_gs_zip_path(
      gs_zip_path
    )

    # If the most recent upload was created over 2 hours ago, don't use it
    if int(self.m.time.time()) - int(latest_upload_timestamp) > 7200:
      return None, None

    # Check to see if the patch's parent revision is newer than the
    # recently uploaded revision. We can't use the uploaded results
    # in that case.
    url = self.m.gclient.c.solutions[0].url
    uploaded_cp = self.get_first_committed_ancestor_position(
      url, latest_upload_revision, step_name_suffix='uploaded revision'
    )
    patch_cp = self.get_first_committed_ancestor_position(
      url, patch_parent_revision
    )
    if not patch_cp or patch_cp > uploaded_cp:
      return None, None

    return gs_zip_path, latest_upload_revision

  def _download_recent_tot_analysis(self, gs_zip_path, staging_dir):
    local_zip = self.m.path.mkstemp()
    self.m.gsutil.download(
      bucket=self.results_bucket,
      source=gs_zip_path,
      dest=local_zip,
      name='Downloading zip',
    )

    results_dir = staging_dir / 'without_patch_gs'
    self.m.zip.unzip('Unzipping tot analysis', local_zip, results_dir)
    return results_dir

  def _build_and_measure(
    self,
    results_basename,
    source_dir: Path,
    build_dir: Path,
    staging_dir,
    analysis_func,
  ):
    suffix = ' (' + results_basename.replace('_', ' ') + ')'

    raw_result = self.m.chromium_tests.run_mb_and_compile(
      source_dir,
      build_dir,
      self.m.chromium.get_builder_id(),
      self.compile_targets,
      None,
      suffix,
    )

    if raw_result.status != common_pb.SUCCESS:
      return None, raw_result

    results_dir = staging_dir / results_basename
    self.m.file.ensure_directory('mkdir ' + results_basename, results_dir)

    analysis_func(source_dir, build_dir, results_dir)

    return results_dir, None

  def _check_for_undocumented_increase(
    self, results_path, staging_dir, allow_regressions, warning_statuses
  ):
    result_json = self.m.file.read_json(
      constants.RESULT_JSON_STEP_NAME,
      results_path,
      test_data=constants.TEST_RESULT_JSON,
    )
    # Upload files to storage bucket.
    filename_map = {}
    for filename in result_json['archive_filenames']:
      filename_map[filename] = self._archive_artifact(staging_dir, filename)

    step_result = self.m.step.empty(
      constants.RESULTS_STEP_NAME, step_text=result_json['summary']
    )
    logname_map = {}
    for link in result_json['links']:
      if 'lines' in link:
        step_result.presentation.logs[link['name']] = link['lines']
        if 'log_name' in link:
          logname_map[link['log_name']] = self._synthesize_log_link(
            constants.RESULTS_STEP_NAME, link['name']
          )
      else:
        url = _linkify_filenames(link['url'], filename_map)
        step_result.presentation.links[link['name']] = url

    gerrit_plugin_details = result_json.get('gerrit_plugin_details')
    if gerrit_plugin_details:
      for listing in gerrit_plugin_details['listings']:
        if 'log_name' in listing and listing['log_name'] in logname_map:
          listing['url'] = logname_map[listing['log_name']]
      for extra in gerrit_plugin_details['extras']:
        if 'url' in extra:
          url = extra['url']
          url = _linkify_filenames(url, filename_map)
          extra['url'] = url

    if not allow_regressions and result_json['status_code'] != 0:
      warning = warning_statuses.get(result_json['status_code'])
      if warning:
        step_result.presentation.status = self.m.step.WARNING
        step_result.presentation.step_text += '<br/>{}<br/>'.format(warning)
      else:
        step_result.presentation.status = self.m.step.FAILURE
    return step_result, gerrit_plugin_details

  def _synthesize_log_link(self, step_name, log_name):
    normalized_log_name = _normalize_name(log_name)
    normalized_step_name = _normalize_name(step_name)
    logdog = self.m.buildbucket.build.infra.logdog
    url = 'https://{}/logs/{}/{}/+/u/{}/{}'.format(
      logdog.hostname,
      logdog.project,
      logdog.prefix,
      normalized_step_name,
      normalized_log_name,
    )
    return url

  def _create_diffs_android(
    self,
    author,
    review_subject,
    review_url,
    source_dir: Path,
    size_config_json_name: str,
    before_dir,
    after_dir,
    results_path,
    staging_dir,
    affected_files=None,
  ):
    checker_script = (
      source_dir / 'tools/binary_size/trybot_commit_size_checker.py'
    )

    with self.m.context(env={'PYTHONUNBUFFERED': '1'}):
      cmd = [checker_script]
      cmd += ['--author', author]
      cmd += ['--review-subject', review_subject]
      cmd += ['--review-url', review_url]
      cmd += ['--size-config-json-name', size_config_json_name]
      cmd += ['--before-dir', before_dir]
      cmd += ['--after-dir', after_dir]
      cmd += ['--results-path', results_path]
      cmd += ['--staging-dir', staging_dir]
      if affected_files:
        # Set a cap to prevent excessive length.
        for f in affected_files[:20]:
          cmd += ['--changed-file', f]
      self.m.step(name='Generate diffs', cmd=cmd)

  def _create_diffs_fuchsia(
    self,
    author,
    review_subject,
    review_url,
    source_dir: Path,
    size_config_json_name: str,
    before_dir,
    after_dir,
    results_path,
    staging_dir,
    affected_files=None,
  ):
    checker_script = source_dir / 'build/fuchsia/binary_size_differ.py'
    with self.m.context(env={'PYTHONUNBUFFERED': '1'}):
      cmd = [checker_script]
      cmd += ['--before-dir', before_dir]
      cmd += ['--after-dir', after_dir]
      milestone = int(self.m.chromium.get_version(source_dir)['MAJOR'])
      if (
        milestone >= constants.FUCHSIA_AUTHOR_FLOW_MILESTONE
      ):  # pragma: no cover
        cmd += ['--author', author]
      cmd += ['--results-path', results_path]
      self.m.step(name='Generate diffs', cmd=cmd)

  def _archive_artifact(self, staging_dir, filename):
    today = self.m.time.utcnow().date()
    gs_dest = '{}/{}/{}/{}'.format(
      self.m.buildbucket.builder_name,
      today.strftime('%Y/%m/%d'),
      self.m.buildbucket.build.number,
      filename,
    )
    self.m.gsutil.upload(
      source=staging_dir / filename,
      bucket=self.results_bucket,
      dest=gs_dest,
      name='archive ' + filename,
      unauthenticated_url=True,
    )
    return constants.ARCHIVED_URL_FMT.format(
      bucket=self.results_bucket, dest=gs_dest
    )

  def _get_failed_expectations(self, build_dir: Path, suffix):
    with self.m.context(cwd=build_dir):
      checker_script = self.resource('trybot_failed_expectations_checker.py')

      TEST_DATA = lambda: self.m.json.test_api.output(
        {
          'success': True,
          'failed_messages': [],
        }
      )
      step_result = self.m.step(
        'Run Expectations Script' + suffix,
        [
          'python3',
          checker_script,
          '--check-expectations',
          '--results-path',
          self.m.json.output(),
          '--output-directory',
          build_dir,
          '--clear-expectations',
        ],
        step_test_data=TEST_DATA,
      )
      return step_result.json.output

  def _check_expectations(
    self,
    expectations_with_patch_json,
    expectations_without_patch_json,
    allow_expectations_regressions=False,
  ):
    with self.m.step.nest(constants.EXPECTATIONS_STEP_NAME) as presentation:
      if expectations_with_patch_json['success']:
        presentation.step_text += '<br/>Expectations are up-to-date.'
        return True

      presentation.logs['failed expectations'] = expectations_with_patch_json[
        'failed_messages'
      ]

      if expectations_with_patch_json == expectations_without_patch_json:
        presentation.status = self.m.step.WARNING
        presentation.step_text += (
          '<br/>Expectations have failures but they are the same failures '
          'with and without patch, so ignoring.'
        )
        return True

      presentation.step_text += '<br/>Expectations file need to be updated.'
      presentation.status = self.m.step.FAILURE

      # For android-internal-binary-size, expectations are diffs against base
      # expectations in //src, and sometimes changes to the base files can
      # cause the diffs to become stale. Don't fail trybots in this case.
      if (
        expectations_without_patch_json
        and not expectations_without_patch_json['success']
      ):
        presentation.step_text += (
          '<br/>Note: Expectations did not match both with and '
          'without patch. You need to update the expecations to '
          'account for your change as well as some unrelated changes '
          '(this is fine / normal).'
        )

      return allow_expectations_regressions

  def _clear_failed_expectation_files(self, build_dir: Path):
    """Clear expectation files from a previous build."""

    checker_script = self.resource('trybot_failed_expectations_checker.py')

    # This step needs to happen after gn gen but before compile since it
    # requires a var to be written to build_vars.txt. Otherwise, it raises an
    # exception if this is the first run of the bot after a gn clean.
    # But that is not possible because both are combined into one step.
    # However, we can safely ignore said exception because if build_vars.txt is
    # empty, then there are no expectation files regardless. If the problem is
    # more serious, it will be caught later in
    # _check_for_failed_expectation_files.
    self.m.step(
      'Clear Expectation Files',
      [
        'python3',
        checker_script,
        '--clear-expectations',
        '--output-directory',
        build_dir,
      ],
      ok_ret='any',
    )
