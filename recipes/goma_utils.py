# Copyright (c) 2015 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Functions specific to handle goma related info.
"""

from __future__ import absolute_import
from __future__ import print_function

import datetime
import getpass
import glob
import gzip
import json
import multiprocessing
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tarfile
import tempfile
import time

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(
    0, os.path.abspath(os.path.join(THIS_DIR, os.pardir, 'scripts'))
)

from common import chromium_utils
import bot_utils

# The Google Cloud Storage bucket to store logs related to goma.
GOMA_LOG_GS_BUCKET = 'chrome-goma-log'

# <cmd>.<host>.<user>.log.<severity>.<yyyy><mm><dd>-<hh><mm><ss>.<xxxxx>
FILENAME_TIMESTAMP_PATTERN = re.compile(
    '(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})'
)
TIMESTAMP_PATTERN = re.compile('(\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2})')
TIMESTAMP_FORMAT = '%Y/%m/%d %H:%M:%S'


def GetShortHostname():
  """Get this machine's short hostname in lower case."""
  return socket.gethostname().split('.')[0].lower()


def GetGomaLogDirectory():
  """Get goma's log directory.

  Returns:
    a string of a directory name where goma's log may exist.

  Raises:
    chromium_utils.PathNotFound if it cannot find an available log directory.
  """
  candidates = ['GLOG_log_dir', 'GOOGLE_LOG_DIR', 'TEST_TMPDIR']
  default_dir = None
  if chromium_utils.IsWindows():
    candidates.extend(['TMP', 'TEMP', 'USERPROFILE'])
    # Note: I believe one of environment variables is set for usual Windows
    # environment, let me avoid to check the Windows directory, which we
    # need to use win32api on Python.
  else:
    candidates.extend(['TMPDIR', 'TMP'])
    default_dir = '/tmp'

  for candidate in candidates:
    value = os.environ.get(candidate)
    if value and os.path.isdir(value):
      return value
  if default_dir:
    return default_dir
  raise chromium_utils.PathNotFound('Cannot find Goma log directory.')


def GetLatestGlogInfoFile(pattern):
  """Get a filename of the latest google glog INFO file.

  Args:
    pattern: a string of INFO file pattern.

  Returns:
    the latest glog INFO filename in fullpath.  Or, None if not found.
  """
  dirname = GetGomaLogDirectory()
  info_pattern = os.path.join(dirname, '%s.*.INFO.*' % pattern)
  candidates = glob.glob(info_pattern)
  if not candidates:
    return None
  return sorted(candidates, reverse=True)[0]


def GetLatestGomaCompilerProxyInfo():
  """Get a filename of the latest goma comiler_proxy.INFO."""
  return GetLatestGlogInfoFile('compiler_proxy')


def GetLatestGomaCompilerProxySubprocInfo():
  """Get a filename of the latest goma comiler_proxy-subproc.INFO."""
  return GetLatestGlogInfoFile('compiler_proxy-subproc')


def GetListOfGomaccInfoAfterCompilerProxyStart():
  """Returns list of gomacc.INFO generated after compiler_proxy starts.

  Returns:
    list of gomacc.INFO file path strings.
  """
  compiler_proxy_start_time = GetCompilerProxyStartTime()
  print('listing gomacc logs newer than: %s' % compiler_proxy_start_time)
  recent_gomacc_infos = []
  logs = glob.glob(os.path.join(GetGomaLogDirectory(), 'gomacc.*.INFO.*'))
  print('total %s gomacc logs found' % len(logs))
  for log in logs:
    timestamp = GetLogFileTimestamp(log)
    if timestamp and timestamp > compiler_proxy_start_time:
      recent_gomacc_infos.append(log)
  print('%s gomacc logs are new' % len(recent_gomacc_infos))
  recent_gomacc_infos.sort()
  if len(recent_gomacc_infos) > 100:
    recent_gomacc_infos = recent_gomacc_infos[:50] + recent_gomacc_infos[-50:]
  return recent_gomacc_infos


def SetBuilderIDToCounter(builder_id, counter):
  """Set BuilderID to counter dictionary.

  Args:
    builder_id: BuilderId dictionary to use.
    counter: ts_mon counter dictionary to be updated.
  """
  for key, value in builder_id.items():
    counter[key] = value


