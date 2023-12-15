# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process
from recipe_engine.config import List
from recipe_engine.recipe_api import Property

DEPS = [
    'gn',
    'recipe_engine/assertions',
    'recipe_engine/path',
    'recipe_engine/properties',
    'recipe_engine/raw_io',
]

PROPERTIES = {
    'label_or_pattern': Property(kind=str),
    'what_to_show': Property(kind=str),
}


def RunSteps(api, label_or_pattern, what_to_show):
  refs = api.gn.desc(api.path['cache'] / 'builder' / 'src' / 'out' / 'Release',
                     label_or_pattern, what_to_show)
  api.assertions.assertEqual(refs, ['file1', 'file2'])


def GenTests(api):
  yield api.test(
      'basic',
      api.properties(
          label_or_pattern='target1',
          what_to_show='runtime_deps',
      ),
      api.override_step_data(
          'Run gn desc', stdout=api.raw_io.output_text('file1\nfile2')),
      api.post_process(post_process.DropExpectation),
  )
