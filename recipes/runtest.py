#!/usr/bin/env python3
# Copyright (c) 2012 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""A tool used to run a Chrome test executable and process the output.

This script is used by builders running tests locally. It must be run
from the directory where the checkout occurs (i.e. the directory that
contains src for a chromium checkout).

For a list of command-line options, call this script with '--help'.
"""

import logging
import optparse
import os
import platform
import re
import signal
import subprocess
import sys
import tempfile

# The following note was added in 2010 by nsylvain:
#
# sys.path needs to be modified here because python2.6 automatically adds the
# system "google" module (/usr/lib/pymodules/python2.6/google) to sys.modules
# when we import "chromium_config" (I don't know why it does this). This causes
# the import of our local "google.*" modules to fail because python seems to
# only look for a system "google.*", even if our path is in sys.path before
# importing "google.*". If we modify sys.path here, before importing
# "chromium_config", python2.6 properly uses our path to find our "google.*"
# (even though it still automatically adds the system "google" module to
# sys.modules, and probably should still be using that to resolve "google.*",
# which I really don't understand).
sys.path.insert(0, os.path.abspath('src/tools/python'))

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(
  0, os.path.abspath(os.path.join(THIS_DIR, os.pardir, 'scripts'))
)
sys.path.insert(
  0,
  THIS_DIR,
)

from common import chromium_utils
from common import gtest_utils

import bot_utils
import crash_utils
import xvfb

USAGE = '%s [options] test.exe [test args]' % os.path.basename(sys.argv[0])

CHROME_SANDBOX_PATH = '/opt/chromium/chrome_sandbox'

# The directory that this script is in.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _GetTempCount():
  """Returns the number of files and directories inside the temporary dir."""
  return len(os.listdir(tempfile.gettempdir()))


def _LaunchDBus():
  """Launches DBus to work around a bug in GLib.

  Works around a bug in GLib where it performs operations which aren't
  async-signal-safe (in particular, memory allocations) between fork and exec
  when it spawns subprocesses. This causes threads inside Chrome's browser and
  utility processes to get stuck, and this harness to hang waiting for those
  processes, which will never terminate. This doesn't happen on users'
  machines, because they have an active desktop session and the
  DBUS_SESSION_BUS_ADDRESS environment variable set, but it does happen on the
  bots. See crbug.com/309093 for more details.

  Returns:
    True if it actually spawned DBus.
  """
  if (
    platform.uname()[0].lower() == 'linux'
    and 'DBUS_SESSION_BUS_ADDRESS' not in os.environ
  ):
    try:
      print('DBUS_SESSION_BUS_ADDRESS env var not found, starting dbus-launch')
      dbus_output = subprocess.check_output(
        ['dbus-launch'], universal_newlines=True
      ).split('\n')
      for line in dbus_output:
        m = re.match(r'([^=]+)\=(.+)', line)
        if m:
          os.environ[m.group(1)] = m.group(2)
          print(' setting %s to %s' % (m.group(1), m.group(2)))
      return True
    except (subprocess.CalledProcessError, OSError) as e:
      print('Exception while running dbus_launch: %s' % e)
  return False


def _ShutdownDBus():
  """Manually kills the previously-launched DBus daemon.

  It appears that passing --exit-with-session to dbus-launch in
  _LaunchDBus(), above, doesn't cause the launched dbus-daemon to shut
  down properly. Manually kill the sub-process using the PID it gave
  us at launch time.

  This function is called when the flag --spawn-dbus is given, and if
  _LaunchDBus(), above, actually spawned the dbus-daemon.
  """
  if 'DBUS_SESSION_BUS_PID' in os.environ:
    dbus_pid = os.environ['DBUS_SESSION_BUS_PID']
    try:
      os.kill(int(dbus_pid), signal.SIGTERM)
      print(' killed dbus-daemon with PID %s' % dbus_pid)
    except OSError as e:
      print(' error killing dbus-daemon with PID %s: %s' % (dbus_pid, e))
  # Try to clean up any stray DBUS_SESSION_BUS_ADDRESS environment
  # variable too. Some of the bots seem to re-invoke runtest.py in a
  # way that this variable sticks around from run to run.
  if 'DBUS_SESSION_BUS_ADDRESS' in os.environ:
    del os.environ['DBUS_SESSION_BUS_ADDRESS']
    print(' cleared DBUS_SESSION_BUS_ADDRESS environment variable')


def _RunGTestCommand(
  options, command, extra_env, log_processor=None, pipes=None
):
  """Runs a test, printing and possibly processing the output.

  Args:
    options: Options passed for this invocation of runtest.py.
    command: A list of strings in a command (the command and its arguments).
    extra_env: A dictionary of extra environment variables to set.
    log_processor: A log processor instance which has the ProcessLine method.
    pipes: A list of command string lists which the output will be piped to.

  Returns:
    The process return code.
  """
  env = os.environ.copy()
  if extra_env:
    print('Additional test environment:')
    for k, v in sorted(extra_env.items()):
      print('  %s=%s' % (k, v))
  env.update(extra_env or {})

  # Trigger bot mode (test retries, redirection of stdio, possibly faster,
  # etc.) - using an environment variable instead of command-line flags because
  # some internal waterfalls run this (_RunGTestCommand) for totally non-gtest
  # code.
  # TODO(phajdan.jr): Clean this up when internal waterfalls are fixed.
  env.update({'CHROMIUM_TEST_LAUNCHER_BOT_MODE': '1'})

  parser_func = log_processor.ProcessLine if log_processor else None

  result = chromium_utils.RunCommand(
    command, pipes=pipes, parser_func=parser_func, env=env
  )

  return result


def _BuildTestBinaryCommand(test_exe_path, options):
  """Builds a command to run a test binary.

  Args:
    test_exe_path: Path to test command binary.
    options: Options passed for this invocation of runtest.py.

  Returns:
    A command, represented as a list of command parts.
  """
  command = [
    test_exe_path,
  ]

  if options.parse_gtest_output:
    command.append('--test-launcher-bot-mode')

  return command


def _UsingGtestJson(options):
  """Returns True if we're using GTest JSON summary."""
  return options.parse_gtest_output and not options.run_python_script


