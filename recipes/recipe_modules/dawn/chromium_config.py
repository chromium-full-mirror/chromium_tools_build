# Copyright 2025 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from RECIPE_MODULES.build.chromium import CONFIG_CTX


@CONFIG_CTX(includes=['ninja', 'mb'])
def dawn_base(c):
  c.project_generator.isolate_map_paths = [
      'infra/config/global/generated/testing/gn_isolate_map.pyl',
  ]
  c.project_generator.config_path = 'infra/specs/mb_config.pyl'
  c.targets_spec_dir = 'infra/specs'
