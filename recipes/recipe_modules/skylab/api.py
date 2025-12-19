# Copyright 2020 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import attr
import base64
import os
import re

from google.protobuf import json_format
from recipe_engine import recipe_api

from .test_runner import TestRunner
from RECIPE_MODULES.build.chromium_tests.resultdb import ResultDB

from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb2
from PB.go.chromium.org.luci.resultdb.proto.v1 import common as common_rdb_pb
from PB.go.chromium.org.luci.resultdb.proto.v1 import (invocation as
                                                       invocation_pb)


# Skylab prioritizes tests by the Quota Scheduler account attached in the
# request. We applied account "lacros", which has limited high priority
# quota. It is supposed to grant to the production builders only.
# For fyi builders, we use 'lacros_fyi' which only contains the free quota,
# aka the lowest priority.
QS_ACCOUNT_PROD = 'lacros'
QS_ACCOUNT_FYI = 'lacros_fyi'
CTP_BUILDER = 'cros_test_platform'
CTP_BUILDER_DEV = 'cros_test_platform-dev'
CROS_BUCKET = 'gs://chromeos-image-archive/'

# Prefix of args that indicates variable names of Tast '-var' flags.
TAST_VARS_PREFIX = 'tast.'

def _base64_encode_str(s):
  return base64.b64encode(s.encode('utf-8')).decode('ascii')


