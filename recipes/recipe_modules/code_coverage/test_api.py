# Copyright 2019 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import recipe_test_api

from PB.recipe_modules.build.code_coverage import properties


class CodeCoverageTestApi(recipe_test_api.RecipeTestApi):
  def __call__(
    self,
    coverage_gs_bucket=None,
    use_clang_coverage=False,
    use_java_coverage=False,
    use_javascript_coverage=False,
    coverage_test_types=None,
    coverage_exclude_sources=None,
    coverage_reference_commit=None,
    export_coverage_to_zoss=False,
    generate_blame_list=False,
  ):
    return self.m.properties(
      **{
        '$build/code_coverage': properties.InputProperties(
          coverage_gs_bucket=coverage_gs_bucket,
          use_clang_coverage=use_clang_coverage,
          use_java_coverage=use_java_coverage,
          use_javascript_coverage=use_javascript_coverage,
          coverage_test_types=coverage_test_types,
          coverage_exclude_sources=coverage_exclude_sources,
          coverage_reference_commit=coverage_reference_commit,
          export_coverage_to_zoss=export_coverage_to_zoss,
          generate_blame_list=generate_blame_list,
        ),
      }
    )
