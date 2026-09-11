# Copyright 2018 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process

from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb2

from RECIPE_MODULES.build import chromium_types

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import (
  chromium,
  chromium_tests,
  chromium_tests_builder_config,
)
from RECIPE_MODULES.depot_tools import gclient
from RECIPE_MODULES.recipe_engine import (
  buildbucket,
  json,
  properties,
  step,
)


@dataclass
class DEPS(RecipeScriptApi):
  buildbucket: buildbucket.API
  chromium: chromium.API
  chromium_tests: chromium_tests.API
  chromium_tests_builder_config: chromium_tests_builder_config.API
  gclient: gclient.API
  json: json.API
  properties: properties.API
  step: step.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  buildbucket: buildbucket.TEST_API
  chromium: chromium.TEST_API
  chromium_tests: chromium_tests.TEST_API
  properties: properties.TEST_API


# Name of pinpoint try builder -> (perf builder group, perf builder name)
_PINPOINT_MAPPING = {
  'Android Compile Perf': ('chromium.perf.pinpoint', 'android-builder-perf'),
  'Android Compile Perf PGO': (
    'chromium.perf.pinpoint',
    'android-builder-perf-pgo',
  ),
  'Android arm64 Compile Perf': (
    'chromium.perf.pinpoint',
    'android_arm64-builder-perf',
  ),
  'Android arm64 Compile Perf PGO': (
    'chromium.perf.pinpoint',
    'android_arm64-builder-perf-pgo',
  ),
  'Android arm64 High End Compile Perf': (
    'chromium.perf.pinpoint',
    'android_arm64_high_end-builder-perf',
  ),
  'Android arm64 High End Compile Perf PGO': (
    'chromium.perf.pinpoint',
    'android_arm64_high_end-builder-perf-pgo',
  ),
  'Android arm64 High End Compile Perf PGO Heapdump': (
    'chromium.perf.pinpoint',
    'android_arm64_high_end-builder-perf-pgo-heapdump',
  ),
  'Fuchsia Builder Perf': ('chromium.perf.fyi', 'fuchsia-builder-perf-arm64'),
  'Linux Builder Perf': ('chromium.perf.pinpoint', 'linux-builder-perf'),
  'Linux Builder Perf PGO': (
    'chromium.perf.pinpoint',
    'linux-builder-perf-pgo',
  ),
  'Mac Builder Perf': ('chromium.perf.pinpoint', 'mac-builder-perf'),
  'Mac Builder Perf PGO': ('chromium.perf.pinpoint', 'mac-builder-perf-pgo'),
  'Mac arm Builder Perf': ('chromium.perf.pinpoint', 'mac-arm-builder-perf'),
  'Mac arm Builder Perf PGO': (
    'chromium.perf.pinpoint',
    'mac-arm-builder-perf-pgo',
  ),
  'mac-laptop_high_end-perf': (
    'chromium.perf.pinpoint',
    'mac-laptop_high_end-perf',
  ),
  'Win x64 Builder Perf': ('chromium.perf.pinpoint', 'win64-builder-perf'),
  'Win x64 Builder Perf PGO': (
    'chromium.perf.pinpoint',
    'win64-builder-perf-pgo',
  ),
  'win-arm64-builder-perf': ('chromium.perf', 'win-arm64-builder-perf'),
}


def _is_googler_authorized(api: DEPS) -> bool:
  """Returns True if the build was triggered or requested by a Googler."""
  created_by = api.buildbucket.build.created_by
  if created_by.startswith('user:') and created_by.endswith('@google.com'):
    return True

  user = api.properties.get('pinpoint_user') or api.properties.get('user')
  if user and user.endswith('@google.com'):
    return True

  for tag in api.buildbucket.build.tags:
    if tag.key in ('user_email', 'pinpoint_user') and tag.value.endswith(
      '@google.com'
    ):
      return True

  return False


