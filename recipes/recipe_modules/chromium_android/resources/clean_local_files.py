#!/usr/bin/env python3
# Copyright 2022 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import os
import shutil
import sys

shutil.rmtree(sys.argv[1], True)
shutil.rmtree(sys.argv[2], True)
try:
  os.remove(sys.argv[3])
except OSError:
  pass

for base, _dirs, files in os.walk(sys.argv[4]):
  for f in files:
    if f.endswith('.pyc'):
      os.remove(os.path.join(base, f))
