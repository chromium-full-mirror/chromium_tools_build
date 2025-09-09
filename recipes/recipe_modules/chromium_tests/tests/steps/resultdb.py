# Copyright 2020 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process

from RECIPE_MODULES.build.chromium_tests.steps import ResultDB

DEPS = [
    'chromium',
    'chromium_tests',
    'recipe_engine/assertions',
    'recipe_engine/buildbucket',
    "recipe_engine/resultdb",
    "recipe_engine/path",
    'recipe_engine/platform',
]


def RunSteps(api):
  cmd = ["echo", "foo"]

  rdb = ResultDB.create(enable=False)
  api.assertions.assertEqual(
      rdb.wrap(api, cmd),
      ['vpython3',
       api.chromium_tests.resource('reset_luci_context.py')] + cmd,
  )

  # test_location_base, test_id_prefix, coerce_negative_duration
  rdb = ResultDB.create(
      enable=True,
      coerce_negative_duration=False,
      exonerate_unexpected_pass=False)
  api.assertions.assertEqual(
      rdb.wrap(api, cmd),
      [
          'rdb', 'stream', '-inherit-sources', '-baseline-id', 'ci:Linux Tests',
          '--'
      ] + cmd,
  )
  api.assertions.assertEqual(
      rdb.wrap(api, cmd, test_location_base='//path'),
      [
          'rdb', 'stream', '-test-location-base', '//path', '-inherit-sources',
          '-baseline-id', 'ci:Linux Tests', '--'
      ] + cmd,
  )
  api.assertions.assertEqual(
      rdb.wrap(api, cmd, test_id_prefix='blink_web_tests'),
      [
          'rdb', 'stream', '-test-id-prefix', 'blink_web_tests',
          '-inherit-sources', '-baseline-id', 'ci:Linux Tests', '--'
      ] + cmd,
  )
  api.assertions.assertEqual(
      rdb.wrap(
          api,
          cmd,
          coerce_negative_duration=True,
          exonerate_unexpected_pass=False),
      [
          'rdb', 'stream', '-coerce-negative-duration', '-inherit-sources',
          '-baseline-id', 'ci:Linux Tests', '--'
      ] + cmd,
  )
  api.assertions.assertEqual(
      rdb.wrap(
          api,
          cmd,
          inv_extended_properties_dir='${ISOLATED_OUTDIR}/invocations'),
      [
          'rdb', 'stream', '-inherit-sources', '-baseline-id', 'ci:Linux Tests',
          '-inv-extended-properties-dir', '${ISOLATED_OUTDIR}/invocations', '--'
      ] + cmd,
  )

  # Checks module name and module scheme integration.
  api.buildbucket.build.input.experiments.append(
      'chromium_tests.resultdb_module')
  api.assertions.assertEqual(
      rdb.wrap(
          api,
          cmd,
          test_id_prefix='blink_web_tests',
          module_name='//chrome/build:foo_bar',
          module_scheme='gtest'),
      [
          'rdb', 'stream', '-module-name', '//chrome/build:foo_bar',
          '-module-scheme', 'gtest', '-inherit-sources', '-baseline-id',
          'ci:Linux Tests', '-previous-test-id-prefix', 'blink_web_tests', '--'
      ] + cmd,
  )

  api.assertions.assertEqual(
      rdb.wrap(
          api,
          cmd,
          result_format='json',
          test_id_as_test_location=True,
          test_id_prefix='blink_web_tests',
          module_name='//chrome/build:foo_bar',
          module_scheme='gtest',
      ),
      [
          'rdb', 'stream', '-module-name', '//chrome/build:foo_bar',
          '-module-scheme', 'gtest', '-inherit-sources', '-baseline-id',
          'ci:Linux Tests', '-previous-test-id-prefix', 'blink_web_tests', '--'
      ] + [
          'result_adapter', 'json', '-result-file',
          '${ISOLATED_OUTDIR}/output.json', '-scheme', 'gtest',
          '-artifact-directory', '${ISOLATED_OUTDIR}', '-test-location', '--'
      ] + cmd,
  )
  api.buildbucket.build.input.experiments.remove(
      'chromium_tests.resultdb_module')

  # step_name
  rdb = ResultDB.create(
      enable=True,
      coerce_negative_duration=False,
      exonerate_unexpected_pass=False)
  api.assertions.assertEqual(
      rdb.wrap(api, cmd, step_name='test1'),
      [
          'rdb', 'stream', '-tag', 'step_name:test1', '-inherit-sources',
          '-baseline-id', 'ci:Linux Tests', '--'
      ] + cmd,
  )

  # base_tags
  rdb = ResultDB.create(
      enable=True,
      coerce_negative_duration=False,
      base_tags=[('k1', 'v1')],
      exonerate_unexpected_pass=False)
  api.assertions.assertEqual(
      rdb.wrap(api, cmd, base_tags=[('k2', 'v2')]),
      [
          'rdb', 'stream', '-tag', 'k1:v1', '-tag', 'k2:v2', '-inherit-sources',
          '-baseline-id', 'ci:Linux Tests', '--'
      ] + cmd,
  )
  api.assertions.assertEqual(
      rdb.wrap(api, cmd, base_tags=[('k1', 'v1')]),
      [
          'rdb', 'stream', '-tag', 'k1:v1', '-inherit-sources', '-baseline-id',
          'ci:Linux Tests', '--'
      ] + cmd,
  )

  # base_variant
  rdb = ResultDB.create(
      enable=True,
      coerce_negative_duration=False,
      base_variant={"k1": "v1"},
      exonerate_unexpected_pass=False)
  api.assertions.assertEqual(
      rdb.wrap(api, cmd, base_variant={"k1": "v2"}),
      [
          'rdb', 'stream', '-var', 'k1:v2', '-inherit-sources', '-baseline-id',
          'ci:Linux Tests', '--'
      ] + cmd,
  )
  api.assertions.assertEqual(
      rdb.wrap(api, cmd, base_variant={"k2": "v2"}),
      [
          'rdb', 'stream', '-var', 'k1:v1', '-var', 'k2:v2', '-inherit-sources',
          '-baseline-id', 'ci:Linux Tests', '--'
      ] + cmd,
  )

  # result_format
  rdb = ResultDB.create(
      enable=True,
      coerce_negative_duration=False,
      exonerate_unexpected_pass=False)
  api.assertions.assertEqual(
      rdb.wrap(api, cmd, result_format='gtest'),
      [
          'rdb', 'stream', '-inherit-sources', '-baseline-id', 'ci:Linux Tests',
          '--'
      ] + [
          'result_adapter', 'gtest', '-result-file',
          '${ISOLATED_OUTDIR}/output.json', '-artifact-directory',
          '${ISOLATED_OUTDIR}', '--'
      ] + cmd,
  )
  api.assertions.assertEqual(
      rdb.wrap(api, cmd, result_format='gtest', test_id_as_test_location=True),
      [
          'rdb', 'stream', '-inherit-sources', '-baseline-id', 'ci:Linux Tests',
          '--'
      ] +
      # test_id_as_test_location should be ignore, as the format is not json.
      [
          'result_adapter',
          'gtest',
          '-result-file',
          '${ISOLATED_OUTDIR}/output.json',
          '-artifact-directory',
          '${ISOLATED_OUTDIR}',
          '--',
      ] + cmd,
  )
  api.assertions.assertEqual(
      rdb.wrap(api, cmd, result_format='json', test_id_as_test_location=True),
      [
          'rdb', 'stream', '-inherit-sources', '-baseline-id', 'ci:Linux Tests',
          '--'
      ] + [
          'result_adapter', 'json', '-result-file',
          '${ISOLATED_OUTDIR}/output.json', '-artifact-directory',
          '${ISOLATED_OUTDIR}', '-test-location', '--'
      ] + cmd,
  )
  api.assertions.assertEqual(
      rdb.unwrap(
          api,
          rdb.wrap(
              api, cmd, result_format='gtest', test_id_as_test_location=True)),
      cmd,
  )
  rdb.unwrap(api, ['no-wrapping'])


def GenTests(api):
  yield api.test(
      'test_results',
      api.chromium.ci_build(
          builder_group='chromium.linux',
          builder='Linux Tests',
      ),
      api.post_process(post_process.DropExpectation),
  )
