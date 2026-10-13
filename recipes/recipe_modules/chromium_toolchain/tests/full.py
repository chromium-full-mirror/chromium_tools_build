# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb
from PB.recipe_modules.build.chromium_toolchain import (
  properties as properties_pb,
)
from recipe_engine import post_process
from recipe_engine import recipe_test_api
from recipe_engine.recipe_api import RecipeScriptApi
from RECIPE_MODULES.build import chromium_toolchain
from RECIPE_MODULES.recipe_engine import (
  buildbucket,
  json,
  platform,
  properties,
)


@dataclass
class DEPS(RecipeScriptApi):
  buildbucket: buildbucket.API
  chromium_toolchain: chromium_toolchain.API
  properties: properties.API


@dataclass
class TEST_DEPS(recipe_test_api.RecipeTestApi):
  buildbucket: buildbucket.TEST_API
  chromium_toolchain: chromium_toolchain.TEST_API
  json: json.TEST_API
  platform: platform.TEST_API
  properties: properties.TEST_API


def RunSteps(api: DEPS) -> None:
  if output_commit := api.properties.get('output_commit'):
    api.buildbucket.set_output_gitiles_commit(
      common_pb.GitilesCommit(
        host='chromium.googlesource.com',
        project='chromium/src',
        ref='refs/heads/main',
        id=output_commit,
      )
    )
  if api.properties.get('pass_explicit_properties'):
    props = properties_pb.InputProperties(
      toolchain=properties_pb.InputProperties.CLANG
    )
    api.chromium_toolchain.trusted_package(props)
  else:
    api.chromium_toolchain.trusted_package()


