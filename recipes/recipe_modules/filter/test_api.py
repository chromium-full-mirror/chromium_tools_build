# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from collections.abc import Collection

from recipe_engine import recipe_test_api


class FilterTestApi(recipe_test_api.RecipeTestApi):
  def analyze_output(
    self,
    *,
    status: str,
    compile_targets: Collection[str],
    test_targets: Collection[str],
  ) -> recipe_test_api.StepTestData:
    """Overrides the analyze step for normal execution.

    This does not support an error or invalid targets outcome.
    """
    assert status in (
      'Found dependency',
      'Found dependency (all)',
      'No dependency',
    )
    return self.override_step_data(
      'analyze',
      self.m.json.output(
        {
          'status': status,
          'compile_targets': list(compile_targets),
          'test_targets': list(test_targets),
        }
      ),
    )

  def no_dependency(self) -> recipe_test_api.StepTestData:
    return self.analyze_output(
      status='No dependency',
      compile_targets=[],
      test_targets=[],
    )

  def analyze_config(
    self,
    *,
    exclusions: list[str] | None = None,
    ignores: list[str] | None = None,
  ):
    config = {}
    if exclusions is not None:
      config['exclusions'] = exclusions
    if ignores is not None:
      config['ignores'] = ignores
    return self.override_step_data(
      'read filter exclusion spec',
      self.m.file.read_json({'base': config}),
    )

  def exclude_everything(self) -> recipe_test_api.StepTestData:
    """Overrides analyze step data so that all targets get compiled.

    This is generally not needed: by default, analyze will return all
    input targets as affected.
    """
    return self.analyze_config(exclusions=['.+'])
