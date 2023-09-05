# Copyright 2014 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import re

from recipe_engine import post_process
from recipe_engine.engine_types import freeze
from RECIPE_MODULES.build import chromium

from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb
from PB.recipes.build.chromium_codesearch import (InputProperties,
                                                  RecipeProperties)

PROPERTIES = InputProperties

DEPS = [
    'build',
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
    'recipe_engine/commit_position',
    'recipe_engine/context',
    'recipe_engine/file',
    'recipe_engine/json',
    'recipe_engine/path',
    'recipe_engine/properties',
    'recipe_engine/raw_io',
    'recipe_engine/step',
    'recipe_engine/time',
    'reclient',
]

# Regular expression to identify a Git hash.
GIT_COMMIT_HASH_RE = re.compile(r'[a-zA-Z0-9]{40}')

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


def _use_reclient(api, gn_args):
  args = api.gn.parse_gn_args(gn_args)
  return args.get('use_remoteexec') == 'true'


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


def generate_compilation_database(api, out_path, compile_commands_json_file,
                                  targets):
  try:
    step_result = api.step('generate compilation database', [
        'python3', '-u', api.path['checkout'].join(
            'tools', 'clang', 'scripts', 'generate_compdb.py'), '-p', out_path,
        '-o', compile_commands_json_file
    ] + list(targets))
  except api.step.StepFailure as e:
    raise e
  return step_result


def generate_gn_compilation_database(api, out_path, targets):
  with api.context(cwd=api.path['checkout'], env=api.chromium.get_env()):
    export_compile_cmd = '--export-compile-commands'
    if targets:
      export_compile_cmd += '=' + ','.join(targets)
    api.step(
        'generate gn compilation database', [
            'python3', '-u', api.depot_tools.gn_py_path, 'gen',
            export_compile_cmd, out_path
        ],
        stdout=api.raw_io.output_text())


