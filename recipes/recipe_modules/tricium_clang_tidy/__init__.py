# Copyright 2020 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations


DEPS = [
    'chromium',
    'recipe_engine/buildbucket',
    'recipe_engine/context',
    'recipe_engine/file',
    'recipe_engine/findings',
    'recipe_engine/path',
    'recipe_engine/platform',
    'recipe_engine/step',
    'siso',
]

_clang_tidy_path = ('third_party', 'llvm-build', 'Release+Asserts', 'bin',
                    'clang-tidy')

from .api import TriciumClangTidyApi as API
