# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine.post_process import (DoesNotRunRE, DropExpectation, MustRun)

DEPS = [
    'recipe_engine/buildbucket',
    'recipe_engine/cipd',
    'recipe_engine/context',
    'recipe_engine/file',
    'recipe_engine/json',
    'recipe_engine/path',
    'recipe_engine/properties',
    'recipe_engine/raw_io',
    'recipe_engine/step',
    'v8',
    'v8_auto_roller',
]

RETSAM = 'retsam'[::-1]

V8_DEPS = """v8: https://chromium.googlesource.com/v8/v8.git
v8/buildtools: https://chromium.googlesource.com/chromium/buildtools.git@5fd66957f08bb752dca714a591c84587c9d70762
src/tools/luci-go:infra/tools/luci/isolate/${platform}: https://chrome-infra-packages.appspot.com/infra/tools/luci/isolate/${platform}@git_revision:8b15ba47cbaf07a56f93326e39f0c8e5069c19e9
src/ninja:infra/3pp/tools/ninja/${platform}: https://chrome-infra-packages.appspot.com/infra/3pp/tools/ninja/${platform}@version:2@1.8.2.chromium.3
src/mock-skip-chromium-roll: mock/skip-chromium-roll.git@1
v8/mock-tot-rolled: https://chromium.googlesource.com/tot-rolled.git@d3f34f8dfaecc23202a6ef66957e83462d6c826d
v8/mock-tot-retsam-rolled: https://chromium.googlesource.com/tot-retsam-rolled.git@d3f34f8dfaecc23202a6ef66957e83462d6c826d
v8/tools/clang: https://chromium.googlesource.com/chromium/src/tools/clang@d3f34f8dfaecc23202a6ef66957e83462d6c826d
v8/tools/clang-reviewed: https://example.com/tools/clang-reviewed.git@d3f34f8dfaecc23202a6ef66957e83462d6c826d
v8/mock-set-dep-failing: mock/set-dep-failing.git@1
v8/mock-package-without-latest-ref:mock/package-without-latest-ref: https://chrome-infra-packages.appspot.com/mock/package-without-latest-ref@7fd66957f08bb752dca714a591c84587c9d70764
v8/mock-package-latest:mock/package-latest: https://chrome-infra-packages.appspot.com/mock/package-latest@6fd66957f08bb752dca714a591c84587c9d70763"""

CHROMIUM_DEPS = """src: https://chromium.googlesource.com/chromium/src.git
src/buildtools: https://chromium.googlesource.com/chromium/buildtools.git@5fd66957f08bb752dca714a591c84587c9d70762
src/tools/luci-go:infra/tools/luci/isolate/${platform}: https://chrome-infra-packages.appspot.com/infra/tools/luci/isolate/${platform}@git_revision:3d8f881462b1a93c7525499381fafc8a08691be7
src/ninja:infra/3pp/tools/ninja/${platform}: https://chrome-infra-packages.appspot.com/infra/3pp/tools/ninja/${platform}@version:2@1.8.2.chromium.4
src/mock-set-dep-failing: mock/set-dep-failing.git@2
src/mock-skip-chromium-roll: mock/skip-chromium-roll.git@2"""


class DummyCLManager:

  def __init__(self, api):
    self.bugs = 'dummy:123'
    self.api = api

  def upload_cl(self, *args, **kwargs):
    self.api.step('Upload CL', [str(args), str(kwargs)])
    return 'https://chromium-review.googlesource.com/c/dummy/+/123'

  def abandon_active_cls(self, *args, **kwargs):
    self.api.step('Abandon CLs', [str(args), str(kwargs)])


def RunSteps(api):
  """This tests all roll handlers at once. The CL manager is mocked out to
  reduce the verbosity of the test.
  """

  autoroller_config = {
      'subject': 'dummy roll',
      'reviewers': ['piedone@punto.it'],
      'show_commit_log': True,
      'commit_msg_footers': ['Also-Do: This', 'Also-Do: That'],
      'dependency_version_sources': {
          'mock-skip-chromium-roll': 'tip_of_tree',
      },
  }

  api.v8_auto_roller.setup_target(
      'dummy',
      'https://chromium.googlesource.com/dumb/dumber',
  )

  clm = DummyCLManager(api)

  api.v8_auto_roller.regular_roll(autoroller_config, clm)
  api.v8_auto_roller.cft_pin_roll(autoroller_config, clm)
  api.v8_auto_roller.scripted_rolls(autoroller_config, clm, [
      'puppeteer-core',
      'browser-protocol',
  ])
  api.v8_auto_roller.test262_roll(autoroller_config, clm)

  return api.v8_auto_roller.report_result()


