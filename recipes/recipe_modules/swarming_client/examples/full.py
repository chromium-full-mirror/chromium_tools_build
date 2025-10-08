# Copyright 2014 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations


DEPS = [
  'swarming_client',
]


def RunSteps(api):
  _ = api.swarming_client.path


def GenTests(api):
  yield api.test(
      'basic',
  )
