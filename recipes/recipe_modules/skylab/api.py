# Copyright 2020 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

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


def _base64_encode_args(args, skip_prefix=None):
  """Base64-encode some args which contain shell-unsafe characters

    Args:
      args (list[str]): List of tast args, in the form of name=value
      skip_prefix: If a name starts with this, it is copied to the result as-is.
    Returns:
      list of args, with some args encoded in base64 and _b64 suffix added to the name.
  """
  find_unsafe = re.compile(r'[^\w@%+=:,./-]', re.ASCII).search

  def encode_if_necessary(arg):
    a = arg.split('=', 1)
    if len(a) < 2:
      return arg
    name, value = a
    if skip_prefix and name.startswith(skip_prefix):
      return arg
    if not find_unsafe(value):
      return arg
    b = _base64_encode_str(value)
    return f'{name}_b64={b}'

  return [encode_if_necessary(arg) for arg in args]


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

  def schedule_suite(self, test, suffix, retry_shards=None):
    """Schedule a Skylab test by invoking the cros_test_platform(CTP) build.

    Translate each SkylabTest object into a CTP request and call Buildbucket
    to schedule them.

    Args:
    * test (step.SkylabTest): a steps.SkylabTest to schedule.
    * suffix: A string suffix.
    * retry_shards (list[str]): the index for shards to retry. None by default.
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
      ]

      cmd.extend(['--board', test.spec.cros_board])

      if test.spec.cros_model:
        cmd.extend(['--model', test.spec.cros_model])

      if test.spec.cros_cbx:
        cmd.extend(['--cbx'])

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

      cmd.extend(['--timeout-mins', str(int(test.spec.timeout_sec / 60))])

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

      test_args.append('resultdb_settings=%s' % _base64_encode_str(rdb_str))

      if test.spec.tast_expr:
        # Due to crbug/1173329, skylab does not support arbitrary tast
        # expressions. As a workaround, we encode test argument which may
        # contain complicated patterns to base64.
        test_args.append('tast_expr_b64=%s' %
                         _base64_encode_str(test.spec.tast_expr))

      if test.spec.test_args:
        if test.is_tast_test:
          test_args.extend(
              _base64_encode_args(test.spec.test_args, TAST_VARS_PREFIX))
        else:
          test_args.append('test_args_b64=%s' %
                           _base64_encode_str(' '.join(test.spec.test_args)))

      test_retries = '2'
      if test.spec.test_level_retries != None:
        test_retries = test.spec.test_level_retries
      test_args.append('retries=%s' % test_retries)

      if test.spec.shard_level_retries_on_ctp >= 0:
        cmd.extend(['--retry', str(test.spec.shard_level_retries_on_ctp)])

      if test.exe_rel_path:
        test_args.append('exe_rel_path=%s' % test.exe_rel_path)

      if test.tast_expr_file:
        test_args.append('tast_expr_file=%s' % test.tast_expr_file)
        if test.spec.tast_expr_key:
          test_args.append('tast_expr_key=%s' % test.spec.tast_expr_key)

      if test.spec.extra_browser_args:
        test_args.append('extra_browser_args_b64=%s' %
                         _base64_encode_str(test.spec.extra_browser_args))

      if test.spec.benchmark:
        test_args.append('benchmark=%s' % test.spec.benchmark)

      if test.spec.results_label:
        test_args.append('results_label=%s' % test.spec.results_label)

      if test.spec.story_filter:
        test_args.append('story_filter=%s' % test.spec.story_filter)

      if test.spec.test_shard_map_filename:
        test_args.append('test_shard_map_filename=%s' %
                         test.spec.test_shard_map_filename)

      if test.spec.max_run_sec:
        test_args.append('max_run_sec=%s' % test.spec.max_run_sec)

      # TODO(crbug.com/1233676): Support chromium perf tests.
      # if test.telemetry_shard_index is not None:
      #   test_args.append('test_shard_index=%s' % test.telemetry_shard_index)

      if test.spec.bucket and 'chromium' in test.spec.bucket:
        test_args.append('run_private_tests=False')
      cmd.extend(['--test-args', ' '.join(test_args)])

      lacros_gcs_path = os.path.join(test.lacros_gcs_path,
                                     'skylab_runtime_deps.tar.zst')
      cmd.extend(['--lacros-gcs-path', lacros_gcs_path])

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

      if test.spec.run_cft:
        cmd.append('--run-cft')

      for retry_shard in retry_shards or []:
        cmd.extend(['--shard-indexes', retry_shard])

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
            '0': {
                'url':
                    'https://ci.chromium.org/p/chromeos/builders/test_runner/'
                    f'test_runner/b{test.ctp_build_ids[suffix]}0',
                'log_url':
                    'https://cros-test-analytics.appspot.com/p/chromeos/logs/'
                    'browse/chromeos-test-logs/test-runner/prod/abcd',
                'status': 'SUCCESS'
            }
        }))
    if (hasattr(step_result, 'json') and step_result.json.output):
      for shard, test_runner in step_result.json.output.items():
        if test_runner:
          tr = TestRunner.create(test, shard=int(shard), **test_runner)
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
        'cros_img': cros_img,
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
