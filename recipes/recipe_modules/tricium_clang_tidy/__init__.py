# Copyright 2020 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

DEPS = [
    'chromium',
    'recipe_engine/context',
    'recipe_engine/file',
    'recipe_engine/path',
    'recipe_engine/platform',
    'recipe_engine/step',
    'recipe_engine/tricium',
    'reclient',
]

_clang_tidy_path = ('third_party', 'llvm-build', 'Release+Asserts', 'bin',
                    'clang-tidy')
