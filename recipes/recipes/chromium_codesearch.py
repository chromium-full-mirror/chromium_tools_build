# Copyright 2014 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import re

from recipe_engine import post_process
from recipe_engine.engine_types import freeze
from recipe_engine.post_process import (DoesNotRun, MustRun,
                                        StepCommandContains, StepTextEquals,
                                        StatusSuccess)
from RECIPE_MODULES.build import chromium_types

from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb
from PB.recipes.build.chromium_codesearch import (InputProperties,
                                                  RecipeProperties)

PROPERTIES = InputProperties

DEPS = [
    'chromium',
    'gn',
    'infra/codesearch',
    'depot_tools/bot_update',
    'depot_tools/depot_tools',
    'depot_tools/gclient',
    'depot_tools/git',
    'depot_tools/gsutil',
    'depot_tools/tryserver',
    'recipe_engine/buildbucket',
    'recipe_engine/cipd',
    'recipe_engine/commit_position',
    'recipe_engine/context',
    'recipe_engine/file',
    'recipe_engine/json',
    'recipe_engine/path',
    'recipe_engine/platform',
    'recipe_engine/properties',
    'recipe_engine/raw_io',
    'recipe_engine/step',
    'recipe_engine/time',
    'siso',
]

# Regular expression to identify a Git hash.
GIT_COMMIT_HASH_RE = re.compile(r'[a-zA-Z0-9]{40}')

# Regular expression for filenames produced by `kythe view -extract`.
KYTHE_CU_FILENAME_RE = re.compile(r'.+-([0-9a-f]+)\.unit$')

# Allowlist for smaller kzip with minimal set of Rust CUs before wider rollout.
KYTHE_RUST_CU_ALLOWLIST = [
    # build/rust/tests/test_proc_macro_crate/crate/src/lib.rs (ios)
    "de9204a1600b94dd644c515e73c84feaf35617a8138b975e29cb828326a109d3",
]

# Defines the trybots and the mirrored CI builder
# The trybot will use the parameters from the mirrored CI builder.
# It has the following strcture:
# {
#   <group which contains the trybots>: {
#     <trybot name>: <mirred CI builder name, which should be one of builders
#                     from SPEC['builders']>,
#   }
# }
TRYBOT_SPEC = freeze({
    'tryserver.chromium.codesearch': {
        'gen-android-try': 'codesearch-gen-chromium-android',
        'gen-chromiumos-try': 'codesearch-gen-chromium-chromiumos',
        'gen-fuchsia-try': 'codesearch-gen-chromium-fuchsia',
        'gen-ios-try': 'codesearch-gen-chromium-ios',
        'gen-lacros-try': 'codesearch-gen-chromium-lacros',
        'gen-linux-try': 'codesearch-gen-chromium-linux',
        'gen-mac-try': 'codesearch-gen-chromium-mac',
        'gen-webview-try': 'codesearch-gen-chromium-webview',
        'gen-win-try': 'codesearch-gen-chromium-win',
    }
})


def _get_revision(api):  # pragma: no cover
  """Returns the git commit hash of the project.
  """
  commit = api.chromium.build_properties.get('got_revision')
  if commit and GIT_COMMIT_HASH_RE.match(commit):
    return commit


def _get_commit_position(api):
  """Returns the commit position of the project.
  """
  got_revision_cp = api.chromium.build_properties.get('got_revision_cp')
  if not got_revision_cp:
    # For some downstream bots, the build properties 'got_revision_cp' are not
    # generated. To resolve this issue, use 'got_revision' property here
    # instead.
    return _get_revision(api)  # pragma: no cover
  _, rev = api.commit_position.parse(got_revision_cp)
  return rev


def generate_compilation_database(api, source_dir, out_path,
                                  compile_commands_json_file, targets):
  try:
    step_result = api.step('generate compilation database', [
        'python3', '-u',
        source_dir.joinpath('tools', 'clang', 'scripts', 'generate_compdb.py'),
        '-p', out_path, '-o', compile_commands_json_file
    ] + list(targets))
  except api.step.StepFailure as e:
    raise e
  return step_result


