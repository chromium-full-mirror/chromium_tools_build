# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Indexes Chromium source code into a CodeQL database and stores the result.

This requires compiling the Chromium `all` target before building the index.

Upload destination is specified by UPLOAD_BUCKET.
"""

from recipe_engine import post_process

DEPS = [
    "chromium_tests",
    "chromium_tests_builder_config",
    "depot_tools/gclient",
    "depot_tools/bot_update",
    'depot_tools/depot_tools',
    'depot_tools/gsutil',
    "build/chromium",
    "recipe_engine/cipd",
    "recipe_engine/context",
    "recipe_engine/path",
    "recipe_engine/time",
    "recipe_engine/step",
    "infra/zip",
    "reclient",
]

UPLOAD_BUCKET = 'chrome-codeql-databases'


def RunSteps(api):
  api.gclient.set_config("chromium")
  api.chromium.set_config()
  api.reclient.use_download_remoteexec_cfg_hook(api.gclient.c.solutions[0])
  api.bot_update.ensure_checkout()
  api.gclient.runhooks()
  out_dir = 'out'
  target_dir = "release"
  build_dir = out_dir + "/" + target_dir
  gn_path = api.depot_tools.gn_py_path
  cipd_root = api.path['start_dir'].join('cipd')
  db_path = api.path['start_dir'].join("codeql_db")
  with api.context(
      cwd=api.path['checkout'], env_suffixes={'PATH': [cipd_root]}):
    codeql_root = api.path['start_dir'].join('codeql')
    # If we end up needing to change this version frequently, consider changing
    # this to use an input property to set the version instead of hardcoding it
    # here.
    ensure_file = api.cipd.EnsureFile().add_package(
        'infra/3pp/tools/codeql/${platform}', "version:2@2.15.4")
    api.cipd.ensure(codeql_root, ensure_file)
    codeql_path = codeql_root.join('codeql')
    api.step(
        'gn gen out/release',
        ['python3', gn_path, 'gen', build_dir, '--args=use_remoteexec=true'])
    api.chromium.compile(
        use_reclient=True, targets=['all'], out_dir=out_dir, target=target_dir)
    codeql_script_path = api.path['checkout'].join('tools', 'codeql',
                                                   'index_target.py')
    api.step("index_target.py", [
        'vpython3', codeql_script_path, '--out_path', build_dir, '--db_path',
        db_path, '--codeql_binary_path', codeql_path
    ])
    zip_out_dir = api.path['start_dir'].join('codeql_output')
    cur_date = api.time.utcnow()
    zip_out_filename = 'codeql-' + cur_date.strftime(
        '%Y-%m-%d-%H:%M:%S') + '.zip'
    zip_out_file = zip_out_dir.join(zip_out_filename)
    api.step("mkdir codeql_output", ['mkdir', zip_out_dir])
    api.zip.directory('zip codeql dir', db_path, zip_out_file)
    api.gsutil.upload(
        zip_out_file, UPLOAD_BUCKET, zip_out_filename, link_name="CodeQL index")


def GenTests(api):
  yield api.test(
      "basic",
      api.post_process(post_process.DropExpectation),
  )
