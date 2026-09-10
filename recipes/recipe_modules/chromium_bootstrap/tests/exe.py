# Copyright 2022 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process
from recipe_engine.recipe_api import Property

from PB.go.chromium.org.luci.swarming.proto.api_v2 import (
  swarming as swarming_pb,
)
from PB.infra.chromium import chromium_bootstrap

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import chromium_bootstrap as chromium_bootstrap_module
from RECIPE_MODULES.recipe_engine import assertions, properties


@dataclass
class DEPS(RecipeScriptApi):
  assertions: assertions.API
  chromium_bootstrap: chromium_bootstrap_module.API
  properties: properties.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  chromium_bootstrap: chromium_bootstrap_module.TEST_API
  properties: properties.TEST_API


PROPERTIES = {
  'expected_exe_cas_hash': Property(kind=str),
  'expected_exe_cas_size_bytes': Property(kind=int),
}


def RunSteps(api: DEPS, expected_exe_cas_hash, expected_exe_cas_size_bytes):
  exe = api.chromium_bootstrap.exe
  api.assertions.assertEqual(exe.cas.digest.hash, expected_exe_cas_hash)
  api.assertions.assertEqual(
    exe.cas.digest.size_bytes, expected_exe_cas_size_bytes
  )


def GenTests(api: TEST_DEPS):
  yield api.test(
    'basic',
    api.chromium_bootstrap.properties(
      exe=chromium_bootstrap.BootstrappedExe(
        cas=swarming_pb.CASReference(
          cas_instance=('projects/chromium-swarm/instances/default_instance'),
          digest=swarming_pb.Digest(
            hash='examplehash',
            size_bytes=71,
          ),
        ),
        cmd='luciexe',
      )
    ),
    api.properties(
      expected_exe_cas_hash='examplehash',
      expected_exe_cas_size_bytes=71,
    ),
    api.post_process(post_process.DropExpectation),
  )
