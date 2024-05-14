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
    "recipe_engine/file",
    "recipe_engine/path",
    "recipe_engine/raw_io",
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
  update_result = api.bot_update.ensure_checkout()
  source_dir = update_result.source_root.path
  api.gclient.runhooks()
  out_dir = 'out'
  target_dir = "release"
  build_dir = out_dir + "/" + target_dir
  gn_path = api.depot_tools.gn_py_path
  cipd_root = api.path.start_dir / 'cipd'
  db_path = api.path.mkdtemp('codeql_dbs')
  with api.context(cwd=source_dir, env_suffixes={'PATH': [cipd_root]}):
    codeql_root = api.path.start_dir / 'codeql'
    # If we end up needing to change this version frequently, consider changing
    # this to use an input property to set the version instead of hardcoding it
    # here.
    ensure_file = api.cipd.EnsureFile().add_package(
        'infra/3pp/tools/codeql/${platform}', "version:2@2.15.4")
    api.cipd.ensure(codeql_root, ensure_file)
    codeql_path = codeql_root / 'codeql'
    api.step(
        'gn gen out/release',
        ['python3', gn_path, 'gen', build_dir, '--args=use_remoteexec=true'])
    api.chromium.compile(
        use_reclient=True, targets=["all"], out_dir=out_dir, target=target_dir)
    codeql_script_path = source_dir.joinpath('tools', 'codeql',
                                             'index_target.py')
    api.step("index_target.py", [
        'vpython3', codeql_script_path, '--out_path', build_dir, '--db_path',
        db_path, '--codeql_binary_path', codeql_path, '--gn_path', gn_path
    ])

    zip_out_dir = api.path.start_dir / 'codeql_output'
    api.step("mkdir codeql_output", ['mkdir', zip_out_dir])
    cur_date = api.time.utcnow()
    TEST_DATA = ['chrome']
    db_list = api.file.listdir(
        "get list of codeql db paths", db_path, test_data=TEST_DATA)
    for target_db_path in db_list:
      target_db_basename = api.path.basename(target_db_path)
      zip_out_filename = target_db_basename + '-codeql-' + cur_date.strftime(
          '%Y-%m-%d-%H:%M:%S') + '.zip'
      zip_out_file = zip_out_dir / zip_out_filename
      api.zip.directory('zip codeql dir', db_path, zip_out_file)
    cloud_folder_name = "codeql-" + cur_date.strftime('%Y-%m-%d-%H:%M:%S')
    api.gsutil.upload(
        zip_out_dir,
        UPLOAD_BUCKET,
        cloud_folder_name,
        args=['-r'],
        link_name='CodeQL indices')


def GenTests(api):
  yield api.test(
      "basic",
      api.post_process(post_process.DropExpectation),
  )
