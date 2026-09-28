# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process

from PB.recipe_modules.build.archive import properties

from RECIPE_MODULES.build import chromium_tests_builder_config as ctbc

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import (
  archive,
  chromium,
  chromium_tests,
  chromium_tests_builder_config,
)
from RECIPE_MODULES.depot_tools import bot_update
from RECIPE_MODULES.recipe_engine import (
  path,
  platform,
  properties as properties_module,
  raw_io,
  runtime,
)


@dataclass
class DEPS(RecipeScriptApi):
  archive: archive.API
  bot_update: bot_update.API
  chromium: chromium.API
  chromium_tests: chromium_tests.API
  chromium_tests_builder_config: chromium_tests_builder_config.API
  path: path.API
  properties: properties_module.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  archive: archive.TEST_API
  chromium: chromium.TEST_API
  chromium_tests_builder_config: chromium_tests_builder_config.TEST_API
  platform: platform.TEST_API
  properties: properties_module.TEST_API
  raw_io: raw_io.TEST_API
  runtime: runtime.TEST_API


_NPM_STEP = 'Generic Archiving Steps.publish chromium-bidi to npm'
_CHECK_PUBLISHED_STEP = f'{_NPM_STEP}.check if version is already published'
_DRY_RUN_STEP = f'{_NPM_STEP}.npm publish (dry run)'
_PUBLISH_STEP = f'{_NPM_STEP}.npm publish'
_SKIP_PUBLISH_STEP = f'{_NPM_STEP}.skip npm publish'
_MANIFEST_UPLOAD_STEP = (
  f'{_NPM_STEP}.gsutil upload publishing manifest to OSS Exit Gate'
)

# The access token that recipe_engine/service_account returns in tests.
_TEST_TOKEN = 'extra.secret.token.should.not.be.logged'


def RunSteps(api: DEPS):
  _, builder_config = api.chromium_tests_builder_config.lookup_builder()
  api.chromium_tests.configure_build(builder_config)
  update_properties = api.properties.get('update_properties', {})
  custom_vars = api.properties.get('custom_vars', {})

  update_result = api.bot_update.ensure_checkout()
  checkout_dir = update_result.checkout_dir
  source_dir = update_result.source_root.path
  build_dir = api.chromium.default_build_dir(source_dir)

  # On trybuilders, $build/archive and source-side specs may only verify
  # paths, so only an explicit config reaches npm_archive there.
  config = None
  if api.properties.get('explicit_config'):
    config = properties.InputProperties(npm_archive_datas=[_npm_archive_data()])

  api.archive.generic_archive(
    checkout_dir=checkout_dir,
    source_dir=source_dir,
    build_dir=build_dir,
    update_properties=update_properties,
    custom_vars=custom_vars,
    config=config,
  )


def _npm_archive_data():
  npm_archive_data = properties.NpmArchiveData()
  npm_archive_data.package_name = 'chromium-bidi'
  npm_archive_data.version = '{%chromium_version%}'
  npm_archive_data.dist_tag = 'canary'
  npm_archive_data.registry_url = (
    'https://us-npm.pkg.dev/oss-exit-gate-prod/chrome-devtools-npm--npm/'
  )
  npm_archive_data.package_source_dir = 'third_party/chromium-bidi'
  npm_archive_data.package_root_files.extend(
    ['package.json', 'README.md', 'LICENSE', '.browser']
  )
  npm_archive_data.artifact_mappings['gen/third_party/chromium-bidi/src'] = (
    'out/Default/gen/src'
  )
  npm_archive_data.file_mappings.extend(
    [
      properties.ArchiveFileRename(
        from_file='third_party/chromium-bidi/src/index.ts',
        to_file='out/Default/gen/src/index.js',
      ),
      properties.ArchiveFileRename(
        from_file='third_party/chromium-bidi/src/index.ts',
        to_file='out/Default/gen/src/index.d.ts',
      ),
    ]
  )
  npm_archive_data.pruned_outputs.extend(
    ['*.tsbuildinfo', '*-tsconfig.json', '*.test.*']
  )
  npm_archive_data.manifest_bucket = 'oss-exit-gate-prod-projects-bucket'
  npm_archive_data.manifest_path = (
    'chrome-devtools-npm/npm/manifests/publishing_manifest.json'
  )
  return npm_archive_data


def _minimal_npm_archive_data():
  """Returns an NpmArchiveData with only the fields needed to publish."""
  npm_archive_data = properties.NpmArchiveData()
  npm_archive_data.package_name = 'chromium-bidi'
  npm_archive_data.registry_url = 'https://npm.example.com/registry'
  npm_archive_data.package_root_files.append('package.json')
  return npm_archive_data


def _token_is_not_logged(check, steps):
  for step_name, step in steps.items():
    if step_name == '$result':
      continue
    for log_name, log in step.logs.items():
      check(
        f'log {log_name!r} of step {step_name!r} has no access token',
        _TEST_TOKEN not in log,
      )


def _npm_executable_endswith(check, steps, step_name, suffix):
  check(steps[step_name].cmd[0].endswith(suffix))


