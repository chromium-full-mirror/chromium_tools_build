# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from google.protobuf import timestamp_pb2

from recipe_engine import post_process
from recipe_engine.recipe_api import Property

from RECIPE_MODULES.build.chromium_utr.instruction import get_utr_instruction


def RunSteps(api):
  get_utr_instruction('run', 'project', 'bucket', 'builder', ['test 1'],
                      ['filter'])
  get_utr_instruction('run', 'project', 'bucket', 'builder', [], ['filter'])


def GenTests(api):
  yield api.test('basic', api.post_process(post_process.DropExpectation))
