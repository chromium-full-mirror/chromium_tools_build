# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import re
import textwrap

from recipe_engine.post_process import (DropExpectation, StepCommandContains,
                                        StatusFailure, StatusSuccess)
from packaging.version import parse

from dataclasses import dataclass
from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi
from RECIPE_MODULES.infra import codesearch, infra_checkout
from RECIPE_MODULES.depot_tools import bot_update, gclient, gsutil
from RECIPE_MODULES.recipe_engine import (buildbucket, cipd, context, file,
                                          golang, json, path, platform,
                                          properties, raw_io, step)


@dataclass
class DEPS(RecipeScriptApi):
  codesearch: codesearch.API
  infra_checkout: infra_checkout.API
  bot_update: bot_update.API
  gclient: gclient.API
  gsutil: gsutil.API
  buildbucket: buildbucket.API
  cipd: cipd.API
  context: context.API
  file: file.API
  golang: golang.API
  path: path.API
  platform: platform.API
  properties: properties.API
  step: step.API
  json: json.API
  raw_io: raw_io.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  buildbucket: buildbucket.TEST_API
  file: file.TEST_API
  path: path.TEST_API
  platform: platform.TEST_API
  properties: properties.TEST_API

MODULE_RE = re.compile(r'^module\s+([^\s#]+)', re.MULTILINE)
GO_VERSION_RE = re.compile(r'^go (\d+\.\d+(?:\.\d+)?)$', re.MULTILINE)