def GenTests(api):
  MOCK_DEP_REFS_MAIN = [
    ('3', 'mock-skip-chromium-roll (main)'),
    ('deadbeef', 'mock-tot-rolled (main)'),
    ('4', 'tools_clang (main)'),
    ('5', 'tools_clang-reviewed (main)'),
  ]

  yield api.test('default') + api.override_step_data(
      'Update test262 import deps.Update Test262 status file.',
      api.raw_io.stream_output_text('range 1..3'),
  ) + api.override_step_data(
      'Update chromium pin deps.gclient get chrome deps',
      api.raw_io.stream_output_text('123.0.4500.7', stream='stdout'),
  ) + api.override_step_data(
      'Find updated deps.gclient get dummy deps',
      api.raw_io.stream_output_text(V8_DEPS, stream='stdout'),
  ) + api.override_step_data(
      'Find updated deps.gclient get src deps',
      api.raw_io.stream_output_text(CHROMIUM_DEPS, stream='stdout'),
  ) + api.override_step_data(
      'Find updated deps.cipd instances mock/package-without-latest-ref',
      api.cipd._resultify({
          'instances': [{
              'pin': {
                  'package': 'mock/package-without-latest-ref',
                  'instance_id': api.cipd.make_resolved_version('no-latest'),
              },
              'registered_by': 'user:doe@developer.gserviceaccount.com',
              'registered_ts': 1987654321,
              'refs': None,
          }]
      })) + api.override_step_data(
          f'Find updated deps.look up mock-tot-retsam-rolled ({RETSAM})',
          api.raw_io.stream_output_text(
              f'deadbeef\trefs/heads/{RETSAM}', stream='stdout'),
      ) + api.override_step_data(
          'Update trusted deps.gclient setdep mock-set-dep-failing',
          retcode=1,
      ) + sum([
        api.override_step_data(
            f'Find updated deps.look up {repo}',
            api.raw_io.stream_output_text(
                f'{rev}\trefs/heads/main', stream='stdout'),
      ) for rev, repo in MOCK_DEP_REFS_MAIN], api.empty_test_data())

  yield api.test('not pin update') + api.override_step_data(
      'Update test262 import deps.Update Test262 status file.',
      api.raw_io.stream_output_text('range 1..3'),
  ) + api.override_step_data(
      'Update chromium pin deps.gclient get chrome deps',
      api.raw_io.stream_output_text('123', stream='stdout'),
  ) + api.post_process(DoesNotRunRE, r'^Update \w* deps\.gclient setdep .*'
                      ) + api.post_process(DropExpectation)

  yield api.test('chrome version is None') + api.override_step_data(
      'Update test262 import deps.Update Test262 status file.',
      api.raw_io.stream_output_text('range 1..3'),
  ) + api.post_process(
      # `Update chromium pin deps.gclient get chrome deps` having no output
      # will result in no pic updates.
      DoesNotRunRE,
      r'^Update \w* deps\.gclient setdep .*') + api.post_process(
          DropExpectation)

  # TODO(liviurau): Bug: Following failures in regular roll handler should still
  # allow the other hadnlers to run."

  yield api.test(
      'deps malformed',
      status='FAILURE',
  ) + api.override_step_data(
      'Find updated deps.gclient get dummy deps',
      api.raw_io.stream_output_text(
          'src/mock-malformed:  https://example.com/', stream='stdout'),
  ) + api.override_step_data(
      'Find updated deps.gclient get src deps',
      api.raw_io.stream_output_text('', stream='stdout'),
  ) + api.expect_exception('Exception') + api.post_process(DropExpectation)

  yield api.test(
      'deps changed location',
      status='FAILURE',
  ) + api.override_step_data(
      'Find updated deps.gclient get dummy deps',
      api.raw_io.stream_output_text(
          '8/mock-changed-location: foo/changed-location@1', stream='stdout'),
  ) + api.override_step_data(
      'Find updated deps.gclient get src deps',
      api.raw_io.stream_output_text(
          'src/mock-changed-location: bar/changed-location@2', stream='stdout'),
  ) + api.post_process(
      MustRun,
      "Find updated deps.dep mock-changed-location has changed repo from foo/changed-location to bar/changed-location",
  ) + api.post_process(DropExpectation)

  yield api.test(
      'no tip of tree',
      status='INFRA_FAILURE',
  ) + api.override_step_data(
      'Find updated deps.gclient get dummy deps',
      api.raw_io.stream_output_text(
        'v8/icu: https://example.com/repo.git@version', stream='stdout'),
  ) + api.expect_exception('AssertionError') + api.post_process(DropExpectation)
