# Copyright 2020 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

_clang_tidy_path = ('third_party', 'llvm-build', 'Release+Asserts', 'bin',
                    'clang-tidy')

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from RECIPE_MODULES.build import (
    chromium,
    siso,
)
from RECIPE_MODULES.recipe_engine import (
    buildbucket,
    context,
    file,
    findings,
    path,
    platform,
    step,
)


@dataclass
class DEPS(RecipeScriptApi):
  chromium: chromium.API
  buildbucket: buildbucket.API
  context: context.API
  file: file.API
  findings: findings.API
  path: path.API
  platform: platform.API
  step: step.API
  siso: siso.API


from .api import TriciumClangTidyApi as API
