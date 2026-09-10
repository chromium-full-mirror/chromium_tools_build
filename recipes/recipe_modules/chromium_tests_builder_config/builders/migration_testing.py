# Copyright 2022 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from .. import builder_spec


def _migration_testing_spec(**kwargs):
  kwargs.setdefault('gclient_config', 'chromium')
  kwargs.setdefault('chromium_config', 'chromium')
  return builder_spec.BuilderSpec.create(**kwargs)


# These builders don't actually exist, the configs are created to provide a
# known set of configs for integration testing the migration tracking scripts
SPEC = {
  'foo': _migration_testing_spec(),
  'foo-x-tests': _migration_testing_spec(
    execution_mode=builder_spec.TEST,
    parent_buildername='foo',
  ),
  'foo-y-tests': _migration_testing_spec(
    execution_mode=builder_spec.TEST,
    parent_buildername='foo',
  ),
  'bar': _migration_testing_spec(),
  'bar-tests': _migration_testing_spec(
    execution_mode=builder_spec.TEST,
    parent_buildername='bar',
  ),
}