def GenTests(api: TEST_DEPS):
  ctbc_api = api.chromium_tests_builder_config
  builder_spec = ctbc.BuilderSpec.create(
    gclient_config='chromium',
    chromium_config='chromium',
  )

  def archive_properties(npm_archive_data):
    return api.properties(
      **{
        '$build/archive': properties.InputProperties(
          npm_archive_datas=[npm_archive_data]
        )
      }
    )

  def ci_build(npm_archive_data):
    return (
      api.chromium.generic_build(
        builder_group='fake-group', builder='fake-builder'
      )
      + ctbc_api.properties(
        ctbc_api.properties_assembler_for_ci_builder(
          builder_group='fake-group',
          builder='fake-builder',
          builder_spec=builder_spec,
        ).assemble()
      )
      + archive_properties(npm_archive_data)
    )

  def try_build():
    return api.chromium.try_build(
      builder_group='fake-try-group', builder='fake-try-builder'
    ) + ctbc_api.properties(
      ctbc_api.properties_assembler_for_try_builder()
      .with_mirrored_builder(
        builder_group='fake-group',
        builder='fake-builder',
        builder_spec=builder_spec,
      )
      .assemble()
    )

  def chromium_version():
    return api.chromium.override_version(
      major=155,
      minor=0,
      build=8057,
      patch=0,
      step_name=f'{_NPM_STEP}.get version',
    )

  yield api.test(
    'basic_npm_archive',
    ci_build(_npm_archive_data()),
    chromium_version(),
    api.post_process(
      post_process.StepCommandContains,
      f'{_NPM_STEP}.install infra/3pp/tools/nodejs.ensure_installed',
      ['-ensure-file', 'infra/3pp/tools/nodejs/${platform} version:3@26.8.2'],
    ),
    api.post_process(
      post_process.MustRun,
      f'{_NPM_STEP}.prune *.tsbuildinfo',
      f'{_NPM_STEP}.prune *-tsconfig.json',
      f'{_NPM_STEP}.prune *.test.*',
    ),
    api.post_process(
      post_process.StepCommandContains,
      f'{_NPM_STEP}.npm version',
      [
        'version',
        '155.0.8057-0',
        '--no-git-tag-version',
        '--allow-same-version',
      ],
    ),
    api.post_process(
      _npm_executable_endswith, f'{_NPM_STEP}.npm version', 'bin/npm'
    ),
    api.post_process(
      post_process.StepCommandContains,
      f'{_NPM_STEP}.write .npmrc',
      [
        '//us-npm.pkg.dev/oss-exit-gate-prod/chrome-devtools-npm--npm/'
        f':_authToken="{_TEST_TOKEN}"\n'
      ],
    ),
    api.post_process(_token_is_not_logged),
    api.post_process(
      post_process.StepCommandContains,
      _CHECK_PUBLISHED_STEP,
      ['view', 'chromium-bidi@155.0.8057-0', 'version'],
    ),
    api.post_process(
      post_process.StepCommandContains,
      _DRY_RUN_STEP,
      ['--tag', 'canary', '--dry-run'],
    ),
    api.post_process(
      post_process.StepCommandContains,
      _PUBLISH_STEP,
      ['--tag', 'canary'],
    ),
    api.post_process(post_process.MustRun, _MANIFEST_UPLOAD_STEP),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'windows_npm_archive',
    api.platform('win', 64),
    ci_build(_npm_archive_data()),
    api.post_process(_npm_executable_endswith, _PUBLISH_STEP, 'npm.cmd'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'experimental_npm_archive_is_dry_run_only',
    ci_build(_npm_archive_data()),
    api.runtime(is_experimental=True),
    api.post_process(post_process.MustRun, _DRY_RUN_STEP),
    api.post_process(
      post_process.DoesNotRun, _PUBLISH_STEP, _MANIFEST_UPLOAD_STEP
    ),
    api.post_process(
      post_process.StepTextContains,
      _SKIP_PUBLISH_STEP,
      ['experimental and try builds only do a dry run'],
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'try_npm_archive_is_dry_run_only',
    try_build(),
    api.properties(explicit_config=True),
    api.post_process(post_process.MustRun, _DRY_RUN_STEP),
    api.post_process(
      post_process.DoesNotRun, _PUBLISH_STEP, _MANIFEST_UPLOAD_STEP
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'npm_version_already_published',
    ci_build(_npm_archive_data()),
    chromium_version(),
    api.step_data(
      _CHECK_PUBLISHED_STEP,
      stdout=api.raw_io.output_text('155.0.8057-0\n'),
    ),
    api.post_process(post_process.DoesNotRun, _DRY_RUN_STEP, _PUBLISH_STEP),
    api.post_process(
      post_process.StepTextContains,
      _SKIP_PUBLISH_STEP,
      ['chromium-bidi@155.0.8057-0 is already published'],
    ),
    api.post_process(post_process.MustRun, _MANIFEST_UPLOAD_STEP),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'minimal_npm_archive',
    ci_build(_minimal_npm_archive_data()),
    api.post_process(
      post_process.DoesNotRun,
      f'{_NPM_STEP}.npm version',
      _MANIFEST_UPLOAD_STEP,
    ),
    api.post_process(
      post_process.StepCommandContains,
      f'{_NPM_STEP}.write .npmrc',
      [f'//npm.example.com/registry/:_authToken="{_TEST_TOKEN}"\n'],
    ),
    api.post_process(
      post_process.StepCommandContains,
      _CHECK_PUBLISHED_STEP,
      ['view', 'chromium-bidi@1.0.0', 'version'],
    ),
    api.post_process(
      post_process.StepCommandDoesNotContain, _PUBLISH_STEP, ['--tag']
    ),
    api.post_process(post_process.DropExpectation),
  )