def generate_gn_metadata(api,
                         source_dir,
                         out_path,
                         export_compile_cmd=False,
                         export_compile_targets=None):
  """Generates metadata using gn gen.
  """
  with api.context(cwd=source_dir, env=api.chromium.get_env(source_dir)):
    cmd = [
        'python3', '-u', api.depot_tools.gn_py_path, 'gen',
        '--export-rust-project'
    ]
    if export_compile_cmd:
      export_compile_flag = '--export-compile-commands'
      if export_compile_targets:
        export_compile_flag += '=' + ','.join(export_compile_targets)
      cmd.append(export_compile_flag)
    cmd.append(out_path)
    api.step('run gn gen', cmd, stdout=api.raw_io.output_text())


def generate_gn_target_list(api,
                            source_dir,
                            out_path,
                            gn_targets_json_file,
                            targets=None):
  with api.context(cwd=source_dir, env=api.chromium.get_env(source_dir)):
    targets_cmd = '*'
    if targets:
      targets_cmd = ' '.join(targets)
    output = api.step(
        'generate gn target list', [
            'python3', '-u', api.depot_tools.gn_py_path, 'desc', out_path,
            targets_cmd, '--format=json'
        ],
        stdout=api.raw_io.output_text()).stdout
  api.file.write_raw('write gn target list', gn_targets_json_file, output)


def extract_minimal_rust_kzip(api, rust_index_pack_path):
  """Extracts a minimal .kzip from the full Rust .kzip.

  The full Rust .kzip is known to crash the Kythe Rust ingester, so in order
  to have some Rust xrefs at all, extract a minimal .kzip with a known safe CU
  so that we start feeding the Kythe Rust pipeline.

  Over time this function should be deleted as we discover the root cause
  of the ingestion crashes and safely increase the amount of CUs we send to
  Kythe.
  TODO(b/420540280): Debug the Rust kzip issues so this function can be
  deleted.

  Because we don't want this function to prevent the rest of .kzip generation
  to work, skip remaining work and return None there's any issues.
  """
  kzip_bin = api.codesearch.ensure_kythe().joinpath(
      'tools',
      'kzip') if api.platform.is_linux else api.codesearch.ensure_kythe_go(
      ).joinpath('kzip')

  # Workaround for `kzip view` (without -extract) not outputting the digests
  # for the CUs themselves.
  # Because this entire function should not exist if the Rust indexer pipeline
  # was able to ingest the full Rust .kzip, we consider this tradeoff
  # acceptable (adds ~1min to a 1.5-2h builder)
  extract_cus_dir = api.path.mkdtemp()
  extract_cus_args = [
      kzip_bin,
      'view',
      '-extract',
      extract_cus_dir,
      rust_index_pack_path,
  ]
  try:
    api.step('extract CUs', extract_cus_args)
  except api.step.StepFailure as e:
    e.result.presentation.step_text = 'kzip view failed, skipping'
    return None

  cu_files = api.file.glob_paths('list CU metadata', extract_cus_dir, '*.unit')
  if not cu_files:
    api.step.empty('no CUs found, skipping')
    return None

  allowed_cus = []
  for cu_file in cu_files:
    cu_filename_match = KYTHE_CU_FILENAME_RE.match(cu_file.name)
    if not cu_filename_match:
      continue
    digest = cu_filename_match.group(1)
    if digest in KYTHE_RUST_CU_ALLOWLIST:
      allowed_cus.append(digest)

  if not allowed_cus:
    api.step.empty('no allowed CUs found, skipping')
    return None

  # Create a minimal kzip with only this CU.
  filtered_kzip_path = api.path.mkstemp()
  filter_kzip_args = [
      kzip_bin,
      'filter',
      '-input',
      rust_index_pack_path,
      '-output',
      filtered_kzip_path,
  ] + allowed_cus
  try:
    api.step('create minimal kzip', filter_kzip_args)
  except api.step.StepFailure as e:
    e.result.presentation.step_text = 'kzip filter failed, skipping'
    return None
  return filtered_kzip_path


