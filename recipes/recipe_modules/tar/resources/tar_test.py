#!/usr/bin/env python3
# Copyright 2022 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import json
import os.path
import subprocess
import tempfile
import unittest

THIS_DIR = os.path.dirname(__file__)


class Test(unittest.TestCase):
  def test_tar_untar(self):
    with tempfile.TemporaryDirectory() as tdir:
      input_dir = os.path.join(tdir, 'input')
      os.mkdir(input_dir)
      output = os.path.join(tdir, 'output.tar.gz')

      with open(os.path.join(input_dir, 'empty'), 'w'):
        pass

      json_input = json.dumps(
        {
          "entries": [
            {
              "type": "dir",
              "path": input_dir,
            }
          ],
          "output": output,
          "compression": "gz",
          "root": tdir,
        }
      )

      subprocess.run(
        ['python3', os.path.join(THIS_DIR, 'tar.py')],
        input=json_input,
        text=True,
        check=True,
      )

      json_input = json.dumps(
        {
          "output": os.path.join(tdir, "out"),
          "tar_file": output,
          "quiet": False,
        }
      )

      subprocess.run(
        ['python3', os.path.join(THIS_DIR, 'untar.py')],
        input=json_input,
        text=True,
        check=True,
      )


if __name__ == '__main__':
  unittest.main()
