# Copyright 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from PB.infra.chromium import chromium_bootstrap

PROPERTIES = chromium_bootstrap.ChromiumBootstrapModuleProperties

DEPS = [
    'depot_tools/gclient',
    'recipe_engine/json',
    'recipe_engine/properties',
    'recipe_engine/step',
]