def RunSteps(api, properties):
  name_suffix = ''
  builder_id = api.chromium.get_builder_id()
  if api.tryserver.is_tryserver:
    name_suffix = ' (with patch)'
    builder = TRYBOT_SPEC.get(builder_id.group, {}).get(builder_id.builder)
    assert builder is not None, ('Could not find trybot %s:%s in TRYBOT_SPEC' %
                                 (builder_id.group, builder_id.builder))
  else:
    builder = builder_id.builder

  bot_config = properties.recipe_properties
  assert bot_config is not None, (
      'Could not find "recipe_properties" input property')
  platform = bot_config.platform or 'linux'
  experimental = bot_config.experimental or False
  corpus = bot_config.corpus or 'chromium-linux'
  build_config = bot_config.build_config or ''
  # compile_targets is of type RepeatedScalarFieldContainer.
  targets = list(bot_config.compile_targets or [])

  gen_repo_branch = bot_config.gen_repo_branch or 'main'
  gen_repo_out_dir = bot_config.gen_repo_out_dir or 'Debug'
  internal = bot_config.internal or False

  project = 'chromium' if not internal else 'chrome'
  api.codesearch.set_config(
      project,
      PROJECT=project,
      PLATFORM=platform,
      EXPERIMENTAL=experimental,
      SYNC_GENERATED_FILES=bot_config.sync_generated_files,
      GEN_REPO_BRANCH=gen_repo_branch,
      GEN_REPO_OUT_DIR=gen_repo_out_dir,
      CORPUS=corpus,
      BUILD_CONFIG=build_config,
  )

  # Checkout the repositories that are needed for the compile.
  gclient_config = api.gclient.make_config('chromium')
  target_os = 'linux'
  host_os = 'linux'
  if platform in ('android', 'webview', 'cronet'):
    target_os = 'android'
  elif platform in ('chromeos', 'lacros'):
    target_os = 'chromeos'
  elif platform == 'fuchsia':
    target_os = 'fuchsia'
  elif platform == 'mac':
    target_os = 'mac'
    host_os = 'mac'
  elif platform == 'ios':
    target_os = 'ios'
    host_os = 'mac'
  elif platform in ('win', 'windows'):
    target_os = 'win'
    host_os = 'win'

  gclient_config.target_os = [target_os]
  api.gclient.c = gclient_config

  api.gclient.apply_config('android_prebuilts_build_tools')

  if internal:
    api.gclient.apply_config('chrome_internal')

  if api.siso.enabled:
    api.siso.enable_download_remoteexec_cfg_hook()

  checkout_dir = api.path.cache_dir / 'builder'
  with api.context(cwd=checkout_dir, env={'PACKFILE_OFFLOADING': 1}):
    update_result = api.bot_update.ensure_checkout(
        root_solution_revision=properties.root_solution_revision)
  api.chromium.set_build_properties(update_result.properties)
  source_dir = update_result.source_root.path
  build_dir = source_dir / 'out' / gen_repo_out_dir
  compile_commands_json_file = build_dir / 'compile_commands.json'
  gn_targets_json_file = build_dir / 'gn_targets.json'

  api.chromium.set_config(
      'codesearch',
      BUILD_CONFIG='Debug',
      TARGET_PLATFORM=target_os,
      HOST_PLATFORM=host_os)

  if target_os == 'ios':
    api.chromium.ensure_toolchains(checkout_dir)

  # CHROME_HEADLESS makes sure that running 'gclient runhooks' doesn't require
  # entering 'y' to agree to a license.
  with api.context(env={'CHROME_HEADLESS': '1'}):
    api.chromium.runhooks(
        source_dir, build_dir, name='runhooks%s' % name_suffix)

  # Cleans up generated files. This is to prevent old generated files from
  # being left in the out directory. Note that this needs to be run *before*
  # generating the compilation database, otherwise some of the files generated
  # by that step may be deleted (if they've been unchanged for the past week).
  api.codesearch.cleanup_old_generated()

  api.chromium.mb_gen(
      source_dir,
      build_dir,
      chromium_types.BuilderId.create_for_group(builder_id.group,
                                                builder_id.builder),
      name='generate build files')

  if platform == 'webview':
    # Experiment to use gn gen to generate compilation database, it supports
    # target list.
    # GN target format is different than ninja's, we might need a dedicated GN
    # target list.
    webview_gn_targets = ['//android_webview:system_webview_apk']
    generate_gn_metadata(
        api=api,
        source_dir=source_dir,
        out_path=build_dir,
        export_compile_cmd=True,
        export_compile_targets=targets)
    generate_gn_target_list(api, source_dir, build_dir, gn_targets_json_file,
                            webview_gn_targets)
  else:
    generate_compilation_database(
        api=api,
        source_dir=source_dir,
        out_path=build_dir,
        compile_commands_json_file=compile_commands_json_file,
        targets=targets)
    generate_gn_metadata(api=api, source_dir=source_dir, out_path=build_dir)
    generate_gn_target_list(api, source_dir, build_dir, gn_targets_json_file)

  # Prepare Java Kythe output directory
  kzip_dir = api.codesearch.c.javac_extractor_output_dir
  api.file.ensure_directory('java kzip', kzip_dir)

  # If the compile fails, abort execution and don't upload the pack. When we
  # upload an incomplete (due to compile failures) pack to Kythe, it fails
  # validation and doesn't get pushed out anyway, so there's no point in
  # uploading at all.
  with api.context(
      env={
          'KYTHE_ROOT_DIRECTORY': source_dir,
          'KYTHE_OUTPUT_DIRECTORY': kzip_dir,
          'KYTHE_CORPUS': corpus
      }):
    raw_result = api.chromium.compile(
        source_dir, build_dir, targets=targets, name='compile%s' % name_suffix)
  if raw_result.status != common_pb.SUCCESS:
    return raw_result

  # Process annotations and add kythe metadata.
  api.codesearch.add_kythe_metadata()

  # Create the initial kythe index pack.
  initial_index_pack_path = api.codesearch.create_kythe_index_pack(
      use_siso=True)

  # Create the Rust index pack on supported platforms.
  # The extractor is currently only built for Linux and Mac.
  rust_index_pack_path = None
  if target_os in ('linux', 'mac', 'ios'):
    with api.step.nest('create rust kzip'):
      rust_index_pack_path = api.codesearch.run_rust_project_extractor(
          source_dir=source_dir)
      # On iOS, reduce this full kzip to a minimal kzip.
      # (This means the full kzip will be merged on Linux and Mac.)
      if target_os == 'ios':
        rust_index_pack_path = extract_minimal_rust_kzip(
            api, rust_index_pack_path)

  # Run Python extractor. Since it's universal, choose a platform with
  # the most light-weight tasks.
  python_kzip_path = None
  if platform == 'webview':
    try:
      extractor_out_dir = api.path.mkstemp()
      extractor_script = api.codesearch.resource('python_extractor.py')

      out_json_path = extractor_out_dir / 'python_metadata.json'
      api.step('extract python metadata', [
          'vpython3',
          extractor_script,
          checkout_dir,
          out_json_path,
          '--corpus',
          corpus,
          '--root',
          checkout_dir,
      ])

      exec_path = api.cipd.ensure_tool("infra/tools/kzip_builder/${platform}",
                                       "latest")
      python_kzip_path = extractor_out_dir / 'python_kzip.kzip'
      api.step('build python kzip', [
          exec_path, '--output', python_kzip_path, '--root', checkout_dir,
          out_json_path
      ])
    except api.step.StepFailure as exc:
      python_kzip_path = None
      if api.step.active_result:
        result = api.step.active_result
        result.presentation.step_text = 'python kzip extraction failed, skipping'
        result.presentation.logs['exception'] = str(exc).splitlines()

  # Merge the index packs when available.
  final_index_pack_path = initial_index_pack_path
  additional_kzips = [
      kzip for kzip in (rust_index_pack_path, python_kzip_path) if kzip
  ]
  if additional_kzips:
    final_index_pack_path = api.codesearch.run_kzip_merge(
        initial_index_pack_path, *additional_kzips)

  # Early return if it's tryserver, don't upload and sync repo.
  if api.tryserver.is_tryserver:
    return

  # Upload the initial kythe index pack.
  api.codesearch.upload_kythe_index_pack(
      index_pack_kythe_path=final_index_pack_path,
      commit_hash=properties.codesearch_mirror_revision or _get_revision(api),
      commit_timestamp=int(properties.codesearch_mirror_revision_timestamp or
                           properties.root_solution_revision_timestamp or
                           api.time.time()),
      commit_position=_get_commit_position(api))

  # Check out the generated files repo and sync the generated files
  # into this checkout. This may fail due to other builders pushing to the
  # remote repo at the same time, so we retry this 3 times before giving up.
  copy_config = {
      source_dir.joinpath('out', gen_repo_out_dir):
          api.path.join(gen_repo_out_dir)
  }
  _RunStepWithRetry(
      api, lambda: api.codesearch.checkout_generated_files_repo_and_sync(
          copy_config, _get_revision(api)))


