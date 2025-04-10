# Copyright 2025 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from RECIPE_MODULES.build.chromium_tests_builder_config import try_spec

_SPEC = {
    'dawn-cq-linux-x64-sws-rel':
        try_spec.TrySpec.create(
            mirrors=[
                try_spec.TryMirror.create(
                    builder_group='dawn',
                    buildername='dawn-linux-x64-builder-rel',
                    tester='dawn-linux-x64-sws-rel',
                ),
            ],),
}

TRYBOTS = try_spec.TryDatabase.create({
    'dawn': _SPEC,
})
