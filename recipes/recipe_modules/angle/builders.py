# Copyright 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from RECIPE_MODULES.build.chromium_tests_builder_config import (builder_db,
                                                                builder_spec)


_SPEC = {}

BUILDERS = builder_db.BuilderDatabase.create({
    'angle': _SPEC,
})
