#!/usr/bin/env python3
# Copyright 2022 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import os
import shutil
import sys

if os.path.exists(sys.argv[2]):
  shutil.rmtree(sys.argv[2])
shutil.move(sys.argv[1], sys.argv[2])
