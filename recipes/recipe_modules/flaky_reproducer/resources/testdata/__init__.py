# Copyright 2022 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import os

THIS_DIR = os.path.dirname(os.path.abspath(__file__))


def get_test_path(filename):
  """Return test data filepath"""
  return os.path.join(THIS_DIR, filename)


def get_test_data(filename):
  """Return test data as str"""
  with open(get_test_path(filename), 'rb') as fp:
    return fp.read()
