# Copyright 2020 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process

from RECIPE_MODULES.build import chromium_types
from RECIPE_MODULES.build.chromium_tests_builder_config import (
  try_spec as try_spec_module,
)

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.recipe_engine import assertions


@dataclass
class DEPS(RecipeScriptApi):
  assertions: assertions.API


def RunSteps(api: DEPS):
  api.assertions.maxDiff = None

  # TryMirror creation *********************************************************

  # Creation of a TryMirror without tester
  mirror = try_spec_module.TryMirror.create('fake-group', 'fake-builder')
  api.assertions.assertEqual(
    mirror.builder_id,
    chromium_types.BuilderId.create_for_group('fake-group', 'fake-builder'),
  )
  api.assertions.assertIsNone(mirror.tester_id)

  # Creation of a TryMirror with tester
  mirror = try_spec_module.TryMirror.create(
    'fake-group', 'fake-builder', 'fake-tester', 'fake-tester-group'
  )
  api.assertions.assertEqual(
    mirror.builder_id,
    chromium_types.BuilderId.create_for_group('fake-group', 'fake-builder'),
  )
  api.assertions.assertEqual(
    mirror.tester_id,
    chromium_types.BuilderId.create_for_group(
      'fake-tester-group', 'fake-tester'
    ),
  )

  # Creation of a TryMirror with tester without tester group
  mirror = try_spec_module.TryMirror.create(
    'fake-group', 'fake-builder', 'fake-tester'
  )
  api.assertions.assertEqual(
    mirror.builder_id,
    chromium_types.BuilderId.create_for_group('fake-group', 'fake-builder'),
  )
  api.assertions.assertEqual(
    mirror.tester_id,
    chromium_types.BuilderId.create_for_group('fake-group', 'fake-tester'),
  )

  # Creation of a TryMirror with builder for tester
  mirror = try_spec_module.TryMirror.create(
    'fake-group', 'fake-builder', 'fake-builder'
  )
  api.assertions.assertEqual(
    mirror.builder_id,
    chromium_types.BuilderId.create_for_group('fake-group', 'fake-builder'),
  )
  api.assertions.assertIsNone(mirror.tester_id)

  # TryMirror normalization ****************************************************

  # Normalization of a TryMirror
  mirror = try_spec_module.TryMirror.create('fake-group', 'fake-builder')
  mirror2 = try_spec_module.TryMirror.normalize(mirror)
  api.assertions.assertIs(mirror2, mirror)

  # Normalization of a BuilderId
  builder_id = chromium_types.BuilderId.create_for_group(
    'fake-group', 'fake-builder'
  )
  mirror = try_spec_module.TryMirror.normalize(builder_id)
  api.assertions.assertEqual(
    mirror, try_spec_module.TryMirror.create('fake-group', 'fake-builder')
  )

  # TryDatabase mapping interface **********************************************
  db = try_spec_module.TryDatabase.create(
    {
      'fake-try-group-1': {
        'fake-try-builder-1': try_spec_module.TrySpec.create(
          mirrors=[
            chromium_types.BuilderId.create_for_group('group-1', 'builder-1'),
          ]
        ),
      },
      'fake-try-group-2': {
        'fake-try-builder-2': try_spec_module.TrySpec.create(
          mirrors=[
            chromium_types.BuilderId.create_for_group('group-2', 'builder-2'),
          ]
        ),
      },
    }
  )

  try_key_1 = chromium_types.BuilderId.create_for_group(
    'fake-try-group-1', 'fake-try-builder-1'
  )
  try_key_2 = chromium_types.BuilderId.create_for_group(
    'fake-try-group-2', 'fake-try-builder-2'
  )

  api.assertions.assertEqual(set(db.keys()), {try_key_1, try_key_2})
  api.assertions.assertEqual(
    db[try_key_1],
    try_spec_module.TrySpec.create(
      mirrors=[
        try_spec_module.TryMirror.create(
          builder_group='group-1', buildername='builder-1'
        )
      ]
    ),
  )
  api.assertions.assertEqual(
    db[try_key_2],
    try_spec_module.TrySpec.create(
      mirrors=[
        try_spec_module.TryMirror.create(
          builder_group='group-2', buildername='builder-2'
        )
      ]
    ),
  )


def GenTests(api: RecipeTestApi):
  yield api.test(
    'full',
    api.post_process(post_process.DropExpectation),
  )
