# Copyright 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations


DEPS = [
    'recipe_engine/cipd',
    'recipe_engine/path',
    'recipe_engine/platform',
    'recipe_engine/step',
]

from .api import SquashfsApi as API
