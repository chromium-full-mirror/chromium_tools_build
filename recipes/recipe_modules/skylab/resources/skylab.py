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
import time
import zlib

import requests
from google.protobuf import json_format

# Buildbucket v2 API
BUILDBUCKET_RPC = 'https://beefy-dot-cr-buildbucket.appspot.com/prpc'
BUILDBUCKET_GET_ENDPOINT = BUILDBUCKET_RPC + '/buildbucket.v2.Builds/GetBuild'
BUILDBUCKET_SCHEDULE_ENDPOINT = (
  BUILDBUCKET_RPC + '/buildbucket.v2.Builds/ScheduleBuild'
)
BUILDBUCKET_SEARCH_ENDPOINT = (
  BUILDBUCKET_RPC + '/buildbucket.v2.Builds/SearchBuilds'
)
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


def _call_buildbucket(bb_request_data, json_creds, end_point, retries=3):
  headers = {'content-type': 'application/json', 'accept': 'application/json'}
  token = get_oauth_token(json_creds)
  headers['authorization'] = 'Bearer %s' % token
  for attempt in range(retries):
    try:
      r = requests.post(
        end_point, data=bb_request_data, headers=headers, timeout=60
      )
      if r.status_code == requests.codes.ok:
        content = r.content.decode('utf-8')
        return json.loads(content[content.find('\n') + 1 :])
      logging.warning(
        'Fetch failed (attempt %d/%d: %s %s)\n%s',
        attempt + 1,
        retries,
        r.status_code,
        r.reason,
        r.text,
      )
      if attempt == retries - 1 or r.status_code < 500:
        r.raise_for_status()
    except requests.RequestException as e:
      logging.warning(
        'Request exception (attempt %d/%d): %s', attempt + 1, retries, e
      )
      if attempt == retries - 1:
        raise
    time.sleep(2**attempt)
  return None


def _extract_cros_test_log_url(task_url, child_builds):
  if not task_url:
    return None
  m = re.search(r'/b?(\d+)$', task_url)
  if not m:
    return None
  build_id = m.group(1)
  build = child_builds.get(build_id)
  if not build:
    return None
  for s in build.get('steps', []):
    step_name = s.get('name', '')
    if step_name.startswith('Read Container Log'):
      for log in s.get('logs', []):
        if log.get('name') == 'cros-test Log':
          return log.get('viewUrl')
  return None


def _list_child_trs_status(ctp_bbid, json_creds):
  bb_request_data = {
    'predicate': {
      'childOf': str(ctp_bbid),
    },
    'pageSize': 1000,
    'mask': {
      'fields': 'id,status,tags,steps,output.status',
    },
  }
  return _call_buildbucket(
    json.dumps(bb_request_data), json_creds, BUILDBUCKET_SEARCH_ENDPOINT
  )


def _fix_test_runner_status(task_result):
  from chromite.api.gen.test_platform import taskstate_pb2

  for v in task_result.prejob_steps:
    if v.verdict not in [
      taskstate_pb2.TaskState.VERDICT_PASSED,
      taskstate_pb2.TaskState.VERDICT_PASSED_ON_RETRY,
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
      taskstate_pb2.TaskState.VERDICT_PASSED_ON_RETRY,
    ]:
      failing_tests.append(v)

  if not total_tests:
    return "INFRA_FAILURE"
  if failing_tests:
    return "FAILURE"
  return "SUCCESS"


def _status_from_child_build(tr):
  if tr.get('status') not in ('SUCCESS', 'FAILURE'):
    return 'INFRA_FAILURE'
  has_tests = False
  for s in tr.get('steps', []):
    name = s.get('name', '')
    if not name.startswith('Results|'):
      continue
    sub = name[len('Results|') :]
    if sub.startswith('Provision of '):
      if s.get('status') != 'SUCCESS':
        return 'INFRA_FAILURE'
      continue
    if sub == 'tauto.tast.chrome-from-gcs' or sub.startswith(
      'tauto.tast.chrome-from-gcs '
    ):
      continue
    has_tests = True
  if not has_tests:
    return 'INFRA_FAILURE'
  return tr['status']


