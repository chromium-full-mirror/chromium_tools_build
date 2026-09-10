#!/usr/bin/env python3
# Copyright 2022 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import sys

sys.path.append(sys.argv[1])
from devil import devil_env

devil_env.config.Initialize()
devil_env.config.PrefetchPaths(dependencies=['adb'])
