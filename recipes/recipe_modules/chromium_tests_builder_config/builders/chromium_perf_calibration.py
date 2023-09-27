# Copyright 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from . import chromium_perf
from .. import builder_spec

SPEC = {}


def _AddCalibrationTestSpec(name, platform, target_bits):
  """Defines the bot spec for calibration, which will build and test. The reason
  is that calibration is triggered daily and is not triggered by any specific
  builder. As we don't have any compiled version available to checkout, we will
  build it again. Calibration targets on the Stable branch so Goma should save
  lots of compiling effort.
  This function means to be a union of BuildSpec and TestSpec from
  chromium_perf.py. More arguments and logic will be added when more builders
  are added.
  """
  kwargs = chromium_perf._common_kwargs(
      execution_mode=builder_spec.COMPILE_AND_TEST,
      config_name='chromium_perf',
      platform=platform,
      target_bits=target_bits,
  )

  kwargs['bisect_archive_build'] = False

  # crbug/1257650: try removing this tag to allow download wpr
  # kwargs['gclient_apply_config'].append('chromium_skip_wpr_archives_download')

  spec = builder_spec.BuilderSpec.create(**kwargs)

  SPEC[name] = spec


_AddCalibrationTestSpec('linux-perf-calibration', 'linux', 64)
_AddCalibrationTestSpec('android-pixel2-perf-calibration', 'android', 64)
