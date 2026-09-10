# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import json

from recipe_engine.post_process import DoesNotRunRE, DropExpectation, MustRun

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import v8, v8_auto_roller
from RECIPE_MODULES.recipe_engine import (
  buildbucket,
  cipd,
  context,
  file,
  json as json_module,
  path,
  properties,
  raw_io,
  step,
)


@dataclass
class DEPS(RecipeScriptApi):
  buildbucket: buildbucket.API
  cipd: cipd.API
  context: context.API
  file: file.API
  json: json_module.API
  path: path.API
  properties: properties.API
  raw_io: raw_io.API
  step: step.API
  v8: v8.API
  v8_auto_roller: v8_auto_roller.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  cipd: cipd.TEST_API
  file: file.TEST_API
  path: path.TEST_API
  raw_io: raw_io.TEST_API


RETSAM = 'retsam'[::-1]

V8_DEPS = "deps = " + json.dumps(
  {
    "buildtools": {
      "url": "https://chromium.googlesource.com/chromium/buildtools.git@5fd66957f08bb752dca714a591c84587c9d70762",
      "condition": "requires_buildtools",
    },
    "tools/luci-go": {
      "dep_type": "cipd",
      "packages": [
        {
          "package": r"infra/tools/luci/isolate/${{platform}}",
          "version": "git_revision:8b15ba47cbaf07a56f93326e39f0c8e5069c19e9",
        },
      ],
    },
    "ninja": {
      "dep_type": "cipd",
      "packages": [
        {
          "package": r"infra/3pp/tools/ninja/${{platform}}",
          "version": "version:2@1.8.2.chromium.3",
        },
      ],
    },
    "mock-skip-chromium-roll": "https://chromium.googlesource.com/mock/skip-chromium-roll.git@1",
    "mock-tot-rolled": "https://chromium.googlesource.com/tot-rolled.git@d3f34f8dfaecc23202a6ef66957e83462d6c826d",
    "mock-tot-retsam-rolled": "https://chromium.googlesource.com/tot-retsam-rolled.git@d3f34f8dfaecc23202a6ef66957e83462d6c826d",
    "mock-tot-rolled-no-upate": "https://chromium.googlesource.com/tot-rolled-no-update.git@aaaaaaaa",
    "tools/clang": "https://chromium.googlesource.com/chromium/src/tools/clang@d3f34f8dfaecc23202a6ef66957e83462d6c826d",
    "tools/clang-reviewed": "https://example.com/tools/clang-reviewed.git@d3f34f8dfaecc23202a6ef66957e83462d6c826d",
    "mock-package-without-latest-ref": {
      "dep_type": "cipd",
      "packages": [
        {
          "package": "mock/package-without-latest-ref",
          "version": "7fd66957f08bb752dca714a591c84587c9d70764",
        },
      ],
    },
    "mock-package-latest": {
      "dep_type": "cipd",
      "packages": [
        {
          "package": "mock/package-latest",
          "version": "6fd66957f08bb752dca714a591c84587c9d70763",
        },
      ],
    },
  }
)

CHROMIUM_DEPS = "deps = " + json.dumps(
  {
    "src/buildtools": "https://chromium.googlesource.com/chromium/buildtools.git@5fd66957f08bb752dca714a591c84587c9d70762",
    "src/tools/luci-go": {
      "dep_type": "cipd",
      "packages": [
        {
          "package": r"infra/tools/luci/isolate/${{platform}}",
          "version": "git_revision:3d8f881462b1a93c7525499381fafc8a08691be7",
        },
      ],
    },
    "src/ninja": {
      "dep_type": "cipd",
      "packages": [
        {
          "package": r"infra/3pp/tools/ninja/${{platform}}",
          "version": "version:2@1.8.2.chromium.4",
        },
      ],
    },
    "src/chrome/android/orderfiles/arm": {
      "packages": [
        {
          "package": "chromium/chrome/android/orderfiles/arm",
          "version_file": "chrome/build/android-arm.orderfile.txt",
        },
      ],
      "dep_type": "cipd",
    },
    "src/mock-skip-chromium-roll": "https://chromium.googlesource.com/mock/skip-chromium-roll.git@2",
    "src/third_party/js_code_coverage": {
      "dep_type": "gcs",
      "bucket": "chromium-nodejs",
      "objects": [
        {
          "object_name": "js_code_coverage/d538975c93eefc7bafd599b50f867e90c1ef17f3",
          "sha256sum": "646bb00ced0a930b2eb1e4dbcfac18ebbb8f889bb80599e0254d9d6505427914",
          "size_bytes": 1469185,
          "generation": 1657780123604338,
        },
      ],
    },
  }
)


class DummyCLManager:
  def __init__(self, api: DEPS):
    self.bugs = 'dummy:123'
    self.api = api

  def upload_cl(self, *args, **kwargs):
    self.api.step('Upload CL', [str(args), str(kwargs)])
    return 'https://chromium-review.googlesource.com/c/dummy/+/123'

  def abandon_active_cls(self, *args, **kwargs):
    self.api.step('Abandon CLs', [str(args), str(kwargs)])
    return True


