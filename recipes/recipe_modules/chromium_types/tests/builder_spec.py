# Copyright 2025 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process
from recipe_engine.engine_types import FrozenDict

from RECIPE_MODULES.build.chromium_types import BuilderSpec

DEPS = ['recipe_engine/assertions']


def RunSteps(api):
  builder_spec = BuilderSpec.create(
      chromium_config='hello',
      chromium_apply_config=['a', 'b', 'c'],
      chromium_config_kwargs={
          'varname': 100,
      },
      clobber=True,
  )
  api.assertions.assertEqual(builder_spec.chromium_config, 'hello')
  api.assertions.assertEqual(builder_spec.chromium_apply_config,
                             ('a', 'b', 'c'))
  api.assertions.assertEqual(builder_spec.chromium_config_kwargs,
                             FrozenDict({'varname': 100}))
  api.assertions.assertEqual(builder_spec.clobber, True)


def GenTests(api):
  yield api.test(
      'full',
      api.post_process(post_process.DropExpectation),
  )
