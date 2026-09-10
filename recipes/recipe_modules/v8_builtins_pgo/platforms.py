# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

# Paths relative to the V8 checkout directory
from __future__ import annotations


PROFILE_ONLY_PATH = 'tools/builtins-pgo/profile_only.py'
D8_OUT_PATH = 'out/build/d8'


class UnixPlatform:
  @property
  def profile_only_path(self):
    return PROFILE_ONLY_PATH

  @property
  def d8_out_path(self):
    return D8_OUT_PATH


class WindowsPlatform:
  def _to_windows_path(self, path):
    return path.replace('/', '\\')

  @property
  def profile_only_path(self):
    return self._to_windows_path(PROFILE_ONLY_PATH)

  @property
  def d8_out_path(self):
    return self._to_windows_path(f'{D8_OUT_PATH}.exe')
