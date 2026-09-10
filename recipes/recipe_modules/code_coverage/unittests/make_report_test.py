#!/usr/bin/env vpython3
# Copyright 2018 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest

import mock

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(
  0, os.path.abspath(os.path.join(THIS_DIR, os.pardir, 'resources'))
)

import make_report
import reporter


class MakeReportTest(unittest.TestCase):
  def test_parameters(self):
    with mock.patch.object(reporter, 'generate_report') as mock_gen_report:
      out_dir = 'out-dir'
      compilation_dir = 'compilation-dir'
      profdata_file = 'merge.profdata'
      # Not the real path.
      llvm_cov = '/usr/bin/llvm_cov'
      binaries = ['binary1', 'binary2']
      sources = ['base/file1.cc', 'base/file2.cc']
      arch = 'x86_64'

      # Test successful call, without sources.
      args = [
        'make_report.py',
        '--report-directory',
        out_dir,
        '--compilation-directory',
        compilation_dir,
        '--profdata-path',
        profdata_file,
        '--llvm-cov',
        llvm_cov,
        '--binaries',
      ]
      args.extend(binaries)
      with mock.patch('os.path.exists'):
        with mock.patch('os.path.isfile'):
          with mock.patch('os.access'):
            with mock.patch('sys.argv', args):
              make_report.main()
      self.assertEqual(
        mock.call(
          llvm_cov,
          profdata_file,
          out_dir,
          compilation_dir,
          binaries,
          None,
          None,
        ),
        mock_gen_report.call_args,
      )

      # With sources.
      args.append('--sources')
      args.extend(sources)
      with mock.patch('os.path.exists'):
        with mock.patch('os.path.isfile'):
          with mock.patch('os.access'):
            with mock.patch('sys.argv', args):
              make_report.main()
      self.assertEqual(
        mock.call(
          llvm_cov,
          profdata_file,
          out_dir,
          compilation_dir,
          binaries,
          sources,
          None,
        ),
        mock_gen_report.call_args,
      )

      # With arch.
      args.extend(['--arch', 'x86_64'])
      with mock.patch('os.path.exists'):
        with mock.patch('os.path.isfile'):
          with mock.patch('os.access'):
            with mock.patch('sys.argv', args):
              make_report.main()
      self.assertEqual(
        mock.call(
          llvm_cov,
          profdata_file,
          out_dir,
          compilation_dir,
          binaries,
          sources,
          arch,
        ),
        mock_gen_report.call_args,
      )

      # Test validation.
      args = [
        'make_report.py',
        '--report-directory',
        out_dir,
        '--compilation-directory',
        compilation_dir,
        '--profdata-path',
        profdata_file,
        '--llvm-cov',
        llvm_cov,
        '--binaries',
      ]
      args.extend(binaries)
      args.append('--sources')
      args.extend(sources)
      with mock.patch('sys.argv', args):
        with mock.patch('os.path.isfile'):
          with mock.patch('os.path.exists') as mock_exists:
            with mock.patch('os.access') as mock_access:
              # Report dir does not exist.
              with self.assertRaisesRegex(RuntimeError, '.*Output directory.*'):
                mock_exists.side_effect = lambda x: 'out-dir' not in x
                make_report.main()
              # Profdata does not exist
              with self.assertRaisesRegex(RuntimeError, '.*profdata.*'):
                mock_exists.side_effect = lambda x: 'profdata' not in x
                make_report.main()
              # llvm-cov is not executable.
              with self.assertRaisesRegex(RuntimeError, '.*executable.*'):
                mock_exists.side_effect = lambda x: True
                mock_access.return_value = False
                make_report.main()

      # Test call with missing argument.
      args = [
        'make_report.py',
        #'--report-directory', out_dir,
        '--profdata-path',
        profdata_file,
        '--llvm-cov',
        llvm_cov,
      ] + binaries
      with mock.patch('sys.argv', args):
        with self.assertRaises(SystemExit):
          make_report.main()

  def test_cov_invocation(self):
    with mock.patch.object(subprocess, 'check_output') as mock_run:
      out_dir = 'out-dir'
      compilation_dir = 'compilation-dir'
      profdata_file = 'merge.profdata'
      # Not the real path.
      llvm_cov = '/usr/bin/llvm_cov'
      binaries = ['binary1', 'binary2']
      sources = ['base/file1.cc', 'base/file2.cc']

      with mock.patch('platform.system', return_value='Linux'):
        reporter.generate_report(
          llvm_cov, profdata_file, out_dir, compilation_dir, binaries, sources
        )
      self.assertEqual(
        mock.call(
          [
            '/usr/bin/llvm_cov',
            'show',
            '-format=html',
            '-output-dir=out-dir',
            '-compilation-dir=compilation-dir',
            '-Xdemangler',
            'c++filt',
            '-Xdemangler',
            '-n',
            '-instr-profile=merge.profdata',
            'binary1',
            '-object',
            'binary2',
            'base/file1.cc',
            'base/file2.cc',
          ],
          text=True,
        ),
        mock_run.call_args,
      )

      with mock.patch('platform.system', return_value='Windows'):
        reporter.generate_report(
          llvm_cov, profdata_file, out_dir, compilation_dir, binaries, sources
        )
      self.assertEqual(
        mock.call(
          [
            '/usr/bin/llvm_cov',
            'show',
            '-format=html',
            '-output-dir=out-dir',
            '-compilation-dir=compilation-dir',
            '-Xdemangler',
            'llvm-undname.exe',
            '-instr-profile=merge.profdata',
            'binary1',
            '-object',
            'binary2',
            'base/file1.cc',
            'base/file2.cc',
          ],
          text=True,
        ),
        mock_run.call_args,
      )

  def test_arch(self):
    with mock.patch.object(subprocess, 'check_output') as mock_run:
      out_dir = 'out-dir'
      compilation_dir = 'compilation-dir'
      profdata_file = 'merge.profdata'
      # Not the real path.
      llvm_cov = '/usr/bin/llvm_cov'
      binaries = ['binary1', 'binary2']
      sources = ['base/file1.cc', 'base/file2.cc']
      arch = 'x86_64'

      with mock.patch('platform.system', return_value='Darwin'):
        reporter.generate_report(
          llvm_cov,
          profdata_file,
          out_dir,
          compilation_dir,
          binaries,
          sources,
          arch,
        )
      self.assertEqual(
        mock.call(
          [
            '/usr/bin/llvm_cov',
            'show',
            '-format=html',
            '-output-dir=out-dir',
            '-compilation-dir=compilation-dir',
            '-arch=x86_64',
            '-arch=x86_64',
            '-num-threads=1',
            '-Xdemangler',
            'c++filt',
            '-Xdemangler',
            '-n',
            '-instr-profile=merge.profdata',
            'binary1',
            '-object',
            'binary2',
            'base/file1.cc',
            'base/file2.cc',
          ],
          text=True,
        ),
        mock_run.call_args,
      )


if __name__ == '__main__':
  unittest.main()
