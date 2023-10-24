# Copyright 2019 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine.post_process import DropExpectation, Filter, MustRun

DEPS = [
    'depot_tools/bot_update',
    'depot_tools/depot_tools',
    'depot_tools/gclient',
    'depot_tools/osx_sdk',
    'reclient',
    'recipe_engine/buildbucket',
    'recipe_engine/context',
    'recipe_engine/file',
    'recipe_engine/path',
    'recipe_engine/platform',
    'recipe_engine/properties',
    'recipe_engine/step',
]

PROPERTIES = {
}

step_test_data = {
    "linux": {
        "build_steps": [{
            "name":
                "Build Wabt",
            "command": [
                "src/build.py", "--no-sync", "--no-test", "--build-include=wabt"
            ]
        }],
        "test_steps": [{
            "name":
                "Emscripten testsuite (upstream)",
            "command": [
                "src/build.py", "--no-sync", "--no-build",
                "--test-include=emtest"
            ]
        }, {
            "name":
                "Emscripten testsuite (asm2wasm)",
            "command": [
                "src/build.py", "--no-sync", "--no-build",
                "--test-include=emtest-asm"
            ]
        }]
    }
}


def ExecBuildSteps(api, build_steps, sync_dir, dir_flags):
  for step in build_steps:
    script = sync_dir.join(step['command'][0])
    args = step['command'][1:]
    api.step(step['name'], ['vpython3', script] + dir_flags + args)


def RunSteps(api):
  api.gclient.set_config('emscripten_releases')
  env = {
      'BUILDBOT_MASTERNAME': 'emscripten-releases',
      'BUILDBOT_BUILDERNAME': api.buildbucket.builder_name,
      'BUILDBOT_REVISION': api.buildbucket.gitiles_commit.id,
      'BUILDBOT_BUILDNUMBER': api.buildbucket.build.number,
      'BUILDBOT_BUCKET': api.buildbucket.build.builder.bucket,
  }

  env.update({'USE_RECLIENT': '1'})
  api.reclient.use_download_remoteexec_cfg_hook(api.gclient.c.solutions[0])

  cache_dir = api.path['cache'].join('builder')
  sync_dir = cache_dir.join('emscripten-releases')
  api.file.ensure_directory('Ensure sync dir', sync_dir)
  build_dir = cache_dir.join('emscripten-releases', 'build')
  install_dir = api.path['start_dir'].join('install')
  dir_flags = ['--sync-dir=%s' % sync_dir,
               '--build-dir=%s' % build_dir,
               '--prebuilt-dir=%s' % sync_dir,
               '--v8-dir=%s' % cache_dir.join('v8'),
               '--install-dir=%s' % install_dir]

  with api.osx_sdk('mac'):
    api.file.ensure_directory('Ensure install dir', install_dir)
    with api.context(cwd=cache_dir):
      api.bot_update.ensure_checkout()
      api.gclient.runhooks()

    api.reclient.reclient_dir = cache_dir.join('v8', 'buildtools')
    # Get list of build.py build and test steps
    bot_steps = api.file.read_json(
        'Read steps from JSON',
        sync_dir.join('bots.json'),
        test_data=step_test_data)

    builder = api.buildbucket.builder_name
    assert builder in ('linux', 'mac', 'win', 'linux-test-suites')

    # Depot tools on path is for gsutil.py.
    with api.depot_tools.on_path(), api.context(env=env):
      build_steps = bot_steps[builder]['build_steps']
      with api.reclient.process('compile', '', False):
        ExecBuildSteps(api, build_steps, sync_dir, dir_flags)

      with api.step.defer_results():
        for step in bot_steps[builder]['test_steps']:
          script = sync_dir.join(step['command'][0])
          args = step['command'][1:]
          api.step(step['name'], ['vpython3', script] + dir_flags + args)


def GenTests(api):

  def build():
    return api.buildbucket.ci_build(
        project='emscripten-releases',
        builder='linux',
        build_number=42,
    ) + api.reclient.properties()

  yield api.test(
      'linux',
      build(),
  )

  yield api.test(
      'linux_buildfail',
      build(),
      api.step_data('Build Wabt', retcode=1),
      api.post_process(
          Filter('postprocess for reclient.cleanup reclient log dir')),
      api.expect_status('FAILURE'),
  )

  # Check that if the first test fails, the second runs but the overall
  # result is failure.
  yield api.test(
      'linux_emtest_fail',
      build(),
      api.step_data('Emscripten testsuite (upstream)', retcode=1) +
      api.post_process(Filter('Emscripten testsuite (asm2wasm)', '$result')),
      api.expect_status('FAILURE'),
  )

  yield api.test(
      'mac',
      build(),
      api.platform.name('mac'),
  )
