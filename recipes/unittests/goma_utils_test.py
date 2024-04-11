#!/usr/bin/env vpython
# Copyright (c) 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import datetime
import os
import shutil
import sys
import tempfile
import unittest

import mock

ROOT_DIR = os.path.normpath(os.path.join(__file__, '..', '..', '..'))
sys.path.extend([
    os.path.join(ROOT_DIR, 'recipes'),
])

import goma_utils


class GomaUtilTest(unittest.TestCase):

  def setUp(self):
    self._tmp_dir = tempfile.mkdtemp()
    self._glog_log_dir = os.environ.get('GLOG_log_dir')
    os.environ['GLOG_log_dir'] = self._tmp_dir

  def tearDown(self):
    shutil.rmtree(self._tmp_dir)
    if self._glog_log_dir is None:
      del os.environ['GLOG_log_dir']
    else:
      os.environ['GLOG_log_dir'] = self._glog_log_dir

  def createInfoLog(self, name, t):
    fname = os.path.join(
        self._tmp_dir,
        '%s.host.user.log.INFO.%04d%02d%02d-%02d%02d%02d.123456' %
        (name, t.year, t.month, t.day, t.hour, t.minute, t.second)
    )
    with open(fname, 'w') as f:
      f.write(
          'Log file created at: %04d/%02d/%02d %02d:%02d:%02d\n' %
          (t.year, t.month, t.day, t.hour, t.minute, t.second)
      )
    return fname

  def testGetLogFileTimestamp(self):
    self.assertEquals(
        datetime.datetime(2006, 1, 2, 15, 4, 5),
        goma_utils.
        GetLogFileTimestamp('gomacc.host.user.log.INFO.20060102-150405.123456')
    )

  def testGetListOfGomaccInfoAfterCompilerProxyStartNoGomaccInfo(self):
    self.createInfoLog(
        'compiler_proxy', datetime.datetime(2021, 1, 2, 15, 4, 5)
    )
    gomacc_infos = goma_utils.GetListOfGomaccInfoAfterCompilerProxyStart()
    self.assertEquals([], gomacc_infos)

  def testGetListOfGomaccInfoAfterCompilerProxyStart(self):
    self.createInfoLog(
        'compiler_proxy', datetime.datetime(2021, 1, 2, 15, 4, 5)
    )
    gomacc_log = self.createInfoLog(
        'gomacc', datetime.datetime(2021, 1, 2, 15, 4, 6)
    )
    gomacc_infos = goma_utils.GetListOfGomaccInfoAfterCompilerProxyStart()
    self.assertEquals([gomacc_log], gomacc_infos)

  def testGetListOfGomaccInfoAfterCompilerProxyStartOldGomaccInfo(self):
    self.createInfoLog(
        'compiler_proxy', datetime.datetime(2021, 1, 2, 15, 4, 5)
    )
    self.createInfoLog('gomacc', datetime.datetime(2021, 1, 2, 15, 4, 4))
    gomacc_infos = goma_utils.GetListOfGomaccInfoAfterCompilerProxyStart()
    self.assertEquals([], gomacc_infos)

  def testUploadToGomaLogGS(self):
    file_path = os.path.join(self._tmp_dir, 'file')
    with open(file_path, 'w') as f:
      f.write('test')

    with mock.patch('bot_utils.GSUtilCopy') as mocked_GSUtilCopy:
      goma_utils.UploadToGomaLogGS(file_path, 'gs_filename')
      mocked_GSUtilCopy.assert_called_once()


if __name__ == '__main__':
  unittest.main()
