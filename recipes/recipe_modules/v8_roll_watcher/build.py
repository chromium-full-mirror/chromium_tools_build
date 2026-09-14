# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from PB.go.chromium.org.luci.buildbucket.proto.common import (
  FAILURE,
  INFRA_FAILURE,
)


class BBBuild:
  def __init__(self, build):
    self.build = build

  def __getattr__(self, name):
    return getattr(self.build, name)

  def is_cq_build(self):
    return any(
      tag.key == 'cq_experimental' and tag.value == 'false' for tag in self.tags
    )

  def has_failed(self):
    return self.status in [FAILURE, INFRA_FAILURE]
