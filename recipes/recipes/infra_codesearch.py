# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import re
import textwrap

from recipe_engine.post_process import (DropExpectation, StepCommandContains,
                                        StatusFailure, StatusSuccess)
from packaging.version import parse

DEPS = [
    'chromium',
    'infra/codesearch',
    'infra/infra_checkout',
    'depot_tools/bot_update',
    'depot_tools/gclient',
    'depot_tools/git',
    'depot_tools/gsutil',
    'recipe_engine/buildbucket',
    'recipe_engine/context',
    'recipe_engine/file',
    'recipe_engine/golang',
    'recipe_engine/path',
    'recipe_engine/platform',
    'recipe_engine/properties',
    'recipe_engine/step',
]

MODULE_RE = re.compile(r'^module\s+([^\s#]+)', re.MULTILINE)
GO_VERSION_RE = re.compile(r'^go (\d+\.\d+(?:\.\d+)?)$', re.MULTILINE)


def RunSteps(api):
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
        'infra_internal/dep/bcid.git/go.mod',
        'infra_internal/dep/bcid.git/proto-public/go.mod',
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
        'cloud.google.com/go/kms/apiv1/kmspb=cloud.google.com/go/kms/apiv1/kmspb@v1.23.0',
    ])

    api.step('generate go kzip', [
        kythe_bin, '--corpus',
        'chromium.googlesource.com/infra/infra_superproject//main',
        '--use_default_corpus_for_stdlib=true',
        '--use_default_corpus_for_deps=true', '--output', kzip_loc
    ] + targets)

  api.gsutil.upload(
      name='upload kythe index pack',
      source=kzip_loc,
      bucket='chrome-internal-codesearch' if internal else 'chrome-codesearch',
      dest='infra/%s' % kzip_name)


def GenTests(api):
  yield api.test(
      'basic',
      api.buildbucket.try_build(
          project='infra',
          builder='generic tester',
          git_repo='https://chromium.googlesource.com/infra/infra_superproject'
      ),
      api.platform('linux', 64),
      api.path.exists(
          api.path.cache_dir / 'infra_superproject',
          api.path.cache_dir / 'infra_superproject' /
          'infra/go/src/go.chromium.org/chromiumos/config/go/src/go.chromium.org/chromiumos/config/go/go.mod',
          api.path.cache_dir / 'infra_superproject' /
          'infra/go/src/go.chromium.org/chromiumos/infra/proto/go/go.mod',
          api.path.cache_dir / 'infra_superproject' /
          'infra/go/src/go.chromium.org/luci/go.mod',
          api.path.cache_dir / 'infra_superproject' /
          'infra/go/src/infra/go.mod',
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
      api.post_process(StepCommandContains, 'ensure_installed (2)',
                       ['infra/3pp/tools/go/${platform} version:3@1.26.2']),
      api.post_process(StepCommandContains, 'init go modules', [
          'go', 'work', 'init',
          'infra/go/src/go.chromium.org/chromiumos/config/go/src/go.chromium.org/chromiumos/config/go',
          'infra/go/src/go.chromium.org/chromiumos/infra/proto/go',
          'infra/go/src/go.chromium.org/luci', 'infra/go/src/infra',
      ]),
      api.post_process(StepCommandContains, 'generate go kzip', [
          'go.chromium.org/chromiumos/config/go/...',
          'go.chromium.org/chromiumos/infra/proto/go/...',
          'go.chromium.org/luci/...', 'go.chromium.org/infra/...',
      ]),
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