def _CreateLogProcessor(options):
  """Creates a log processor instance.

  Args:
    options: Command-line options (from OptionParser).

  Returns:
    An instance of a log processor class, or None.
  """
  if _UsingGtestJson(options):
    return gtest_utils.GTestJSONParser(options.builder_group or '')

  if options.parse_gtest_output:
    return gtest_utils.GTestLogParser()

  return None


def _GenerateRunIsolatedCommand(build_dir, test_exe_path, options, command):
  """Converts the command to run through the run isolate script.

  All commands are sent through the run isolated script, in case
  they need to be run in isolate mode.
  """
  run_isolated_test = os.path.join(BASE_DIR, 'runisolatedtest.py')
  isolate_command = [
    sys.executable,
    run_isolated_test,
    '--test_name',
    options.test_type,
    '--builder_name',
    options.builder_name,
    '--checkout_dir',
    os.path.dirname(os.path.dirname(os.path.dirname(build_dir))),
  ]
  isolate_command += [test_exe_path, '--'] + command

  return isolate_command


def _GetSanitizerSymbolizeCommand(strip_path_prefix=None, json_file_name=None):
  script_path = os.path.abspath(
    os.path.join('src', 'tools', 'valgrind', 'asan', 'asan_symbolize.py')
  )
  command = [sys.executable, script_path]
  if strip_path_prefix:
    command.append(strip_path_prefix)
  if json_file_name:
    command.append('--test-summary-json-file=%s' % json_file_name)
  return command


