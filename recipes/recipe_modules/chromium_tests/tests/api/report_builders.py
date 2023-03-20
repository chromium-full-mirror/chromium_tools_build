# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process

from RECIPE_MODULES.build import chromium_tests_builder_config as ctbc

DEPS = [
    'chromium',
    'chromium_tests',
    'chromium_tests_builder_config',
]

BUILDERS = ctbc.BuilderDatabase.create({
    'fake-group': {
        'fake-builder': ctbc.BuilderSpec.create(),
    },
})

TRYBOTS = ctbc.TryDatabase.create({
    'fake-try-group': {
        'fake-try-builder':
            ctbc.TrySpec.create_for_single_mirror(
                builder_group='fake-group',
                buildername='fake-builder',
            ),
    },
})


def RunSteps(api):
  _, builder_config = api.chromium_tests_builder_config.lookup_builder(
      builder_db=BUILDERS, try_db=TRYBOTS)
  api.chromium_tests.report_builders(
      builder_config, report_mirroring_builders=True)


def check_link(check, steps, link_name, expected_link):
  check(steps['report builders'].links[link_name] == expected_link)


def GenTests(api):
  for bucket, link_bucket in (
      ('try', 'ci'),
      ('try-beta', 'ci-beta'),
      ('try-stable', 'ci-stable'),
  ):
    expected_link = (
        'https://ci.chromium.org/p/chromium/builders/%s/fake-builder' %
        link_bucket)
    yield api.test(
        bucket,
        api.chromium.try_build(
            bucket=bucket,
            builder_group='fake-try-group',
            builder='fake-try-builder',
        ),
        api.post_check(check_link, 'fake-builder', expected_link),
        api.post_process(post_process.DropExpectation),
    )

  ctbc_api = api.chromium_tests_builder_config

  yield api.test(
      'mirroring-try-builder',
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-try-builder',
      ),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_try_builder().with_mirrored_builder(
              builder_group='fake-group',
              builder='fake-builder',
          ).assemble()),
      api.post_check(
          check_link,
          'fake-builder',
          'https://ci.chromium.org/p/chromium/builders/ci/fake-builder',
      ),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'standalone-try-builder',
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-try-builder',
      ),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_try_builder().with_mirrored_builder(
              builder_group='fake-try-group',
              bucket='try',
              builder='fake-try-builder',
          ).assemble()),
      api.post_check(
          check_link,
          'fake-try-builder',
          'https://ci.chromium.org/p/chromium/builders/try/fake-try-builder',
      ),
      api.post_process(post_process.DropExpectation),
  )

  ctbc_props = ctbc_api.properties_assembler_for_ci_builder(
      builder_group='fake-group',
      builder='fake-builder',
  ).assemble()
  mirroring = ctbc_props.builder_config.mirroring_builder_group_and_names.add()
  mirroring.group = 'fake-try-group'
  mirroring.builder = 'fake-try-builder'

  yield api.test(
      'ci-builder-with-mirroring-try-builder',
      api.chromium.ci_build(
          builder_group='fake-group',
          builder='fake-builder',
      ),
      ctbc_api.properties(ctbc_props),
      api.post_check(post_process.PropertyEquals, 'mirrored_builders',
                     ['fake-try-group:fake-try-builder']),
      api.post_process(post_process.DropExpectation),
  )