def _populate_suite_request_for_autotest(opts, test_suite):
  test_args = opts.test_arg[:]
  test_args.append(['lacros_gcs_path', opts.lacros_gcs_path])
  test_args.append(['total_shards', '1'])
  test_args.append(['shard_index', '0'])
  test_args.append(['is_cft', 'True'])

  autotest_name = opts.autotest_name.replace('tauto.', '')
  autotest_name = f'tauto.{autotest_name}'

  if opts.strip:
    test_args.append(('chrome_deploy_strip', 'True'))

  if opts.secondary_boards:
    if any(opts.secondary_lacros_gcs_path):
      test_args.append(
        ['secondary_lacros_gcs_path', ','.join(opts.secondary_lacros_gcs_path)]
      )

  for test_arg in test_args:
    arg = test_suite.execution_metadata.args.add()
    arg.flag = test_arg[0]
    arg.value = test_arg[1]

  return autotest_name


def _populate_req_common(opts, req, cft_ash_chrome_provision):

  # Import the protobuf binding for cros_test_platform requests(the entry
  # point for Skylab) from chromite. As the script lives in recipe repo,
  # it has to receive the chromium/src path from the input.
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
  if opts.strip and cft_ash_chrome_provision:
    sw_kv = schedule_targets.sw_target.legacy_sw.key_values.add()
    sw_kv.key = 'ash_chrome_strip'
    sw_kv.value = 'true'
  if opts.pool == 'DUT_POOL_QUOTA':
    if _if_require_large_bot(opts.ash_chrome_gcs_path) or _if_require_large_bot(
      opts.lacros_gcs_path
    ):
      schedule_targets.hw_target.legacy_hw.swarming_dimensions.append(
        'label-bot_size:BOT_SIZE_LARGE'
      )

  if opts.cbx:
    assert False, "Not supported in CTPv2's hw_target"
  req.suite_request.maximum_duration.seconds = opts.timeout_mins * 60

  if opts.retry >= 0:
    req.suite_request.retry_count = opts.retry if opts.retry > 0 else 99999

  if opts.model:
    schedule_targets.hw_target.legacy_hw.model = opts.model

  assert (
    len(opts.secondary_boards)
    == len(opts.secondary_images)
    == len(opts.secondary_lacros_gcs_path)
  ), (
    'Length of --secondary-lacros-gcs-path and --secondary-images '
    'must match --secondary-boards. Pass empty string if not '
    'require CrOS and Lacros provision.'
  )

  for board, img in zip(opts.secondary_boards, opts.secondary_images):
    assert opts.autotest_name, (
      'Multi-DUT must be used together with'
      ' autotest_name specified '
      '(autotest wrapper).'
    )
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


def _clear_builder_name(builder_name):
  pattern = r'(?:-(?:tests|tast|gtest-and-tast))+$'
  return re.sub(pattern, '', builder_name)


def _tests_from_file(f, board, builder_name):
  v = []
  with open(f, encoding='utf-8') as d:
    for l in d:  # noqa: E741
      stripped = l.strip()
      if stripped == '':
        continue
      if stripped.startswith('#'):
        continue
      splitted = stripped.split('@')
      test_name = splitted[0]
      include = False
      if len(splitted) == 1:
        include = True
      elif splitted[1] == board:
        include = True
      elif _clear_builder_name(splitted[1]) == _clear_builder_name(
        builder_name
      ):
        include = True
      if not include:
        continue
      if not test_name.startswith('tast.'):
        test_name = 'tast.' + test_name
      v.append(test_name)
  return v


def _tests_from_files(root, files, board, builder_name):
  v = []
  for f in files:
    v.extend(_tests_from_file(os.path.join(root, f), board, builder_name))
  return list(set(v))


def _if_require_large_bot(path):
  if not path:
    return False
  try:
    out = subprocess.check_output(['gsutil', 'ls', '-l', path]).decode('utf-8')
    size_gb = (
      int(out.split('\n', maxsplit=1)[0].split(' ', maxsplit=1)[0])
      / 1024.0
      / 1024.0
      / 1024.0
    )
    # For tar.zstd greater than 3GB, we request a large bot (physical drone
    # or larger cloudbots) to ensure the disk have enough space to extract
    # the tarball.
    # On average, build artifacts are compressed around 1:4-1:5, this means
    # bots with around 15GB of remaining disk probably can hold the
    # extraction.
    if size_gb > 3:
      return True
  except Exception as e:
    logging.warning('gsutil error: %s', str(e))
  return False