def UploadToGomaLogGS(
    file_path,
    gs_filename,
    text_to_append=None,
    metadata=None,
    override_gsutil=None
):
  """Upload a file to Google Cloud Storage (gs://chrome-goma-log).

  Note that the uploaded file would automatically be gzip compressed.

  Args:
    file_path: a path of a file to be uploaded.
    gs_filename: a name of a file in Google Storage.
    metadata: (dict) A dictionary of string key/value metadata entries.
    text_to_append: an addtional text to be added to a file in GS.

  Returns:
    a stored path name without the bucket name in GS.
  """
  hostname = GetShortHostname()
  today = datetime.datetime.utcnow().date()
  log_path = '%s/%s/%s.gz' % (today.strftime('%Y/%m/%d'), hostname, gs_filename)
  gs_path = 'gs://%s/%s' % (GOMA_LOG_GS_BUCKET, log_path)
  temp = tempfile.NamedTemporaryFile(mode='wb', delete=False)
  try:
    with temp as f_out:
      with gzip.GzipFile(fileobj=f_out, mode='wb') as gzipf_out:
        with open(file_path, 'rb') as f_in:
          shutil.copyfileobj(f_in, gzipf_out)
        if text_to_append:
          gzipf_out.write(text_to_append.encode('utf-8'))
    bot_utils.GSUtilCopy(
        temp.name, gs_path, metadata=metadata, override_gsutil=override_gsutil
    )
    print("Copied log file to %s" % gs_path)
  finally:
    os.remove(temp.name)
  return log_path


def UploadGomaCompilerProxyInfo(
    override_gsutil=None,
    builder='unknown',
    builder_id=None,
    is_experimental=False
):
  """Upload compiler_proxy{,-subproc}.INFO and gomacc.INFO to Google Storage.

  Args:
    override_gsutil: gsutil path to override.
    builder: a string name of a builder.
    builder_id: a dictionary that represents BuilderID.
    is_experimental: True if this is experimental build.
  """
  latest_subproc_info = GetLatestGomaCompilerProxySubprocInfo()

  builderinfo = {
      'builder': builder,
      'os': chromium_utils.PlatformName(),
      'is_experimental': is_experimental,
  }
  if builder_id:
    builderinfo['builder_id'] = builder_id

  # Needs to begin with x-goog-meta for custom metadata.
  # https://cloud.google.com/storage/docs/gsutil/addlhelp/WorkingWithObjectMetadata#custom-metadata
  metadata = {'x-goog-meta-builderinfo': json.dumps(builderinfo)}

  if latest_subproc_info:
    UploadToGomaLogGS(
        latest_subproc_info,
        os.path.basename(latest_subproc_info),
        metadata=metadata,
        override_gsutil=override_gsutil
    )
  else:
    print('No compiler_proxy-subproc.INFO to upload')
  latest_info = GetLatestGomaCompilerProxyInfo()
  if not latest_info:
    print('No compiler_proxy.INFO to upload')
    return
  # Since a filename of compiler_proxy.INFO is fairly unique,
  # we might be able to upload it as-is.
  log_path = UploadToGomaLogGS(
      latest_info,
      os.path.basename(latest_info),
      metadata=metadata,
      override_gsutil=override_gsutil
  )
  viewer_url = (
      'https://chromium-build-stats.appspot.com/compiler_proxy_log/' + log_path
  )
  print('Visualization at %s' % viewer_url)

  gomacc_logs = GetListOfGomaccInfoAfterCompilerProxyStart()
  if gomacc_logs:
    UploadGomaccInfo(
        gomacc_logs, metadata=metadata, override_gsutil=override_gsutil
    )
  return viewer_url


def UploadGomaccInfo(gomacc_logs, metadata=None, override_gsutil=None):
  """Upload gomacc logs if any

  Args:
    gomacc_logs: An array that contains paths to gomacc logs
    override_gsutil: gsutil path to override
    metadata: metadata which will be attached as gs metadata
  """

  temp_file = tempfile.NamedTemporaryFile(delete=False)
  try:
    print('creating tar for %s gomacc logs' % len(gomacc_logs))
    with tarfile.TarFile(fileobj=temp_file, mode='w') as tf:
      for log in gomacc_logs:
        tf.add(log, arcname=os.path.basename(log))

    # Taking the first name as gomacc log filename on gs.
    gs_tarfile_name = os.path.basename(gomacc_logs[0]) + '.tar'

    # Since UploadToGomaLogGS opens temp_file.name, we have to
    # close the file here. Otherwise this will fail on Win.
    temp_file.close()

    UploadToGomaLogGS(
        temp_file.name,
        gs_tarfile_name,
        metadata=metadata,
        override_gsutil=override_gsutil
    )
  finally:
    if not temp_file.closed:
      temp_file.close()
    os.remove(temp_file.name)


