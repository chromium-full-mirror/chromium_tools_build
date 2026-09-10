# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import ssci
from RECIPE_MODULES.recipe_engine import (
  assertions,
  buildbucket,
  json,
  path,
  properties,
)


@dataclass
class DEPS(RecipeScriptApi):
  assertions: assertions.API
  buildbucket: buildbucket.API
  json: json.API
  path: path.API
  properties: properties.API
  ssci: ssci.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  buildbucket: buildbucket.TEST_API
  json: json.TEST_API


def RunSteps(api: DEPS):
  source_dir = api.path.cache_dir / 'builder/src'

  api.ssci.run(
    src_dir=api.path.abspath(source_dir),
    build_dir='out/Release',
    sbom_bucket='my-bucket',
    sbom_folder='os/version',
    sbom_filename_postfix='Stable',
    targets=["//example:example"],
  )

  # Mock Partybot output file existing after the first call to `run` for the
  # build.
  partybot_dir = api.path.cleanup_dir / 'partybot'
  out_file = 'third_party_luci-8945511751514863184.json'
  api.path.mock_add_file(partybot_dir / out_file)

  res = api.ssci.run(
    src_dir=api.path.abspath(source_dir),
    build_dir='out/Release',
    sbom_bucket='my-bucket',
    sbom_folder='os/version',
    sbom_filename_postfix='Canary',
    targets=["Example.apk"],
  )

  api.assertions.assertEqual(
    res,
    {
      'ExampleCanary.apk': {
        'digest': 'testhash',
        'file': 'gs://my-bucket/os/version/luci-8945511751514863184/ExampleCanary.apk.spdx.json',
        'filename': 'ExampleCanary.apk',
        'sbom_name': 'ExampleCanary.apk.spdx.json',
        'sbom_path': '[CLEANUP]/tmp_tmp_3/spdx-out.json',
        'target': 'Example.apk',
      }
    },
  )


def GenTests(api: TEST_DEPS):
  yield api.test(
    'depbot-output-empty',
    api.buildbucket.ci_build(
      project='myproject', bucket='mybucket', builder='mybuilder'
    ),
    api.override_step_data(
      'SSCI collection.run depbot', api.json.output(name="summary", data={})
    ),
    api.post_process(
      post_process.StepException, "SSCI collection.SBOM's generated"
    ),
    api.post_process(
      post_process.MustRunRE,
      r'.+\.run partybot to collect 3P deps',
      at_least=1,
      at_most=1,
    ),
    api.post_process(
      post_process.MustRunRE,
      r'.+\.using existing Partybot results',
      at_least=1,
    ),
    api.post_process(post_process.DropExpectation),
  )