def _RunStepWithRetry(api, step_function, max_tries=3):
  failures = 0
  while failures < max_tries:
    try:
      step_function()
      break
    except api.step.StepFailure as f:
      failures += 1
      if failures == max_tries:
        raise # pragma: no cover

      api.step.active_result.presentation.step_text = f.reason_message()
      api.step.active_result.presentation.status = api.step.WARNING


def _sanitize_nonalpha(text):
  return ''.join(c if c.isalnum() else '_' for c in text)


def _format_builder_name(platform, internal=False):
  name = 'codesearch-gen-chromium-%s' % platform
  if internal:
    name += '-internal'
  return name


SAMPLE_GN_DESC_OUTPUT = '''
{
   "//ipc:mojom_constants__generator": {
      "args": [ "--use_bundled_pylibs", "generate", "-d", "../../", "-I", "../../", "-o", "gen", "--bytecode_path", "gen/mojo/public/tools/bindings", "--filelist={{response_file_name}}", "-g", "c++", "--typemap", "gen/ipc/mojom_constants__type_mappings" ],
      "deps": [ "//ipc:mojom_constants__parsed", "//ipc:mojom_constants__type_mappings", "//ipc:mojom_constants__verify_deps", "//mojo/public/tools/bindings:precompile_templates" ],
      "inputs": [ "//mojo/public/tools/bindings/generators/mojom_cpp_generator.py" ],
      "outputs": [ "//out/Debug/gen/ipc/constants.mojom.cc", "//out/Debug/gen/ipc/constants.mojom.h", "//out/Debug/gen/ipc/constants.mojom-test-utils.cc", "//out/Debug/gen/ipc/constants.mojom-test-utils.h" ],
      "public": "*",
      "script": "//mojo/public/tools/bindings/mojom_bindings_generator.py",
      "sources": [ "//ipc/constants.mojom" ],
      "testonly": false,
      "toolchain": "//build/toolchain/linux:clang_x64",
      "type": "action",
      "visibility": [ "//ipc:*" ]
   }
}
'''