def UploadNinjaLog(
    outdir,
    compiler,
    command,
    exit_status,
    build_id,
    step_name,
    override_gsutil=None
):
  """Upload .ninja_log to Google Cloud Storage (gs://chrome-goma-log),
  in the same folder with goma's compiler_proxy.INFO.

  Args:
    outdir: a directory that contains .ninja_log.
    compiler: compiler used for the build.
    command: command line.
    exit_status: ninja's exit status.
    build_id: unique build id assigned to LUCI build.
    step_name: name of compile step e.g. "compile (with patch)"
  """
  ninja_log_path = os.path.join(outdir, '.ninja_log')
  try:
    st = os.stat(ninja_log_path)
    mtime = datetime.datetime.fromtimestamp(st.st_mtime)
  except OSError as e:
    print(e)
    return

  cwd = os.getcwd()
  platform = chromium_utils.PlatformName()

  # info['cmdline'] should be list of string for
  # go struct on chromium-build-stats.
  if isinstance(command, str):
    command = [command]

  info = {
      'cmdline': command, 'cwd': cwd, 'platform': platform, 'exit': exit_status,
      'build_id': build_id, 'step_name': step_name, 'env': {}
  }
  for k, v in os.environ.items():
    info['env'][k] = v
  if compiler:
    info['compiler'] = compiler

  compiler_proxy_info = GetLatestGomaCompilerProxyInfo()
  if compiler_proxy_info:
    info['compiler_proxy_info'] = compiler_proxy_info

  username = getpass.getuser()
  hostname = GetShortHostname()
  pid = os.getpid()
  ninja_log_filename = 'ninja_log.%s.%s.%s.%d' % (
      hostname, username, mtime.strftime('%Y%m%d-%H%M%S'), pid
  )
  additional_text = '# end of ninja log\n' + json.dumps(info)
  log_path = UploadToGomaLogGS(
      ninja_log_path,
      ninja_log_filename,
      text_to_append=additional_text,
      override_gsutil=override_gsutil
  )
  viewer_url = 'https://chromium-build-stats.appspot.com/ninja_log/' + log_path
  print('Visualization at %s' % viewer_url)

  return viewer_url


def IsCompilerProxyKilledByFatalError():
  """Returns true if goma compiler_proxy is killed by CHECK or LOG(FATAL)."""
  info_file = GetLatestGomaCompilerProxyInfo()
  if not info_file:
    return False
  fatal_pattern = re.compile(r'^F\d{4} \d{2}:\d{2}:\d{2}\.\d{6} ')
  with open(info_file) as f:
    for line in f.readlines():
      if fatal_pattern.match(line):
        return True
  return False


def MakeGomaExitStatusCounter(
    goma_stats_file,
    goma_crash_report,
    builder='unknown',
    builder_id=None,
):
  """Make Goma exit status counter. This counter indicates compiler_proxy
     has finished without problem, crashed, or killed. This counter will
     be used to alert to goma team.

  Args:
    goma_stats_file: path to goma stats file if any
    goma_crash_report: path to goma crash report file if any
    builder: builder name
    builder_id: a dictionary that represents BuilderID.
  """

  try:
    counter = {
        'name': 'goma/status_luci',
        'value': 1,
        'os': chromium_utils.PlatformName(),
    }
    SetBuilderIDToCounter(builder_id, counter)

    if goma_stats_file and os.path.exists(goma_stats_file):
      counter['status'] = 'success'
    elif goma_crash_report and os.path.exists(goma_crash_report):
      counter['status'] = 'crashed'
    elif IsCompilerProxyKilledByFatalError():
      counter['status'] = 'killed'
    else:
      counter['status'] = 'unknown'

    start_time = GetCompilerProxyStartTime()
    if start_time:
      counter['start_time'] = int(time.mktime(start_time.timetuple()))

    return counter
  except Exception as ex:
    print('error while generating status counter: %s' % ex)
    return None


def GetLogFileTimestamp(glog_log):
  """Returns timestamp when the given glog log was created.

  Args:
    glog_log: a filename of a google-glog log.

  Returns:
    datetime instance when the logfile was created.
    Or, returns None if not a glog file.

  Raises:
    IOError if this function cannot open glog_log.
  """
  matched = FILENAME_TIMESTAMP_PATTERN.search(glog_log)
  if matched:
    return datetime.datetime(
        int(matched.group(1)), int(matched.group(2)), int(matched.group(3)),
        int(matched.group(4)), int(matched.group(5)), int(matched.group(6))
    )
  with open(glog_log) as f:
    matched = TIMESTAMP_PATTERN.search(f.readline())
    if matched:
      return datetime.datetime.strptime(matched.group(1), TIMESTAMP_FORMAT)
  return None


