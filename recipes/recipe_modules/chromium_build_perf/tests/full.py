# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from RECIPE_MODULES.build import chromium_types
from RECIPE_MODULES.build import chromium_tests_builder_config as ctbc
from recipe_engine import post_process

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import (
  builder_group,
  chromium,
  chromium_build_perf,
  chromium_tests,
  chromium_tests_builder_config,
  reclient,
  siso,
)
from RECIPE_MODULES.recipe_engine import (
  buildbucket,
  file,
  path,
  platform,
)


@dataclass
class DEPS(RecipeScriptApi):
  buildbucket: buildbucket.API
  builder_group: builder_group.API
  chromium: chromium.API
  chromium_build_perf: chromium_build_perf.API
  chromium_tests: chromium_tests.API
  chromium_tests_builder_config: chromium_tests_builder_config.API
  file: file.API
  path: path.API
  platform: platform.API
  reclient: reclient.API
  siso: siso.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  chromium: chromium.TEST_API
  chromium_tests_builder_config: chromium_tests_builder_config.TEST_API
  platform: platform.TEST_API
  reclient: reclient.TEST_API
  siso: siso.TEST_API


def RunSteps(api: DEPS):
  builder_id = chromium_types.BuilderId.create_for_group(
    api.builder_group.for_current, api.buildbucket.builder_name
  )
  _, builder_config = api.chromium_tests_builder_config.lookup_builder(
    builder_id, use_try_db=False
  )
  api.chromium_tests.configure_build(builder_config)
  source_dir = api.path.cache_dir / 'builder/src'
  build_dir = api.chromium.default_build_dir(source_dir)

  api.chromium_build_perf.build_with_ninja(
    source_dir, build_dir, 'all', with_remote_cache=True
  )
  api.chromium_build_perf.build_with_ninja(
    source_dir, build_dir, 'all', with_remote_cache=False
  )
  api.chromium_build_perf.build_with_siso(
    source_dir, build_dir, 'all', with_remote_cache=True
  )
  api.chromium_build_perf.build_with_siso(
    source_dir, build_dir, 'all', with_remote_cache=False
  )
  api.chromium_build_perf.build_with_siso(
    source_dir, build_dir, 'all', use_rbe=False
  )
  api.chromium_build_perf.build_with_siso(
    source_dir,
    build_dir,
    'all',
    with_remote_cache=True,
    siso_experiments=['fail-on-bad-deps'],
  )
  api.chromium_build_perf.build_with_ninja(
    source_dir,
    build_dir,
    'all',
    with_remote_cache=False,
    step_name_suffix=' suffix',
  )
  api.file.write_raw('write .siso_deps', build_dir / '.siso_deps', 'siso deps')
  api.chromium_build_perf.recreate_build_dir(source_dir, build_dir)
  api.chromium_build_perf.recreate_build_dir(source_dir, source_dir / 'foo')
  api.chromium_build_perf.recreate_build_dir(
    source_dir, build_dir, remove_deps_cache=True
  )
  api.chromium_build_perf.checkout(source_dir, build_dir, 'abcd')

  rusage = {'foo': 1}
  include_analysis = {
    'target': 'chrome',
    'revision': 'abcd',
    'date': None,
    'files': [
      'a.cc',
      'a.h',
    ],
    'roots': [0],
    'includes': [[1]],
    'included_by': [[], [0]],
    'sizes': [10, 20],
    'tsizes': [30, 20],
    'archive_link': '',
  }
  api.chromium_build_perf.upload_build_stats_to_bq(rusage, include_analysis)


def GenTests(api: TEST_DEPS):
  ctbc_api = api.chromium_tests_builder_config
  builder = {
    'builder_group': 'fake-group',
    'builder': 'fake-builder',
  }
  yield api.test(
    'full',
    api.chromium.ci_build(**builder),
    ctbc_api.properties(
      ctbc_api.properties_assembler_for_ci_builder(
        builder_spec=ctbc.BuilderSpec.create(
          gclient_config='chromium',
          chromium_config='chromium',
        ),
        **builder,
      ).assemble()
    ),
    api.reclient.properties(),
    api.siso.properties(),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'ios',
    api.chromium.ci_build(**builder),
    ctbc_api.properties(
      ctbc_api.properties_assembler_for_ci_builder(
        builder_spec=ctbc.BuilderSpec.create(
          gclient_config='ios',
          chromium_config='chromium',
          chromium_config_kwargs={
            'TARGET_PLATFORM': 'ios',
          },
        ),
        **builder,
      ).assemble()
    ),
    api.platform.name('mac'),
    api.reclient.properties(),
    api.siso.properties(),
    api.post_process(post_process.DropExpectation),
  )
