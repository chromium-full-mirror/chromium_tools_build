# Copyright 2020 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

_clang_tidy_path = (
  'third_party',
  'llvm-build',
  'Release+Asserts',
  'bin',
  'clang-tidy',
)

from dataclasses import dataclass  # noqa: E402

from recipe_engine.recipe_api import RecipeScriptApi  # noqa: E402
from RECIPE_MODULES.build import (  # noqa: E402
  chromium,
  siso,
)
from RECIPE_MODULES.recipe_engine import (  # noqa: E402
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


from .api import TriciumClangTidyApi as API  # noqa: E402, F401
