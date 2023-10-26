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

CXX17_FAILURE_SUFFIX = ('Potentially relevant: clang-tidy currently runs in '
                        'c++17 mode. Use of c++20-only features is '
                        'currently discouraged. See crbug.com/1406869.')