def _report_outcome(test_name, exit_code, log_processor):
  """Output information about the outcome of the test."""

  # Always print raw exit code of the subprocess. This is very helpful
  # for debugging, especially when one gets the "crashed or hung" message
  # with no output (exit code can have some clues, especially on Windows).
  if exit_code < -100:
    # Windows error codes such as 0xC0000005 and 0xC0000409 are much easier to
    # recognize and differentiate in hex. In order to print them as unsigned
    # hex we need to add 4 Gig to them.
    print('exit code (as seen by runtest.py): 0x%08X' % (exit_code + (1 << 32)))
  else:
    print('exit code (as seen by runtest.py): %d' % exit_code)

  if log_processor.ParsingErrors():
    print('runtest.py encountered the following errors')
    for e in log_processor.ParsingErrors():
      print('  ', e)

  print()
  print(test_name)
  print('%s disabled' % log_processor.DisabledTests())
  print('%s flaky' % log_processor.FlakyTests())

  SUCCESS, WARNINGS, FAILURE = list(range(3))
  status = SUCCESS

  if exit_code == SUCCESS:
    if (
      log_processor.ParsingErrors()
      or log_processor.FailedTests()
      or log_processor.MemoryToolReportHashes()
    ):
      status = WARNINGS
  elif exit_code == bot_utils.WARNING_EXIT_CODE:
    status = WARNINGS
  else:
    status = FAILURE

  failed_test_count = len(log_processor.FailedTests())
  if failed_test_count == 0:
    if status == SUCCESS:
      return
    if status == WARNINGS:
      print('warnings')
      return

  if log_processor.RunningTests():
    print('did not complete')

  if failed_test_count:
    print('failed %d' % failed_test_count)
  else:
    print('crashed or hung')


def _MainMac(options, args, extra_env):
  """Runs the test on mac."""
  if len(args) < 1:
    raise chromium_utils.MissingArgument('Usage: %s' % USAGE)

  build_dir = options.build_dir
  test_exe = args[0]
  if options.run_python_script:
    test_exe_path = test_exe
  else:
    test_exe_path = os.path.join(build_dir, test_exe)

  # Nuke anything that appears to be stale chrome items in the temporary
  # directory from previous test runs (i.e.- from crashes or unittest leaks).
  bot_utils.RemoveChromeTemporaryFiles()

  if options.run_python_script:
    command = [sys.executable, test_exe]
  else:
    command = _BuildTestBinaryCommand(test_exe_path, options)
  command.extend(args[1:])

  log_processor = _CreateLogProcessor(options)

  try:
    if _UsingGtestJson(options):
      json_file_name = log_processor.PrepareJSONFile(
        options.test_launcher_summary_output
      )
      command.append('--test-launcher-summary-output=%s' % json_file_name)
    elif options.test_launcher_summary_output:
      command.append(
        '--test-launcher-summary-output=%s'
        % (options.test_launcher_summary_output)
      )

    pipes = []
    if options.use_symbolization_script:
      pipes = [_GetSanitizerSymbolizeCommand()]

    command = _GenerateRunIsolatedCommand(
      build_dir, test_exe_path, options, command
    )
    result = _RunGTestCommand(
      options, command, extra_env, pipes=pipes, log_processor=log_processor
    )
  finally:
    if _UsingGtestJson(options):
      log_processor.ProcessJSONFile(os.path.dirname(options.build_dir))

  if options.parse_gtest_output:
    _report_outcome(options.test_type, result, log_processor)

  return result


