# Copyright 2024 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process
from recipe_engine.recipe_api import Property

from PB.recipe_modules.build.chromium_utr.request import Request

DEPS = [
    'chromium_utr',
    'recipe_engine/properties',
]

PROPERTIES = Request


def RunSteps(api, request):
  api.chromium_utr.create_rerun_result([('y', request.rerun_options)], '',
                                       request.output_properties_file)


def GenTests(api):

  def boilerplate_properties(
      preserve_gn_args=False,
      bypass_gclient=True,
      bypass_gn_args=True,
      output_properties_file='checkout/output_properties.json',
      bypass_branch_check=False,
      skip_instrumentation=False,
  ):
    return api.properties(
        output_properties_file=output_properties_file,
        rerun_options=Request.RerunOptions(
            bypass_gclient=bypass_gclient,
            bypass_gn_args=bypass_gn_args,
            preserve_gn_args=preserve_gn_args,
            bypass_branch_check=bypass_branch_check,
            skip_instrumentation=skip_instrumentation,
        ),
    )

  def boilerplate(**kwargs,):
    return sum([
        boilerplate_properties(**kwargs),
    ], api.empty_test_data())

  yield api.test(
      'basic',
      boilerplate(
          preserve_gn_args=False,
          bypass_gclient=False,
      ),
      api.post_process(post_process.DropExpectation),
  )
