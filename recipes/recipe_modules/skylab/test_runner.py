# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import urllib

from RECIPE_MODULES.build.attr_utils import attrib, attrs, enum
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb2


@attrs()
class TestRunner:
  """Wrapper of a Skylab test runner build for Chromium recipe's usage.

  Attributes listed here are for infra usages. All test results should be
  fetched from ResultDB.

  Attributes:
  * url - Link of the test runner build.
  * log_url - Link of the stainless(raw log) of the test. Unlike the log in
    RDB, this also includes system log for troubleshooting infra related
    issues.
  * status - Build status.
  * shard - Shard of this test run.
  """

  name = attrib(str, default='')
  url = attrib(str, default='')
  log_url = attrib(str, default='')
  status = attrib(
    enum(common_pb2.Status.values()), default=common_pb2.STATUS_UNSPECIFIED
  )
  shard = attrib(int, default=-1)
  log_dir = attrib(str, default='')

  @classmethod
  def create(cls, test, **kwargs):
    """Create TestRunner object.

    Args:
      test: A step.SkylabTest object.
    """
    status = kwargs.pop('status', None)
    if status and common_pb2.Status.DESCRIPTOR.values_by_name.get(status):
      kwargs['status'] = common_pb2.Status.Value(status)

    # Set shard to -1 for shards not managed by recipe_module/skylab
    shard = kwargs.pop('shard', None)
    if shard is None:
      shard = -1
    kwargs['shard'] = shard

    # The log url extracted from cros_test_platform is pointing to the
    # console of all system logs from the device, which might be too
    # confusing for browser developers. So transform it to our test
    # execution log.
    log_url = kwargs.pop('log_url', '')
    if log_url:
      kwargs['log_url'] = log_url

    return cls(**kwargs)