def GenTests(api: TEST_DEPS) -> Iterator[recipe_test_api.TestData]:

  def gen_props(toolchain, **kwargs):
    return api.chromium_toolchain.properties(toolchain=toolchain, **kwargs)

  yield api.test(
    'clang_success',
    api.platform.name('linux'),
    api.buildbucket.ci_build(
      project='chromium',
      bucket='ci',
      builder='trusted-packaging-linux-clang',
      git_ref='refs/heads/main',
      revision='a' * 40,
    ),
    gen_props(properties_pb.InputProperties.CLANG),
    api.post_process(post_process.StatusSuccess),
    api.post_process(post_process.MustRun, 'package clang.request build'),
    api.post_process(
      post_process.StepCommandContains,
      'package clang.request build',
      ['--clang'],
    ),
    api.post_process(
      post_process.StepCommandContains,
      'package clang.request build',
      [
        '-chromium_src_commit',
        'a' * 40,
      ],
    ),
    api.post_process(
      post_process.StepCommandDoesNotContain,
      'package clang.request build',
      ['-chromium_src_ref'],
    ),
    api.post_process(post_process.MustRun, 'package clang.fetch build'),
    api.post_process(
      post_process.StepCommandContains,
      'package clang.fetch build',
      [
        '--build_name',
        (
          'projects/fake-project/locations/fake-location/'
          'instances/fake-instance/pools/fake-pool/builds/fake-build'
        ),
        '--logs=false',
      ],
    ),
    api.post_process(
      post_process.PropertyEquals,
      'time-to-first-tbi-step',
      2448.48,
    ),
  )

  yield api.test(
    'rust_success',
    api.platform.name('linux'),
    api.buildbucket.ci_build(
      project='chromium',
      bucket='ci',
      builder='trusted-packaging-linux-rust',
      git_ref='refs/branch-heads/5400',
      revision='b' * 40,
    ),
    gen_props(properties_pb.InputProperties.RUST),
    api.post_process(post_process.StatusSuccess),
    api.post_process(post_process.MustRun, 'package rust.request build'),
    api.post_process(
      post_process.StepCommandContains,
      'package rust.request build',
      [
        '-chromium_src_commit',
        'b' * 40,
      ],
    ),
    api.post_process(
      post_process.StepCommandDoesNotContain,
      'package rust.request build',
      ['-chromium_src_ref'],
    ),
    api.post_process(
      post_process.StepCommandDoesNotContain,
      'package rust.request build',
      ['--rust'],
    ),
    api.post_process(
      post_process.StepCommandDoesNotContain,
      'package rust.request build',
      ['--clang'],
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'no_gitiles_commit',
    api.platform.name('linux'),
    api.buildbucket.ci_build(
      project='chromium',
      bucket='ci',
      builder='trusted-packaging-linux-clang',
      git_ref='',
      revision='',
      execution_timeout=21600,
    ),
    gen_props(properties_pb.InputProperties.CLANG),
    api.post_process(post_process.StatusSuccess),
    api.post_process(post_process.MustRun, 'package clang.request build'),
    api.post_process(
      post_process.StepCommandDoesNotContain,
      'package clang.request build',
      [
        '-chromium_src_commit',
        '-chromium_src_ref',
      ],
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'try_job_success',
    api.platform.name('linux'),
    api.buildbucket.try_build(
      project='chromium',
      bucket='try',
      builder='trusted-packaging-linux-clang',
      git_repo='https://chromium.googlesource.com/chromium/src',
      change_number=123456,
      patch_set=16,
    ),
    gen_props(properties_pb.InputProperties.CLANG),
    api.post_process(post_process.StatusSuccess),
    api.post_process(post_process.MustRun, 'package clang.request build'),
    api.post_process(
      post_process.StepEnvContains,
      'package clang.request build',
      {
        'GERRIT_CHANGE_ID': '123456',
        'GERRIT_PATCHSET_ID': '16',
      },
    ),
    api.post_process(
      post_process.StepCommandDoesNotContain,
      'package clang.request build',
      ['-chromium_src_commit'],
    ),
  )

  yield api.test(
    'unknown_toolchain',
    api.platform.name('linux'),
    api.buildbucket.ci_build(
      project='chromium',
      bucket='ci',
      builder='trusted-packaging-linux-clang',
    ),
    gen_props(properties_pb.InputProperties.UNKNOWN),
    api.expect_status('FAILURE'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'clang_with_tbi_properties',
    api.platform.name('linux'),
    api.buildbucket.ci_build(
      project='chromium',
      bucket='ci',
      builder='trusted-packaging-linux-clang',
      git_ref='refs/heads/main',
      revision='a' * 40,
    ),
    gen_props(
      properties_pb.InputProperties.CLANG,
      trusted_build_instance='fake-instance',
      trusted_build_instance_pool='fake-pool',
      trusted_build_instance_env='fake-env',
      trusted_build_instance_project='fake-project',
      config_path='fake/config/path/linux.textproto',
    ),
    api.post_process(post_process.StatusSuccess),
    api.post_process(post_process.MustRun, 'package clang.request build'),
    api.post_process(
      post_process.StepCommandContains,
      'package clang.request build',
      ['--clang'],
    ),
    api.post_process(
      post_process.StepCommandContains,
      'package clang.request build',
      [
        '--instance',
        'fake-instance',
        '--pool',
        'fake-pool',
        '--env',
        'fake-env',
        '--project',
        'fake-project',
      ],
    ),
    api.post_process(
      post_process.StepCommandContains,
      'package clang.request build',
      [
        '--build_prefix',
        'bb-8945511751514863184',
      ],
    ),
    api.post_process(
      post_process.StepCommandContains,
      'package clang.request build',
      [
        '-chromium_src_commit',
        'a' * 40,
      ],
    ),
    api.post_process(post_process.MustRun, 'package clang.fetch build'),
    api.post_process(
      post_process.StepCommandContains,
      'package clang.fetch build',
      [
        '--instance',
        'fake-instance',
        '--pool',
        'fake-pool',
        '--env',
        'fake-env',
        '--project',
        'fake-project',
      ],
    ),
    api.post_process(
      post_process.PropertyEquals,
      'time-to-first-tbi-step',
      2448.48,
    ),
  )

  yield api.test(
    'clang_default_build_prefix',
    api.platform.name('linux'),
    api.buildbucket.ci_build(
      project='chromium',
      bucket='ci',
      builder='trusted-packaging-linux-clang',
      git_ref='refs/heads/main',
      revision='a' * 40,
      build_id=123456789,
    ),
    gen_props(properties_pb.InputProperties.CLANG),
    api.post_process(post_process.StatusSuccess),
    api.post_process(post_process.MustRun, 'package clang.request build'),
    api.post_process(
      post_process.StepCommandContains,
      'package clang.request build',
      [
        '--build_prefix',
        'bb-123456789',
      ],
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'explicit_properties',
    api.platform.name('linux'),
    api.buildbucket.ci_build(
      project='chromium',
      bucket='ci',
      builder='trusted-packaging-linux-clang',
      git_ref='refs/heads/main',
      revision='a' * 40,
    ),
    api.properties(pass_explicit_properties=True),
    api.post_process(post_process.StatusSuccess),
    api.post_process(post_process.MustRun, 'package clang.request build'),
    api.post_process(
      post_process.StepCommandContains,
      'package clang.request build',
      ['--clang'],
    ),
  )

  yield api.test(
    'no_build_name_in_summary',
    api.platform.name('linux'),
    api.buildbucket.ci_build(
      project='chromium',
      bucket='ci',
      builder='trusted-packaging-linux-clang',
      git_ref='refs/heads/main',
      revision='a' * 40,
    ),
    gen_props(properties_pb.InputProperties.CLANG),
    api.step_data(
      'package clang.request build',
      api.json.output(name='summary', data={}),
    ),
    api.post_process(post_process.StatusSuccess),
    api.post_process(post_process.MustRun, 'package clang.request build'),
    api.post_process(post_process.DoesNotRun, 'package clang.fetch build'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'no_timestamps_in_build_details',
    api.platform.name('linux'),
    api.buildbucket.ci_build(
      project='chromium',
      bucket='ci',
      builder='trusted-packaging-linux-clang',
      git_ref='refs/heads/main',
      revision='a' * 40,
    ),
    gen_props(properties_pb.InputProperties.CLANG),
    api.step_data(
      'package clang.fetch build',
      stdout=api.json.output({}),
    ),
    api.post_process(post_process.StatusSuccess),
    api.post_process(post_process.MustRun, 'package clang.fetch build'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'missing_time_fields_in_timestamps',
    api.platform.name('linux'),
    api.buildbucket.ci_build(
      project='chromium',
      bucket='ci',
      builder='trusted-packaging-linux-clang',
      git_ref='refs/heads/main',
      revision='a' * 40,
    ),
    gen_props(properties_pb.InputProperties.CLANG),
    api.step_data(
      'package clang.fetch build',
      stdout=api.json.output(
        {
          'timestamps': {
            'createdTime': '2026-10-11T19:00:34.630447Z',
          },
        }
      ),
    ),
    api.post_process(post_process.StatusSuccess),
    api.post_process(post_process.MustRun, 'package clang.fetch build'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'output_gitiles_commit',
    api.platform.name('linux'),
    api.buildbucket.ci_build(
      project='chromium',
      bucket='ci',
      builder='trusted-packaging-linux-clang',
      git_ref='',
      revision='',
    ),
    gen_props(properties_pb.InputProperties.CLANG),
    api.properties(output_commit='c' * 40),
    api.post_process(post_process.StatusSuccess),
    api.post_process(post_process.MustRun, 'package clang.request build'),
    api.post_process(
      post_process.StepCommandContains,
      'package clang.request build',
      [
        '-chromium_src_commit',
        'c' * 40,
      ],
    ),
    api.post_process(post_process.DropExpectation),
  )
