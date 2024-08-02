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