def GetCompilerProxyStartTime():
  """Returns timestamp when the latest compiler_proxy started.

  Returns:
    datetime instance of timestamp when the latest compiler_proxy start.
    Or, returns None if not a glog file.
  """
  return GetLogFileTimestamp(GetLatestGomaCompilerProxyInfo())


def MakeGomaStatusCounter(
    json_file,
    exit_status,
    builder='unknown',
    builder_id=None,
):
  """Make latest Goma status counter which will be sent to ts_mon.

  Args:
    json_file: json filename string that has goma_ctl.py jsonstatus.
    exit_status: integer exit status of the build.
    builder: builder name
    builder_id: a dictionary that represents BuilderID.

  Returns:
    counter dict if succeeded. None if failed.
  """
  json_statuses = {}
  try:
    with open(json_file) as f:
      json_statuses = json.load(f)

    if not json_statuses:
      print('no json status is recorded in %s' % json_file)
      return None

    if len(json_statuses.get('notice', [])) != 1:
      print('unknown json statuses style: %s' % json_statuses)
      return None

    json_status = json_statuses['notice'][0]
    if json_status['version'] != 1:
      print('unknown version: %s' % json_status)
      return None

    infra_status = json_status.get('infra_status')

    result = 'success'

    if exit_status is None:
      result = 'exception'
    elif exit_status != 0:
      result = 'failure'
      if (exit_status < 0 or not infra_status or
          infra_status['ping_status_code'] != 200 or
          infra_status.get('num_user_error', 0) > 0):
        result = 'exception'

    num_failure = 0
    ping_status_code = 0
    if infra_status:
      num_failure = infra_status['num_exec_compiler_proxy_failure']
      ping_status_code = infra_status['ping_status_code']

    counter = {
        'name': 'goma/failure_luci', 'value': num_failure,
        'os': chromium_utils.PlatformName(),
        'ping_status_code': ping_status_code, 'result': result
    }
    SetBuilderIDToCounter(builder_id, counter)
    start_time = GetCompilerProxyStartTime()
    if start_time:
      counter['start_time'] = int(time.mktime(start_time.timetuple()))
    return counter

  except Exception as ex:
    print(
        'error while making goma status counter for ts_mon: jons_file=%s: %s' %
        (json_file, ex)
    )
    return None


def MakeGomaFailureReasonCounter(
    json_file,
    exit_status,
    builder='unknown',
    builder_id=None,
):
  """Make latest Goma failure reason counter which will be sent to ts_mon.

  Args:
    json_file: json filename string that has goma_ctl.py jsonstatus.
    exit_status: integer exit status of the build.
    builder: builder name
    builder_id: a dictionary that represents BuilderID.

  Returns:
    counter dict if succeeded. None if failed.
  """
  try:
    with open(json_file) as f:
      json_statuses = json.load(f)

    if not json_statuses:
      print('no json status is recorded in %s' % json_file)
      return None

    if len(json_statuses.get('notice', [])) != 1:
      print('unknown json statuses style: %s' % json_statuses)
      return None

    json_status = json_statuses['notice'][0]
    if json_status['version'] != 1:
      print('unknown version: %s' % json_status)
      return None

    infra_status = json_status.get('infra_status')

    result = 'success'
    reason = 'OK'

    if infra_status is None:
      reason = 'GOMA_SETUP_FAILURE'
    elif infra_status.get('ping_status_code', 200) != 200:
      reason = 'GOMA_PING_FAILURE'
    elif infra_status.get('num_user_error', 0) > 0:
      reason = 'GOMA_BUILD_ERROR'

    if exit_status is None:
      if reason == 'OK':
        # Maybe some failure on goma set up?
        reason = 'EXIT_STATUS_IS_NONE'
    elif exit_status != 0:
      result = 'failure'

    if reason != 'OK':
      result = 'exception'

    counter = {
        'name': 'goma/failure_reason_luci', 'value': 1,
        'os': chromium_utils.PlatformName(), 'result': result,
        'exception_reason': reason
    }
    SetBuilderIDToCounter(builder_id, counter)
    start_time = GetCompilerProxyStartTime()
    if start_time:
      counter['start_time'] = int(time.mktime(start_time.timetuple()))
    return counter

  except Exception as ex:
    print(
        'error while making goma status counter for ts_mon: jons_file=%s: %s' %
        (json_file, ex)
    )
    return None
