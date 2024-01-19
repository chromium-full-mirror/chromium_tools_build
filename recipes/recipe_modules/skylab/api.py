# Copyright 2020 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import attr
import base64
import os

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

  def schedule_suites(self,
                      tests,
                      step_name='schedule skylab tests',
                      retry_shards=None):
    """Schedule CrOS autotest suites by invoking the cros_test_platform recipe.

    Translate each skylab test request into a CTP Buildbucket request and call
    Buildbucket's Batch method to schedule them. Each CTP build represents one
    test suite.

    Args:
    * tests (list[SkylabTest]): List of steps.SkylabTest to schedule.
    * step_name (str): a name of scheduling buildbucket build.
    """
    with self.m.step.nest(step_name) as presentation:
      for t in tests:
        with self.m.step.nest(t.name):
          cmd = [
              'vpython3',
              self.resource('skylab.py'),
              '--chromium-src',
              str(self.m.chromium_checkout.src_dir),
              '--json-outfile',
              self.m.json.output(),
              'request',
          ]

          cmd.extend(['--board', t.spec.cros_board])

          if t.spec.cros_model:
            cmd.extend(['--model', t.spec.cros_model])

          if t.spec.bucket:
            cmd.extend(['--bucket', t.spec.bucket])

          if t.spec.public_builder and t.spec.public_builder_bucket:
            cmd.extend(['--public-builder', t.spec.public_builder])
            cmd.extend(
                ['--public-builder-bucket', t.spec.public_builder_bucket])

          cmd.extend([
              '--pool', t.spec.dut_pool if t.spec.dut_pool else 'DUT_POOL_QUOTA'
          ])

          if t.spec.use_lkgm:
            assert not t.spec.cros_img, 'cros_img should be empty when use_lkgm is True'
            is_public = t.spec.bucket.startswith('chromiumos-')
            lkgm_cros_img = self.get_lkgm_version(
                t.spec.cros_board, str(self.m.chromium_checkout.src_dir),
                is_public)
            assert lkgm_cros_img, 'chromite build_api command not found'

            cmd.extend(['--image', lkgm_cros_img])
          else:
            cmd.extend(['--image', t.spec.cros_img])

          if t.spec.secondary_cros_board:
            boards = t.spec.secondary_cros_board.split(',')
            imgs = [''] * len(boards)
            if t.spec.secondary_cros_img:
              imgs = t.spec.secondary_cros_img.split(',')
              if len(boards) != len(imgs):
                raise recipe_api.StepFailure('Length of secondary_cros_img'
                                             ' must match secondary_cros_board')
            for b, img in zip(boards, imgs):
              if img == 'use_lkgm':
                is_public = t.spec.bucket.startswith('chromiumos-')
                img = self.get_lkgm_version(
                    b, str(self.m.chromium_checkout.src_dir), is_public)
              cmd.extend(['--secondary-boards', b])
              cmd.extend(['--secondary-images', img])

          cmd.extend(['--timeout-mins', str(int(t.spec.timeout_sec / 60))])

          cmd.extend([
              '--qs-account', QS_ACCOUNT_FYI
              if 'fyi' in self.m.buildbucket.builder_name else QS_ACCOUNT_PROD
          ])

          resultdb = self.gen_rdb_config(t)
          assert resultdb and resultdb.enable, ('Skylab tests should '
                                                'have resultdb enabled.')
          rdb_str = self.m.json.dumps({
              k: getattr(resultdb, k)
              for k in attr.fields_dict(ResultDB)
              if not getattr(resultdb, k) in [None, '']
          })

          test_args = []

          test_args.append('resultdb_settings=%s' % _base64_encode_str(rdb_str))

          if t.spec.tast_expr:
            # Due to crbug/1173329, skylab does not support arbitrary tast
            # expressions. As a workaround, we encode test argument which may
            # contain complicated patterns to base64.
            test_args.append('tast_expr_b64=%s' %
                             _base64_encode_str(t.spec.tast_expr))

          if t.spec.test_args:
            test_args.append('test_args_b64=%s' %
                             _base64_encode_str(' '.join(t.spec.test_args)))

          test_retries = '2'
          if t.spec.test_level_retries != None:
            test_retries = t.spec.test_level_retries
          test_args.append('retries=%s' % test_retries)

          if t.exe_rel_path:
            test_args.append('exe_rel_path=%s' % t.exe_rel_path)

          if t.tast_expr_file:
            test_args.append('tast_expr_file=%s' % t.tast_expr_file)
            if t.spec.tast_expr_key:
              test_args.append('tast_expr_key=%s' % t.spec.tast_expr_key)

          if t.spec.extra_browser_args:
            test_args.append('extra_browser_args_b64=%s' %
                             _base64_encode_str(t.spec.extra_browser_args))

          if t.spec.benchmark:
            test_args.append('benchmark=%s' % t.spec.benchmark)

          if t.spec.results_label:
            test_args.append('results_label=%s' % t.spec.results_label)

          if t.spec.story_filter:
            test_args.append('story_filter=%s' % t.spec.story_filter)

          if t.spec.test_shard_map_filename:
            test_args.append('test_shard_map_filename=%s' %
                             t.spec.test_shard_map_filename)

          if t.spec.max_run_sec:
            test_args.append('max_run_sec=%s' % t.spec.max_run_sec)

          # TODO(crbug.com/1233676): Support chromium perf tests.
          # if t.telemetry_shard_index is not None:
          #   test_args.append('test_shard_index=%s' % t.telemetry_shard_index)

          if t.spec.bucket and 'chromium' in t.spec.bucket:
            test_args.append('run_private_tests=False')
          cmd.extend(['--test-args', ' '.join(test_args)])

          lacros_gcs_path = os.path.join(t.lacros_gcs_path,
                                         'lacros_compressed.squash')
          cmd.extend(['--lacros-gcs-path', lacros_gcs_path])

          if t.spec.secondary_cros_board:
            should_provision_browser_files = t.spec.should_provision_browser_files or [
                False
            ] * len(boards)
            if len(should_provision_browser_files) != len(boards):
              raise recipe_api.StepFailure(
                  'Length of should_provision_browser_files'
                  ' must match secondary_cros_board')
            for s in should_provision_browser_files:
              cmd.extend(
                  ['--secondary-lacros-gcs-path', lacros_gcs_path if s else ''])

          cmd.extend(['--autotest-name', t.spec.autotest_name])
          cmd.extend(['--total-shards', t.spec.shards])
          for shard in retry_shards or []:
            cmd.extend(['--shard-indexes', shard])

          step_result = self.m.step(
              'schedule',
              cmd,
              raise_on_failure=False,
              stdout=self.m.json.output(),
              step_test_data=lambda: self.m.json.test_api.output(
                  {'ctp_build_id': '889900'}))

          if step_result.retcode == 0:
            build_id = int(step_result.json.output['ctp_build_id'])
            presentation.links[
                t.name] = 'https://ci.chromium.org/b/%s' % build_id
            t.ctp_build_ids.append(build_id)

  def _try_wait_ctp_builds(self, test_suites, timeout_seconds):
    """Helper to wait for the given CTP builds to finish."""
    with self.m.step.nest('collect skylab results'):
      all_build_ids = [
          t.ctp_build_ids[-1] for t in test_suites if t.ctp_build_ids
      ]
      try:
        self.m.buildbucket.collect_builds(
            all_build_ids, timeout=timeout_seconds)
      except self.m.step.StepFailure as err:
        # Perhaps some of the builds have completed, so continue
        # to collect their results even if the step timed out.
        if err.had_timeout:
          pass
        else:
          raise

  def _fetch_test_runner(self, ctp_build_id):
    """Helper to fetch test runner builds kicked of by given CTP build

    Args:
      ctp_build_id: (int64) A CTP's Buildbucket ID.

    Returns:
      A dict of ChromeOS test runner builds in `build_pb2.Build` keyed with
      shards. An empty dict if hit any exceptions.
    """
    cmd = [
        'vpython3',
        self.resource('skylab.py'),
        '--chromium-src',
        str(self.m.chromium_checkout.src_dir),
        '--json-outfile',
        self.m.json.output(),
        'response',
        '--ctp-build-id',
        ctp_build_id,
    ]
    step_result = self.m.step(
        'read_ctp_response',
        cmd,
        stdout=self.m.raw_io.output(),
        stderr=self.m.raw_io.output(),
        raise_on_failure=False,
        step_test_data=lambda: self.m.json.test_api.output({
            '0': {
                'url':
                    'https://ci.chromium.org/p/chromeos/builders/test_runner/'
                    f'test_runner/b{ctp_build_id}0',
                'log_url':
                    'https://cros-test-analytics.appspot.com/p/chromeos/logs/'
                    'browse/chromeos-test-logs/test-runner/prod/abcd',
                'status': 'SUCCESS'
            }
        }))
    if (hasattr(step_result, 'json') and step_result.json.output):
      return step_result.json.output
    step_result.presentation.status = self.m.step.EXCEPTION
    return {}

  def wait_on_suites(self, tests, timeout_seconds):
    """Wait for the CTP builds to complete and return their test runner builds.

    Args:
    * tests (list[SkylabTest]): See schedule_suites().
    * timeout_seconds: How long to wait for results before giving up.

    Returns:
    * A dict of request tag to dict of CTP build (the shard request) to list of
        test_runner attempts.
    * A list of the CTP build ID in int64, in the order of shards.
    """
    cur = [t for t in tests if t.ctp_build_ids]
    attempt = 0
    while cur:
      self._try_wait_ctp_builds(cur, timeout_seconds)
      prev, cur = cur, []
      with self.m.step.nest('find test runner build') as step:
        while prev:
          t = prev.pop()
          runner_by_shards = self._fetch_test_runner(t.ctp_build_ids[-1])
          retry_shards = []
          # Retry for infra issues that should not be caused by tests
          # regressions. Specifically for below scenarios:
          # - no test runner builds found, aka runner_by_shards is empty.
          # - test runner has infra issue, aka the build status is out of
          #   success and failure.
          # Requests from browser builders do not enable retry for CTP,
          # because CTP does not separate test failure and infra failures.
          # Test failure is better to get retried by test_level_retry.
          for shard, test_runner in runner_by_shards.items():
            if test_runner:
              tr = TestRunner.create(t, **test_runner)
              t.test_runner_builds.setdefault(shard, []).append(tr)
              if tr.status in [common_pb2.SUCCESS, common_pb2.FAILURE]:
                continue
            retry_shards.append(shard)

          if not runner_by_shards or retry_shards:
            step.presentation.status = self.m.step.EXCEPTION
            if attempt < t.spec.retries:
              self.schedule_suites([t], retry_shards=retry_shards)
              cur.append(t)
      attempt += 1

  def gen_rdb_config(self, test):
    """Generate the resultDB config for SkylabTest.

    Args:
      test: A step.SkylabTest object.

    Returns:
      A new config of ResultDB. See chromium_tests.ResultDB.
    """
    var = dict(
        test.spec.resultdb.base_variant or {}, test_suite=test.canonical_name)
    var.update({
        'device_type': test.spec.cros_board,
        'os': 'ChromeOS',
        'cros_img': test.spec.cros_img,
    })
    result_format = 'gtest'
    if test.is_tast_test:
      result_format = 'tast'
    elif test.is_GPU_test:
      result_format = 'native'

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
