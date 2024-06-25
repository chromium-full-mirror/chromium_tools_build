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
  from chromite.api.gen.test_platform import request_pb2 as ctp_request

  def _scheduling_for_pool(pool):
    mp = ctp_request.Request.Params.Scheduling.ManagedPool
    if pool == 'DUT_POOL_QUOTA':
      return ctp_request.Request.Params.Scheduling(
          managed_pool=mp.MANAGED_POOL_QUOTA)
    return ctp_request.Request.Params.Scheduling(unmanaged_pool=pool)

  tagged_requests = {}
  for i in opts.shard_indexes or range(opts.total_shards):
    req = ctp_request.Request()
    _bucket = opts.bucket.replace('gs://', '').rstrip('/')
    gs_url = f'gs://{_bucket}/{opts.image}'
    req.params.metadata.test_metadata_url = gs_url
    req.params.metadata.debug_symbols_archive_url = gs_url
    if opts.bucket:
      sw_dep = req.params.software_dependencies.add()
      sw_dep.chromeos_build_gcs_bucket = _bucket
    sw_dep = req.params.software_dependencies.add()
    sw_dep.chromeos_build = opts.image
    req.params.scheduling.CopyFrom(_scheduling_for_pool(opts.pool))
    req.params.scheduling.qs_account = opts.qs_account
    req.params.decorations.tags.append(f'label-board:{opts.board}')
    # TODO(b/242007010): Known issues in CTP that mixes build_target and DUT board.
    # Keep DUT board until issues is fixed.
    req.params.software_attributes.build_target.name = opts.board
    req.params.time.maximum_duration.seconds = opts.timeout_mins * 60

    if opts.retry >= 0:
      # Needs to be critical to enable retry.
      req.params.test_execution_behavior = \
          ctp_request.Request.Params.TestExecutionBehavior.CRITICAL
      req.params.retry.allow = True
      req.params.retry.max = opts.retry

    if opts.model:
      req.params.hardware_attributes.model = opts.model

    _test_args = (f'{opts.test_args} '
                  f'lacros_gcs_path={opts.lacros_gcs_path} '
                  f'total_shards={opts.total_shards} '
                  f'shard_index={i}')

    autotest_name = opts.autotest_name.replace('tauto.', '')
    if opts.run_cft:
      req.params.metadata.container_metadata_url = os.path.join(
          gs_url, CONTAINER_METADATA_LOC)
      req.params.run_via_cft = True
      _test_args += ' is_cft=True'
      autotest_name = f'tauto.{autotest_name}'

    if opts.secondary_boards:
      assert len(opts.secondary_boards) == len(opts.secondary_images) == len(
          opts.secondary_lacros_gcs_path), (
              'Length of --secondary-lacros-gcs-path and --secondary-images '
              'must match --secondary-boards. Pass empty string if not '
              'require CrOS and Lacros provision.')

      for board, img in zip(opts.secondary_boards, opts.secondary_images):
        secondary_device = req.params.secondary_devices.add()
        secondary_sw_dep = secondary_device.software_dependencies.add()
        secondary_sw_dep.chromeos_build = img
        # TODO(b/242007010): Known issues in CTP that mixes build_target and DUT board.
        # Keep DUT board until issues is fixed.
        secondary_device.software_attributes.build_target.name = board

      if any(opts.secondary_lacros_gcs_path):
        _test_args += (' secondary_lacros_gcs_path'
                       f'={",".join(opts.secondary_lacros_gcs_path)}')

    autotest = req.test_plan.test.add()
    autotest.autotest.name = autotest_name
    autotest.autotest.test_args = _test_args
    tagged_requests[str(i)] = json_format.MessageToDict(req)

  bb_request_data = json.dumps({
      'builder': {
          'project': 'chromeos',
          'bucket': opts.public_builder_bucket or 'testplatform',
          'builder': opts.public_builder or 'cros_test_platform',
      },
      'properties': {
          'requests': tagged_requests,
      },
  })
  resp = _call_buildbucket(bb_request_data, opts.json_creds,
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
    # The field name should align with TestRunner defined in ../test_runner.py.
    res[k] = {
        'url': v.task_results[0].task_url,
        'log_url': v.task_results[0].log_data.testhaus_url,
        'status': _check_build_status(v.task_results[0].task_url, opts),
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
      '--autotest-name', type=str, default='', help='Autotest wrapper name.')
  subparser.add_argument(
      '--test-args',
      type=str,
      default='',
      help='The test arguments to pass to the autotest wrapper.')
  subparser.add_argument(
      '--run-cft', action='store_true', help='Run the test on CFT.')
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