def _MainIOS(options, args, extra_env):
  """Runs the test on iOS."""
  if len(args) < 1:
    raise chromium_utils.MissingArgument('Usage: %s' % USAGE)

  def kill_simulator():
    chromium_utils.RunCommand(['/usr/bin/killall', 'iPhone Simulator'])

  # For iOS tests, the args come in in the following order:
  #   [0] test display name formatted as 'test_name (device[ ios_version])'
  #   [1:] gtest args (e.g. --gtest_print_time)

  # Set defaults in case the device family and iOS version can't be parsed out
  # of |args|
  device = 'iPhone Retina (4-inch)'
  ios_version = '7.1'

  # Parse the test_name and device from the test display name.
  # The expected format is: <test_name> (<device>)
  result = re.match(r'(.*) \((.*)\)$', args[0])
  if result is not None:
    test_name, device = result.groups()
    # Check if the device has an iOS version. The expected format is:
    # <device_name><space><ios_version>, where ios_version may have 2 or 3
    # numerals (e.g. '4.3.11' or '5.0').
    result = re.match(r'(.*) (\d+\.\d+(\.\d+)?)$', device)
    if result is not None:
      device = result.groups()[0]
      ios_version = result.groups()[1]
  else:
    # If first argument is not in the correct format, log a warning but
    # fall back to assuming the first arg is the test_name and just run
    # on the iphone simulator.
    test_name = args[0]
    print(
      'Can\'t parse test name, device, and iOS version. '
      'Running %s on %s %s' % (test_name, device, ios_version)
    )

  # Build the args for invoking iossim, which will install the app on the
  # simulator and launch it, then dump the test results to stdout.

  build_dir = options.build_dir
  app_exe_path = os.path.join(build_dir, test_name + '.app')
  test_exe_path = os.path.join(build_dir, 'iossim')
  tmpdir = tempfile.mkdtemp()
  command = [
    test_exe_path,
    '-d',
    device,
    '-s',
    ios_version,
    '-t',
    '120',
    '-u',
    tmpdir,
    app_exe_path,
    '--',
  ]
  command.extend(args[1:])

  log_processor = gtest_utils.GTestLogParser()

  # Make sure the simulator isn't running.
  kill_simulator()

  # Nuke anything that appears to be stale chrome items in the temporary
  # directory from previous test runs (i.e.- from crashes or unittest leaks).
  bot_utils.RemoveChromeTemporaryFiles()

  dirs_to_cleanup = [tmpdir]
  crash_files_before = set([])
  crash_files_after = set([])
  crash_files_before = set(crash_utils.list_crash_logs())

  result = _RunGTestCommand(options, command, extra_env, log_processor)

  # Because test apps kill themselves, iossim sometimes returns non-zero
  # status even though all tests have passed.  Check the log_processor to
  # see if the test run was successful.
  if log_processor.CompletedWithoutFailure():
    result = 0
  else:
    result = 1

  if result != 0:
    crash_utils.wait_for_crash_logs()
  crash_files_after = set(crash_utils.list_crash_logs())

  kill_simulator()

  new_crash_files = crash_files_after.difference(crash_files_before)
  crash_utils.print_new_crash_files(new_crash_files)

  for a_dir in dirs_to_cleanup:
    try:
      chromium_utils.RemoveDirectory(a_dir)
    except OSError as e:
      print(e, file=sys.stderr)
      # Don't fail.

  return result


def _MainLinux(options, args, extra_env):
  """Runs the test on Linux."""
  if len(args) < 1:
    raise chromium_utils.MissingArgument('Usage: %s' % USAGE)

  build_dir = options.build_dir
  test_exe = args[0]
  if options.run_python_script:
    test_exe_path = test_exe
  else:
    test_exe_path = os.path.join(build_dir, test_exe)
  if not os.path.exists(test_exe_path):
    msg = 'Unable to find %s' % test_exe_path
    raise chromium_utils.PathNotFound(msg)

  # Unset http_proxy and HTTPS_PROXY environment variables.  When set, this
  # causes some tests to hang.  See http://crbug.com/139638 for more info.
  if 'http_proxy' in os.environ:
    del os.environ['http_proxy']
    print('Deleted http_proxy environment variable.')
  if 'HTTPS_PROXY' in os.environ:
    del os.environ['HTTPS_PROXY']
    print('Deleted HTTPS_PROXY environment variable.')

  # Path to SUID sandbox binary. This must be installed on all bots.
  extra_env['CHROME_DEVEL_SANDBOX'] = CHROME_SANDBOX_PATH

  # Nuke anything that appears to be stale chrome items in the temporary
  # directory from previous test runs (i.e.- from crashes or unittest leaks).
  bot_utils.RemoveChromeTemporaryFiles()

  extra_env['LD_LIBRARY_PATH'] = ''

  if options.enable_lsan:
    # Use the debug version of libstdc++ under LSan. If we don't, there will be
    # a lot of incomplete stack traces in the reports.
    extra_env['LD_LIBRARY_PATH'] += '/usr/lib/x86_64-linux-gnu/debug:'

  extra_env['LD_LIBRARY_PATH'] += '%s:%s/lib:%s/lib.target' % (
    build_dir,
    build_dir,
    build_dir,
  )

  if options.run_python_script:
    command = [sys.executable, test_exe]
  else:
    command = _BuildTestBinaryCommand(test_exe_path, options)
  command.extend(args[1:])

  log_processor = _CreateLogProcessor(options)

  try:
    start_xvfb = False
    json_file_name = None

    # TODO(dpranke): checking on test_exe is a temporary hack until we
    # can change the buildbot master to pass --xvfb instead of --no-xvfb
    # for these two steps. See
    # https://code.google.com/p/chromium/issues/detail?id=179814
    start_xvfb = (
      options.xvfb
      or 'layout_test_wrapper' in test_exe
      or 'devtools_perf_test_wrapper' in test_exe
    )
    if start_xvfb:
      xvfb.StartVirtualX(build_dir)

    if _UsingGtestJson(options):
      json_file_name = log_processor.PrepareJSONFile(
        options.test_launcher_summary_output
      )
      command.append('--test-launcher-summary-output=%s' % json_file_name)
    elif options.test_launcher_summary_output:
      command.append(
        '--test-launcher-summary-output=%s'
        % (options.test_launcher_summary_output)
      )

    pipes = []
    # See the comment in main() regarding offline symbolization.
    if options.use_symbolization_script:
      symbolize_command = _GetSanitizerSymbolizeCommand(
        strip_path_prefix=options.strip_path_prefix
      )
      pipes = [symbolize_command]

    command = _GenerateRunIsolatedCommand(
      build_dir, test_exe_path, options, command
    )
    result = _RunGTestCommand(
      options, command, extra_env, pipes=pipes, log_processor=log_processor
    )
  finally:
    if start_xvfb:
      xvfb.StopVirtualX()
    if _UsingGtestJson(options):
      log_processor.ProcessJSONFile(os.path.dirname(options.build_dir))

  if options.parse_gtest_output:
    _report_outcome(options.test_type, result, log_processor)

  return result


