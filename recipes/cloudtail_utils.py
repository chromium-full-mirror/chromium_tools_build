# Copyright 2020 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import errno
import os
import signal
import subprocess
import sys
import time


def start_cloudtail(args):
  """Write process id of started cloudtail to file object.

  Args:
    args: any type so long as it provides these fields:
          * cloudtail_path
          * cloudtail_project_id
          * cloudtail_log_id
          * cloudtail_log_path
          * pid_file
          * cloudtail_service_account_json (optional)
  """

  kwargs = {}
  if sys.platform == 'win32':
    kwargs['creationflags'] = subprocess.CREATE_NEW_PROCESS_GROUP

  cloudtail_cmd = [
    args.cloudtail_path,
    'tail',
    '--project-id',
    args.cloudtail_project_id,
    '--log-id',
    args.cloudtail_log_id,
    '--path',
    args.cloudtail_log_path,
    '--service-account-json',
    ':gce',
  ]

  proc = subprocess.Popen(cloudtail_cmd, **kwargs)

  with open(args.pid_file, 'w') as f:
    pidstr = str(proc.pid)
    f.write(pidstr)
    print('cloudtail started pid=%s' % pidstr)


def is_running_posix(pid):
  """Returns True if process of pid is running.

  Args:
    pid(int): pid of process which this function checks
              whether it is running or not.

  Returns:
    bool: True if process of pid is running.

  Raises:
    OSError if something happens in os.kill(pid, 0)
  """

  try:
    os.kill(pid, 0)
  except OSError as e:
    if e.errno == errno.ESRCH or e.errno == errno.EPERM:
      return False
    raise e
  return True


class NotDiedError(Exception):
  def __str__(self):
    return "NotDiedError"


class Error(Exception):
  """Raised on something unexpected happens."""


def wait_termination_win(pid):
  """Send CTRL_C_EVENT or SIGINT to pid and wait termination of pid.

  Args:
    pid(int): pid of process which this function waits termination.

  Raises:
    Error: WaitForSingleObject to wait process termination returns neither of
           WAIT_TIMEOUT or WAIT_OBJECT_0 (i.e. termination succeeded).
    NotDiedError: if cloudtail kept on running 10 seconds after it signaled.
  """
  # pylint: disable=import-outside-toplevel
  import win32api
  import win32con
  import win32event
  import winerror
  import pywintypes

  handle = None
  try:
    handle = win32api.OpenProcess(
      win32con.PROCESS_QUERY_INFORMATION | win32con.SYNCHRONIZE, False, pid
    )
    try:
      os.kill(pid, signal.CTRL_C_EVENT)  # pylint: disable=no-member
      print(
        'CTRL_C_EVENT has been sent to process %d. '
        'Going to wait for the process finishes.' % pid
      )
    except WindowsError as e:  # pylint: disable=E0602
      # If a target process does not share terminal, we cannot send Ctrl-C.
      if e[0] == winerror.ERROR_INVALID_PARAMETER:
        os.kill(pid, signal.SIGINT)
        print('SIGINT has been sent to process %d.' % pid)
    ret = win32event.WaitForSingleObject(handle, 10 * 10**3)
    if ret == win32event.WAIT_TIMEOUT:
      print('process %d running more than 10 seconds' % pid)
      raise NotDiedError()
    if ret == win32event.WAIT_OBJECT_0:
      return
    raise Error('Unexpected return code %d for pid %d.' % (ret, pid))
  except pywintypes.error as e:
    if e[0] == winerror.ERROR_INVALID_PARAMETER and e[1] == 'OpenProcess':
      print('Can\'t open process %d. Already dead? error %s.' % (pid, e))
      return
    raise
  except OSError as e:
    if e.errno in (errno.ECHILD, errno.EPERM, errno.ESRCH):
      print(
        'Can\'t send SIGINT to process %d. Already dead? Errno %d.'
        % (pid, e.errno)
      )
      return
    raise
  finally:
    if handle:
      win32api.CloseHandle(handle)


def wait_termination(pid):
  """Send SIGINT to pid and wait termination of pid.

  Args:
    pid(int): pid of process which this function waits termination.

  Raises:
    OSError: is_running_posix, os.waitpid and os.kill may throw OSError.
    NotDiedError: if cloudtail is running after 10 seconds waiting,
                  NotDiedError is raised.
  """
  if os.name == 'nt':
    wait_termination_win(pid)
  else:
    try:
      os.kill(pid, signal.SIGINT)
    except OSError as e:
      if e.errno in (errno.ECHILD, errno.EPERM, errno.ESRCH):
        print(
          'Can\'t send SIGINT to process %d. Already dead? Errno %d.'
          % (pid, e.errno)
        )
        return
      raise
    for _ in xrange(10):
      time.sleep(1)
      if not is_running_posix(pid):
        return
    print('process %d running more than 10 seconds' % pid)
    raise NotDiedError()


def stop_cloudtail(args):
  """Stops the cloudtail process identified in the PID file

  Args:
    args: any type so long as it provides these fields:
          * killed_pid_file
  """
  with open(args.killed_pid_file) as f:
    # cloudtail flushes log and terminates
    # within 5 seconds when it receives SIGINT.
    pid = int(f.read())
  try:
    wait_termination(pid)
  except Exception as e:
    print('Going to send SIGTERM to process %d due to Error %s' % (pid, e))
    # Since Windows does not have SIGKILL, we need to use SIGTERM.
    try:
      os.kill(pid, signal.SIGTERM)
    except OSError as e:
      print('Failed to send SIGTERM to process %d: %s' % (pid, e))
    # We do not reraise because I believe not suspending the process
    # is more important than completely killing cloudtail.
