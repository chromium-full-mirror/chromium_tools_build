# Copyright 2020 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.


from __future__ import annotations


DEPS = [
  'chromiumdash',
]

def RunSteps(api):
  api.chromiumdash.releases('Android', 'Beta', 1)
  api.chromiumdash.milestones(3, only_branched=True)
  api.chromiumdash.milestones(0, only_active=True)
  api.chromiumdash.fetch_commit_info('abcdefg')


def GenTests(api):
  yield api.test('basic')