def _MainWin(options, args, extra_env):
  """Runs tests on windows.

  Using the target build configuration, run the executable given in the
  first non-option argument, passing any following arguments to that
  executable.

  Args:
    options: Command-line options for this invocation of runtest.py.
    args: Command and arguments for the test.
    extra_env: A dictionary of extra environment variables to set.

  Returns:
    Exit status code.
  """
  if len(args) < 1:
    raise chromium_utils.MissingArgument('Usage: %s' % USAGE)

  # Nuke anything that appears to be stale chrome items in the temporary
  # directory from previous test runs (i.e.- from crashes or unittest leaks).
  bot_utils.RemoveChromeTemporaryFiles()

  test_exe = args[0]
  build_dir = options.build_dir
  if options.run_python_script:
    test_exe_path = test_exe
  else:
    test_exe_path = os.path.join(build_dir, test_exe)

  if not os.path.exists(test_exe_path):
    raise chromium_utils.PathNotFound('Unable to find %s' % test_exe_path)

  if options.run_python_script:
    command = [sys.executable, test_exe]
  else:
    command = _BuildTestBinaryCommand(test_exe_path, options)

  command.extend(args[1:])

  log_processor = _CreateLogProcessor(options)

  try:
    if _UsingGtestJson(options):
      json_file_name = log_processor.PrepareJSONFile(
        options.test_launcher_summary_output
      )
      command.append('--test-launcher-summary-output=%s' % json_file_name)
    elif options.test_launcher_summary_output:
      command.append(
        '--test-launcher-summary-output=%s'
        % (options.test_launcher_summary_output)
      )

    command = _GenerateRunIsolatedCommand(
      build_dir, test_exe_path, options, command
    )
    result = _RunGTestCommand(options, command, extra_env, log_processor)
  finally:
    if _UsingGtestJson(options):
      log_processor.ProcessJSONFile(os.path.dirname(options.build_dir))

  if options.parse_gtest_output:
    _report_outcome(options.test_type, result, log_processor)

  return result


def _MainAndroid(options, args, extra_env):
  """Runs tests on android.

  Running GTest-based tests on android is different than on Linux as it requires
  src/build/android/test_runner.py to deploy and communicate with the device.
  Python scripts are the same as with Linux.

  Args:
    options: Command-line options for this invocation of runtest.py.
    args: Command and arguments for the test.
    extra_env: A dictionary of extra environment variables to set.

  Returns:
    Exit status code.
  """
  if not os.environ.get('CHROMIUM_OUTPUT_DIR'):
    extra_env['CHROMIUM_OUTPUT_DIR'] = options.build_dir
  if options.run_python_script:
    return _MainLinux(options, args, extra_env)

  raise Exception(
    'runtest.py without --run-python-script not supported for Android'
  )


