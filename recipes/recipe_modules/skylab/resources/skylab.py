#!/usr/bin/env vpython3
# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Browser infra version of crosfleet command.

Since crosfleet is not supported for automatated usage, we created this helper
script to map browser test to a cros_test_platform request and run in CrOS lab.
"""

import argparse
import base64
import json
import logging
import os
import re
import subprocess
import sys
import zlib

import requests
from google.protobuf import json_format

# Buildbucket v2 API
BUILDBUCKET_RPC = 'https://beefy-dot-cr-buildbucket.appspot.com/prpc'
BUILDBUCKET_GET_ENDPOINT = (BUILDBUCKET_RPC + '/buildbucket.v2.Builds/GetBuild')
BUILDBUCKET_SCHEDULE_ENDPOINT = (
    BUILDBUCKET_RPC + '/buildbucket.v2.Builds/ScheduleBuild')
CONTAINER_METADATA_LOC = 'metadata/containers.jsonpb'


def get_oauth_token(json_creds=None):
  """Get an oauth2 token to access infra services."""
  token = None
  cmd = ['luci-auth', 'token']
  if json_creds:
    cmd.extend(['--service-account-json', json_creds])
  stdout = subprocess.check_output(cmd, text=True)
  lines = stdout.splitlines()
  if len(lines) != 1 or ' ' in stdout:
    raise Exception('Bad token output:\n%s' % stdout)
  token = lines[0]
  return token


def _call_buildbucket(bb_request_data, json_creds, end_point):
  headers = {'content-type': 'application/json', 'accept': 'application/json'}
  token = get_oauth_token(json_creds)
  headers['authorization'] = 'Bearer %s' % token
  r = requests.post(end_point, data=bb_request_data, headers=headers)
  if r.status_code != requests.codes.ok:
    logging.warning('Fetch failed (%s %s)\n%s', r.status_code, r.reason, r.text)
    r.raise_for_status()
    return None
  content = r.content.decode('utf-8')
  return json.loads(content[content.find('\n') + 1:])


def _check_build_status(url, opts):
  if not url:
    return None
  match = re.search(
      r'https://ci.chromium.org/p/chromeos/builders/test_runner/'
      r'test_runner[-a-z]*/b(\d+)', url)
  if not match:
    logging.error('Could not find results of the test runner build.')
    return None
  bb_request_data = json.dumps({
      'id': match.group(1),
      'mask': {
          'fields': 'status',
      }
  })
  resp = _call_buildbucket(bb_request_data, opts.json_creds,
                           BUILDBUCKET_GET_ENDPOINT)
  return resp.get('status')


def schedule_skylab_tests(opts):

  # Import the protobuf binding for cros_test_platform requests(the entry
  # point for Skylab) from chromite. As the script lives in recipe repo,
  # it has to receive the chromium/src path from the input.
  # pylint: disable=import-outside-toplevel
  from chromite.api.gen.chromiumos.test.api import ctp2_pb2 as ctpv2

  v2req = ctpv2.CTPv2Request()
  for i in opts.shard_indexes or range(opts.total_shards):
    req = v2req.requests.add()
    _bucket = opts.bucket.replace('gs://', '').rstrip('/')
    gs_url = f'gs://{_bucket}/{opts.image}'
    root_schedule_targets = req.schedule_targets.add()
    schedule_targets = root_schedule_targets.targets.add()
    schedule_targets.sw_target.legacy_sw.gcs_path = gs_url
    schedule_targets.hw_target.legacy_hw.board = opts.board
    if opts.bucket:
      sw_kv = schedule_targets.sw_target.legacy_sw.key_values.add()
      sw_kv.key = 'chromeos_build_gcs_bucket'
      sw_kv.value = _bucket
    sw_kv = schedule_targets.sw_target.legacy_sw.key_values.add()
    sw_kv.key = 'chromeos_build'
    sw_kv.value = opts.image
    req.pool = opts.pool
    if opts.pool == 'DUT_POOL_QUOTA':
      req.scheduler_info.scheduler = ctpv2.SchedulerInfo.SCHEDUKE
    else:
      req.scheduler_info.scheduler = ctpv2.SchedulerInfo.QSCHEDULER
    req.scheduler_info.qs_account = opts.qs_account

    if opts.cbx:
      assert False, "Not supported in CTPv2's hw_target"
    req.suite_request.maximum_duration.seconds = opts.timeout_mins * 60

    if opts.retry >= 0:
      req.suite_request.retry_count = opts.retry if opts.retry > 0 else 99999

    if opts.model:
      schedule_targets.hw_target.legacy_hw.model = opts.model

    _test_args = (f'{opts.test_args} '
                  f'lacros_gcs_path={opts.lacros_gcs_path} '
                  f'total_shards={opts.total_shards} '
                  f'shard_index={i} '
                  'is_cft=True')

    autotest_name = opts.autotest_name.replace('tauto.', '')
    autotest_name = f'tauto.{autotest_name}'

    if opts.strip:
      _test_args += ' chrome_deploy_strip=True'

    if opts.secondary_boards:
      assert len(opts.secondary_boards) == len(opts.secondary_images) == len(
          opts.secondary_lacros_gcs_path), (
              'Length of --secondary-lacros-gcs-path and --secondary-images '
              'must match --secondary-boards. Pass empty string if not '
              'require CrOS and Lacros provision.')

      for board, img in zip(opts.secondary_boards, opts.secondary_images):
        secondary_targets = root_schedule_targets.targets.add()
        secondary_targets.hw_target.legacy_hw.board = board
        secondary_targets.sw_target.legacy_sw.gcs_path = f'gs://{_bucket}/{img}'
        if opts.bucket:
          sw_kv = secondary_targets.sw_target.legacy_sw.key_values.add()
          sw_kv.key = 'chromeos_build_gcs_bucket'
          sw_kv.value = _bucket
        sw_kv = secondary_targets.sw_target.legacy_sw.key_values.add()
        sw_kv.key = 'chromeos_build'
        sw_kv.value = img

      if any(opts.secondary_lacros_gcs_path):
        _test_args += (' secondary_lacros_gcs_path'
                       f'={",".join(opts.secondary_lacros_gcs_path)}')

    # TODO(b/407636890): use more meaningful name for suite/shard name.
    req.suite_request.test_suite.name = f'{opts.chromium_suite_name}-shard-{i}'
    test_case = req.suite_request.test_suite.test_case_ids.test_case_ids.add()
    test_case.value = autotest_name
    # TODO(b/406664342): migrate to structured
    # req.suite_request.test_suite.execution_metadata.args
    req.suite_request.test_args = _test_args

  bb_request_data = {
      'builder': {
          'project': 'chromeos',
          'bucket': opts.public_builder_bucket or 'testplatform',
          'builder': opts.public_builder or 'cros_test_platform',
      },
      'properties': {
          'ctpv2_request': json_format.MessageToDict(v2req),
      },
  }
  if opts.parent_build_id:
    bb_request_data['tags'] = [{
        # "cros_test_platform" uses this tag to track the parent.
        "key": "parent_buildbucket_id",
        "value": opts.parent_build_id,
    }]

    # TODO(b/383918630): Uncomment the below line. This causes the permission
    # error for some reason. See the issue for the detail.
    # bb_request_data['parentBuildId'] = opts.parent_build_id
    bb_request_data['canOutliveParent'] = 0  # 0 is UNSET

  bb_request_data_json = json.dumps(bb_request_data)
  resp = _call_buildbucket(bb_request_data_json, opts.json_creds,
                           BUILDBUCKET_SCHEDULE_ENDPOINT)
  if opts.json_outfile:
    with open(opts.json_outfile, 'w', encoding='utf-8') as json_file:
      json.dump({'ctp_build_id': resp['id']}, json_file)
  else:
    logging.info('ctp_build_id: %s\n', resp['id'])


def read_ctp_results(opts):
  # pylint: disable=import-outside-toplevel
  from chromite.api.gen.test_platform.steps import execution_pb2 as ctp_resp

  bb_request_data = json.dumps({
      'id': opts.ctp_build_id,
      "mask": {
          "fields": "output.properties",
      }
  })
  resp = _call_buildbucket(bb_request_data, opts.json_creds,
                           BUILDBUCKET_GET_ENDPOINT)

  compressed_proto = resp.get('output', {}).get('properties',
                                                {}).get('compressed_responses')
  if not compressed_proto:
    logging.error('Could not find results in build\'s output properties.')
    return
  wire_format = zlib.decompress(base64.b64decode(compressed_proto))
  sharded_resp = ctp_resp.ExecuteResponses.FromString(
      wire_format).tagged_responses

  res = {}
  for k, v in sharded_resp.items():
    task_result = v.task_results[-1]

    # The field name should align with TestRunner defined in ../test_runner.py.
    res[k] = {
        'url': task_result.task_url,
        'log_url': task_result.log_data.testhaus_url,
        'status': _check_build_status(task_result.task_url, opts),
    }
  if opts.json_outfile:
    with open(opts.json_outfile, 'w', encoding='utf-8') as json_file:
      json.dump(res, json_file)
  else:
    logging.info(res)


def main(args):
  p = argparse.ArgumentParser(
      description='Call Buildbucket API to schedule chromium test on Skylab.')
  p.add_argument(
      '--json-outfile', help='Output file to pass results to recipes.')
  p.add_argument(
      '--json-creds',
      help='Path to saved credentials file (e.g. for service accounts).')
  p.add_argument(
      '--chromium-src',
      type=str,
      help='The local path of chromium/src, where we can import the CTP '
      'request and response proto bindings from third_party/chromite.')
  subparsers = p.add_subparsers()

  # Subcommand: request
  subparser = subparsers.add_parser(
      'request', help=('Schedule Skylab test requests via Buildbucket API.'))
  subparser.add_argument('--board', type=str, help='ChromeOS board name.')
  subparser.add_argument(
      '--model', type=str, default=None, help='ChromeOS model name.')
  subparser.add_argument(
      '--cbx', action='store_true', help='Require CBX model.')
  subparser.add_argument(
      '--bucket',
      type=str,
      default='gs://chromeos-image-archive',
      help='GCS bucket to pass browser artifacts to Skylab.')
  subparser.add_argument(
      '--public-builder',
      type=str,
      default=None,
      help='Buildbucket builder for public Skylab, aka Chromium CQ tests.')
  subparser.add_argument(
      '--public-builder-bucket',
      type=str,
      default=None,
      help='Buildbucket bucket for public Skylab, aka Chromium CQ tests.')
  subparser.add_argument(
      '--parent-build-id',
      type=str,
      default=None,
      help='Parent Buildbucket ID if any.')
  subparser.add_argument(
      '--pool', type=str, default='DUT_POOL_QUOTA', help='Skylab pool.')
  subparser.add_argument(
      '--image', type=str, help='ChromeOS image for the board to run tests.')
  subparser.add_argument(
      '--lacros-gcs-path',
      type=str,
      default='',
      help='The full GCS path to the lacros artifact for the test.')
  subparser.add_argument(
      '--secondary-boards',
      type=str,
      action='append',
      help='Secondary board for nearby tests. May be repeated.')
  subparser.add_argument(
      '--secondary-images',
      type=str,
      action='append',
      help='CrOS image for the secondary boards. May be repeated.')
  subparser.add_argument(
      '--secondary-lacros-gcs-path',
      type=str,
      action='append',
      help='The full GCS path to the lacros artifact for secondary board of '
      'nearby tests. May be repeated.')
  subparser.add_argument(
      '--qs-account',
      type=str,
      default='',
      help='Quota account for the tests thus test could get prioritized to run.'
  )
  subparser.add_argument(
      '--timeout-mins',
      type=int,
      default=60,
      help='Timeout in minute for the CTP build.')
  subparser.add_argument(
      '--retry',
      type=int,
      default=-1,
      help='Specify the maximum number of retries (zero for infinite retries '
      'until timeout, or negative number for no retry).')
  subparser.add_argument(
      '--total-shards', type=int, default=1, help='Total shards.')
  subparser.add_argument(
      '--shard-indexes',
      type=int,
      action='append',
      help='Shard index. May be repeated for multiple shards. If specified, '
      'total shards will be ignored and only invoke assigned shards.')
  subparser.add_argument(
      '--chromium-suite-name',
      type=str,
      default='',
      help='Test suite name on Chromium config')
  subparser.add_argument(
      '--autotest-name', type=str, default='', help='Autotest wrapper name.')
  subparser.add_argument(
      '--test-args',
      type=str,
      default='',
      help='The test arguments to pass to the autotest wrapper.')
  subparser.add_argument(
      '--strip', action='store_true', help='Strip Chrome before deploy.')
  subparser.set_defaults(func=schedule_skylab_tests)

  # Subcommand: read build results.
  subparser = subparsers.add_parser(
      'response', help=('Read the result of cros_test_platform build.'))
  subparser.add_argument(
      '--ctp-build-id', type=str, help='The CTP build ID to query.')
  subparser.set_defaults(func=read_ctp_results)

  opts = p.parse_args(args)
  logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s')
  sys.path.insert(0, os.path.join(opts.chromium_src, 'third_party'))
  opts.func(opts)


if '__main__' == __name__:
  sys.exit(main(sys.argv[1:]))
