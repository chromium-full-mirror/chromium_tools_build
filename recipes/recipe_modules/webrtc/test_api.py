# Copyright 2014 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

# Exposes the builder and recipe configurations to GenTests in recipes.

from __future__ import annotations

from recipe_engine import post_process
from recipe_engine import recipe_test_api

from PB.go.chromium.org.luci.resultdb.proto.v1 \
    import test_result as test_result_pb2

_BUILDER_PREFIX = {
    'client.webrtc': 'luci_webrtc_ci',
    'client.webrtc.perf': 'luci_webrtc_perf',
    'internal.client.webrtc': 'luci_webrtc_internal_ci',
    'tryserver.webrtc': 'luci_webrtc_try',
}


def _sanitize_builder_name(name):
  safe_with_spaces = ''.join(c if c.isalnum() else ' ' for c in name.lower())
  return '_'.join(safe_with_spaces.split())


def _run_tests(builder_id):
  # Decides which builder run tests based on the builder's name. It represents
  # the builders that have a 'test_suites' section in waterfalls.pyl.
  buildername = builder_id.builder.lower()
  return not any(b in buildername for b in ('builder', 'fuzzer', 'compile'))


class WebRTCTestApi(recipe_test_api.RecipeTestApi):

  def example_binary_sizes(self):
    return self.m.json.output({'some_binary': 123456})

  def generate_builder(self,
                       builders_db,
                       builder_id,
                       failing_test=False,
                       fail_compile=False,
                       suffix='',
                       fail_android_archive=False,
                       fail_iwyu=False,
                       is_experimental=False,
                       gn_analyze_output=None,
                       tags=None):
    builder_config = builders_db[builder_id]
    builder_name = _sanitize_builder_name(builder_id.builder)
    project = 'webrtc-internal' if 'internal' in builder_id.group else 'webrtc'
    test_target = 'dummy_test'
    status = ('FAILURE' if fail_compile or failing_test or
              fail_android_archive or fail_iwyu else 'SUCCESS')

    chromium_kwargs = builder_config.chromium_config_kwargs
    test = self.test(
        '%s_%s%s' % (_BUILDER_PREFIX[builder_id.group], builder_name, suffix),
        self.m.builder_group.for_current(builder_id.group),
        self.m.properties(
            buildername=builder_id.builder,
            bot_id='bot_id',
            BUILD_CONFIG=chromium_kwargs['BUILD_CONFIG']),
        self.m.platform(builder_config.simulation_platform or 'linux',
                        chromium_kwargs.get('TARGET_BITS', 64)),
        self.m.runtime(is_experimental=is_experimental),
        self.m.path.files_exist(self.m.path.checkout_dir.joinpath('foo.cc')),
        status=status,
    )

    if 'mac_toolchain' in builder_config.chromium_apply_config:
      test += self.m.properties(xcode_build_version='dummy_xcode')
    test += self.m.properties(
        revision='a' * 40,
        got_revision='a' * 40,
        got_revision_cp='refs/heads/main@{#1337}')
    if builder_config.parent_buildername:
      parent_buildername = builder_config.parent_buildername
      parent_rev = 'a' * 40
      test += self.m.properties(parent_buildername=parent_buildername)
      test += self.m.properties(parent_got_revision=parent_rev)
      test += self.m.properties(
          swarming_command_lines={test_target: ['./dummy_cmd']})

    test += self.m.siso.properties()

    if fail_compile:
      test += self.step_data('compile', retcode=1)
      test += self.expect_status('FAILURE')
      test += self.post_process(post_process.DropExpectation)

    if failing_test:
      step_name = '%s on Ubuntu-16.04' % test_target
      test += self.override_step_data(
          step_name,
          self.m.chromium_swarming.canned_summary_output(
              self.m.test_utils.canned_gtest_output(False),
              failure=True,
              invocations=['invocations/0']))
      test += self.m.resultdb.query(
          {
              'invocations/0':
                  self.m.resultdb.Invocation(test_results=[
                      test_result_pb2.TestResult(
                          test_id='Test.One', status=test_result_pb2.FAIL)
                  ]),
          },
          step_name='collect tasks.%s results' % test_target,
      )

    if fail_android_archive:
      step_test_data = recipe_test_api.StepTestData()
      step_test_data.retcode = 1
      test += self.override_step_data('build android archive', step_test_data)

    if fail_iwyu:
      step_test_data = recipe_test_api.StepTestData()
      step_test_data.retcode = 1
      test += self.override_step_data('apply_include_cleaner.py foo.cc',
                                      step_test_data)

    git_repo = 'https://webrtc.googlesource.com/src'
    nb_phase = 3 if 'more_configs' in builder_name else 1
    if 'try' in builder_id.group:
      if 'fuzzer' not in builder_name:
        # The "all" and "default" rules for gn are different:
        # https://gn.googlesource.com/gn/+/main/docs/reference.md#the-all-and-default-rules
        # To work around issues with //base dependencies on Android,
        # use "default" on Android and "all" otherwise.
        if 'android' in builder_name:
          compile_targets = ['default']
        else:
          compile_targets = ['all']
        for i in range(nb_phase):
          run_tests = _run_tests(builder_id) and (nb_phase == 1 or i == 2)
          json_output = self.m.json.output(
              gn_analyze_output or {
                  'compile_targets': compile_targets,
                  'status': 'Found dependency',
                  'test_targets': [test_target] if run_tests else [],
              })
          suffix = '' if i == 0 else ' (%d)' % (i + 1)
          test += self.override_step_data('analyze' + suffix, json_output)

      test += self.m.buildbucket.try_build(
          project=project,
          bucket='try',
          builder=builder_id.builder,
          git_repo=git_repo,
          revision='a' * 40,
          tags=tags)
    else:
      test += self.m.buildbucket.ci_build(
          project=project,
          bucket='perf' if 'perf' in builder_id.group else 'ci',
          builder=builder_id.builder,
          git_repo=git_repo,
          revision='a' * 40,
          tags=tags)
    test += self.m.properties(buildnumber=1337)

    if _run_tests(builder_id):
      target_os = 'Ubuntu-16.04'
      if builder_config.simulation_platform == 'mac':
        target_os = 'Mac-10.13'
      elif builder_config.simulation_platform == 'win':
        target_os = 'Windows-10'
      test += self.m.chromium_tests.read_targets_spec(
          builder_id.group,
          contents={
              builder_id.builder: {
                  'gtest_tests': [{
                      'test': test_target,
                      'swarming': {
                          'dimensions': {
                              'os': target_os,
                          },
                      }
                  }]
              }
          })

    return test