def GenTests(api):

  def props(platform, internal=False):
    return api.properties(
        root_solution_revision='HEAD',
        root_solution_revision_timestamp=1337000001,
        xcode_build_version="123",
        recipe_properties=RecipeProperties(
            compile_targets=['all'],
            platform=platform,
            experimental=False,
            sync_generated_files=True,
            corpus='chromium.googlesource.com/chromium/src',
            build_config=platform,
            gen_repo_branch='main',
            gen_repo_out_dir='%s-Debug' % platform,
            internal=internal,
        )) + api.siso.properties()

  for platform in ('android', 'lacros', 'linux', 'fuchsia', 'chromiumos', 'mac',
                   'ios', 'win', 'webview'):
    for internal in (True, False):
      buildername = _format_builder_name(platform, internal)
      platform_map = {'win': 'win', 'mac': 'mac', 'ios': 'mac'}
      yield api.test(
          'full_%s' % (_sanitize_nonalpha(buildername)),
          api.platform(platform_map.get(platform, 'linux'), 64),
          props(platform, internal),
          api.chromium.generic_build(builder=buildername),
          api.step_data('generate gn target list',
                        api.raw_io.stream_output_text(SAMPLE_GN_DESC_OUTPUT)),
      )

      yield api.test(
          'full_%s_with_revision' % (_sanitize_nonalpha(buildername)),
          api.platform(platform_map.get(platform, 'linux'), 64),
          props(platform, internal),
          api.chromium.generic_build(builder=buildername),
          api.step_data('generate gn target list',
                        api.raw_io.stream_output_text(SAMPLE_GN_DESC_OUTPUT)),
          api.properties(
              root_solution_revision='a' * 40,
              root_solution_revision_timestamp=1531887759),
      )

  yield api.test(
      'full_%s_with_patch' % _sanitize_nonalpha('gen-linux-try'),
      api.platform('linux', 64),
      props('linux'),
      api.chromium.try_build(
          builder_group='tryserver.chromium.codesearch',
          builder='gen-linux-try'),
      api.step_data('generate gn target list',
                    api.raw_io.stream_output_text(SAMPLE_GN_DESC_OUTPUT)),
  )

  yield api.test(
      'full_%s_delete_generated_files_fail' %
      _sanitize_nonalpha('codesearch-gen-chromium-win'),
      api.platform('win', 64),
      props('win'),
      api.chromium.generic_build(builder='codesearch-gen-chromium-win'),
      api.step_data('delete old generated files', retcode=1),
      api.step_data('generate gn target list',
                    api.raw_io.stream_output_text(SAMPLE_GN_DESC_OUTPUT)),
  )

  yield api.test(
      'full_%s_compile_fail' %
      _sanitize_nonalpha('codesearch-gen-chromium-linux'),
      props('linux'),
      api.chromium.generic_build(builder='codesearch-gen-chromium-linux'),
      api.step_data('compile', retcode=1),
      api.expect_status('FAILURE'),
  )

  yield api.test(
      'full_%s_last_compile_fail' %
      _sanitize_nonalpha('codesearch-gen-chromium-linux'),
      props('linux'),
      api.chromium.generic_build(builder='codesearch-gen-chromium-linux'),
      api.path.exists(api.path.cache_dir.joinpath('builder', 'cr-cs-sentinel')),
  )

  yield api.test(
      'full_%s_translation_unit_fail' %
      _sanitize_nonalpha('codesearch-gen-chromium-chromiumos'),
      props('chromiumos'),
      api.chromium.generic_build(builder='codesearch-gen-chromium-chromiumos'),
      api.step_data('generate gn target list',
                    api.raw_io.stream_output_text(SAMPLE_GN_DESC_OUTPUT)),
  )

  yield api.test(
      'full_%s_generate_compile_database_fail' %
      _sanitize_nonalpha('codesearch-gen-chromium-chromiumos'),
      props('chromiumos'),
      api.chromium.generic_build(builder='codesearch-gen-chromium-chromiumos'),
      api.step_data('generate compilation database', retcode=1),
      api.expect_status('FAILURE'),
  )

  yield api.test(
      'full_%s_git_config_fail' %
      _sanitize_nonalpha('codesearch-gen-chromium-win'),
      api.platform('win', 64),
      props('win'),
      api.chromium.generic_build(builder='codesearch-gen-chromium-win'),
      api.step_data('set core.longpaths', retcode=1),
      api.step_data('generate gn target list',
                    api.raw_io.stream_output_text(SAMPLE_GN_DESC_OUTPUT)),
  )

  yield api.test(
      'full_%s_sync_generated_files_fail' %
      _sanitize_nonalpha('codesearch-gen-chromium-linux'),
      props('linux'),
      api.chromium.generic_build(builder='codesearch-gen-chromium-linux'),
      api.step_data('sync generated files', retcode=1),
      api.step_data('generate gn target list',
                    api.raw_io.stream_output_text(SAMPLE_GN_DESC_OUTPUT)),
  )

  yield api.test(
      'rust_kzip_ignore_extract_cus_fail',
      api.platform('mac', 64),
      props('ios'),
      api.chromium.generic_build(builder='codesearch-gen-chromium-ios'),
      api.step_data('create rust kzip.extract CUs', retcode=1),
      api.post_process(StepTextEquals, 'create rust kzip.extract CUs',
                       'kzip view failed, skipping'),
      api.post_process(DoesNotRun, ('create rust kzip.'
                                    'create minimal kzip')),
      api.post_process(DoesNotRun, 'merge kzips'),
      api.post_process(
          StepCommandContains,
          'gsutil upload kythe index pack',
          # Non-merged original package_index output file.
          ['[CLEANUP]/tmp_tmp_1']),
      api.post_process(StatusSuccess),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'rust_kzip_ignore_zero_rust_cus',
      api.platform('mac', 64),
      props('ios'),
      api.chromium.generic_build(builder='codesearch-gen-chromium-ios'),
      api.step_data('create rust kzip.list CU metadata',
                    api.file.glob_paths([])),
      api.post_process(MustRun, 'create rust kzip.no CUs found, skipping'),
      api.post_process(DoesNotRun, ('create rust kzip.'
                                    'create minimal kzip')),
      api.post_process(DoesNotRun, 'merge kzips'),
      api.post_process(
          StepCommandContains,
          'gsutil upload kythe index pack',
          # Non-merged original package_index output file.
          ['[CLEANUP]/tmp_tmp_1']),
      api.post_process(StatusSuccess),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'rust_kzip_ignore_no_allowed_cus',
      api.platform('mac', 64),
      props('ios'),
      api.chromium.generic_build(builder='codesearch-gen-chromium-ios'),
      api.step_data(
          'create rust kzip.list CU metadata',
          api.file.glob_paths([
              '/tmp/hello-0123456789abcdef.txt',
              '/tmp/foo-0123456789abcdef.unit'
          ])),
      api.post_process(MustRun, ('create rust kzip.'
                                 'no allowed CUs found, skipping')),
      api.post_process(DoesNotRun, ('create rust kzip.'
                                    'create minimal kzip')),
      api.post_process(DoesNotRun, 'merge kzips'),
      api.post_process(
          StepCommandContains,
          'gsutil upload kythe index pack',
          # Non-merged original package_index output file.
          ['[CLEANUP]/tmp_tmp_1']),
      api.post_process(StatusSuccess),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'rust_kzip_filter_ignore_filter_fail',
      api.platform('mac', 64),
      props('ios'),
      api.chromium.generic_build(builder='codesearch-gen-chromium-ios'),
      api.step_data(
          'create rust kzip.list CU metadata',
          api.file.glob_paths([
              '/tmp/foo-de9204a1600b94dd644c515e73c84feaf35617a8138b975e29cb828326a109d3.unit'
          ])),
      api.step_data('create rust kzip.create minimal kzip', retcode=1),
      api.post_process(StepTextEquals, ('create rust kzip.'
                                        'create minimal kzip'),
                       'kzip filter failed, skipping'),
      api.post_process(DoesNotRun, 'merge kzips'),
      api.post_process(
          StepCommandContains,
          'gsutil upload kythe index pack',
          # Non-merged original package_index output file.
          ['[CLEANUP]/tmp_tmp_1']),
      api.post_process(StatusSuccess),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'rust_kzip_success_linux',
      api.platform('linux', 64),
      props('linux'),
      api.chromium.generic_build(builder='codesearch-gen-chromium-linux'),
      api.post_process(MustRun, 'create rust kzip.extract Rust kzips'),
      api.post_process(DoesNotRun, 'create rust kzip.create minimal kzip'),
      api.post_process(MustRun, 'merge kzips'),
      api.post_process(MustRun, 'get c++ dependencies from siso'),
      api.post_process(StepCommandContains, 'merge kzips',
                       ['--output', '[CLEANUP]/tmp_tmp_3']),
      api.post_process(
          StepCommandContains,
          'gsutil upload kythe index pack',
          # Note how this path corresponds to the merge kzip output.
          ['[CLEANUP]/tmp_tmp_3']),
      api.post_process(StatusSuccess),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'rust_kzip_success_mac',
      api.platform('mac', 64),
      props('mac'),
      api.chromium.generic_build(builder='codesearch-gen-chromium-mac'),
      api.post_process(MustRun, 'create rust kzip.extract Rust kzips'),
      api.post_process(DoesNotRun, 'create rust kzip.create minimal kzip'),
      api.post_process(MustRun, 'merge kzips'),
      api.post_process(StepCommandContains, 'merge kzips',
                       ['--output', '[CLEANUP]/tmp_tmp_3']),
      api.post_process(
          StepCommandContains,
          'gsutil upload kythe index pack',
          # Note how this path corresponds to the merge kzip output.
          ['[CLEANUP]/tmp_tmp_3']),
      api.post_process(StatusSuccess),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'rust_kzip_success_ios',
      api.platform('mac', 64),
      props('ios'),
      api.chromium.generic_build(builder='codesearch-gen-chromium-ios'),
      api.step_data(
          'create rust kzip.list CU metadata',
          api.file.glob_paths([
              '/tmp/foo-0123456789abcdef.unit',
              '/tmp/foo-de9204a1600b94dd644c515e73c84feaf35617a8138b975e29cb828326a109d3.unit'
          ])),
      api.step_data('create rust kzip.create minimal kzip'),
      api.post_process(MustRun, 'create rust kzip.create minimal kzip'),
      api.post_process(MustRun, 'merge kzips'),
      api.post_process(StepCommandContains, 'merge kzips',
                       ['--output', '[CLEANUP]/tmp_tmp_5']),
      api.post_process(
          StepCommandContains,
          'gsutil upload kythe index pack',
          # Note how this path corresponds to the merge kzip output.
          ['[CLEANUP]/tmp_tmp_5']),
      api.post_process(StatusSuccess),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'rust_kzip_disabled_android',
      api.platform('linux', 64),
      props('android'),
      api.chromium.generic_build(builder='codesearch-gen-chromium-android'),
      api.post_process(DoesNotRun,
                       'create minimal rust kzip.create minimal kzip'),
      api.post_process(DoesNotRun, 'merge kzips'),
      api.post_process(
          StepCommandContains,
          'gsutil upload kythe index pack',
          # Non-merged original package_index output file.
          ['[CLEANUP]/tmp_tmp_1']),
      api.post_process(StatusSuccess),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'python_kzip_ignore_extract_fail',
      api.platform('linux', 64),
      props('webview'),
      api.chromium.generic_build(builder='codesearch-gen-chromium-webview'),
      api.step_data('extract python metadata', retcode=1),
      api.post_process(StepTextEquals, 'extract python metadata',
                       'python kzip extraction failed, skipping'),
      api.post_process(DoesNotRun, 'build python kzip'),
      api.post_process(DoesNotRun, 'merge kzips'),
      api.post_process(
          StepCommandContains,
          'gsutil upload kythe index pack',
          # Non-merged original package_index output file.
          ['[CLEANUP]/tmp_tmp_1']),
      api.post_process(StatusSuccess),
      api.post_process(post_process.DropExpectation),
  )