def schedule_skylab_tests(opts):

  # Import the protobuf binding for cros_test_platform requests(the entry
  # point for Skylab) from chromite. As the script lives in recipe repo,
  # it has to receive the chromium/src path from the input.
  from chromite.api.gen.chromiumos.test.api import ctp2_pb2 as ctpv2

  v2req = ctpv2.CTPv2Request()
  if opts.autotest_name:
    if opts.total_shards > 1 or any(s != 0 for s in (opts.shard_indexes or [])):
      raise ValueError('Multiple shards are not supported for autotest.')
    req = v2req.requests.add()
    _populate_req_common(opts, req, False)

    req.suite_request.test_suite.name = f'{opts.chromium_suite_name}-shard-0'
    autotest_name = _populate_suite_request_for_autotest(
      opts, req.suite_request.test_suite
    )
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
    for v in set(
      opts.cros_test_names
      + _tests_from_files(
        opts.chromium_src,
        opts.cros_test_names_from_file,
        opts.board,
        opts.builder_name,
      )
    ):
      req.suite_request.test_suite.test_case_tag_criteria.test_names.append(v)
    for v in set(
      opts.cros_test_names_exclude
      + _tests_from_files(
        opts.chromium_src,
        opts.cros_test_names_exclude_from_file,
        opts.board,
        opts.builder_name,
      )
    ):
      req.suite_request.test_suite.test_case_tag_criteria.test_name_excludes.append(
        v
      )
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
      'bucket': opts.ctp_bucket,
      'builder': opts.ctp_builder_name,
    },
    'properties': {
      'ctpv2_request': json_format.MessageToDict(v2req),
    },
  }
  bb_request_data['priority'] = 20
  if opts.parent_build_id:
    bb_request_data['tags'] = [
      {
        # "cros_test_platform" uses this tag to track the parent.
        "key": "parent_buildbucket_id",
        "value": opts.parent_build_id,
      }
    ]
    if not opts.schedule:
      # Use can_outlive_parent so it can be passed to Python-proto bindings.
      bb_request_data['can_outlive_parent'] = 2  # 2 is NO

  bb_request_data_json = json.dumps(bb_request_data)
  if not opts.schedule:
    if opts.json_outfile:
      with open(opts.json_outfile, 'w', encoding='utf-8') as json_file:
        json_file.write(bb_request_data_json)
    else:
      logging.info('request to send: %s', bb_request_data_json)
    return
  resp = _call_buildbucket(
    bb_request_data_json, opts.json_creds, BUILDBUCKET_SCHEDULE_ENDPOINT
  )
  if opts.json_outfile:
    with open(opts.json_outfile, 'w', encoding='utf-8') as json_file:
      json.dump({'ctp_build_id': resp['id']}, json_file)
  else:
    logging.info('ctp_build_id: %s', resp['id'])


def _infra_failure_attempt(ctp_build_id, shard, attempt):
  return {
    'url': f'https://ci.chromium.org/b/{ctp_build_id}',
    'shard': shard,
    'attempt': attempt,
    'log_url': '',
    'status': 'INFRA_FAILURE',
  }


def _attempt_from_child_build(tr, shard, attempt, child_builds):
  tr_id = str(tr['id'])
  task_url = f'https://ci.chromium.org/b/{tr_id}'
  res = {
    'url': task_url,
    'shard': shard,
    'attempt': attempt,
    'log_url': _extract_cros_test_log_url(task_url, child_builds) or '',
    'status': _status_from_child_build(tr),
  }
  if any(
    s.get('name', '').startswith('Results|tast.') for s in tr.get('steps', [])
  ):
    res['log_dir'] = (
      'https://tests.chromeos.goog/p/chromeos/logs/unified/'
      f'invocations/build-{tr_id}?treeQuery=cros-test'
    )
  return res


def _attempt_from_proto(attempt, shard, child_builds):
  cros_test_log_url = _extract_cros_test_log_url(attempt.task_url, child_builds)
  testhaus_url = attempt.log_data.testhaus_url
  if testhaus_url and 'tests.chromeos.goog' in testhaus_url:
    separator = '&' if '?' in testhaus_url else '?'
    testhaus_url += f'{separator}treeQuery=cros-test'

  res = {
    'url': attempt.task_url,
    'shard': shard,
    'attempt': attempt.attempt,
    'log_url': cros_test_log_url or '',
    'status': _fix_test_runner_status(attempt),
  }
  if any(tc.name.startswith('tast.') for tc in attempt.test_cases):
    res['log_dir'] = testhaus_url
  return res


