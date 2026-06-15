# Copyright 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine.engine_types import freeze

from RECIPE_MODULES.build.chromium_tests_builder_config import try_spec


_SPEC = {}

TRYBOTS = try_spec.TryDatabase.create({
    'angle': _SPEC,
})
