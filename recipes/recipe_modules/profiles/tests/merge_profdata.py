# Copyright 2020 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process


DEPS = [
    'profiles',
    'recipe_engine/assertions',
    'recipe_engine/path',
]


def RunSteps(api):
  api.profiles.src_dir = api.path.cleanup_dir
  assert api.profiles.llvm_profdata_exec == api.profiles.src_dir.joinpath(
      'third_party', 'llvm-build', 'Release+Asserts', 'bin', 'llvm-profdata')
  new_path = '/some/other/path/llvm-profdata'
  api.profiles.llvm_profdata_exec = new_path
  assert api.profiles.llvm_profdata_exec == new_path
  api.profiles.merge_profdata('some_artifact', '.*', sparse=True)


def GenTests(api):

  yield api.test(
      'basic',
      api.post_process(post_process.MustRun,
                       'merge all profile files into a single .profdata'),
      api.post_process(post_process.DropExpectation),
  )
