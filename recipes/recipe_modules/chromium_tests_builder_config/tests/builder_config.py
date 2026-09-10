# Copyright 2020 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process

from RECIPE_MODULES.build.chromium_types import BuilderId
from RECIPE_MODULES.build.chromium_tests_builder_config import (
  builder_config as builder_config_module,
  builder_db,
  builder_spec,
  try_spec,
)
from RECIPE_MODULES.build.chromium_tests_builder_config.builder_config import (
  BuildbucketBuilderId,
)

from PB.go.chromium.org.luci.buildbucket.proto import (
  builder_common as builder_common_pb,
)

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.recipe_engine import assertions, step


@dataclass
class DEPS(RecipeScriptApi):
  assertions: assertions.API
  step: step.API


def RunSteps(api: DEPS):
  # Set up data for testing builder_config methods
  builders = builder_db.BuilderDatabase.create(
    {
      'fake-group': {
        'fake-builder': builder_spec.BuilderSpec.create(),
        'fake-tester': builder_spec.BuilderSpec.create(
          execution_mode=builder_spec.TEST,
          parent_buildername='fake-builder',
        ),
      },
      'fake-group2': {
        'fake-builder2': builder_spec.BuilderSpec.create(),
      },
      'fake-group3': {
        'fake-tester2': builder_spec.BuilderSpec.create(
          execution_mode=builder_spec.TEST,
          parent_builder_group='fake-group',
          parent_buildername='fake-builder',
        ),
      },
    }
  )

  trybots = try_spec.TryDatabase.create(
    {
      'fake-try-group': {
        'fake-try-builder-with-bad-builder': try_spec.TrySpec.create_for_single_mirror(
          builder_group='fake-group',
          buildername='fake-tester',
        ),
        'fake-try-builder-with-bad-tester': try_spec.TrySpec.create_for_single_mirror(
          builder_group='fake-group2',
          buildername='fake-builder2',
          tester_group='fake-group',
          tester='fake-builder',
        ),
      },
    }
  )

  # Test create failures
  with api.assertions.assertRaises(
    builder_config_module.BuilderConfigException
  ) as caught:
    builder_config_module.BuilderConfig.create(builders, [])
  message = "No builder IDs specified"
  api.assertions.assertEqual(str(caught.exception), message)

  with api.assertions.assertRaises(
    builder_config_module.BuilderConfigException
  ) as caught:
    builder_config_module.BuilderConfig.create(
      builders,
      [BuilderId.create_for_group('non-existent-group', 'fake-builder')],
    )
  message = "No configuration present for group 'non-existent-group'"
  api.assertions.assertEqual(str(caught.exception), message)

  with api.assertions.assertRaises(
    builder_config_module.BuilderConfigException
  ) as caught:
    builder_config_module.BuilderConfig.create(
      builders,
      [BuilderId.create_for_group('fake-group', 'non-existent-builder')],
    )
  message = (
    "No configuration present for builder 'non-existent-builder'"
    " in group 'fake-group'"
  )
  api.assertions.assertEqual(str(caught.exception), message)

  # Test create failure when using step API
  with api.assertions.assertRaises(api.step.InfraFailure) as caught:
    builder_config_module.BuilderConfig.create(
      builders,
      [BuilderId.create_for_group('non-existent-group', 'fake-builder')],
      step_api=api.step,
    )
  name = "No configuration present for group 'non-existent-group'"
  api.assertions.assertEqual(caught.exception.result.name, name)

  # Test lookup failures
  with api.assertions.assertRaises(
    builder_config_module.BuilderConfigException
  ) as caught:
    builder_config_module.BuilderConfig.lookup(
      BuilderId.create_for_group(
        'fake-try-group', 'fake-try-builder-with-bad-builder'
      ),
      builders,
      trybots,
    )
  message = (
    "try builder 'fake-try-group:fake-try-builder-with-bad-builder' specifies"
    " 'fake-group:fake-tester' as a builder, but it has execution mode test,"
    " it must be compile/test"
  )
  api.assertions.assertEqual(str(caught.exception), message)

  with api.assertions.assertRaises(
    builder_config_module.BuilderConfigException
  ) as caught:
    builder_config_module.BuilderConfig.lookup(
      BuilderId.create_for_group(
        'fake-try-group', 'fake-try-builder-with-bad-tester'
      ),
      builders,
      trybots,
    )
  message = (
    "try builder 'fake-try-group:fake-try-builder-with-bad-tester'"
    " specifies 'fake-group:fake-builder' as a tester,"
    " but it has execution mode compile/test, it must be test"
  )
  api.assertions.assertEqual(str(caught.exception), message)

  # Test lookup failures when using step API
  with api.assertions.assertRaises(api.step.InfraFailure) as caught:
    builder_config_module.BuilderConfig.lookup(
      BuilderId.create_for_group(
        'fake-try-group', 'fake-try-builder-with-bad-builder'
      ),
      builders,
      trybots,
      step_api=api.step,
    )
  name = (
    "try builder 'fake-try-group:fake-try-builder-with-bad-builder' specifies"
    " 'fake-group:fake-tester' as a builder, but it has execution mode test,"
    " it must be compile/test"
  )
  api.assertions.assertEqual(caught.exception.result.name, name)

  # Test builder config
  builder_config = builder_config_module.BuilderConfig.create(
    builders,
    builder_ids=[
      BuilderId.create_for_group('fake-group', 'fake-builder'),
      BuilderId.create_for_group('fake-group2', 'fake-builder2'),
    ],
    builder_ids_in_scope_for_testing=[
      BuilderId.create_for_group('fake-group', 'fake-tester'),
      BuilderId.create_for_group('fake-group3', 'fake-tester2'),
    ],
  )

  # Test builders_id property
  api.assertions.assertEqual(
    builder_config.builder_ids,
    (
      BuilderId.create_for_group('fake-group', 'fake-builder'),
      BuilderId.create_for_group('fake-group2', 'fake-builder2'),
    ),
  )

  # Test targets_spec_files property
  api.assertions.assertEqual(
    builder_config.targets_spec_files,
    {
      'fake-group': 'fake-group.json',
      'fake-group2': 'fake-group2.json',
      'fake-group3': 'fake-group3.json',
    },
  )

  # Test get_buildbucket_id method
  api.assertions.assertIsNone(
    builder_config.get_buildbucket_builder_id(
      BuilderId.create_for_group('fake-group', 'fake-builder')
    )
  )

  builder_config_with_bb_ids = builder_config_module.BuilderConfig.create(
    builders,
    builder_ids=[BuilderId.create_for_group('fake-group', 'fake-builder')],
    builder_ids_in_scope_for_testing=[
      BuilderId.create_for_group('fake-group', 'fake-tester')
    ],
    bb_builder_id_by_builder_id={
      BuilderId.create_for_group(
        'fake-group', 'fake-builder'
      ): BuildbucketBuilderId(
        project='fake-project', bucket='fake-bucket', builder='fake-builder'
      ),
      BuilderId.create_for_group(
        'fake-group', 'fake-tester'
      ): BuildbucketBuilderId(
        project='fake-project', bucket='fake-bucket', builder='fake-tester'
      ),
    },
  )

  bb_builder_id = builder_config_with_bb_ids.get_buildbucket_builder_id(
    BuilderId.create_for_group('fake-group', 'fake-builder')
  )
  api.assertions.assertEqual(
    bb_builder_id,
    BuildbucketBuilderId(
      project='fake-project', bucket='fake-bucket', builder='fake-builder'
    ),
  )
  api.assertions.assertEqual(
    bb_builder_id.to_proto(),
    builder_common_pb.BuilderID(
      project='fake-project', bucket='fake-bucket', builder='fake-builder'
    ),
  )
  bb_tester_id = builder_config_with_bb_ids.get_buildbucket_builder_id(
    BuilderId.create_for_group('fake-group', 'fake-tester')
  )
  api.assertions.assertEqual(
    bb_tester_id,
    BuildbucketBuilderId(
      project='fake-project', bucket='fake-bucket', builder='fake-tester'
    ),
  )
  bb_builder2_id = builder_config_with_bb_ids.get_buildbucket_builder_id(
    BuilderId.create_for_group('fake-group2', 'fake-builder2')
  )
  api.assertions.assertIsNone(bb_builder2_id)

  # Test BuilderSpec-consistent properties
  builder_config_with_matched_values = (
    builder_config_module.BuilderConfig.create(
      builders,
      [
        BuilderId.create_for_group('fake-group', 'fake-builder'),
        BuilderId.create_for_group('fake-group2', 'fake-builder2'),
      ],
    )
  )
  api.assertions.assertEqual(
    builder_config_with_matched_values.execution_mode,
    builder_spec.COMPILE_AND_TEST,
  )

  builder_config_with_mismatched_values = (
    builder_config_module.BuilderConfig.create(
      builders,
      [
        BuilderId.create_for_group('fake-group', 'fake-builder'),
        BuilderId.create_for_group('fake-group3', 'fake-tester2'),
      ],
    )
  )
  with api.assertions.assertRaises(ValueError) as caught:
    # pylint: disable=pointless-statement
    builder_config_with_mismatched_values.execution_mode
  api.assertions.assertIn('Inconsistent value', str(caught.exception))


def GenTests(api: RecipeTestApi):
  yield api.test(
    'full',
    api.post_process(post_process.DropExpectation),
  )