def RunSteps(api: DEPS):
  # In Perf on CQ, if CQ triggers Pinpoint, the builds triggered by those
  # Pinpoint jobs will show up in Gerrit. We should not show them because
  # this should be part of Pinpoint workflow and be transparent to gerrit
  # end users.
  api.buildbucket.hide_current_build_in_gerrit()

  pinpoint_builder = api.buildbucket.builder_name
  perf_builder = _PINPOINT_MAPPING.get(pinpoint_builder)
  if perf_builder is None:
    api.step.empty(
      'no pinpoint mapping',
      status=api.step.INFRA_FAILURE,
      step_text=(
        'No pinpoint mapping is configured for {!r}.\n'
        'Please update pinpoint/builder.py'
      ).format(pinpoint_builder),
    )

  with api.chromium.chromium_layout():
    builder_id = chromium_types.BuilderId.create_for_group(*perf_builder)
    _, builder_config = api.chromium_tests_builder_config.lookup_builder(
      builder_id, use_try_db=False
    )

    api.chromium_tests.configure_build(builder_config)

    # Prevent untrusted external patches from checking out internal repositories
    # or running hooks with access to internal proprietary source code.
    gerrit_changes = api.buildbucket.build.input.gerrit_changes
    if gerrit_changes:
      has_public_patch = any(
        c.host != 'chrome-internal-review.googlesource.com'
        for c in gerrit_changes
      )
      is_googler = _is_googler_authorized(api)

      if has_public_patch:
        # Check if the builder requires internal mobile repositories (Clank).
        requires_clank = api.chromium.c.TARGET_PLATFORM == 'android'
        if requires_clank and not is_googler:
          api.step.empty(
            'untrusted patch for internal target',
            status=api.step.INFRA_FAILURE,
            step_text=(
              'Untrusted external patches cannot trigger builds requiring '
              'internal repositories (Clank). Please request a Googler to '
              'sponsor or review your patch.'
            ),
          )

        # For public patches, always strip checkout_src_internal and
        # checkout_pgo_profiles so that unreviewed code cannot access internal
        # repositories during gclient runhooks.
        for solution in api.gclient.c.solutions:
          if 'checkout_src_internal' in solution.custom_vars:
            solution.custom_vars['checkout_src_internal'] = 'False'
          if 'checkout_pgo_profiles' in solution.custom_vars:
            solution.custom_vars['checkout_pgo_profiles'] = 'False'
          if not is_googler or not requires_clank:
            if 'checkout_mobile_internal' in solution.custom_vars:
              solution.custom_vars['checkout_mobile_internal'] = 'False'

        api.step.empty(
          'sanitize public patch checkout',
          step_text=(
            'Public patch detected: stripped internal checkout custom vars '
            '(checkout_src_internal, checkout_pgo_profiles) for security.'
          ),
        )

    for key, value in api.gclient.c.repo_path_map.items():
      if value[1] == 'HEAD':
        # Pinpoint should use the exact revision given in DEPS, instead of HEAD.
        api.gclient.c.repo_path_map[key] = (value[0], None)
    update_step, build_dir, targets_config = (
      api.chromium_tests.prepare_checkout(
        builder_config, enforce_fetch=True, clean_ignored=True
      )
    )
    source_dir = update_step.source_root.path
    compile_result, compile_output = (
      api.chromium_tests.compile_specific_targets(
        build_dir,
        builder_id,
        builder_config,
        update_step,
        targets_config,
        targets_config.compile_targets,
        targets_config.all_tests,
      )
    )

    if compile_result and compile_result.status != common_pb2.SUCCESS:
      return compile_result

    if compile_output:
      api.chromium_tests.isolate_test_targets(
        source_dir, build_dir, builder_config, update_step, compile_output
      )
    return compile_result