def _ConfigureSanitizerTools(options, args, extra_env):
  if (
    options.enable_asan
    or options.enable_tsan
    or options.enable_msan
    or options.enable_lsan
  ):
    # Instruct GTK to use malloc while running ASan, TSan, MSan or LSan tests.
    extra_env['G_SLICE'] = 'always-malloc'
    extra_env['NSS_DISABLE_ARENA_FREE_LIST'] = '1'
    extra_env['NSS_DISABLE_UNLOAD'] = '1'

  symbolizer_path = os.path.abspath(
    os.path.join(
      'src',
      'third_party',
      'llvm-build',
      'Release+Asserts',
      'bin',
      'llvm-symbolizer',
    )
  )
  disable_sandbox_flag = '--no-sandbox'
  if args and 'layout_test_wrapper' in args[0]:
    disable_sandbox_flag = '--additional-drt-flag=%s' % disable_sandbox_flag

  # Symbolization of sanitizer reports.
  if sys.platform in ['win32', 'cygwin']:
    # On Windows, the in-process symbolizer works even when sandboxed.
    symbolization_options = []
  elif options.enable_tsan or options.enable_lsan:
    # TSan and LSan are not sandbox-compatible, so we can use online
    # symbolization. In fact, they need symbolization to be able to apply
    # suppressions.
    symbolization_options = [
      'symbolize=1',
      'external_symbolizer_path=%s' % symbolizer_path,
      'strip_path_prefix=%s' % options.strip_path_prefix,
    ]
  elif options.enable_asan or options.enable_msan:
    # ASan and MSan use a script for offline symbolization.
    # Important note: when running ASan or MSan with leak detection enabled,
    # we must use the LSan symbolization options above.
    symbolization_options = ['symbolize=0']
    # Set the path to llvm-symbolizer to be used by asan_symbolize.py
    extra_env['LLVM_SYMBOLIZER_PATH'] = symbolizer_path
    options.use_symbolization_script = True

  def AddToExistingEnv(env_dict, key, options_list):
    # Adds a key to the supplied environment dictionary but appends it to
    # existing environment variables if it already contains values.
    assert isinstance(env_dict, dict)
    assert isinstance(options_list, list)
    env_dict[key] = ' '.join(filter(bool, [os.environ.get(key)] + options_list))

  # ThreadSanitizer
  if options.enable_tsan:
    tsan_options = symbolization_options
    AddToExistingEnv(extra_env, 'TSAN_OPTIONS', tsan_options)
    # Disable sandboxing under TSan for now. http://crbug.com/223602.
    args.append(disable_sandbox_flag)

  # LeakSanitizer
  if options.enable_lsan:
    # Symbolization options set here take effect only for standalone LSan.
    lsan_options = symbolization_options
    AddToExistingEnv(extra_env, 'LSAN_OPTIONS', lsan_options)

    # Disable sandboxing under LSan.
    args.append(disable_sandbox_flag)

  # AddressSanitizer
  if options.enable_asan:
    asan_options = symbolization_options
    if options.enable_lsan:
      asan_options += ['detect_leaks=1']
    AddToExistingEnv(extra_env, 'ASAN_OPTIONS', asan_options)

  # MemorySanitizer
  if options.enable_msan:
    msan_options = symbolization_options
    if options.enable_lsan:
      msan_options += ['detect_leaks=1']
    AddToExistingEnv(extra_env, 'MSAN_OPTIONS', msan_options)


