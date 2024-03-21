# Copyright 2015 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Generates BoringSSL documentation and uploads it to Cloud Storage."""


DEPS = [
  'chromium',
  'depot_tools/bot_update',
  'depot_tools/gclient',
  'depot_tools/gsutil',
  'recipe_engine/buildbucket',
  'recipe_engine/context',
  'recipe_engine/path',
  'recipe_engine/properties',
  'recipe_engine/runtime',
  'recipe_engine/step',
]


def RunSteps(api):
  # Sync and pull in everything.
  api.gclient.set_config('boringssl')
  cache_dir = api.path['cache'].join('builder')
  with api.context(cwd=cache_dir):
    api.bot_update.ensure_checkout()
    api.gclient.runhooks()

  # Set up paths.
  util = cache_dir.join('boringssl', 'util')
  goroot = util.join('bot', 'golang')
  output = api.path.mkdtemp('boringssl-docs')

  # Generate and upload documentation.
  with api.context(
      cwd=util,
      env={'GOROOT': goroot},
      env_prefixes={'PATH': [goroot.join('bin')]}):
    api.step('generate', ['go', 'run', 'doc.go', '-out', output])
  # Upload docs only if run after commit and on not experimental builds.
  if api.buildbucket.build.builder.bucket == 'ci':
    if api.runtime.is_experimental:
      api.step('skipping uploading docs on experimental build', cmd=None)
    else:
      api.gsutil(['-m', 'cp', '-a', 'public-read', api.path.join(output, '**'),
                  'gs://chromium-boringssl-docs/'])


def GenTests(api):
  yield api.test(
      'docs',
      api.buildbucket.ci_build(
          project='boringssl',
          bucket='ci',
          builder='docs',
          git_repo='https://boringssl.googlesource.com/boringssl',
      ),
  )
  yield api.test(
      'docs-experimental',
      api.runtime(is_experimental=True),
      api.buildbucket.ci_build(
          project='boringssl',
          bucket='ci',
          builder='docs',
          git_repo='https://boringssl.googlesource.com/boringssl',
      ),
  )

  yield api.test(
      'docs-try',
      api.buildbucket.try_build(
          project='boringssl',
          bucket='try',
          builder='docs',
          git_repo='https://boringssl.googlesource.com/boringssl',
      ),
  )
