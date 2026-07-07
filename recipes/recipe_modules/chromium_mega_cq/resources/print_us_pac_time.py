#!/usr/bin/env vpython3
# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

# [VPYTHON:BEGIN]
# python_version: "3.11"
# wheel: <
#   name: "infra/python/wheels/pytz-py2_py3"
#   version: "version:2021.1"
# >
# [VPYTHON:END]
"""A dumb script that simply prints the current datetime in US/Pacific.

Should handle DST gracefully.
"""

from __future__ import annotations

import datetime
import pytz

now = datetime.datetime.now(tz=pytz.utc)
now_pst = now.astimezone(pytz.timezone('US/Pacific'))
print(now_pst)
