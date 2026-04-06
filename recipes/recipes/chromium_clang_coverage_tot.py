# Copyright 2018 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
from recipe_engine.engine_types import freeze

from RECIPE_MODULES.build import chromium_types

DEPS = [
    'chromium',
    'chromium_checkout',
    'depot_tools/bot_update',
    'depot_tools/depot_tools',
    'recipe_engine/context',
    'recipe_engine/file',
    'recipe_engine/json',
    'recipe_engine/platform',
    'recipe_engine/step',
]

BUILDERS = freeze({
    'chromium.clang': {
        'builders': {
            'ToTMacCoverage':
                chromium_types.BuilderSpec.create(
                    chromium_config='clang_tot_mac',
                    chromium_apply_config=[],
                    gclient_apply_config=['clang_tot'],
                    chromium_config_kwargs={
                        'BUILD_CONFIG': 'Release',
                        'TARGET_PLATFORM': 'mac',
                        'TARGET_BITS': 64,
                    },
                ),
            'ToTLinuxCoverage':
                chromium_types.BuilderSpec.create(
                    chromium_config='clang_tot_linux',
                    chromium_apply_config=[],
                    gclient_apply_config=['clang_tot'],
                    chromium_config_kwargs={
                        'BUILD_CONFIG': 'Release',
                        'TARGET_PLATFORM': 'linux',
                        'TARGET_BITS': 64,
                    },
                ),
            'ToTWindowsCoverage':
                chromium_types.BuilderSpec.create(
                    chromium_config='chromium_win_clang_tot',
                    chromium_apply_config=[],
                    gclient_apply_config=['clang_tot'],
                    chromium_config_kwargs={
                        'BUILD_CONFIG': 'Release',
                        'TARGET_PLATFORM': 'win',
                        'TARGET_BITS': 64,
                    },
                ),
        },
    },
})

# Sample targets that are used to test the coverage script against clang tot
# coverage tools.
SAMPLE_TARGETS = [
    'base_unittests', 'boringssl_crypto_tests', 'boringssl_ssl_tests',
    'unit_tests'
]

def RunSteps(api):
  builder_id, bot_config = api.m.chromium.configure_bot(BUILDERS, ['mb'])
  with api.m.context(cwd=api.m.chromium_checkout.default_checkout_dir):
    _RunStepsInBuilderCacheDir(api, builder_id, bot_config)


def _RunStepsInBuilderCacheDir(api, builder_id, bot_config):
  update_result = api.bot_update.ensure_checkout()
  checkout_dir = update_result.checkout_dir
  source_dir = update_result.source_root.path
  build_dir = api.chromium.default_build_dir(source_dir)

  api.chromium.ensure_toolchains(checkout_dir)

  api.chromium.runhooks(source_dir, build_dir)
  clang_revision_file = source_dir.joinpath('third_party', 'llvm-build',
                                            'Release+Asserts',
                                            'cr_build_revision')
  revision = api.file.read_text(
      'Read clang revision', clang_revision_file, test_data='332838-1')
  api.step.active_result.presentation.step_text = revision

  api.chromium.mb_gen(source_dir, build_dir, builder_id)

  coverage_script = 'coverage.py'
  coverage_script_path = source_dir.joinpath('tools', 'code_coverage',
                                             coverage_script)
  output_dir_name = 'clang_tot_coverage_report'
  output_dir_path = source_dir.joinpath('out', output_dir_name)

  cmd = ['vpython3', coverage_script_path]
  cmd.extend(SAMPLE_TARGETS)

  for target in SAMPLE_TARGETS:
    if api.platform.is_win:
      target += '.exe'
    cmd.extend(['-c', build_dir / target])

  cmd.extend(['-b', build_dir])
  cmd.extend(['-o', output_dir_path])

  coverage_tools_dir_path = source_dir.joinpath('third_party', 'llvm-build',
                                                'Release+Asserts', 'bin')
  cmd.extend(['--coverage-tools-dir', coverage_tools_dir_path])

  cmd.extend(['-v'])
  cmd.extend(['--no-compile'])
  with api.depot_tools.on_path():
    api.chromium.compile(
        source_dir=source_dir, build_dir=build_dir, targets=SAMPLE_TARGETS)
    api.step('run coverage script', cmd)

  # Following steps are added for debugging purpose.
  for target in SAMPLE_TARGETS:
    log_file_name = '%s_output.log' % target
    log_file_path = output_dir_path.joinpath(api.platform.name, 'logs',
                                             log_file_name)

    log_content = api.file.read_text(
        'read log output of %s' % target, log_file_path, test_data='aaa\nbbb')
    log_content_lines = log_content.splitlines()
    api.step.active_result.presentation.logs[log_file_name] = log_content_lines

  summary_file_name = 'summary.json'
  summary_file_path = output_dir_path.joinpath(api.platform.name,
                                               summary_file_name)
  api.file.read_json('read %s' % summary_file_name, summary_file_path)


def GenTests(api):
  for test in api.chromium.gen_tests_for_builders(BUILDERS):
    yield test
