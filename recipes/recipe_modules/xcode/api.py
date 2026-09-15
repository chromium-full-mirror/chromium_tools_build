# Copyright 2022 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import re

from recipe_engine import recipe_api

_VERSION_RE = re.compile(r'^[a-zA-Z0-9]+$')


class XcodeApi(recipe_api.RecipeApi):
  def __init__(self, input_properties, *args, **kwargs):
    super().__init__(*args, **kwargs)
    self.xcode_config_path = input_properties.xcode_config_path

  # get the xcode version from json file
  # if the version attribute is missing, it will throw an error.
  # if path or checkout_dir is not specified,
  # or the file does not exist it will return None.
  def get_xcode_version(self, checkout_dir):
    if self.xcode_config_path and checkout_dir:
      full_path = checkout_dir / self.xcode_config_path

      # we don't throw an error when file does not exist,
      # because it will fall back using the xcode_build_version
      # to determine the xcode version
      if not self.m.path.exists(full_path):
        return None

      xcode_confg = self.m.file.read_json(
        'Read xcode_configs from repo',
        full_path,
        test_data={
          'xcode_build_version': '123ABC',
        },
      )
      version = xcode_confg.get('xcode_build_version')
      if not isinstance(version, str) or not _VERSION_RE.match(version):
        raise recipe_api.StepFailure(
          f'Invalid or missing xcode_build_version in {full_path}: {version!r}'
        )
      return version
    return None