def GenTests(api: TEST_DEPS):
  # pylint: disable=import-outside-toplevel
  from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb2

  # Data used in tests.
  builder = {
    'builder_group': 'tryserver.chromium.perf',
    'builder': 'Android Compile Perf',
  }
  targets_spec = (
    'chromium.perf.pinpoint',
    {
      'android-go-wembley-perf': {
        'isolated_scripts': [
          {
            'test': 'performance_test_suite_android_clank_trichrome_bundle',
            'name': 'performance_test_suite',
          },
        ],
      },
    },
  )

  yield api.test(
    'basic',
    api.chromium.generic_build(**builder),
    api.chromium_tests.read_targets_spec(*targets_spec),
  )

  yield api.test(
    'with_patch',
    api.chromium.try_build(
      revision='0cd310e5609606ca9c8531313142a1a9f16ae860',
      gerrit_changes=[
        common_pb2.GerritChange(
          host='chromium-review.googlesource.com',
          project='v8/v8',
          change=3592345,
          patchset=1,
        )
      ],
      **builder,
    ),
    api.properties(pinpoint_user='developer@google.com'),
    api.chromium_tests.read_targets_spec(*targets_spec),
    api.post_process(
      post_process.Filter('bot_update', 'pinpoint isolate upload')
    ),
  )

  # Untrusted external patch targeting an internal builder (Clank)
  # must fail fast before checkout.
  yield api.test(
    'untrusted_patch_internal_target',
    api.chromium.try_build(
      revision='0cd310e5609606ca9c8531313142a1a9f16ae860',
      gerrit_changes=[
        common_pb2.GerritChange(
          host='chromium-review.googlesource.com',
          project='chromium/src',
          change=9999999,
          patchset=1,
        )
      ],
      created_by='user:attacker@example.com',
      **builder,
    ),
    api.post_check(post_process.MustRun, 'untrusted patch for internal target'),
    api.expect_status('INFRA_FAILURE'),
    api.post_process(post_process.DropExpectation),
  )

  # Untrusted external patch targeting a public builder
  # must sanitize internal checkouts (checkout_src_internal = False).
  linux_builder = {
    'builder_group': 'tryserver.chromium.perf',
    'builder': 'Linux Builder Perf',
  }
  linux_targets_spec = (
    'chromium.perf.pinpoint',
    {
      'linux-perf': {
        'isolated_scripts': [
          {
            'test': 'performance_test_suite',
            'name': 'performance_test_suite',
          },
        ],
      },
    },
  )
  yield api.test(
    'untrusted_patch_public_target',
    api.chromium.try_build(
      revision='0cd310e5609606ca9c8531313142a1a9f16ae860',
      gerrit_changes=[
        common_pb2.GerritChange(
          host='chromium-review.googlesource.com',
          project='chromium/src',
          change=9999999,
          patchset=1,
        )
      ],
      created_by='user:attacker@example.com',
      **linux_builder,
    ),
    api.chromium_tests.read_targets_spec(*linux_targets_spec),
    api.post_check(post_process.MustRun, 'sanitize public patch checkout'),
    api.post_check(
      lambda check, steps: check(
        'bot_update disables checkout_src_internal',
        any(
          "'checkout_src_internal': 'False'" in a
          for a in steps['bot_update'].cmd
        ),
      )
    ),
    api.post_check(
      lambda check, steps: check(
        'bot_update disables checkout_pgo_profiles',
        any(
          "'checkout_pgo_profiles': 'False'" in a
          for a in steps['bot_update'].cmd
        ),
      )
    ),
    api.post_process(post_process.DropExpectation),
  )

  # Internal Gerrit patch preserves internal checkouts.
  yield api.test(
    'internal_gerrit_patch',
    api.chromium.try_build(
      revision='0cd310e5609606ca9c8531313142a1a9f16ae860',
      gerrit_changes=[
        common_pb2.GerritChange(
          host='chrome-internal-review.googlesource.com',
          project='chrome/src-internal',
          change=123456,
          patchset=1,
        )
      ],
      **builder,
    ),
    api.chromium_tests.read_targets_spec(*targets_spec),
    api.post_check(
      post_process.DoesNotRun, 'untrusted patch for internal target'
    ),
    api.post_check(post_process.DoesNotRun, 'sanitize public patch checkout'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'git_repo',
    api.chromium.try_build(
      revision='1cd310e5609606ca9c8531313142a1a9f16ae860',
      git_repo='https://chromium.googlesource.com/v8/v8',
      created_by='user:developer@google.com',
      **builder,
    ),
    api.chromium_tests.read_targets_spec(*targets_spec),
    api.post_process(post_process.Filter('pinpoint isolate upload')),
  )

  # Googler authorization via buildbucket tags.
  yield api.test(
    'googler_tag_patch',
    api.chromium.try_build(
      revision='0cd310e5609606ca9c8531313142a1a9f16ae860',
      gerrit_changes=[
        common_pb2.GerritChange(
          host='chromium-review.googlesource.com',
          project='v8/v8',
          change=3592345,
          patchset=1,
        )
      ],
      tags=api.buildbucket.tags(user_email='developer@google.com'),
      **builder,
    ),
    api.chromium_tests.read_targets_spec(*targets_spec),
    api.post_process(
      post_process.Filter('bot_update', 'pinpoint isolate upload')
    ),
  )

  yield api.test(
    'bad_builder',
    api.chromium.generic_build(
      builder_group='tryserver.chromium.perf',
      builder='Nonexistent Compile Perf',
    ),
    api.post_check(post_process.MustRun, 'no pinpoint mapping'),
    api.expect_status('INFRA_FAILURE'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'compile_failure',
    api.chromium.generic_build(**builder),
    api.chromium_tests.read_targets_spec(*targets_spec),
    api.step_data('compile', retcode=1),
    api.expect_status('FAILURE'),
    api.post_process(post_process.DropExpectation),
  )
