#!/usr/bin/env vpython3
# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

# /// script
# requires-python = '>=3.11,<3.12'
# dependencies = [
#   'pytz==2021.1'
# ]
# ///
"""A dumb script that simply prints the current datetime in US/Pacific.

Should handle DST gracefully.
"""

from __future__ import annotations

import datetime
import pytz

now = datetime.datetime.now(tz=pytz.utc)
now_pst = now.astimezone(pytz.timezone('US/Pacific'))
print(now_pst)
