# Copyright 2018 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import chromium_android
from RECIPE_MODULES.depot_tools import bot_update, gclient
from RECIPE_MODULES.recipe_engine import context, path, properties


@dataclass
class DEPS(RecipeScriptApi):
  bot_update: bot_update.API
  chromium_android: chromium_android.API
  context: context.API
  gclient: gclient.API
  path: path.API
  properties: properties.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  path: path.TEST_API
  properties: properties.TEST_API


def RunSteps(api: DEPS):
  api.gclient.set_config('chromium')
  # Sets api.path.checkout_dir.
  update_result = api.bot_update.ensure_checkout()
  source_dir = update_result.source_root.path
  globs = api.properties.get('globs')
  # properties convert list to tuples.
  if globs:
    globs = list(globs)
  api.chromium_android.download_build(
      source_dir,
      'test-bucket',
      'test/path',
      extract_path=api.properties.get('extract_path'),
      globs=globs)


def GenTests(api: TEST_DEPS):
  def check_args(check, step_odict, expected_cwd, expected_args):
    step_name = 'unzip_build_product'
    expected_cwd = expected_cwd or api.path.checkout_dir
    step = step_odict.get(step_name)
    check('No step named "%s"' % step_name, step)

    check('Expected cwd %r but got %r' % (expected_cwd, step.cwd),
          str(expected_cwd) == step.cwd)

    check('Expected args %r but got %r' % (expected_args, step.cmd),
          expected_args == step.cmd)
    return step_odict

  first_args = ['unzip', '-o', '[START_DIR]/src/out/build_product.zip']
  yield api.test(
      'basic',
      api.post_process(post_process.MustRun, 'gsutil download_build_product'),
      api.post_process(check_args, expected_cwd=None, expected_args=first_args),
      api.post_process(post_process.DropExpectation),
  )
  mock_extract_path = api.path.tmp_base_dir / 'bar'
  yield api.test(
      'with extract_path',
      api.properties(extract_path=mock_extract_path),
      api.post_process(post_process.MustRun, 'gsutil download_build_product'),
      api.post_process(
          check_args, expected_cwd=mock_extract_path, expected_args=first_args),
      api.post_process(post_process.DropExpectation),
  )
  mock_globs = ['apks/*', 'gen/*']
  yield api.test(
      'with globs',
      api.properties(globs=mock_globs),
      api.post_process(post_process.MustRun, 'gsutil download_build_product'),
      api.post_process(
          check_args, expected_cwd=None, expected_args=first_args + mock_globs),
      api.post_process(post_process.DropExpectation),
  )
