#!/usr/bin/env vpython3
# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Browser infra version of crosfleet command.

Since crosfleet is not supported for automatated usage, we created this helper
script to map browser test to a cros_test_platform request and run in CrOS lab.
"""

from __future__ import annotations

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
from collections import namedtuple

# Buildbucket v2 API
BUILDBUCKET_RPC = 'https://beefy-dot-cr-buildbucket.appspot.com/prpc'
BUILDBUCKET_GET_ENDPOINT = (BUILDBUCKET_RPC + '/buildbucket.v2.Builds/GetBuild')
BUILDBUCKET_SCHEDULE_ENDPOINT = (
    BUILDBUCKET_RPC + '/buildbucket.v2.Builds/ScheduleBuild')
BUILDBUCKET_SEARCH_ENDPOINT = (
    BUILDBUCKET_RPC + '/buildbucket.v2.Builds/SearchBuilds')
CONTAINER_METADATA_LOC = 'metadata/containers.jsonpb'

Shard = namedtuple('Shard', ['tr_attempt', 'shard'])

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


def _list_child_trs_status(ctp_bbid, json_creds):
  bb_request_data = {
      'predicate': {
          'childOf': str(ctp_bbid),
      },
      'pageSize': 1000,
      'mask': {
          'fields': 'id,tags,output.status',
      },
  }
  return _call_buildbucket(
      json.dumps(bb_request_data), json_creds, BUILDBUCKET_SEARCH_ENDPOINT)


def _fix_test_runner_status(task_result):
  # pylint: disable=import-outside-toplevel
  from chromite.api.gen.test_platform import taskstate_pb2

  for v in task_result.prejob_steps:
    if v.verdict not in [
        taskstate_pb2.TaskState.VERDICT_PASSED,
        taskstate_pb2.TaskState.VERDICT_PASSED_ON_RETRY
    ]:
      return "INFRA_FAILURE"
  failing_tests = []
  total_tests = []
  for v in task_result.test_cases:
    if v.name == "tauto.tast.chrome-from-gcs":
      continue
    total_tests.append(v)
    if v.verdict not in [
        taskstate_pb2.TaskState.VERDICT_PASSED,
        taskstate_pb2.TaskState.VERDICT_PASSED_ON_RETRY
    ]:
      failing_tests.append(v)

  if not total_tests:
    return "INFRA_FAILURE"
  if failing_tests:
    return "FAILURE"
  return "SUCCESS"


def _populate_suite_request_for_autotest(opts, test_suite, shard_idx):
  test_args = opts.test_arg[:]
  test_args.append(['lacros_gcs_path', opts.lacros_gcs_path])
  test_args.append(['total_shards', str(opts.total_shards)])
  test_args.append(['shard_index', str(shard_idx)])
  test_args.append(['is_cft', 'True'])

  autotest_name = opts.autotest_name.replace('tauto.', '')
  autotest_name = f'tauto.{autotest_name}'

  if opts.strip:
    test_args.append(('chrome_deploy_strip', 'True'))

  if opts.secondary_boards:
    if any(opts.secondary_lacros_gcs_path):
      test_args.append([
          'secondary_lacros_gcs_path', ','.join(opts.secondary_lacros_gcs_path)
      ])

  for test_arg in test_args:
    arg = test_suite.execution_metadata.args.add()
    arg.flag = test_arg[0]
    arg.value = test_arg[1]

  return autotest_name


def _populate_req_common(opts, req, cft_ash_chrome_provision):

  # Import the protobuf binding for cros_test_platform requests(the entry
  # point for Skylab) from chromite. As the script lives in recipe repo,
  # it has to receive the chromium/src path from the input.
  # pylint: disable=import-outside-toplevel
  from chromite.api.gen.chromiumos.test.api import ctp2_pb2 as ctpv2

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
  if opts.pool in ('DUT_POOL_QUOTA', 'chrome-gpu'):
    req.scheduler_info.scheduler = ctpv2.SchedulerInfo.SCHEDUKE
  else:
    req.scheduler_info.scheduler = ctpv2.SchedulerInfo.QSCHEDULER
  req.scheduler_info.qs_account = opts.qs_account
  if opts.ash_chrome_gcs_path and cft_ash_chrome_provision:
    sw_kv = schedule_targets.sw_target.legacy_sw.key_values.add()
    sw_kv.key = 'ash_chrome_gcs_path'
    sw_kv.value = opts.ash_chrome_gcs_path
  if opts.ash_chrome_build_output_dir and cft_ash_chrome_provision:
    sw_kv = schedule_targets.sw_target.legacy_sw.key_values.add()
    sw_kv.key = 'ash_chrome_build_output_dir'
    sw_kv.value = opts.ash_chrome_build_output_dir
  if opts.ash_chrome_gcs_path and cft_ash_chrome_provision and opts.pool == 'DUT_POOL_QUOTA':
    try:
      out = subprocess.check_output(
          ['gsutil', 'ls', '-l', opts.ash_chrome_gcs_path]).decode('utf-8')
      size_gb = int(out.split('\n', maxsplit=1)[0].split(
          ' ', maxsplit=1)[0]) / 1024.0 / 1024.0 / 1024.0
      # For tar.zstd greater than 3GB, we request a large bot (physical drone
      # or larger cloudbots) to ensure the disk have enough space to extract
      # the tarball.
      # On average, build artifacts are compressed around 1:4-1:5, this means
      # bots with around 15GB of remaining disk probably can hold the
      # extraction.
      if size_gb > 3:
        schedule_targets.hw_target.legacy_hw.swarming_dimensions.append(
            'label-bot_size:BOT_SIZE_LARGE')
    except Exception as e:
      logging.warning('gsutil error: %s', str(e))

  if opts.cbx:
    assert False, "Not supported in CTPv2's hw_target"
  req.suite_request.maximum_duration.seconds = opts.timeout_mins * 60

  if opts.retry >= 0:
    req.suite_request.retry_count = opts.retry if opts.retry > 0 else 99999

  if opts.model:
    schedule_targets.hw_target.legacy_hw.model = opts.model

  assert len(opts.secondary_boards) == len(opts.secondary_images) == len(
      opts.secondary_lacros_gcs_path), (
          'Length of --secondary-lacros-gcs-path and --secondary-images '
          'must match --secondary-boards. Pass empty string if not '
          'require CrOS and Lacros provision.')

  for board, img in zip(opts.secondary_boards, opts.secondary_images):
    assert opts.autotest_name, ('Multi-DUT must be used together with'
                                ' autotest_name specified '
                                '(autotest wrapper).')
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


def _tests_from_file(f, board):
  v = []
  with open(f, encoding='utf-8') as d:
    for l in d:
      stripped = l.strip()
      if stripped == '':
        continue
      if stripped.startswith('#'):
        continue
      splitted = stripped.split('@')
      test_name = splitted[0]
      if len(splitted) > 1 and splitted[1] != board:
        continue
      if not test_name.startswith('tast.'):
        test_name = 'tast.' + test_name
      v.append(test_name)
  return v


def _tests_from_files(root, files, board):
  v = []
  for f in files:
    v.extend(_tests_from_file(os.path.join(root, f), board))
  return list(set(v))


def schedule_skylab_tests(opts):

  # Import the protobuf binding for cros_test_platform requests(the entry
  # point for Skylab) from chromite. As the script lives in recipe repo,
  # it has to receive the chromium/src path from the input.
  # pylint: disable=import-outside-toplevel
  from chromite.api.gen.chromiumos.test.api import ctp2_pb2 as ctpv2

  v2req = ctpv2.CTPv2Request()
  if opts.autotest_name:
    for i in opts.shard_indexes or range(opts.total_shards):
      req = v2req.requests.add()
      _populate_req_common(opts, req, False)

      req.suite_request.test_suite.name = f'{opts.chromium_suite_name}-shard-{i}'
      autotest_name = _populate_suite_request_for_autotest(
          opts, req.suite_request.test_suite, i)
      test_case = req.suite_request.test_suite.test_case_ids.test_case_ids.add()
      test_case.value = autotest_name
  else:
    req = v2req.requests.add()
    _populate_req_common(opts, req, True)
    if opts.cros_ctp_suite_name:
      req.suite_request.test_suite.name = opts.cros_ctp_suite_name
    else:
      req.suite_request.test_suite.name = opts.chromium_suite_name
    for v in opts.cros_test_tags:
      req.suite_request.test_suite.test_case_tag_criteria.tags.append(v)
    for v in opts.cros_test_tags_exclude:
      req.suite_request.test_suite.test_case_tag_criteria.tag_excludes.append(v)
    for v in set(opts.cros_test_names + _tests_from_files(
        opts.chromium_src, opts.cros_test_names_from_file, opts.board)):
      req.suite_request.test_suite.test_case_tag_criteria.test_names.append(v)
    for v in set(opts.cros_test_names_exclude + _tests_from_files(
        opts.chromium_src, opts.cros_test_names_exclude_from_file, opts.board)):
      req.suite_request.test_suite.test_case_tag_criteria.test_name_excludes.append(
          v)
    if opts.cros_test_max_in_shard > 0:
      req.suite_request.max_in_shard = opts.cros_test_max_in_shard
    for test_arg in opts.test_arg:
      arg = req.suite_request.test_suite.execution_metadata.args.add()
      # Python 3.8 does not have removeprefix
      arg.flag = test_arg[0][1:] if test_arg[0].startswith(' ') else test_arg[0]
      arg.value = test_arg[1]


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
  from chromite.api.gen.test_platform.steps import execution_pb2

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
  ctp_resp = execution_pb2.ExecuteResponses.FromString(wire_format)
  logging.info("CTP Responses:\n%s", ctp_resp)
  tagged_resp = ctp_resp.tagged_responses

  task_results = {}
  for name, exec_resp in tagged_resp.items():
    for consolidated_result in exec_resp.consolidated_results:
      if len(consolidated_result.attempts) == 0:
        if '-shard-' in name:  # Legacy autotest
          shard = int(name.split('-shard-')[1])
        else:
          shard = None
        task_results[name] = Shard(None, shard)
        continue
      for attempt in consolidated_result.attempts:
        shard = None
        if '-shard-' in name:  # Legacy autotest
          shard = int(name.split('-shard-')[1])
          k = name
        else:
          try:
            shard = int(attempt.name.split('-shard-')[1])
          except:  # pylint: disable=bare-except
            pass
          k = attempt.name
        is_infra_failure = (_fix_test_runner_status(attempt) == 'INFRA_FAILURE')
        if k not in task_results or (attempt.attempt
                                     > task_results[k].tr_attempt.attempt and
                                     not is_infra_failure):
          task_results[k] = Shard(attempt, shard)

  res = {}
  for k, task_result in task_results.items():
    if not task_result.tr_attempt:
      res[k] = {
          'url': '',
          'shard': task_result.shard,
          'log_url': '',
          'status': 'INFRA_FAILURE',
      }
      continue
    res[k] = {
        'url': task_result.tr_attempt.task_url,
        'shard': task_result.shard,
        'log_url': task_result.tr_attempt.log_data.testhaus_url,
        'status': _fix_test_runner_status(task_result.tr_attempt),
    }

  # Canceled test_runners will not present anything in cros_test_platform's
  # output properties.
  # List all child test_runners to populate canceled builders as INFRA_FAILURE.
  # If cancellation occurs on retry test_runner, ignore and preserve the
  # initial attempt result.
  # Only canceled builder happening at initial attempt for each shard will
  # present INFRA_FAILURE.
  # INFRA_FAILURE will cause any check of whether suite is complete or not to
  # be false, causing suite failures.
  # Additionally, incomplete result may launch a retry_invalid_shard step, the
  # outer recipe will launch a CTP with 10 minutes timeout, making the builder
  # to timeout.
  trs = _list_child_trs_status(opts.ctp_build_id, opts.json_creds)
  for tr in trs['builds']:
    if tr['output']['status'] != 'CANCELED':
      continue
    name = None
    for tr_tag in tr['tags']:
      if tr_tag['key'] == 'display_name':
        name = tr_tag['value']

    if '-retry-' in name:
      continue
    if '-shard-' not in name:
      continue

    tr_shard = int(name.split('-shard-')[-1])
    has_result = False
    for v in res.values():
      if v['shard'] == tr_shard:
        has_result = True
    if has_result:
      continue

    res[f'cancelled-{tr_shard}'] = {
        'url': f"https://ci.chromium.org/b/{tr['id']}",
        'shard': tr_shard,
        'log_url': None,
        'status': 'INFRA_FAILURE',
    }

  if opts.json_outfile:
    with open(opts.json_outfile, 'w', encoding='utf-8') as json_file:
      json.dump(res, json_file)
  else:
    logging.info(json.dumps(res, indent=2))


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
  # TODO(fqj): Remove lacros-gcs-path
  subparser.add_argument(
      '--lacros-gcs-path',
      type=str,
      default='',
      help='The full GCS path to the lacros artifact for the test.')
  subparser.add_argument(
      '--ash-chrome-gcs-path',
      type=str,
      default='',
      help='The full GCS path to the lacros artifact for the test.')
  subparser.add_argument(
      '--ash-chrome-build-output-dir',
      type=str,
      default='',
      help='Build output dir of Chrome.')
  subparser.add_argument(
      '--secondary-boards',
      type=str,
      default=[],
      action='append',
      help='Secondary board for nearby tests. May be repeated.')
  subparser.add_argument(
      '--secondary-images',
      type=str,
      default=[],
      action='append',
      help='CrOS image for the secondary boards. May be repeated.')
  subparser.add_argument(
      '--secondary-lacros-gcs-path',
      type=str,
      default=[],
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
      '--cros-test-tags',
      action='append',
      default=[],
      help='Tags of the tests to run.',
  )
  subparser.add_argument(
      '--cros-test-tags-exclude',
      action='append',
      default=[],
      help='Tags to exclude for the tests to run.',
  )
  subparser.add_argument(
      '--cros-test-names',
      action='append',
      default=[],
      help='Names of the tests to run.',
  )
  subparser.add_argument(
      '--cros-test-names-exclude',
      action='append',
      default=[],
      help='Names to exclude for the tests to run.',
  )
  subparser.add_argument(
      '--cros-test-names-from-file',
      action='append',
      default=[],
      help='Names of the tests to run.',
  )
  subparser.add_argument(
      '--cros-test-names-exclude-from-file',
      action='append',
      default=[],
      help='Names to exclude for the tests to run.',
  )
  subparser.add_argument(
      '--cros-test-max-in-shard',
      type=int,
      default=0,
      help='maximum number of tests in a shard',
  )
  subparser.add_argument(
      '--cros-ctp-suite-name',
      type=str,
      default='',
      help='Use a different suite name in CTP than --chromium-suite-name',
  )
  subparser.add_argument(
      '--test-arg',
      nargs=2,
      action='append',
      metavar=('KEY', 'VALUE'),
      help=('The test arguments to pass to the autotest wrapper. '
            'Repeat multiple times for multiple test arguments.'))
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
