#!/usr/bin/env python3
# Copyright 2022 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import hashlib
import os
from shutil import copyfile
import sys

for arg in sys.argv[2:]:
  with open(arg, "rb") as fin:
    h = hashlib.md5(fin.read()).hexdigest()
    copyfile(arg, os.path.join(sys.argv[1], "trace_" + h))