# Copyright 2014 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from . import chromium_perf

SPEC = {}


def _AddBuildSpec(
  name, platform, config_name='chromium_perf', target_bits=64, **kwargs
):
  SPEC[name] = chromium_perf.BuildSpec(
    config_name, platform, target_bits, **kwargs
  )


def _AddIsolatedTestSpec(
  name,
  platform,
  parent_buildername=None,
  parent_builder_group=None,
  target_bits=64,
  config_name='chromium_perf',
  **kwargs,
):
  spec = chromium_perf.TestSpec(
    config_name,
    platform,
    target_bits,
    parent_buildername=parent_buildername,
    **kwargs,
  )
  if parent_builder_group:
    spec = spec.evolve(parent_builder_group=parent_builder_group)
  SPEC[name] = spec


_AddBuildSpec('android-cfi-builder-perf-fyi', 'android', target_bits=32)

_AddBuildSpec('android_arm64-cfi-builder-perf-fyi', 'android')

_AddBuildSpec(
  'chromeos-kevin-builder-perf-fyi',
  'chromeos',
  target_bits=32,
  target_arch='arm',
  cros_boards='kevin',
  extra_gclient_apply_config=['arm'],
)

_AddBuildSpec(
  'fuchsia-builder-perf-arm64',
  'fuchsia',
  target_arch='arm',
  extra_gclient_apply_config=[
    'fuchsia_arm64',
    'fuchsia_sd_images',
  ],
)

_AddBuildSpec('win-arm64-builder-perf', 'win', target_arch='arm')

_AddBuildSpec(
  'linux-chromium-builder-perf',
  'linux',
  chromium_config='chromium_public_perf',
  gclient_config='chromium',
)

_AddIsolatedTestSpec(
  'linux-chromium-r350-perf',
  'linux',
  'linux-chromium-builder-perf',
  chromium_config='chromium_public_perf',
  gclient_config='chromium',
)

_AddIsolatedTestSpec(
  'fuchsia-perf-nsn',
  'fuchsia',
  target_arch='arm',
  parent_buildername='fuchsia-builder-perf-arm64',
  parent_builder_group='chromium.perf.fyi',
)

_AddIsolatedTestSpec(
  'fuchsia-perf-shk',
  'fuchsia',
  target_arch='arm',
  parent_buildername='fuchsia-builder-perf-arm64',
  parent_builder_group='chromium.perf.fyi',
)

_AddIsolatedTestSpec(
  'linux-perf-fyi',
  'linux',
  parent_buildername='linux-builder-perf',
  parent_builder_group='chromium.perf',
)

_AddIsolatedTestSpec(
  'win-10_laptop_low_end-perf_HP-Candidate',
  'win',
  parent_buildername='win64-builder-perf',
  parent_builder_group='chromium.perf',
)

_AddIsolatedTestSpec(
  'win-arm64-snapdragon-plus-perf',
  'win',
  target_arch='arm',
  parent_buildername='win-arm64-builder-perf',
  parent_builder_group='chromium.perf.fyi',
)

_AddIsolatedTestSpec(
  'chromeos-kevin-perf-fyi',
  'chromeos',
  parent_buildername='chromeos-kevin-builder-perf-fyi',
  parent_builder_group='chromium.perf.fyi',
  target_bits=32,
  target_arch='arm',
  cros_boards='kevin',
)
