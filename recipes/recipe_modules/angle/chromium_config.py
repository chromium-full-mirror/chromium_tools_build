# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from RECIPE_MODULES.build.chromium import CONFIG_CTX


@CONFIG_CTX(includes=['ninja', 'mb'])
def angle_base(c):
  c.project_generator.isolate_map_paths = [
    'infra/config/generated/testing/gn_isolate_map.pyl',
  ]
  c.project_generator.config_path = 'infra/specs/angle_mb_config.pyl'
  c.targets_spec_dir = 'infra/specs'
  c.analyze_config_path = 'infra/specs/trybot_analyze_config.json'


@CONFIG_CTX(includes=['angle_base', 'clang'])
def angle_clang(c):
  pass
