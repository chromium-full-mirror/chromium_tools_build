# Copyright (c) 2018 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Functions specific to upload goma related info to BigQuery.
"""

import json
import os
import subprocess
import tempfile
import sys

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(THIS_DIR, os.pardir)))
sys.path.insert(0, os.path.abspath(os.path.join(THIS_DIR, 'goma')))

from google.protobuf import json_format
from recipes.goma import compile_events_pb2
from recipes import goma_utils

# The Google BigQuery dataset and table for CompileEvent.
COMPILE_EVENTS_PROJECT = 'goma-logs'
COMPILE_EVENTS_DATASET = 'client_events'
COMPILE_EVENTS_TABLE = 'compile_events'


def SetStepInfo(json_file, exit_status, step_info):
  """
  Set StepInfo for the given exit status.

  Args:
    json_file: "goma_ctl.py jsonstatus" file.
    exit_status: exit status of a compile step.
    step_info: an instance of compile_events_pb2.StepInfo to be filled.
  """
  try:
    with open(json_file) as f:
      json_statuses = json.load(f)

    if not json_statuses:
      print('no json status is recorded in %s' % json_file)
      return

    if len(json_statuses.get('notice', [])) != 1:
      print('unknown json statuses style: %s' % json_statuses)
      return

    json_status = json_statuses['notice'][0]
    if json_status['version'] != 1:
      print('unknown version: %s' % json_status)
      return
    infra_status = json_status.get('infra_status')

    status = compile_events_pb2.StepInfo.SUCCESS
    goma_failure_reason = compile_events_pb2.StepInfo.GOMA_OK
    if infra_status is None:
      goma_failure_reason = compile_events_pb2.StepInfo.GOMA_SETUP_FAILURE
    elif infra_status.get('ping_status_code', 200) != 200:
      goma_failure_reason = compile_events_pb2.StepInfo.GOMA_PING_FAILURE
    elif infra_status.get('num_user_error', 0) > 0:
      goma_failure_reason = compile_events_pb2.StepInfo.GOMA_BUILD_ERROR

    if exit_status is None:
      if goma_failure_reason == compile_events_pb2.StepInfo.GOMA_OK:
        # Maybe some failure on goma set up?
        goma_failure_reason = compile_events_pb2.StepInfo.GOMA_UNKNOWN_FAILURE
    elif exit_status != 0:
      status = compile_events_pb2.StepInfo.FAILURE

    if goma_failure_reason != compile_events_pb2.StepInfo.GOMA_OK:
      status = compile_events_pb2.StepInfo.EXCEPTION

    step_info.status = status
    step_info.goma_failure_reason = goma_failure_reason

  except Exception as ex:
    print(
        'error while making goma status counter for ts_mon: jons_file=%s: %s' %
        (json_file, ex)
    )
    return


def SendCompileEvent(
    goma_stats_file, goma_counterz_file, json_file, exit_status,
    goma_crash_report, build_id, step_name, bqupload
):
  """Insert CompileEvent to BigQuery table.

  Args:
    goma_stats_file: a file that has binary representation of GomaStats.
    goma_counterz_file: a file that has binary representation of CounterzStats.
    json_file: "goma_ctl.py jsonstatus" file.
    exit_status: exit status of a compile step.
    goma_crash_report: a file that has compiler_proxy crash report id
                       if it crashed.
    build_id: Build ID string.
    step_name: a name of a compile step.
    bqupload: a path to the bqupload command.
  """
  event = compile_events_pb2.CompileEvent()
  event.build_id = build_id
  event.step_name = step_name or ''
  SetStepInfo(json_file, exit_status, event.step_info)
  event.exit_status = compile_events_pb2.CompileEvent.DIED_WITH_UNKOWN_REASON
  try:
    if goma_stats_file and os.path.exists(goma_stats_file):
      with open(goma_stats_file, 'rb') as f:
        event.stats.ParseFromString(f.read())
        event.exit_status = compile_events_pb2.CompileEvent.OK

      if goma_counterz_file and os.path.exists(goma_counterz_file):
        with open(goma_counterz_file, 'rb') as f:
          event.counterz_stats.ParseFromString(f.read())

    else:
      if goma_crash_report and os.path.exists(goma_crash_report):
        with open(goma_crash_report) as f:
          event.crash_id = f.read().strip()
        event.exit_status = compile_events_pb2.CompileEvent.CRASHED
      if goma_utils.IsCompilerProxyKilledByFatalError():
        event.exit_status = compile_events_pb2.CompileEvent.DIED_WITH_LOG_FATAL
    # Since bqupload does not accept a multi-line JSON, let me manually
    # convert a dictionary to JSON.
    json_out = json.dumps(
        json_format.MessageToDict(
            message=event, preserving_proto_field_name=True
        )
    )

    # BQ uploader.
    table_name = '%s.%s.%s' % (
        COMPILE_EVENTS_PROJECT, COMPILE_EVENTS_DATASET, COMPILE_EVENTS_TABLE
    )
    args = [bqupload, table_name]

    # To make bqupload work on Windows, we need to close before bqupload
    # touches it.
    temp = tempfile.NamedTemporaryFile(delete=False, mode='w')
    try:
      with temp as f_out:
        f_out.write(json_out)
      args.append(temp.name)
      subprocess.check_call(args)
      print('Uploaded CompileEvent to BQ %s: %s' % (table_name, args))
    finally:
      os.remove(temp.name)
  except Exception as inst:  # safety net
    print('failed to send CompileEvent to BQ: %s' % inst)