def _iter_scheduled_attempts(ctp_resp):
  for s in ctp_resp.get('steps', []):
    step_name = s.get('name', '')
    if '|Schedule tasks|' not in step_name or '-shard-' not in step_name:
      continue
    shards_found = re.findall(r'-shard-(\d+)', step_name)
    if not shards_found:
      continue
    shard = int(shards_found[-1])
    m_retry = re.search(r'-retry-(\d+)', step_name)
    attempt = int(m_retry.group(1)) if m_retry else 0
    m_bid = re.search(r'/b(\d+)', s.get('summaryMarkdown', ''))
    child_id = m_bid.group(1) if m_bid else None
    yield shard, attempt, child_id


def _iter_proto_attempts(tagged_resp):
  for name, exec_resp in tagged_resp.items():
    is_autotest = '-shard-' in name
    for consolidated_result in exec_resp.consolidated_results:
      for attempt in consolidated_result.attempts:
        if is_autotest:
          shard = 0
        else:
          m = re.search(r'-shard-(\d+)', attempt.name)
          shard = int(m.group(1)) if m else 0
        yield shard, attempt.attempt, attempt


def read_ctp_results(opts):
  from chromite.api.gen.test_platform.steps import execution_pb2

  bb_request_data = json.dumps(
    {
      'id': opts.ctp_build_id,
      "mask": {
        "fields": "status,steps,output.properties",
      },
    }
  )
  resp = _call_buildbucket(
    bb_request_data, opts.json_creds, BUILDBUCKET_GET_ENDPOINT
  )

  compressed_proto = (
    resp.get('output', {}).get('properties', {}).get('compressed_responses')
  )
  if compressed_proto:
    wire_format = zlib.decompress(base64.b64decode(compressed_proto))
    ctp_resp = execution_pb2.ExecuteResponses.FromString(wire_format)
    logging.info("CTP Responses:\n%s", ctp_resp)
    tagged_resp = ctp_resp.tagged_responses
  else:
    logging.error('Could not find results in build\'s output properties.')
    tagged_resp = {}

  search_resp = _list_child_trs_status(opts.ctp_build_id, opts.json_creds)
  child_builds = {str(b.get('id')): b for b in search_resp.get('builds', [])}

  # Keyed by (shard, attempt) -> Attempt dict.
  attempts = {}

  # 1. Base layer: every scheduled attempt in CTP 'Schedule tasks' steps.
  for shard, attempt, child_id in _iter_scheduled_attempts(resp):
    tr = child_builds.get(child_id)
    if tr:
      attempts[(shard, attempt)] = _attempt_from_child_build(
        tr, shard, attempt, child_builds
      )
    else:
      attempts[(shard, attempt)] = _infra_failure_attempt(
        opts.ctp_build_id, shard, attempt
      )

  # 2. Override with consolidated_results proto when available.
  for shard, attempt, proto_attempt in _iter_proto_attempts(tagged_resp):
    attempts[(shard, attempt)] = _attempt_from_proto(
      proto_attempt, shard, child_builds
    )

  # 3. Fallback if CTP failed before scheduling any tasks.
  if not attempts:
    attempts[(0, 0)] = _infra_failure_attempt(opts.ctp_build_id, 0, 0)

  # Group by shard in sorted (shard, attempt) order.
  shards = {}
  for (shard, _), attempt_data in sorted(attempts.items()):
    shards.setdefault(shard, []).append(attempt_data)
  res = list(shards.values())

  if opts.json_outfile:
    with open(opts.json_outfile, 'w', encoding='utf-8') as json_file:
      json.dump(res, json_file)
  else:
    logging.info(json.dumps(res, indent=2))


