# Copyright 2014 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations


DEPS = [
    'builder_group',
    'perf_dashboard',
    'recipe_engine/path',
    'recipe_engine/json',
    'recipe_engine/platform',
    'recipe_engine/properties',
    'recipe_engine/step',
]

# To run, pass these options into properties:
# bot_id="multivm-windows-release",
# buildername="multivm-windows-perf-be",
# builder_group="client.dart.fyi", buildnumber=75


def RunSteps(api):
  s1 = api.perf_dashboard.get_skeleton_point('sunspider/string-unpack-code/ref',
                                             33241, '18.5')
  s1['supplemental_columns'] = {'d_supplemental': '167808'}
  s1['error'] = '0.5'
  s1['units'] = 'ms'
  s2 = api.perf_dashboard.get_skeleton_point('sunspider/string-unpack-code',
                                             33241, '18.4')
  s2['supplemental_columns'] = {'d_supplemental': '167808'}
  s2['error'] = '0.4898'
  s2['units'] = 'ms'

  api.perf_dashboard.set_default_config()
  api.perf_dashboard.add_point([s1, s2])

  api.perf_dashboard.add_dashboard_link(
      api.step.active_result.presentation,
      'sunspider/string-unpack-code',
      33241,
      bot='bot_name',
  )


def GenTests(api):
  for platform in ('linux', 'win', 'mac'):
    for staging in (True, False):
      yield api.test(
          platform + ('-staging' if staging else ''),
          api.platform.name(platform),
          api.builder_group.for_current('client.dart.fyi'),
          api.properties(
              bot_id='multivm-windows-release',
              buildername='multivm-windows-perf-be',
              buildnumber=75,
              staging=staging),
      )