class SkylabApi(recipe_api.RecipeApi):
  """Module for issuing commands to Skylab"""

  def get_lkgm_version(self, board: str, chrome_src: str,
                       use_external_config: bool) -> str:
    """Get LKGM or older latest version of ChromeOS available for the board.

    The LKGM version is determined based on //chromeos/CHROMEOS_LKGM file
    in the chrome checkout.
    The returned version is usually the LKGM, but if the image for the board
    is not available, an older version is searched as a fallback.
    Also see chromite.ChromeLkgmSerivce/FindLkgm in BuildAPI description.

    Args:
    * board: The build target board with which tests are run.
    * chrome_src: The location of the chrome checkout.
    * use_external_config: Use the external (=chromiumos) configuration.
    *     When this is False, the internal configuration is used and
    *     the API call requires the read access of ChromeOS release images.


    Returns:
      The fully specified image name. e.g. "octopus-release/R89-13609".

    """
    build_api = self.m.path.join(chrome_src, 'third_party', 'chromite', 'bin',
                                 'build_api')
    cmd = [
        build_api,
        'chromite.api.ChromeLkgmService/FindLkgm',
        '--input-json',
        self.m.json.input({
            "build_target": {
                "name": board,
            },
            "chrome_src": chrome_src,
            "fallback_versions": 20,
            "use_external_config": use_external_config
        }),
        '--output-json',
        self.m.json.output(),
    ]
    step_result = self.m.step(
        'call build API',
        cmd,
    )
    response = step_result.json.output
    if response.get('error'):
      raise recipe_api.StepFailure(
          'chromite.api.ChromeLkgmService/FindLkgm returned error:' +
          response.get('error'))

    result = '/'.join([response.get('configName'), response.get('fullVersion')])

    return result

  def calculate_ctp_timeout(self, test_spec_timeout: int) -> int:
    """
      Calculate a proper CTP timeout from test_spec_timeout.

      Special CTP timeout handling:
      When the builder does not have enough time remaining, set CTP timeout
      to smaller value to ensure the builder can complete normally.
      Effects/Goal:
       1. Initial CTP run:
           1a) CTP completed initial attempt but timed out at retry. Chrome
               builder returns test failures based on what already have.
               Without reducing CTP timeout, Chrome builder will timeout.
           1b) CTP timeouts at initial attempt, Chrome builder will reads
               INFRA_FAILURE on the shard, causing retry_invalid_shards at
               Case 4.
               Without reducing CTP timeouts, Chrome builder will just
               timeout at initial runs, final state to Chrome builder
               unchanged.
       2. Chrome builder retry shards:
           Even CTP timeouts at initial attempts, Chrome builder reads the
           shard as INFRA_FAILURE and reports accordingly based on
           both initial and retry shards runs.
           Without reducing CTP timeout, Chrome builder will timeout.
       3. Chrome try builder retry without patch:
           3a) the CL is the culprit OR CTP level retry is disabled, retry
               without patch will pass/fail initial runs. There is no
               difference reducing timeout or not.
           3b) the CL is not the culprit AND CTP level retry is enabled,
               retry without patch. As long as the first attempt have a valid
               result and didn't timeout, timeout at retry will still produce
               a pass/fail result, which may be inaccurate for flaky tests.
               Without reducing timeout, the builder may timeout.
       4. Special case, less than 5 minutes remaining:
           A 10-minute timeout CTP will be scheduled to mimic builder
           timeout.
       Why:
         This eventually allows setting longer CTP timeout while keep builder
         timeout not so long.
         Without reducing timeout dynamically, we have to set maximum of  to
         3 times CTP timeout as builder timeout to ensure builder does not
         timeout, which is too long to be possible (any infra problem causing
         CTP to stuck at any step would potentially cause Chrome builder to
         run 10+ hours). In that case, the builder will either timeout even
         there are useful results, or we set much smaller CTP timeout, which
         would partially kill CTP-level retry depending on the distribution
         of flaky or regressed tests.
         By dynamically reducing timeout based on remaining seconds, we give
         maximum possibility for CTP to retry at CTP level especially the
         first CTP run, which is most efficient but still keeping builder
         timeout at not too long time since it's unlikely worst case at all
         steps happening together. This would also prevent Chrome builder
         collect() CTP long if something is wrong on CrOS Test Infra side.

      Args:
        test_spec_timeout: the timeout in secs from test spec.
      """
    if self.m.buildbucket.build.execution_timeout.seconds > 0:
      remaining_sec = int(
          self.m.buildbucket.build.start_time.seconds +
          self.m.buildbucket.build.execution_timeout.seconds -
          self.m.time.time() -
          # Have 5 minutes buffer for CTP preparation, result processing,
          # buildbucket.collect() and result processing.
          300)
      if remaining_sec < test_spec_timeout:
        # Override timeout_sec to remaining_sec.
        with self.m.step.nest('timeout handling') as pres:
          pres.step_summary_text = (
              f'CTP timeout reduced to {remaining_sec} seconds.\n'
              'CTP will return proper results if all shards completed initial '
              f'attempt within {remaining_sec} seconds.\n')
          return remaining_sec
      if remaining_sec < 300:  # pragma: nocover
        # If remaining_sec is too small, the test will almost for sure
        # timeout, set slightly longer timeout to mimic the builder timeout.
        return 600
    return test_spec_timeout

  def schedule_suite(self,
                     test,
                     suffix,
                     retry_shards=None,
                     runtime_no_retry=False,
                     runtime_override_tests=None,
                     runtime_excluded_tests=None):
    """Schedule a Skylab test by invoking the cros_test_platform(CTP) build.

    Translate each SkylabTest object into a CTP request and call Buildbucket
    to schedule them.

    Args:
    * test (step.SkylabTest): a steps.SkylabTest to schedule.
    * suffix: A string suffix.
    * retry_shards (list[str]): the index for shards to retry. None by default.
    * runtime_no_retry (bool): whether to disable retry, regardless of test's
      retry settings.
    * runtime_override_tests (list[str]): ignore defined tags, run these tests
      instead.
    * runtime_excluded_tests (list[str]): additional tests to be excluded. Only
      for tag criteria based testing.

    Returns the StepResult of the skylab.py resource script invocation.
    """
    with self.m.step.nest(test.step_name(suffix)) as presentation:
      cmd = [
          'vpython3',
          self.resource('skylab.py'),
          '--chromium-src',
          str(self.m.chromium_checkout.source_dir),
          '--json-outfile',
          self.m.json.output(),
          'request',
          '--chromium-suite-name',
          test.spec.name,
          '--parent-build-id',
          str(self.m.buildbucket.build.id),
          '--builder-name',
          self.m.buildbucket.build.builder.builder,
      ]

      cmd.extend(['--board', test.spec.cros_board])

      if test.spec.cros_model:
        cmd.extend(['--model', test.spec.cros_model])

      if test.spec.bucket:
        cmd.extend(['--bucket', test.spec.bucket])

      if test.spec.public_builder and test.spec.public_builder_bucket:
        cmd.extend(['--public-builder', test.spec.public_builder])
        cmd.extend(['--public-builder-bucket', test.spec.public_builder_bucket])

      cmd.extend([
          '--pool',
          test.spec.dut_pool if test.spec.dut_pool else 'DUT_POOL_QUOTA'
      ])

      if test.spec.use_lkgm:
        assert not test.spec.cros_img, 'cros_img should be empty when use_lkgm is True'
        is_public = test.spec.bucket.startswith('chromiumos-')
        build_target = test.spec.cros_build_target
        if not build_target:
          build_target = test.spec.cros_board
        cros_img = self.get_lkgm_version(
            build_target, str(self.m.chromium_checkout.source_dir), is_public)
        assert cros_img, 'chromite build_api command not found'
      else:
        cros_img = test.spec.cros_img

      cmd.extend(['--image', cros_img])

      if test.spec.secondary_cros_board:
        boards = test.spec.secondary_cros_board.split(',')
        build_targets = [''] * len(boards)
        imgs = [''] * len(boards)
        if test.spec.secondary_cros_build_target:
          build_targets = test.spec.secondary_cros_build_target.split(',')
          assert len(build_targets) == len(
              boards
          ), "if specified, secondary_cros_build_target must have same length as secondary_cros_board"
        if test.spec.secondary_cros_img:
          imgs = test.spec.secondary_cros_img.split(',')
          if len(boards) != len(imgs):
            raise recipe_api.StepFailure('Length of secondary_cros_img'
                                         ' must match secondary_cros_board')
        for b, img, build_target in zip(boards, imgs, build_targets):
          if img == 'use_lkgm':
            if not build_target:
              build_target = b
            is_public = test.spec.bucket.startswith('chromiumos-')
            img = self.get_lkgm_version(
                build_target, str(self.m.chromium_checkout.source_dir),
                is_public)
          cmd.extend(['--secondary-boards', b])
          cmd.extend(['--secondary-images', img])

      timeout_sec = self.calculate_ctp_timeout(test.spec.timeout_sec)
      cmd.extend(['--timeout-mins', int(timeout_sec / 60)])

      cmd.extend([
          '--qs-account', QS_ACCOUNT_FYI
          if 'fyi' in self.m.buildbucket.builder_name else QS_ACCOUNT_PROD
      ])

      resultdb = self.gen_rdb_config(test, cros_img)
      assert resultdb and resultdb.enable, ('Skylab tests should '
                                            'have resultdb enabled.')
      rdb_str = self.m.json.dumps({
          k: getattr(resultdb, k)
          for k in attr.fields_dict(ResultDB)
          if not getattr(resultdb, k) in [None, '']
      })

      test_args = []

      test_args.append(('resultdb_settings', _base64_encode_str(rdb_str)))

      if test.spec.test_args:
        if not test.spec.autotest_name:
          # gtests args are command line parameters and not key-value pair.
          # We should not embed and treat args as key-value pair.
          test_args.extend([
              x.split('=', 1) if '=' in x else (x, 'True')
              for x in test.spec.test_args
          ])
        # test_args_b64 is only for tauto.chromium. tast will automatically
        # ignore test_args_b64 anyway.
        test_args.append(('test_args_b64',
                          _base64_encode_str(' '.join(test.spec.test_args))))

      if test.spec.shard_level_retries_on_ctp >= 0 and not runtime_no_retry:
        cmd.extend(['--retry', str(test.spec.shard_level_retries_on_ctp)])

      lacros_gcs_path = os.path.join(test.lacros_gcs_path,
                                     'skylab_runtime_deps.tar.zst')
      if test.spec.autotest_name:
        cmd.extend(['--lacros-gcs-path', lacros_gcs_path])

        test_retries = '2'
        if test.spec.test_level_retries != None:
          test_retries = test.spec.test_level_retries
        test_args.append(('retries', test_retries))

        # TODO(crbug.com/1233676): Support chromium perf tests.
        # if test.telemetry_shard_index is not None:
        #   test_args.append(('test_shard_index', str(test.telemetry_shard_index)))

        if test.spec.bucket and 'chromium' in test.spec.bucket:
          test_args.append(('run_private_tests', 'False'))

        if test.exe_rel_path:
          test_args.append(('exe_rel_path', test.exe_rel_path))
        elif 'tast' in test.spec.autotest_name:
          # TODO(b/450596053): Remove after M140 is no longer built.
          test_args.append(
              ('exe_rel_path', os.path.join(test.build_output_dir, 'chrome')))

        if test.spec.extra_browser_args:
          test_args.append(('extra_browser_args_b64',
                            _base64_encode_str(test.spec.extra_browser_args)))

        if test.spec.benchmark:
          test_args.append(('benchmark', test.spec.benchmark))

        if test.spec.results_label:
          test_args.append(('results_label', test.spec.results_label))

        if test.spec.story_filter:
          test_args.append(('story_filter', test.spec.story_filter))

        if test.spec.test_shard_map_filename:
          test_args.append(
              ('test_shard_map_filename', test.spec.test_shard_map_filename))

        if test.spec.max_run_sec:
          test_args.append(('max_run_sec', str(test.spec.max_run_sec)))

        if test.spec.secondary_cros_board:
          should_provision_browser_files = test.spec.should_provision_browser_files or [
              True
          ] * len(boards)
          if len(should_provision_browser_files) != len(boards):
            raise recipe_api.StepFailure(
                'Length of should_provision_browser_files'
                ' must match secondary_cros_board')
          for s in should_provision_browser_files:
            cmd.extend(
                ['--secondary-lacros-gcs-path', lacros_gcs_path if s else ''])

        cmd.extend(['--autotest-name', test.spec.autotest_name])
        cmd.extend(['--total-shards', test.spec.shards])
      else:
        if runtime_override_tests:
          for t in runtime_override_tests:
            cmd.extend(['--cros-test-names', t])
        else:
          for tag in test.spec.cros_test_tags:
            cmd.extend(['--cros-test-tags', tag])
          for tag in test.spec.cros_test_tags_exclude:
            cmd.extend(['--cros-test-tags-exclude', tag])
          for t in test.spec.cros_test_names:
            cmd.extend(['--cros-test-names', t])
          for t in test.spec.cros_test_names_exclude:
            cmd.extend(['--cros-test-names-exclude', t])
          for t in test.spec.cros_test_names_from_file:
            cmd.extend(['--cros-test-names-from-file', t])
          for t in test.spec.cros_test_names_exclude_from_file:
            cmd.extend(['--cros-test-names-exclude-from-file', t])

        if runtime_excluded_tests:
          for t in runtime_excluded_tests:
            cmd.extend(['--cros-test-names-exclude', t])

        if test.spec.cros_ctp_suite_name:
          cmd.extend(['--cros-ctp-suite-name', test.spec.cros_ctp_suite_name])

        if test.spec.cros_test_max_in_shard > 0:
          cmd.extend(
              ['--cros-test-max-in-shard', test.spec.cros_test_max_in_shard])

        if test.spec.secondary_cros_board:  # pragma: nocover
          assert False, 'Not supported yet'

        cmd.extend(['--ash-chrome-gcs-path', lacros_gcs_path])
        cmd.extend(['--ash-chrome-build-output-dir', test.build_output_dir])

      if test.spec.strip_chrome:
        cmd.append('--strip')

      for retry_shard in retry_shards or []:
        cmd.extend(['--shard-indexes', retry_shard])

      for test_arg_key, test_arg_value in test_args:
        cmd.extend([
            '--test-arg',
            # Prefix a space to key to bypass argparse treating hyphen-prefixed
            # arguments as new options.
            # This is necessary for argparse implementation in Python < 3.13
            ' ' +
            test_arg_key if test_arg_key.startswith('-') else test_arg_key,
            test_arg_value
        ])

      step_result = self.m.step(
          'schedule',
          cmd,
          raise_on_failure=False,
          step_test_data=lambda: self.m.json.test_api.output(
              {'ctp_build_id': '889900'}))

      if step_result.retcode == 0:
        build_id = int(step_result.json.output['ctp_build_id'])
        presentation.links[
            test.name] = 'https://ci.chromium.org/b/%s' % build_id
        test.ctp_build_ids[suffix] = build_id
      return step_result

  def fetch_test_runners(self, test, suffix):
    """Fetch the CrOS test runner builds for each shard.

    Each test runner build represents a shard like a swarming task, containing
    its runtime info, e.g. std logs and infra status. Fetch them and attach
    to the test's test_runner_builds for the given suffix.

    Args:
    * test (SkylabTest): a steps.SkylabTest.
    * suffix: A string suffix.
    """
    assert test.ctp_build_ids[suffix], ('No CTP build found.'
                                        'Must call schedule_suite() first.')
    cmd = [
        'vpython3',
        self.resource('skylab.py'),
        '--chromium-src',
        str(self.m.chromium_checkout.source_dir),
        '--json-outfile',
        self.m.json.output(),
        'response',
        '--ctp-build-id',
        test.ctp_build_ids[suffix],
    ]
    step_result = self.m.step(
        'read_ctp_response',
        cmd,
        stdout=self.m.raw_io.output(),
        stderr=self.m.raw_io.output(),
        raise_on_failure=False,
        step_test_data=lambda: self.m.json.test_api.output({
            f'{test.spec.name}-tfc-shard-0': {
                'url':
                    'https://ci.chromium.org/p/chromeos/builders/test_runner/'
                    f'test_runner/b{test.ctp_build_ids[suffix]}0',
                'shard': None,
                'log_url':
                    'https://tests.chromeos.goog/p/chromeos/logs/unified/'
                    'invocations/build-8702874632118866193',
                'status': 'SUCCESS'
            }
        }))
    if (hasattr(step_result, 'json') and step_result.json.output):
      for key, test_runner in step_result.json.output.items():
        if test_runner:
          tr = TestRunner.create(test, name=key, **test_runner)
          test.test_runner_builds.setdefault(suffix, []).append(tr)

  def gen_rdb_config(self, test, cros_img):
    """Generate the resultDB config for SkylabTest.

    Args:
      test: A step.SkylabTest object.
      cros_img: A CrOS img name to be used for tests.

    Returns:
      A new config of ResultDB. See chromium_tests.ResultDB.
    """
    var = dict(
        test.spec.resultdb.base_variant or {}, test_suite=test.canonical_name)
    var.update({
        'device_type': test.spec.cros_board,
        'os': 'ChromeOS',
    })
    tags = test.spec.resultdb.base_tags or []
    if not any(t[0] == 'cros_img' for t in tags):
      tags.append(('cros_img', cros_img))
    result_format = test.spec.resultdb.result_format
    # TODO(b/430180607): branched builder configuration does not have
    # result_format in spec, infer from test name. Remove later.
    # Remove once all M142 and older milestone are gone.
    if not result_format:  # pragma: nocover
      if test.spec.autotest_name and 'tast' not in test.spec.autotest_name:
        # GPU test does not have branched builders. Wrapped test must be gtest
        # for recent milestones, except when tast is in test name.
        result_format = 'gtest'
      else:
        result_format = 'tast'

    gitiles_commit = self.m.buildbucket.build.output.gitiles_commit

    sources = invocation_pb.Sources(
        gitiles_commit=common_rdb_pb.GitilesCommit(
            host=gitiles_commit.host,
            project=gitiles_commit.project,
            commit_hash=gitiles_commit.id,
            ref=gitiles_commit.ref,
            position=gitiles_commit.position,
        ),
        changelists=[
            common_rdb_pb.GerritChange(
                host=change.host,
                project=change.project,
                change=change.change,
                patchset=change.patchset)
            for change in self.m.buildbucket.build.input.gerrit_changes
        ])
    return attr.evolve(
        test.spec.resultdb,
        test_id_prefix=test.spec.test_id_prefix,
        base_tags=tags,
        base_variant=var,
        result_format=result_format,
        # Skylab's result_file is hard-coded by the autotest wrapper in OS
        # repo, and not required by callers. It suppose to be None, but then
        # ResultDB will pass the default value ${ISOLATED_OUTDIR}/output.json
        # which is confusing for Skylab test runner. So explicitly set it an
        # empty string, as well as artifact_directory.
        result_file='',
        # Same with result_file, the abs path of artifact directory is
        # determined at runtime on Skylab Drone server. We leave it empty here.
        # CrOS recipe will feed that path to result adapter when uploading
        # results.
        artifact_directory='',
        # The source code position of the test artifacts we send to Skylab.
        sources=json_format.MessageToJson(sources),
    )