def RunSteps(api: DEPS):
  kythe_bin = api.codesearch.ensure_kythe().joinpath('extractors',
                                                     'go_extractor')

  internal = api.properties.get('internal', False)
  c = api.gclient.make_config()
  soln = c.solutions.add()
  soln.name = 'infra_superproject'
  soln.url = 'https://chromium.googlesource.com/infra/infra_superproject.git'
  soln.revision = 'HEAD'
  if internal:
    soln.custom_vars['checkout_internal'] = True
  api.gclient.c = c

  with api.context(cwd=api.path.cache_dir):
    result = api.bot_update.ensure_checkout()
    kzip_name = '%s.kzip' % result.properties['got_revision']

  checkout_dir = api.path.cache_dir / 'infra_superproject'
  kzip_loc = api.path.cache_dir / kzip_name

  # Hardcode the active Go modules inside the superproject. We do this because
  # recursive globbing (walking the entire tree) takes extremely long
  # on LUCI bots due to traversing massive package caches (gomodcache/gopath)
  # and compiler distribution folders.
  # TODO: Figure out a way to glob go.mod files efficiently.
  potential_go_mod_files = [
      'infra/go/src/go.chromium.org/chromiumos/config/go/src/go.chromium.org/chromiumos/config/go/go.mod',
      'infra/go/src/go.chromium.org/chromiumos/infra/proto/go/go.mod',
      'infra/go/src/go.chromium.org/luci/go.mod',
      'infra/go/src/infra/go.mod',
  ]
  if internal:
    potential_go_mod_files += [
        # Internal code won't be readable by public codesearch builders.
        'infra_internal/go/src/infra_internal/go.mod',
    ]

  go_mod_files = []
  for relpath in potential_go_mod_files:
    abs_path = checkout_dir / relpath
    if api.path.exists(abs_path):
      go_mod_files.append(abs_path)

  if not go_mod_files:
    raise api.step.StepFailure('No go.mod files found in the repository.')

  targets = []
  targets_dir = []
  go_version = '1.24.5'
  for mod_file in go_mod_files:
    mod_text = api.file.read_text('read %s' % mod_file, mod_file)
    # Exclude the conflicting standalone version of grpc/stats/opentelemetry.
    # We do this dynamically in all workspace go.mod files so that the Go module
    # resolver completely excludes the standalone module and resolves the import
    # using the package integrated inside the main google.golang.org/grpc module.
    mod_text += '\nexclude google.golang.org/grpc/stats/opentelemetry v0.0.0-20240907200651-3ffb98b2c93a\n'
    api.file.write_text('exclude stats/opentelemetry in %s' % mod_file,
                        mod_file, mod_text)

    match = MODULE_RE.search(mod_text)
    if not match:
      raise api.step.StepFailure('Did not detect Modules for %s' % mod_file)
    targets.append(match.group(1) + '/...')

    mod_dir = api.path.dirname(mod_file)
    rel_mod_dir = api.path.relpath(mod_dir, checkout_dir)
    targets_dir.append(rel_mod_dir)

    match = GO_VERSION_RE.search(mod_text)
    if not match:
      raise api.step.StepFailure('Did not detect Go version for %s' % mod_file)
    go_version = max(go_version, match.group(1), key=parse)

  # KYTHE_ROOT_DIRECTORY makes sub modules relpath to build repo root.
  with api.golang(version=go_version), api.context(
      cwd=checkout_dir, env={'KYTHE_ROOT_DIRECTORY': checkout_dir}):
    # Without go.work we have to loop multiple directories and merge kzips.
    api.step('init go modules', ['go', 'work', 'init'] + targets_dir)

    # Manually patch co-dependent Bazel remote API versions to resolve an upstream
    # packaging bug in remote-apis-sdks where a newer SDK version compiles against
    # an outdated remote-apis dependency lacking the new SpliceBlob method.
    api.step('patch go.work workspace dependencies', [
        'go',
        'work',
        'edit',
        '-replace',
        'github.com/bazelbuild/remote-apis-sdks=github.com/bazelbuild/remote-apis-sdks@v0.0.0-20251007172043-4cdfee7bdc2d',
        '-replace',
        'github.com/bazelbuild/remote-apis=github.com/bazelbuild/remote-apis@v0.0.0-20240926071355-6777112ef7de',
        '-replace',
        'cloud.google.com/go/kms/apiv1/kmspb=cloud.google.com/go/kms@v1.31.0',
        '-replace',
        'go.chromium.org/chromiumos/config/go@v0.0.0-20240309015314-b8a183866804=./infra/go/src/go.chromium.org/chromiumos/config/go/src/go.chromium.org/chromiumos/config/go',
        '-replace',
        'go.chromium.org/chromiumos/infra/proto/go@v0.0.0-20250623190020-bff867fc0474=./infra/go/src/go.chromium.org/chromiumos/infra/proto/go',
        '-replace',
        'go.chromium.org/luci@v0.0.0-20260326213659-7c3f2951dea9=./infra/go/src/go.chromium.org/luci',
    ])

    vnames_config = [{
        "pattern": "infra_internal/(.*)",
        "vname": {
            "corpus":
                "chromium.googlesource.com/infra/infra_superproject//main",
            "path":
                "infra_internal/@1@"
        }
    }]
    vnames_config_json = api.json.dumps(vnames_config)

    api.step('generate go kzip', [
        kythe_bin, '--corpus',
        'chromium.googlesource.com/infra/infra_superproject//main',
        '--use_default_corpus_for_stdlib=true',
        '--use_default_corpus_for_deps=true', '--rules',
        api.raw_io.input_text(vnames_config_json,
                              suffix='.json'), '--output', kzip_loc
    ] + targets)

  final_kzip_loc = kzip_loc

  try:
    extractor_out_dir = api.path.cleanup_dir / 'extractor_output'
    api.file.ensure_directory('create extractor_output dir', extractor_out_dir)
    extractor_script = api.codesearch.resource('python_extractor.py')

    for target_dir in api.file.listdir(
        'list checkout_dir',
        checkout_dir,
        test_data=['infra', '.git', 'README.md', 'gcloud']):
      dir_name = api.path.basename(target_dir)
      if not api.path.isdir(target_dir) or dir_name.startswith(
          '.') or dir_name == 'gcloud':
        continue
      out_json_path = extractor_out_dir / f'{dir_name}.json'
      api.step(f'extract python metadata for {dir_name}', [
          'vpython3',
          extractor_script,
          target_dir,
          out_json_path,
          '--corpus',
          'chromium.googlesource.com/infra/infra_superproject//main',
          '--root',
          checkout_dir,
      ])
    json_files = api.file.glob_paths('list extractor json outputs',
                                     extractor_out_dir, '*.json')

    if json_files:
      exec_path = api.cipd.ensure_tool("infra/tools/kzip_builder/${platform}",
                                       "latest")
      python_kzip_loc = api.path.cache_dir / f'python_{kzip_name}'

      api.step(
          'build python kzip',
          [
              exec_path,
              '--output',
              python_kzip_loc,
              '--root',
              checkout_dir,
          ] + json_files,
      )

      final_kzip_loc = api.codesearch.run_kzip_merge(kzip_loc, python_kzip_loc)

  except api.step.StepFailure as exc:
    if api.step.active_result:
      result = api.step.active_result
      result.presentation.step_text = 'python kzip extraction failed, skipping'
      result.presentation.logs['exception'] = str(exc).splitlines()

  api.gsutil.upload(
      name='upload kythe index pack',
      source=final_kzip_loc,
      bucket='chrome-internal-codesearch' if internal else 'chrome-codesearch',
      dest='infra/%s' % kzip_name)


