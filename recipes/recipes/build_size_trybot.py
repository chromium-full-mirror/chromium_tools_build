# Copyright 2024 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process

DEPS = [
    'binary_size',
    'recipe_engine/properties',
    'recipe_engine/step',
]


def RunSteps(api):

  def create_diffs(*args):
    api.step(name='Generate diffs', cmd=['echo Hello World'])

  def compile_size_analysis(*args):
    'echo Hello World Part II'

  return api.binary_size.compare_size(
      chromium_config='chromium',
      chromium_apply_configs=['mb'],
      gclient_config='chromium',
      binary_size_footer='Build-Size',
      diff_func=create_diffs,
      analysis_func=compile_size_analysis,
      analysis_warning_statuses={})


def GenTests(api):
  yield api.test('basic', api.binary_size.build(),
                 api.post_process(post_process.DropExpectation))