def main():
  """Entry point for runtest.py.

  This function:
    (1) Sets up the command-line options.
    (2) Sets environment variables based on those options.
    (3) Delegates to the platform-specific main functions.

  Returns:
    Exit code for this script.
  """
  option_parser = optparse.OptionParser(usage=USAGE)

  # Since the trailing program to run may have has command-line args of its
  # own, we need to stop parsing when we reach the first positional argument.
  option_parser.disable_interspersed_args()

  option_parser.add_option('--build-dir', help='Path to the build dir.')
  option_parser.add_option(
    '--test-platform', help='Platform to test on, e.g. ios-simulator'
  )
  option_parser.add_option(
    '--run-python-script',
    action='store_true',
    default=False,
    help='treat first argument as a python scriptto run.',
  )
  option_parser.add_option(
    '--xvfb',
    action='store_true',
    dest='xvfb',
    default=True,
    help='Start virtual X server on Linux.',
  )
  option_parser.add_option(
    '--no-xvfb',
    action='store_false',
    dest='xvfb',
    help='Do not start virtual X server on Linux.',
  )
  option_parser.add_option(
    '--builder-group',
    default=None,
    help='The group of the builder running this script.',
  )
  option_parser.add_option(
    '--builder-name',
    default=None,
    help='The name of the builder running this script.',
  )
  option_parser.add_option(
    '--build-number',
    default=None,
    help=('The build number of the builder runningthis script.'),
  )
  option_parser.add_option(
    '--test-type',
    default='',
    help='The test name that identifies the test, e.g. \'unit-tests\'',
  )
  option_parser.add_option(
    '--parse-gtest-output',
    default=False,
    action='store_true',
    help='Parse the gtest JSON output.',
  )
  option_parser.add_option(
    '--enable-asan',
    action='store_true',
    default=False,
    help='Enable fast memory error detection (AddressSanitizer).',
  )
  option_parser.add_option(
    '--enable-lsan',
    action='store_true',
    default=False,
    help='Enable memory leak detection (LeakSanitizer).',
  )
  option_parser.add_option(
    '--enable-msan',
    action='store_true',
    default=False,
    help='Enable uninitialized memory reads detection (MemorySanitizer).',
  )
  option_parser.add_option(
    '--enable-tsan',
    action='store_true',
    default=False,
    help='Enable data race detection (ThreadSanitizer).',
  )
  option_parser.add_option(
    '--strip-path-prefix',
    default='build/src/out/Release/../../',
    help='Source paths in stack traces will be stripped '
    'of prefixes ending with this substring. This '
    'option is used by sanitizer tools.',
  )
  option_parser.add_option(
    '--test-launcher-summary-output',
    help='Path to test results file with all the info from the test launcher',
  )

  options, args = option_parser.parse_args()
  if not options.build_dir:
    raise optparse.OptionValueError('Need to pass --build-dir')

  # Initialize logging.
  log_level = logging.INFO
  logging.basicConfig(
    level=log_level,
    format='%(asctime)s %(filename)s:%(lineno)-3d %(levelname)s %(message)s',
    datefmt='%y%m%d %H:%M:%S',
  )
  logging.basicConfig(level=logging.DEBUG)
  logging.getLogger().addHandler(logging.StreamHandler(stream=sys.stdout))

  print('[Running on builder: "%s"]' % options.builder_name)

  did_launch_dbus = _LaunchDBus()

  try:
    options.build_dir = os.path.normpath(os.path.abspath(options.build_dir))

    # We will use this to accumulate overrides for the command under test,
    # That we may not need or want for other support commands.
    extra_env = {}

    # This option is used by sanitizer code. There is no corresponding command
    # line flag.
    options.use_symbolization_script = False
    # Set up extra environment and args for sanitizer tools.
    _ConfigureSanitizerTools(options, args, extra_env)

    temp_files = _GetTempCount()
    if sys.platform.startswith('darwin'):
      test_platform = options.test_platform
      if test_platform in ('ios-simulator',):
        result = _MainIOS(options, args, extra_env)
      else:
        result = _MainMac(options, args, extra_env)
    elif sys.platform == 'win32':
      result = _MainWin(options, args, extra_env)
    elif sys.platform.startswith('linux'):
      if options.test_platform == 'android':
        result = _MainAndroid(options, args, extra_env)
      else:
        result = _MainLinux(options, args, extra_env)
    else:
      sys.stderr.write('Unknown sys.platform value %s\n' % repr(sys.platform))
      return 1

    new_temp_files = _GetTempCount()
    if temp_files > new_temp_files:
      print(
        'Confused: %d files were deleted from %s during the test run'
        % (temp_files - new_temp_files, tempfile.gettempdir()),
        file=sys.stderr,
      )
    elif temp_files < new_temp_files:
      print(
        '%d new files were left in %s: Fix the tests to clean up themselves.'
        % (new_temp_files - temp_files, tempfile.gettempdir()),
        file=sys.stderr,
      )
      # TODO(maruel): Make it an error soon. Not yet since I want to iron
      # out all the remaining cases before.
      # result = 1
    return result
  finally:
    if did_launch_dbus:
      # It looks like the command line argument --exit-with-session
      # isn't working to clean up the spawned dbus-daemon. Kill it
      # manually.
      _ShutdownDBus()


if '__main__' == __name__:
  sys.exit(main())