def GenTests(api: TEST_DEPS):
  yield api.test(
      'basic',
      api.buildbucket.try_build(
          project='infra',
          builder='generic tester',
          git_repo='https://chromium.googlesource.com/infra/infra_superproject'
      ),
      api.platform('linux', 64),
      api.path.dirs_exist(
          api.path.cache_dir / 'infra_superproject',
          api.path.cache_dir / 'infra_superproject/infra',
          api.path.cache_dir / 'infra_superproject/.git',
      ),
      api.path.exists(
          api.path.cache_dir /
          'infra_superproject/infra/go/src/go.chromium.org/chromiumos/config/go/src/go.chromium.org/chromiumos/config/go/go.mod',
          api.path.cache_dir /
          'infra_superproject/infra/go/src/go.chromium.org/chromiumos/infra/proto/go/go.mod',
          api.path.cache_dir /
          'infra_superproject/infra/go/src/go.chromium.org/luci/go.mod',
          api.path.cache_dir / 'infra_superproject/infra/go/src/infra/go.mod',
      ),
      api.step_data(
          'read [CACHE]/infra_superproject/infra/go/src/go.chromium.org/chromiumos/config/go/src/go.chromium.org/chromiumos/config/go/go.mod',
          api.file.read_text(
              textwrap.dedent('''
              module go.chromium.org/chromiumos/config/go
              go 1.25.0
              ''')),
      ),
      api.step_data(
          'read [CACHE]/infra_superproject/infra/go/src/go.chromium.org/chromiumos/infra/proto/go/go.mod',
          api.file.read_text(
              textwrap.dedent('''
              module go.chromium.org/chromiumos/infra/proto/go
              go 1.25.0
              ''')),
      ),
      api.step_data(
          'read [CACHE]/infra_superproject/infra/go/src/go.chromium.org/luci/go.mod',
          api.file.read_text(
              textwrap.dedent('''
              module go.chromium.org/luci
              go 1.26.2

              tool (
                google.golang.org/a/foo
                google.golang.org/b/bar
              )''')),
      ),
      api.step_data(
          'read [CACHE]/infra_superproject/infra/go/src/infra/go.mod',
          api.file.read_text(
              textwrap.dedent('''
              module go.chromium.org/infra
              go 1.25.9

              tool (
                google.golang.org/a/foo
                google.golang.org/b/bar
              )''')),
      ),
      api.override_step_data(
          'list checkout_dir',
          api.file.listdir(['infra', '.git', 'README.md']),
      ),
      api.step_data(
          'list extractor json outputs',
          api.file.glob_paths(['infra.json']),
      ),
      api.post_process(StepCommandContains, 'ensure_installed (2)',
                       ['infra/3pp/tools/go/${platform} version:3@1.26.2']),
      api.post_process(StepCommandContains, 'init go modules', [
          'go',
          'work',
          'init',
          'infra/go/src/go.chromium.org/chromiumos/config/go/src/go.chromium.org/chromiumos/config/go',
          'infra/go/src/go.chromium.org/chromiumos/infra/proto/go',
          'infra/go/src/go.chromium.org/luci',
          'infra/go/src/infra',
      ]),
      api.post_process(StepCommandContains, 'generate go kzip', [
          '--rules',
          '[{"pattern": "infra_internal/(.*)", "vname": {"corpus": "chromium.googlesource.com/infra/infra_superproject//main", "path": "infra_internal/@1@"}}]',
      ]),
      api.post_process(StepCommandContains, 'generate go kzip', [
          'go.chromium.org/chromiumos/config/go/...',
          'go.chromium.org/chromiumos/infra/proto/go/...',
          'go.chromium.org/luci/...',
          'go.chromium.org/infra/...',
      ]),
      api.post_process(
          StepCommandContains, 'extract python metadata for infra', [
              '--corpus',
              'chromium.googlesource.com/infra/infra_superproject//main',
          ]),
      api.post_process(StepCommandContains, 'build python kzip', [
          '--output',
          '[CACHE]/python_1b84187dd61ba677a120a2cfab0158f20c1d6c39.kzip',
      ]),
      api.post_process(StepCommandContains, 'merge kzips', [
          'merge',
          '--encoding',
          'PROTO',
      ]),
      api.post_process(StatusSuccess),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'python_extraction_failed',
      api.buildbucket.try_build(
          project='infra',
          builder='generic tester',
          git_repo='https://chromium.googlesource.com/infra/infra_superproject'
      ),
      api.platform('linux', 64),
      api.path.dirs_exist(
          api.path.cache_dir / 'infra_superproject',
          api.path.cache_dir / 'infra_superproject/infra',
      ),
      api.path.exists(
          api.path.cache_dir /
          'infra_superproject/infra/go/src/go.chromium.org/chromiumos/config/go/src/go.chromium.org/chromiumos/config/go/go.mod',
          api.path.cache_dir /
          'infra_superproject/infra/go/src/go.chromium.org/chromiumos/infra/proto/go/go.mod',
          api.path.cache_dir /
          'infra_superproject/infra/go/src/go.chromium.org/luci/go.mod',
          api.path.cache_dir / 'infra_superproject/infra/go/src/infra/go.mod',
      ),
      api.step_data(
          'read [CACHE]/infra_superproject/infra/go/src/go.chromium.org/chromiumos/config/go/src/go.chromium.org/chromiumos/config/go/go.mod',
          api.file.read_text(
              textwrap.dedent('''
              module go.chromium.org/chromiumos/config/go
              go 1.25.0
              ''')),
      ),
      api.step_data(
          'read [CACHE]/infra_superproject/infra/go/src/go.chromium.org/chromiumos/infra/proto/go/go.mod',
          api.file.read_text(
              textwrap.dedent('''
              module go.chromium.org/chromiumos/infra/proto/go
              go 1.25.0
              ''')),
      ),
      api.step_data(
          'read [CACHE]/infra_superproject/infra/go/src/go.chromium.org/luci/go.mod',
          api.file.read_text(
              textwrap.dedent('''
              module go.chromium.org/luci
              go 1.26.2

              tool (
                google.golang.org/a/foo
                google.golang.org/b/bar
              )''')),
      ),
      api.step_data(
          'read [CACHE]/infra_superproject/infra/go/src/infra/go.mod',
          api.file.read_text(
              textwrap.dedent('''
              module go.chromium.org/infra
              go 1.25.9

              tool (
                google.golang.org/a/foo
                google.golang.org/b/bar
              )''')),
      ),
      api.override_step_data(
          'list checkout_dir',
          api.file.listdir(['infra']),
      ),
      api.step_data(
          'extract python metadata for infra',
          retcode=1,
      ),
      api.post_process(StatusSuccess),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'no_mod_files',
      api.buildbucket.try_build(
          project='infra',
          builder='generic tester',
          git_repo='https://chromium.googlesource.com/infra/infra_superproject'
      ),
      api.platform('linux', 64),
      api.post_process(StatusFailure),
      api.expect_status('FAILURE'),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'malformed_mod_files',
      api.buildbucket.try_build(
          project='infra',
          builder='generic tester',
          git_repo='https://chromium.googlesource.com/infra/infra_superproject'
      ),
      api.platform('linux', 64),
      api.path.exists(
          api.path.cache_dir / 'infra_superproject',
          api.path.cache_dir / 'infra_superproject' /
          'infra/go/src/infra/go.mod',
      ),
      api.step_data(
          'read [CACHE]/infra_superproject/infra/go/src/infra/go.mod',
          api.file.read_text(
              textwrap.dedent('''
              go 1.26.1

              tool (
                google.golang.org/a/foo
                google.golang.org/b/bar
              )''')),
      ),
      api.post_process(StatusFailure),
      api.expect_status('FAILURE'),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'malformed_mod_version',
      api.buildbucket.try_build(
          project='infra',
          builder='generic tester',
          git_repo='https://chromium.googlesource.com/infra/infra_superproject'
      ),
      api.platform('linux', 64),
      api.path.exists(
          api.path.cache_dir / 'infra_superproject',
          api.path.cache_dir / 'infra_superproject' /
          'infra/go/src/infra/go.mod',
      ),
      api.step_data(
          'read [CACHE]/infra_superproject/infra/go/src/infra/go.mod',
          api.file.read_text(
              textwrap.dedent('''
              module go.chromium.org/infra

              tool (
                google.golang.org/a/foo
                google.golang.org/b/bar
              )''')),
      ),
      api.post_process(StatusFailure),
      api.expect_status('FAILURE'),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'default_go_version',
      api.buildbucket.try_build(
          project='infra',
          builder='generic tester',
          git_repo='https://chromium.googlesource.com/infra/infra_superproject'
      ),
      api.platform('linux', 64),
      api.path.exists(
          api.path.cache_dir / 'infra_superproject',
          api.path.cache_dir / 'infra_superproject' /
          'infra/go/src/infra/go.mod',
      ),
      api.step_data(
          'read [CACHE]/infra_superproject/infra/go/src/infra/go.mod',
          api.file.read_text(
              textwrap.dedent('''
              module go.chromium.org/infra
              go 1.24.0
              ''')),
      ),
      api.post_process(StepCommandContains, 'ensure_installed (2)',
                       ['infra/3pp/tools/go/${platform} version:3@1.24.5']),
      api.post_process(StatusSuccess),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'internal',
      api.buildbucket.try_build(
          project='infra-internal',
          builder='generic tester',
          git_repo='https://chrome-internal.googlesource.com/infra/infra_superproject'
      ) + api.properties(internal=True),
      api.platform('linux', 64),
      api.path.exists(
          api.path.cache_dir / 'infra_superproject',
          api.path.cache_dir / 'infra_superproject' /
          'infra/go/src/infra/go.mod',
      ),
      api.step_data(
          'read [CACHE]/infra_superproject/infra/go/src/infra/go.mod',
          api.file.read_text(
              textwrap.dedent('''
              module go.chromium.org/infra
              go 1.24.0
              ''')),
      ),
      api.post_process(StatusSuccess),
      api.post_process(DropExpectation),
  )
