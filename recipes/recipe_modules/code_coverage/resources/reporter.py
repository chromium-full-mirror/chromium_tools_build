# Copyright 2018 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Functions for interacting with llvm-cov"""

from __future__ import annotations

import json
import logging
import os
import platform
import subprocess


def _call_cov_tool(
  cov_tool_path,
  profile_input_file_path,
  report_output_dir_path,
  compilation_dir_path,
  binaries,
  sources,
  arch,
):
  """Calls the llvm-cov tool.

  Args:
    cov_tool_path (str): path to the llvm-cov executable
    profile_input_file_path (str): path to the merged profdata file.
    report_output_dir_path (str): path to the directory where the report files
        are to be written.
    compilation_dir_path (str): path to the directory used as a base for
        relative coverage mapping paths.
    binaries (list of str): list of paths to the instrumented executables to
        create a report for.
    sources (list of str): list of paths to the source files to include in the
        report, includes all if not specified.
    arch (str): Binary architechture. Consumed by llvm command. Can be None.

  Raises:
    CalledProcessError: An error occurred generating the report.
  """
  logging.info('Generating report.')

  try:
    subprocess_cmd = [
      cov_tool_path,
      'show',
      '-format=html',
      '-output-dir=' + report_output_dir_path,
      '-compilation-dir=' + compilation_dir_path,
    ]

    if arch:
      # The number of -arch=some_arch arguments needs to be the same as the
      # number of binaries passed to llvm-cov command. Nth entry in the arch
      # list corresponds to the Nth specified binary.
      subprocess_cmd.extend(['-arch=%s' % arch] * len(binaries))

      # TODO(crbug.com/1068345): llvm-cov fails with a thread resource
      # unavailable exception if using more than one thread in iOS builder.
      if platform.system() == 'Darwin' and arch == 'x86_64':
        subprocess_cmd.append('-num-threads=1')

    if platform.system() == 'Windows':
      subprocess_cmd.extend(['-Xdemangler', 'llvm-undname.exe'])
    else:
      subprocess_cmd.extend(['-Xdemangler', 'c++filt', '-Xdemangler', '-n'])

    subprocess_cmd.append('-instr-profile=' + profile_input_file_path)

    subprocess_cmd.append(binaries[0])
    for binary in binaries[1:]:
      subprocess_cmd.extend(['-object', binary])
    if sources:
      subprocess_cmd.extend(sources)

    output = subprocess.check_output(subprocess_cmd, text=True)
    logging.debug('Report generation output: %s', output)
  except subprocess.CalledProcessError as error:
    logging.error('Failed to generate report.')
    raise error

  logging.info('Report created in: "%s".', report_output_dir_path)


def generate_report(
  llvm_cov,
  profdata_path,
  report_directory,
  compilation_directory,
  binaries,
  sources=None,
  arch=None,
):
  """Generates an html report for profile data using llvm-cov.

  Args:
    llvm_cov (str): The path to the llvm-cov executable.
    profdata_path (str): The path to the merged input profile.
    report_directory (str): Where to write the report.
    compilation_directory (str): the directory used as a base for relative
        coverage mapping paths.
    binaries (list of str): The binaries to write a report for.
    sources (list of str): list of paths to the source files to include in the
        report, includes all if not specified.
    arch (str): Binary architechture. Consumed by llvm command.
  """
  _call_cov_tool(
    llvm_cov,
    profdata_path,
    report_directory,
    compilation_directory,
    binaries,
    sources,
    arch,
  )