def generate_gn_target_list(api, out_path, gn_targets_json_file, targets=None):
  with api.context(cwd=api.path['checkout'], env=api.chromium.get_env()):
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

  # These values are identical to those in config.py.
  # TODO(crbug.com/1378059): Remove config.py?
  checkout_path = api.path['cache'].join('builder', 'src')
  out_path = checkout_path.join('out', gen_repo_out_dir)
  compile_commands_json_file = out_path.join('compile_commands.json')
  gn_targets_json_file = out_path.join('gn_targets.json')

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
  gclient_config = api.gclient.make_config('chromium_no_telemetry_dependencies')
  target_os = 'linux'
  host_os = 'linux'
  if platform in ('android', 'webview'):
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
  gclient_config.target_os = [target_os]
  api.gclient.c = gclient_config

  api.gclient.apply_config('android_prebuilts_build_tools')

  if internal:
    api.gclient.apply_config('chrome_internal')

  if api.reclient.instance:
    api.reclient.use_download_remoteexec_cfg_hook(api.gclient.c.solutions[0])

  checkout_dir = api.path['cache'].join('builder')
  with api.context(cwd=checkout_dir, env={'PACKFILE_OFFLOADING': 1}):
    update_step = api.bot_update.ensure_checkout(
        root_solution_revision=properties.root_solution_revision)
  api.chromium.set_build_properties(update_step.json.output['properties'])

  # Remove the llvm-build directory, so that gclient runhooks will download
  # a new clang binary and not use the previous one downloaded by
  # api.codesearch.run_clang_tool().
  api.file.rmtree('llvm-build',
                  api.path['checkout'].join('third_party', 'llvm-build'))

  api.chromium.set_config(
      'codesearch',
      BUILD_CONFIG='Debug',
      TARGET_PLATFORM=target_os,
      HOST_PLATFORM=host_os)

  api.chromium.ensure_goma()

  if target_os == 'ios':
    api.chromium.ensure_toolchains()

  # CHROME_HEADLESS makes sure that running 'gclient runhooks' doesn't require
  # entering 'y' to agree to a license.
  with api.context(env={'CHROME_HEADLESS': '1'}):
    api.chromium.runhooks(name='runhooks%s' % name_suffix)

  sentinel_path = api.path['cache'].join('builder', 'cr-cs-sentinel')
  if api.path.exists(sentinel_path):
    # If sentinel file is present, it means last build failed to compile, so
    # remove out directory since it might be in a bad state.
    api.file.rmtree('remove out directory', api.path['checkout'].join('out'))
  else:
    # Cleans up generated files. This is to prevent old generated files from
    # being left in the out directory. Note that this needs to be run *before*
    # generating the compilation database, otherwise some of the files generated
    # by that step may be deleted (if they've been unchanged for the past week).
    api.codesearch.cleanup_old_generated()

  gn_args = api.chromium.mb_gen(
      chromium.BuilderId.create_for_group(builder_id.group, builder_id.builder),
      build_dir=out_path,
      name='generate build files')

  if platform == 'webview':
    # Experiment to use gn gen to generate compilation database, it supports
    # target list.
    # GN target format is different than ninja's, we might need a dedicated GN
    # target list.
    webview_gn_targets = ['//android_webview:system_webview_apk']
    generate_gn_compilation_database(
        api=api, out_path=out_path, targets=targets)
    generate_gn_target_list(api, out_path, gn_targets_json_file,
                            webview_gn_targets)
  else:
    generate_compilation_database(
        api=api,
        out_path=out_path,
        compile_commands_json_file=compile_commands_json_file,
        targets=targets)
    generate_gn_target_list(api, out_path, gn_targets_json_file)

  # Prepare Java Kythe output directory
  kzip_dir = api.codesearch.c.javac_extractor_output_dir
  api.file.ensure_directory('java kzip', kzip_dir)

  # Create sentinel file to keep track of whether compilation succeeded.
  api.file.write_text(
      'create sentinel file', sentinel_path, 'cr-cs-sentinel',
      include_log=False)

  use_reclient = _use_reclient(api, gn_args)

  # If the compile fails, abort execution and don't upload the pack. When we
  # upload an incomplete (due to compile failures) pack to Kythe, it fails
  # validation and doesn't get pushed out anyway, so there's no point in
  # uploading at all.
  with api.context(
      env={
          'KYTHE_ROOT_DIRECTORY': api.path['checkout'],
          'KYTHE_OUTPUT_DIRECTORY': kzip_dir,
          'KYTHE_CORPUS': corpus
      }):
    raw_result = api.chromium.compile(
        targets,
        name='compile%s' % name_suffix,
        use_goma_module=not use_reclient,
        use_reclient=use_reclient,
        out_dir='out',
        target=gen_repo_out_dir)
  if raw_result.status != common_pb.SUCCESS:
    return raw_result

  # Remove sentinel file after compilation completes.
  api.file.remove('remove sentinel file', sentinel_path)

  # Download and run the clang tool.
  api.codesearch.run_clang_tool(run_dirs=[api.context.cwd])

  # Process annotations and add kythe metadata.
  api.codesearch.add_kythe_metadata()

  # Create the kythe index pack and upload it to google storage.
  api.codesearch.create_and_upload_kythe_index_pack(
      commit_hash=properties.codesearch_mirror_revision or _get_revision(api),
      commit_timestamp=int(properties.codesearch_mirror_revision_timestamp or
                           properties.root_solution_revision_timestamp or
                           api.time.time()),
      commit_position=_get_commit_position(api))

  # Check out the generated files repo and sync the generated files
  # into this checkout. This may fail due to other builders pushing to the
  # remote repo at the same time, so we retry this 3 times before giving up.
  copy_config = {
      api.path['checkout'].join('out', gen_repo_out_dir, 'gen'):
          api.path.join(gen_repo_out_dir, 'gen')
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
        ),
        **{'$build/reclient': {
            'instance': 'fake-reclient-instance',
        }})

  for platform in ('android', 'lacros', 'linux', 'fuchsia', 'chromiumos', 'mac',
                   'ios', 'win', 'webview'):
    for internal in (True, False):
      buildername = _format_builder_name(platform, internal)
      yield api.test(
          'full_%s' % (_sanitize_nonalpha(buildername)),
          props(platform, internal),
          api.chromium.generic_build(builder=buildername),
          api.step_data('generate gn target list',
                        api.raw_io.stream_output_text(SAMPLE_GN_DESC_OUTPUT)),
      )

      yield api.test(
          'full_%s_with_revision' % (_sanitize_nonalpha(buildername)),
          props(platform, internal),
          api.chromium.generic_build(builder=buildername),
          api.step_data('generate gn target list',
                        api.raw_io.stream_output_text(SAMPLE_GN_DESC_OUTPUT)),
          api.properties(
              root_solution_revision='a' * 40,
              root_solution_revision_timestamp=1531887759),
      )

      yield api.test(
          'full_%s_reclient' % (_sanitize_nonalpha(buildername)),
          props(platform, internal),
          api.chromium.generic_build(builder=buildername),
          api.step_data(
              'lookup GN args',
              stdout=api.raw_io.output_text('use_remoteexec = true')),
          api.step_data('generate gn target list',
                        api.raw_io.stream_output_text(SAMPLE_GN_DESC_OUTPUT)),
          api.post_process(post_process.DoesNotRun, 'preprocess for goma'),
          api.post_process(post_process.MustRun, 'preprocess for reclient'),
          api.post_process(post_process.DropExpectation),
      )

  yield api.test(
      'full_%s_with_patch' % _sanitize_nonalpha('gen-linux-try'),
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
      api.path.exists(api.path['cache'].join('builder', 'cr-cs-sentinel')),
  )

  yield api.test(
      'full_%s_translation_unit_fail' %
      _sanitize_nonalpha('codesearch-gen-chromium-chromiumos'),
      props('chromiumos'),
      api.chromium.generic_build(builder='codesearch-gen-chromium-chromiumos'),
      api.step_data('run translation_unit clang tool', retcode=2),
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