def main(args):
  p = argparse.ArgumentParser(
    description='Call Buildbucket API to schedule chromium test on Skylab.'
  )
  p.add_argument(
    '--json-outfile', help='Output file to pass results to recipes.'
  )
  p.add_argument(
    '--json-creds',
    help='Path to saved credentials file (e.g. for service accounts).',
  )
  p.add_argument(
    '--chromium-src',
    type=str,
    help='The local path of chromium/src, where we can import the CTP '
    'request and response proto bindings from third_party/chromite.',
  )
  subparsers = p.add_subparsers()

  # Subcommand: request
  subparser = subparsers.add_parser(
    'request', help=('Schedule Skylab test requests via Buildbucket API.')
  )
  subparser.add_argument(
    '--schedule',
    action='store_true',
    default=False,
    help=(
      'Directly send ScheduleBuild request to Buildbucket (for local '
      'testing). Default is off (only generate/output request JSON).'
    ),
  )
  subparser.add_argument(
    '--builder-name', type=str, help='Buildbucket builder name.'
  )
  subparser.add_argument('--board', type=str, help='ChromeOS board name.')
  subparser.add_argument(
    '--model', type=str, default=None, help='ChromeOS model name.'
  )
  subparser.add_argument(
    '--cbx', action='store_true', help='Require CBX model.'
  )
  subparser.add_argument(
    '--bucket',
    type=str,
    default='gs://chromeos-image-archive',
    help='GCS bucket to pass browser artifacts to Skylab.',
  )
  subparser.add_argument(
    '--ctp-builder-name',
    type=str,
    default='cros_test_platform',
    help='Buildbucket builder for CTP.',
  )
  subparser.add_argument(
    '--ctp-bucket',
    type=str,
    default='testplatform',
    help='Buildbucket bucket for CTP.',
  )
  subparser.add_argument(
    '--parent-build-id',
    type=str,
    default=None,
    help='Parent Buildbucket ID if any.',
  )
  subparser.add_argument(
    '--pool', type=str, default='DUT_POOL_QUOTA', help='Skylab pool.'
  )
  subparser.add_argument(
    '--image', type=str, help='ChromeOS image for the board to run tests.'
  )
  # TODO(fqj): Remove lacros-gcs-path
  subparser.add_argument(
    '--lacros-gcs-path',
    type=str,
    default='',
    help='The full GCS path to the lacros artifact for the test.',
  )
  subparser.add_argument(
    '--ash-chrome-gcs-path',
    type=str,
    default='',
    help='The full GCS path to the lacros artifact for the test.',
  )
  subparser.add_argument(
    '--ash-chrome-build-output-dir',
    type=str,
    default='',
    help='Build output dir of Chrome.',
  )
  subparser.add_argument(
    '--secondary-boards',
    type=str,
    default=[],
    action='append',
    help='Secondary board for nearby tests. May be repeated.',
  )
  subparser.add_argument(
    '--secondary-images',
    type=str,
    default=[],
    action='append',
    help='CrOS image for the secondary boards. May be repeated.',
  )
  subparser.add_argument(
    '--secondary-lacros-gcs-path',
    type=str,
    default=[],
    action='append',
    help='The full GCS path to the lacros artifact for secondary board of '
    'nearby tests. May be repeated.',
  )
  subparser.add_argument(
    '--qs-account',
    type=str,
    default='',
    help='Quota account for the tests thus test could get prioritized to run.',
  )
  subparser.add_argument(
    '--timeout-mins',
    type=int,
    default=60,
    help='Timeout in minute for the CTP build.',
  )
  subparser.add_argument(
    '--retry',
    type=int,
    default=-1,
    help='Specify the maximum number of retries (zero for infinite retries '
    'until timeout, or negative number for no retry).',
  )
  subparser.add_argument(
    '--total-shards', type=int, default=1, help='Total shards.'
  )
  subparser.add_argument(
    '--shard-indexes',
    type=int,
    action='append',
    help='Shard index. May be repeated for multiple shards. If specified, '
    'total shards will be ignored and only invoke assigned shards.',
  )
  subparser.add_argument(
    '--chromium-suite-name',
    type=str,
    default='',
    help='Test suite name on Chromium config',
  )
  subparser.add_argument(
    '--autotest-name', type=str, default='', help='Autotest wrapper name.'
  )
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
    help=(
      'The test arguments to pass to the autotest wrapper. '
      'Repeat multiple times for multiple test arguments.'
    ),
  )
  subparser.add_argument(
    '--strip', action='store_true', help='Strip Chrome before deploy.'
  )
  subparser.set_defaults(func=schedule_skylab_tests)

  # Subcommand: read build results.
  subparser = subparsers.add_parser(
    'response', help=('Read the result of cros_test_platform build.')
  )
  subparser.add_argument(
    '--ctp-build-id', type=str, help='The CTP build ID to query.'
  )
  subparser.set_defaults(func=read_ctp_results)

  opts = p.parse_args(args)
  logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s')
  sys.path.insert(0, os.path.join(opts.chromium_src, 'third_party'))
  opts.func(opts)


if '__main__' == __name__:
  sys.exit(main(sys.argv[1:]))
