# Copyright 2020 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import attr
import base64
import os

from collections import defaultdict

from recipe_engine import recipe_api

from RECIPE_MODULES.build.chromium_tests.resultdb import ResultDB

from PB.go.chromium.org.luci.buildbucket.proto \
  import builder_common as builder_common_pb2
from PB.go.chromium.org.luci.buildbucket.proto import (builds_service as
                                                       builds_service_pb2)
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb2


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

  def get_lkgm_version(self, board: str, chrome_src: str) -> str:
    """Get LKGM or older latest version of ChromeOS available for the board.

    This API call requires the read access of ChromeOS release images.
    The LKGM version is determined based on //chromeos/CHROMEOS_LKGM file
    in the chrome checkout.
    The returned version is usually the LKGM, but if the image for the board
    is not available, an older version is searched as a fallback.
    Also see chromite.ChromeLkgmSerivce/FindLkgm in BuildAPI description.

    Args:
    * board: The build target board with which tests are run.
    * chrome_src: The location of the chrome checkout.

    Returns:
      The fully specified image name. e.g. "octopus-release/R89-13609".

    """
    build_api = os.path.join(chrome_src, 'third_party', 'chromite', 'bin',
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
            "fallback_versions": 20
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

  def schedule_suites(self, tests, step_name='schedule skylab tests'):
    """Schedule CrOS autotest suites by invoking the cros_test_platform recipe.

    Translate each skylab test request into a CTP Buildbucket request and call
    Buildbucket's Batch method to schedule them. Each CTP build represents one
    test suite.

    Args:
    * tests (list[SkylabTest]): List of Autotest suites to schedule.
    * step_name (str): a name of scheduling buildbucket build.

    Returns:
      A dict of CTP build IDs keyed by test name(see SkylabTest).
    """
    # Ensure the crosfleet cipd package is installed
    crosfleet_tool = self.m.cipd.ensure_tool(
        'chromiumos/infra/crosfleet/${platform}', 'prod')

    build_ids_by_tags = defaultdict(lambda: [])
    with self.m.step.nest(step_name) as presentation:
      for t in tests:
        with self.m.step.nest(t.name):
          cmd = [crosfleet_tool, 'run', 'test', '-json']

          cmd.extend(['-board', t.spec.cros_board])

          if t.spec.cros_model:
            cmd.extend(['-model', t.spec.cros_model])

          if t.spec.bucket:
            cmd.extend(['-bucket', t.spec.bucket])

          if t.spec.public_builder and t.spec.public_builder_bucket:
            cmd.extend(['-public-builder', t.spec.public_builder])
            cmd.extend(['-public-builder-bucket', t.spec.public_builder_bucket])

          cmd.extend([
              '-pool', t.spec.dut_pool if t.spec.dut_pool else 'DUT_POOL_QUOTA'
          ])

          if t.spec.use_lkgm:
            assert not t.spec.cros_img, 'cros_img should be empty when use_lkgm is True'
            # get_lkgm_version requires the read access of ChromeOS release images.
            assert 'chromiumos' not in t.spec.bucket, 'use_lkgm is not supported for public builders'
            lkgm_cros_img = self.get_lkgm_version(
                t.spec.cros_board, str(self.m.chromium_checkout.src_dir))
            assert lkgm_cros_img, 'chromite build_api command not found'

            cmd.extend(['-image', lkgm_cros_img])
          else:
            cmd.extend(['-image', t.spec.cros_img])

          if t.spec.secondary_cros_board:
            cmd.extend(['-secondary-boards', t.spec.secondary_cros_board])
            if t.spec.secondary_cros_img:
              boards = t.spec.secondary_cros_board.split(',')
              imgs = t.spec.secondary_cros_img.split(',')
              if len(boards) != len(imgs):
                raise recipe_api.StepFailure('Length of secondary_cros_img'
                                             ' must match secondary_cros_board')
              updated_imgs = imgs.copy()
              for i, img in enumerate(imgs):
                if img == 'use_lkgm':
                  assert 'chromiumos' not in t.spec.bucket, 'use_lkgm is not supported for public builders'
                  updated_imgs[i] = self.get_lkgm_version(
                      boards[i], str(self.m.chromium_checkout.src_dir))

              cmd.extend(['-secondary-images', ','.join(updated_imgs)])

          cmd.extend(['-timeout-mins', str(int(t.spec.timeout_sec / 60))])

          cmd.extend([
              '-qs-account', QS_ACCOUNT_FYI
              if 'fyi' in self.m.buildbucket.builder_name else QS_ACCOUNT_PROD
          ])

          if t.spec.retries:
            cmd.extend(['-max-retries', str(int(t.spec.retries))])

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

          if t.lacros_gcs_path:
            cmd.extend(['-lacros-path', t.lacros_gcs_path])

            if t.spec.secondary_cros_board:
              num_boards = len(t.spec.secondary_cros_board.split(','))
              # By default, browser files are sent to all secondary DUTs unless
              # users explicitly override through should_provision_browser_files.
              should_provision_browser_files = [True] * num_boards
              if t.spec.should_provision_browser_files:
                if len(t.spec.should_provision_browser_files) != num_boards:
                  raise recipe_api.StepFailure(
                      'Length of should_provision_browser_files'
                      ' must match secondary_cros_board')
                should_provision_browser_files = t.spec.should_provision_browser_files

              if any(should_provision_browser_files):
                secondary_lacros_paths_list = [
                    t.lacros_gcs_path if p else ''
                    for p in should_provision_browser_files
                ]
                secondary_lacros_paths = ','.join(secondary_lacros_paths_list)
                cmd.extend(['-secondary-lacros-paths', secondary_lacros_paths])

            if t.spec.bucket and 'chromium' in t.spec.bucket:
              test_args.append('run_private_tests=False')

          for shard in range(t.spec.shards):
            # Create a request for each shard
            shard_cmd = list(cmd)
            shard_test_args = list(test_args)

            shard_test_args.append('shard_index={}'.format(shard))
            shard_test_args.append('total_shards={}'.format(t.spec.shards))
            lacros_gcs_path = os.path.join(t.lacros_gcs_path,
                                           'lacros_compressed.squash')
            shard_test_args.append('lacros_gcs_path={}'.format(lacros_gcs_path))

            shard_cmd.extend(['-test-args', ' '.join(shard_test_args)])
            shard_cmd.append('-exit-early')
            shard_cmd.append(t.spec.autotest_name)

            shard_link_name = (
                t.name if shard == 0 else '{0} ({1})'.format(t.name, shard))
            step_result = self.m.step(
                'schedule' if shard == 0 else 'schedule ({})'.format(shard),
                shard_cmd,
                raise_on_failure=False,
                stdout=self.m.json.output(),
                step_test_data=lambda: self.m.json.test_api.output_stream(
                    {"Launches": [{
                        "Build": {
                            "id": str(889900 + shard),
                        },
                    }]}))

            if step_result.retcode == 0:
              shard_build_id = int(
                  step_result.stdout["Launches"][0]["Build"]["id"])
              presentation.links[
                  shard_link_name] = 'https://ci.chromium.org/b/%s' % shard_build_id

              build_ids_by_tags[t.name].append(shard_build_id)

    return build_ids_by_tags

  def wait_on_suites(self, ctp_builds_by_tag, timeout_seconds):
    """Wait for the CTP builds to complete and return their test runner builds.

    Args:
      ctp_builds_by_tag: A dict of CTP build IDs (a list), keyed by request tag.
      timeout_seconds: How long to wait for results before
        giving up.

    Returns:
      A dict of request tag to dict of CTP build (the shard request) to list of
        test_runner attempts
    """
    with self.m.step.nest('collect skylab results'):
      # collect_builds() may hit timeout, but it does not mean
      # all tests are aborted. Some tests may still have exported
      # results to RDB. So an exception or failure here should not
      # block following steps.
      all_build_ids = []
      for ids in ctp_builds_by_tag.values():
        all_build_ids += ids
      try:
        self.m.buildbucket.collect_builds(
            all_build_ids, timeout=timeout_seconds)
      except self.m.step.StepFailure:
        pass
    # TODO(crbug.com/1245438): Remove below once the test runner's invocation
    # is included into its parent build's invocation.
    with self.m.step.nest('find test runner build'):
      test_runners_by_tag = {}
      for test_suite, shard_ctp_build_ids in ctp_builds_by_tag.items():
        # For each shard's CTP build, get any attempts (runner builds)
        for shard_ctp_build_id in shard_ctp_build_ids:
          builds = self.m.buildbucket.search(
              builds_service_pb2.BuildPredicate(
                  builder=builder_common_pb2.BuilderID(
                      project='chromeos',
                      bucket='test_runner',
                      builder='test_runner',
                  ),
                  tags=[
                      common_pb2.StringPair(
                          key='parent_buildbucket_id',
                          value=str(shard_ctp_build_id))
                  ],
                  include_experimental=self.m.runtime.is_experimental))
          if test_suite not in test_runners_by_tag:
            test_runners_by_tag[test_suite] = {}
          test_runners_by_tag[test_suite][shard_ctp_build_id] = builds

    return test_runners_by_tag

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
    )
