# Copyright 2019 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import base64
import zlib

from recipe_engine import post_process
from recipe_engine.engine_types import freeze
from PB.go.chromium.org.luci.common.proto.findings import findings as findings_pb
from RECIPE_MODULES.build.tricium_clang_tidy import _clang_tidy_path
from RECIPE_MODULES.build import chromium_types

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import (
    chromium,
    chromium_checkout,
    gn,
    tricium_clang_tidy,
)
from RECIPE_MODULES.depot_tools import (
    depot_tools,
    gclient,
    gerrit,
    tryserver,
)
from RECIPE_MODULES.recipe_engine import (
    context,
    file,
    json,
    path,
    platform,
    properties,
    proto,
    raw_io,
    step,
)


@dataclass
class DEPS(RecipeScriptApi):
  chromium: chromium.API
  chromium_checkout: chromium_checkout.API
  context: context.API
  depot_tools: depot_tools.API
  file: file.API
  gclient: gclient.API
  gerrit: gerrit.API
  gn: gn.API
  json: json.API
  path: path.API
  platform: platform.API
  properties: properties.API
  proto: proto.API
  raw_io: raw_io.API
  step: step.API
  tricium_clang_tidy: tricium_clang_tidy.API
  tryserver: tryserver.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  chromium: chromium.TEST_API
  file: file.TEST_API
  gerrit: gerrit.TEST_API
  json: json.TEST_API
  path: path.TEST_API
  platform: platform.TEST_API
  tryserver: tryserver.TEST_API


class ClangTidySpec(chromium_types.BuilderSpec):
  """Builder spec for clang-tidy bots."""

  def __init__(self, **kwargs):
    gclient_configs = kwargs.setdefault('gclient_apply_config', [])
    gclient_configs.append('use_clang_tidy')
    super().__init__(**kwargs)


BUILDERS = freeze({
    'tryserver.chromium.tricium': {
        'builders': {
            'android-clang-tidy-rel':
                ClangTidySpec(
                    chromium_apply_config=['android'],
                    gclient_apply_config=['android'],
                ),
            'fuchsia-clang-tidy-rel':
                ClangTidySpec(gclient_apply_config=['fuchsia'],),
            'ios-clang-tidy-rel':
                ClangTidySpec(chromium_apply_config=['mac_toolchain']),
            'linux-chromeos-clang-tidy-rel':
                ClangTidySpec(
                    gclient_apply_config=['chromeos'],
                    chromium_config_kwargs={
                        'TARGET_BITS': 64,
                        'TARGET_PLATFORM': 'chromeos',
                    },
                ),
            'linux-clang-tidy-rel':
                ClangTidySpec(),
            'mac-clang-tidy-rel':
                ClangTidySpec(),
            'win10-clang-tidy-rel':
                ClangTidySpec(),
        },
    },
})

def _should_skip_linting(api):
  revision_info = api.gerrit.get_revision_info(
      'https://%s' % api.tryserver.gerrit_change.host,
      api.tryserver.gerrit_change.change, api.tryserver.gerrit_change.patchset)

  commit_message = revision_info['commit']['message']
  return commit_message.startswith('Revert')


def _normalize_path_for_os(api, path):
  # Some APIs give us paths with `/` in them regardless of OS. On Windows, we
  # need to convert these to `\`
  if api.platform.is_win:
    return path.replace('/', '\\')  # pragma: no cover
  # Otherwise, assume there're no '\'s in the path.
  return path


