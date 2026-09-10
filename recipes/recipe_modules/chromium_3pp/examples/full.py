# Copyright 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import chromium_3pp
from RECIPE_MODULES.recipe_engine import (
  buildbucket,
  file,
  path,
  properties,
  raw_io,
)


@dataclass
class DEPS(RecipeScriptApi):
  buildbucket: buildbucket.API
  chromium_3pp: chromium_3pp.API
  file: file.API
  path: path.API
  properties: properties.API
  raw_io: raw_io.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  buildbucket: buildbucket.TEST_API
  file: file.TEST_API
  path: path.TEST_API
  properties: properties.TEST_API
  raw_io: raw_io.TEST_API


def RunSteps(api: DEPS):
  api.chromium_3pp.prepare()
  api.chromium_3pp.execute()


def GenTests(api: TEST_DEPS):

  def generate_properties(runtime_properties=None, **kwargs):
    chromium_3pp_properties = {
      'platform': 'linux-amd64',
      'package_prefix': 'chromium',
      'gclient_config': 'chromium',
      'gclient_apply_config': ['android'],
    }
    chromium_3pp_properties.update(**kwargs)
    properties = {'$build/chromium_3pp': chromium_3pp_properties}
    if runtime_properties is not None:
      properties['$recipe_engine/runtime'] = runtime_properties
    return api.properties(**properties)

  package_spec_other = '''
  create {
    source { git {} }
    build { dep: "p_apple/some" }
  }
  upload {pkg_prefix: "p_apple"}
  '''
  package_spec_some = '''
  create {
    source { git {} }
  }
  upload {pkg_prefix: "p_apple"}
  '''

  yield api.test(
    'basic',
    generate_properties(),
    api.post_process(
      post_process.MustRun,
      'Load all packages',
    ),
    api.override_step_data(
      'Load all packages.find package specs',
      api.file.glob_paths(
        ['third_party/other/3pp/3pp.pb', 'third_party/some/3pp/3pp.pb']
      ),
    ),
    api.override_step_data(
      "Load all packages.load package specs."
      "read 'third_party/other/3pp/3pp.pb'",
      api.file.read_text(package_spec_other),
    ),
    api.override_step_data(
      "Load all packages.load package specs.read 'third_party/some/3pp/3pp.pb'",
      api.file.read_text(package_spec_some),
    ),
    api.post_process(
      post_process.MustRun, 'building p_apple/other', 'building p_apple/some'
    ),
    # CI should upload the 3pp packages
    api.post_process(
      post_process.MustRun,
      'building p_apple/other.do upload',
      'building p_apple/some.do upload',
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'basic_with_preprocess',
    generate_properties(
      preprocess=[
        {
          'name': 'third_party/foo',
          'cmd': [
            '{CHECKOUT}/src/third_party/foo/bar.py',
            '--verbose',
          ],
        }
      ]
    ),
    api.post_process(
      post_process.MustRun,
      'Preprocessing third_party/foo',
    ),
    api.post_process(
      post_process.StepCommandContains,
      'Preprocessing third_party/foo',
      ['[CACHE]/builder/src/third_party/foo/bar.py', '--verbose'],
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'basic_to_build',
    generate_properties(package_paths_to_build=['third_party/bar/some']),
    api.post_process(
      post_process.MustRun,
      'Load to-build packages from third_party/bar/some',
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'basic_with_local_src',
    generate_properties(
      runtime_properties={'is_experimental': True},
      local_checkout_dir='[CACHE]/hi',
    ),
    api.path.exists(api.path.cache_dir.joinpath('hi', '.gclient')),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'unexpected_3pp_change',
    generate_properties(
      preprocess=[
        {
          'name': 'third_party/foo',
          'cmd': [
            '{CHECKOUT}/src/third_party/foo/bar.py',
            '--verbose',
          ],
        }
      ]
    ),
    api.override_step_data(
      'Confirm no-op',
      api.raw_io.stream_output_text('third_party/qux/3pp/3pp.pb'),
    ),
    api.expect_status('FAILURE'),
    api.post_process(post_process.SummaryMarkdownRE, 'Unexpected 3pp changes'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'unsupported_packages',
    generate_properties(),
    api.override_step_data(
      'Load all packages.find package specs',
      api.file.glob_paths(['pear/3pp/3pp.pb']),
    ),
    api.override_step_data(
      "Load all packages.load package specs.read 'pear/3pp/3pp.pb'",
      api.file.read_text('''
              create { unsupported: true }
              upload { pkg_prefix: "prefix/deps" }
              '''),
    ),
    api.post_process(
      post_process.StepTextEquals, 'Unsupported packages', 'prefix/deps/pear'
    ),
    api.post_process(post_process.DropExpectation),
  )

  # Tests for trybot
  yield api.test(
    'trybot_basic',
    api.buildbucket.try_build(),
    generate_properties(package_paths_to_build=['third_party/some']),
    api.override_step_data(
      'Analyze', api.raw_io.stream_output_text('third_party/other/3pp/fetch.py')
    ),
    api.post_process(
      post_process.LogEquals,
      'Analyze',
      'package_paths_to_build',
      '\n'.join(['third_party/other', 'third_party/some']),
    ),
    api.override_step_data(
      'Load all packages.find package specs',
      api.file.glob_paths(
        ['third_party/other/3pp/3pp.pb', 'third_party/some/3pp/3pp.pb']
      ),
    ),
    api.override_step_data(
      "Load all packages.load package specs."
      "read 'third_party/other/3pp/3pp.pb'",
      api.file.read_text(package_spec_other),
    ),
    api.override_step_data(
      "Load all packages.load package specs.read 'third_party/some/3pp/3pp.pb'",
      api.file.read_text(package_spec_some),
    ),
    api.override_step_data(
      'Load to-build packages from third_party/other.find package specs',
      api.file.glob_paths(['third_party/other/3pp/3pp.pb']),
    ),
    api.override_step_data(
      "Load to-build packages from third_party/other.load package specs."
      "read 'third_party/other/3pp/3pp.pb'",
      api.file.read_text(package_spec_other),
    ),
    api.override_step_data(
      'Load to-build packages from third_party/some.find package specs',
      api.file.glob_paths(['third_party/some/3pp/3pp.pb']),
    ),
    api.override_step_data(
      "Load to-build packages from third_party/some.load package specs."
      "read 'third_party/some/3pp/3pp.pb'",
      api.file.read_text(package_spec_some),
    ),
    api.post_process(
      post_process.MustRun, 'building p_apple/other', 'building p_apple/some'
    ),
    # trybots should never upload the 3pp packages
    api.post_process(
      post_process.DoesNotRun,
      'building p_apple/other.do upload',
      'building p_apple/some.do upload',
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'trybot_no_3pp_change',
    api.buildbucket.try_build(),
    generate_properties(),
    api.override_step_data(
      'Analyze',
      api.raw_io.stream_output_text(
        '\n'.join(['foo.cc', 'testing/buildbot/bar.json'])
      ),
    ),
    api.post_process(post_process.MustRun, 'No 3pp related changes'),
    api.post_process(post_process.DropExpectation),
  )