def RunSteps(api: DEPS):
  """This tests all roll handlers at once. The CL manager is mocked out to
  reduce the verbosity of the test.
  """

  autoroller_config = {
    'subject': 'dummy roll',
    'manual_roll_reviewers': ['piedone@punto.it'],
    'show_commit_log': True,
    'commit_msg_footers': ['Also-Do: This', 'Also-Do: That'],
    'dependency_version_sources': {
      'mock-skip-chromium-roll': 'tip_of_tree',
    },
  }

  update_result = api.v8_auto_roller.setup_target(
    'dummy',
    'https://chromium.googlesource.com/dumb/dumber',
  )
  source_dir = update_result.source_root.path

  clm = DummyCLManager(api)

  api.v8_auto_roller.regular_roll(autoroller_config, clm, source_dir)
  api.v8_auto_roller.cft_pin_roll(autoroller_config, clm, source_dir)
  api.v8_auto_roller.scripted_rolls(
    autoroller_config,
    clm,
    source_dir,
    [
      'puppeteer-core',
      'browser-protocol and CfT',
    ],
  )
  api.v8_auto_roller.test262_roll(autoroller_config, clm, source_dir)

  return api.v8_auto_roller.report_result()


def GenTests(api: TEST_DEPS):
  MOCK_DEP_REFS_MAIN = [
    ('3', 'mock-skip-chromium-roll (main)'),
    ('deadbeef', 'mock-tot-rolled (main)'),
    ('aaaaaaaa', 'mock-tot-rolled-no-upate (main)'),
    ('4', 'tools_clang (main)'),
    ('5', 'tools_clang-reviewed (main)'),
  ]

  def browser_protocol_roll_output():
    return api.path.exists(
      api.path.cleanup_dir.joinpath('roll_output.json')
    ) + api.override_step_data(
      'Scripted rolls.Update Browser Protocol & CfT deps.Read roll output',
      api.file.read_json({"old_revision": "123", "new_revision": "456"}),
    )

  yield (
    api.test('default')
    + api.override_step_data(
      'Update test262 import deps.Update Test262 status file.',
      api.raw_io.stream_output_text('range 1..3'),
    )
    + api.override_step_data(
      'Update chromium pin deps.gclient get chrome deps',
      api.raw_io.stream_output_text('123.0.4500.7', stream='stdout'),
    )
    + api.override_step_data(
      'Find updated deps.Read dummy/DEPS',
      api.file.read_text(V8_DEPS),
    )
    + api.override_step_data(
      'Find updated deps.Read src/DEPS',
      api.file.read_text(CHROMIUM_DEPS),
    )
    + api.override_step_data(
      'Find updated deps.cipd instances mock/package-without-latest-ref',
      api.cipd._resultify(
        {
          'instances': [
            {
              'pin': {
                'package': 'mock/package-without-latest-ref',
                'instance_id': api.cipd.make_resolved_version('no-latest'),
              },
              'registered_by': 'user:doe@developer.gserviceaccount.com',
              'registered_ts': 1987654321,
              'refs': None,
            }
          ]
        }
      ),
    )
    + api.override_step_data(
      f'Find updated deps.look up mock-tot-retsam-rolled ({RETSAM})',
      api.raw_io.stream_output_text(
        f'deadbeef\trefs/heads/{RETSAM}', stream='stdout'
      ),
    )
    + sum(
      [
        api.override_step_data(
          f'Find updated deps.look up {repo}',
          api.raw_io.stream_output_text(
            f'{rev}\trefs/heads/main', stream='stdout'
          ),
        )
        for rev, repo in MOCK_DEP_REFS_MAIN
      ],
      api.empty_test_data(),
    )
    + browser_protocol_roll_output()
  )

  yield (
    api.test('not pin update')
    + api.override_step_data(
      'Update test262 import deps.Update Test262 status file.',
      api.raw_io.stream_output_text('range 1..3'),
    )
    + api.override_step_data(
      'Update chromium pin deps.gclient get chrome deps',
      api.raw_io.stream_output_text('123', stream='stdout'),
    )
    + browser_protocol_roll_output()
    + api.post_process(DoesNotRunRE, r'^Update \w* deps\.gclient setdep .*')
    + api.post_process(DropExpectation)
  )

  yield (
    api.test('chrome version is None')
    + api.override_step_data(
      'Update test262 import deps.Update Test262 status file.',
      api.raw_io.stream_output_text('range 1..3'),
    )
    + browser_protocol_roll_output()
    + api.post_process(
      # `Update chromium pin deps.gclient get chrome deps` having no output
      # will result in no pic updates.
      DoesNotRunRE,
      r'^Update \w* deps\.gclient setdep .*',
    )
    + api.post_process(DropExpectation)
  )

  # TODO(liviurau): Bug: Following failures in regular roll handler should still
  # allow the other hadnlers to run."

  yield (
    api.test(
      'deps malformed',
      status='INFRA_FAILURE',
    )
    + api.override_step_data(
      'Find updated deps.Read dummy/DEPS',
      api.file.read_text('deps = {"mock-malformed": "https://example.com/"}'),
    )
    + api.override_step_data(
      'Find updated deps.Read src/DEPS',
      api.file.read_text('deps = {}'),
    )
    + api.expect_exception('AssertionError')
    + api.post_process(DropExpectation)
  )

  yield (
    api.test(
      'no tip of tree',
      status='INFRA_FAILURE',
    )
    + api.override_step_data(
      'Find updated deps.Read dummy/DEPS',
      api.file.read_text(
        'deps = {"icu": "https://example.com/repo.git@version"}'
      ),
    )
    + api.expect_exception('AssertionError')
    + api.post_process(DropExpectation)
  )