def RunSteps(api: DEPS):
  assert api.tryserver.is_tryserver

  if _should_skip_linting(api):
    return

  skip_checks_footer_lines = api.tryserver.get_footer(
      api.tryserver.normalize_footer_name(
          api.tricium_clang_tidy.SKIP_CHECKS_FOOTER_KEY))
  skip_checks = [
      check.strip()
      for line in skip_checks_footer_lines
      for check in line.split(',')
  ]

  with api.chromium.chromium_layout():
    me, config = api.chromium.configure_bot(BUILDERS)

    # Do not rebase the patch, so that the Tricium analyzer observes the correct
    # line numbers. Otherwise, line numbers would be relative to origin/main,
    # which may be synced to include changes subsequent to the actual patch.
    update_result = api.chromium_checkout.ensure_checkout(
        clobber=config.clobber, gerrit_no_rebase_patch_ref=True)

    source_dir = update_result.source_root.path
    build_dir = api.chromium.default_build_dir(source_dir)
    api.chromium.runhooks(source_dir, build_dir, name='runhooks (with patch)')
    with api.context(cwd=source_dir, env=api.chromium.get_env(source_dir)):
      affected = [
          source_dir.joinpath(_normalize_path_for_os(api, f))
          for f in api.chromium_checkout.get_files_affected_by_patch()
      ]

      api.chromium.ensure_toolchains(update_result.checkout_dir)

      # `gn gen` can take up to a minute, and the script we call out to
      # already does that for us, so set up a minimal build dir.
      gn_args_str = api.chromium.mb_lookup(source_dir, me)

      api.file.ensure_directory('ensure out dir', build_dir)
      api.file.write_text('write args.gn', build_dir / 'args.gn', gn_args_str)

      findings = api.tricium_clang_tidy.lint_source_files(
          source_dir,
          build_dir,
          affected,
          skip_checks=skip_checks,
          is_windows=api.platform.name == 'win',
      )

      if findings:
        # write 'findings' build property that will be consumed by
        # the orchestrator.
        findings = findings_pb.Findings(findings=findings)
        step = api.step.empty(
            'write findings',
            log_text=api.proto.encode(findings, 'JSONPB'),
            log_name='findings.json')
        step.presentation.properties['findings'] = base64.b64encode(
            zlib.compress(api.proto.encode(findings, 'BINARY'))).decode()


def GenTests(api: TEST_DEPS):

  def build_with_patch(affected_files,
                       is_revert=False,
                       author='gbiv@google.com',
                       builder_group='tryserver.chromium.tricium',
                       builder='linux-clang-tidy-rel'):
    commit_message = 'Revert foo' if is_revert else 'foo'
    commit_message += '\nTriciumTest'

    existing_files = [
        api.path.cache_dir.joinpath('builder', 'src', x) for x in affected_files
    ]
    existing_files.append(
        api.path.cache_dir.joinpath('builder', 'src', *_clang_tidy_path))
    return sum([
        api.chromium.try_build(
            builder_group=builder_group,
            builder=builder,
            build_number=1234,
            patch_set=1),
        api.platform('linux', 64),
        api.override_step_data(
            'gerrit changes',
            api.json.output([{
                'revisions': {
                    'a' * 40: {
                        '_number': 1,
                        'commit': {
                            'author': {
                                'email': author,
                            },
                            'message': commit_message,
                        }
                    }
                }
            }])),
        api.path.exists(*existing_files)
    ], api.empty_test_data())

  yield api.test(
      'skip_reverted_cl',
      build_with_patch(
          affected_files=['path/to/some/cc/file.cpp'], is_revert=True),
      api.post_process(post_process.DoesNotRun, 'bot_update'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'generate_findings',
      build_with_patch(affected_files=['path/to/some/cc/file.cpp']),
      api.tryserver.get_files_affected_by_patch(['path/to/some/cc/file.cpp']),
      api.step_data(
          'parse description',
          api.json.output({'Skip-Clang-Tidy-Checks': ['foo,bar', 'baz']})),
      api.step_data(
          'clang-tidy.generate-warnings.read tidy output',
          api.file.read_json({
              'diagnostics': [{
                  'file_path': 'path/to/some/cc/file.cpp',
                  'line_number': 2,
                  'diag_name': 'super-cool-diag',
                  'message': 'hello, world 1',
                  'replacements': [],
                  'expansion_locs': [],
              },]
          })),
      api.post_process(
          post_process.StepCommandContains,
          'clang-tidy.generate-warnings.tricium_clang_tidy_script.py',
          ['--tidy_checks=-foo,-bar,-baz']),
      api.post_process(post_process.PropertiesContain, 'findings'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'set_force_mac_toolchain_env_on_mac',
      build_with_patch(affected_files=['path/to/some/cc/file.cpp']),
      api.tryserver.get_files_affected_by_patch(['path/to/some/cc/file.cpp']),
      api.platform('mac', 64),
      api.step_data(
          'clang-tidy.generate-warnings.read tidy output',
          api.file.read_json({
              'diagnostics': [{
                  'file_path': 'path/to/some/cc/file.cpp',
                  'line_number': 2,
                  'diag_name': 'super-cool-diag',
                  'message': 'hello, world 1',
                  'replacements': [],
                  'expansion_locs': [],
              },]
          })),
      api.post_process(
          post_process.StepEnvContains,
          'clang-tidy.generate-warnings.tricium_clang_tidy_script.py',
          {'FORCE_MAC_TOOLCHAIN': '1'}),
      api.post_process(post_process.DropExpectation))
