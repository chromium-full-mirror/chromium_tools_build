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
  c = api.gclient.make_config()
  soln = c.solutions.add()
  soln.name = 'build'
  soln.url = 'https://chromium.googlesource.com/build.git'
  soln.revision = 'HEAD'
  api.gclient.c = c

  with api.context(cwd=api.path.cache_dir):
    result = api.bot_update.ensure_checkout()
    kzip_name = '%s.kzip' % result.properties['got_revision']

  build_dir = api.path.cache_dir / 'build'
  kzip_loc = api.path.cache_dir / kzip_name
  go_mod_files = api.file.glob_paths(
      'find go.mod files',
      build_dir,
      '*/go.mod',
  )
  if not go_mod_files:
    raise api.step.StepFailure('No go.mod files found in the repository.')

  targets = []
  targets_dir = []
  go_version = '1.26'
  for mod_file in go_mod_files:
    mod_text = api.file.read_text('read %s' % mod_file, mod_file)
    match = MODULE_RE.search(mod_text)
    if not match:
      raise api.step.StepFailure('Did not detect Modules for %s' % mod_file)
    targets.append(match.group(1) + '/...')
    targets_dir.append(api.path.basename(api.path.dirname(mod_file)))
    match = GO_VERSION_RE.search(mod_text)
    if not match:
      raise api.step.StepFailure('Did not detect Go version for %s' % mod_file)
    go_version = max(go_version, match.group(1), key=parse)

  # KYTHE_ROOT_DIRECTORY makes sub modules relpath to build repo root.
  with api.golang(version=go_version), api.context(
      cwd=build_dir, env={'KYTHE_ROOT_DIRECTORY': build_dir}):
    # Without go.work we have to loop multiple directories and merge kzips.
    api.step('init go modules', ['go', 'work', 'init'] + targets_dir)
    api.step('generate go kzip', [
        kythe_bin, '--corpus', 'chromium.googlesource.com/build//main',
        '--use_default_corpus_for_stdlib=true',
        '--use_default_corpus_for_deps=true', '--output', kzip_loc
    ] + targets)

  api.gsutil.upload(
      name='upload kythe index pack',
      source=kzip_loc,
      bucket='chrome-codesearch',
      dest='build/%s' % kzip_name)


def GenTests(api):
  yield api.test(
      'basic',
      api.buildbucket.try_build(
          project='infra',
          builder='generic tester',
          git_repo='https://chromium.googlesource.com/build'),
      api.platform('linux', 64),
      api.path.exists(api.path.cache_dir / 'build'),
      api.step_data(
          'find go.mod files',
          api.file.glob_paths(['siso/go.mod', 'kajiya/go.mod',
                               'bench/go.mod'])),
      api.step_data(
          'read [CACHE]/build/bench/go.mod',
          api.file.read_text(
              textwrap.dedent('''
              module go.chromium.org/build/bench
              go 1.25.9

              tool (
                google.golang.org/a/foo
                google.golang.org/b/bar
              )''')),
      ),
      api.step_data(
          'read [CACHE]/build/siso/go.mod',
          api.file.read_text(
              textwrap.dedent('''
              module go.chromium.org/build/siso
              go 1.26.2

              tool (
                google.golang.org/a/foo
                google.golang.org/b/bar
              )''')),
      ),
      api.step_data(
          'read [CACHE]/build/kajiya/go.mod',
          api.file.read_text(
              textwrap.dedent('''
              module go.chromium.org/build/kajiya
              go 1.26.1

              tool (
                google.golang.org/a/foo
                google.golang.org/b/bar
              )''')),
      ),
      api.post_process(StepCommandContains, 'ensure_installed (2)',
                       ['infra/3pp/tools/go/${platform} version:3@1.26.2']),
      api.post_process(StepCommandContains, 'init go modules',
                       ['go', 'work', 'init', 'bench', 'kajiya', 'siso']),
      api.post_process(StepCommandContains, 'generate go kzip', [
          'go.chromium.org/build/kajiya/...', 'go.chromium.org/build/siso/...'
      ]),
      api.post_process(StatusSuccess),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'no_mod_files',
      api.buildbucket.try_build(
          project='infra',
          builder='generic tester',
          git_repo='https://chromium.googlesource.com/build'),
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
          git_repo='https://chromium.googlesource.com/build'),
      api.platform('linux', 64),
      api.path.exists(api.path.cache_dir / 'build'),
      api.step_data('find go.mod files', api.file.glob_paths([
          'siso/go.mod',
      ])),
      api.step_data(
          'read [CACHE]/build/siso/go.mod',
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
          git_repo='https://chromium.googlesource.com/build'),
      api.platform('linux', 64),
      api.path.exists(api.path.cache_dir / 'build'),
      api.step_data('find go.mod files', api.file.glob_paths([
          'siso/go.mod',
      ])),
      api.step_data(
          'read [CACHE]/build/siso/go.mod',
          api.file.read_text(
              textwrap.dedent('''
              module go.chromium.org/build/kajiya

              tool (
                google.golang.org/a/foo
                google.golang.org/b/bar
              )''')),
      ),
      api.post_process(StatusFailure),
      api.expect_status('FAILURE'),
      api.post_process(DropExpectation),
  )
