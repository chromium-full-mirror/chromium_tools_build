# Copyright 2013 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import json
import urllib

from recipe_engine import recipe_api
from recipe_engine.config_types import Path

from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb
from PB.recipe_engine import result as result_pb
from RECIPE_MODULES.build.chromium_tests_builder_config import builder_spec

from . import builders

_WEBRTC_GS_BUCKET = 'chromium-webrtc'
_DASHBOARD_UPLOAD_URL = 'https://chromeperf.appspot.com'
_PERF_MACHINE_GROUP = 'WebRTCPerf'
_BINARY_SIZE_TARGETS = (
    'AppRTCMobile',
    'libjingle_peerconnection_so',
    'webrtc',
)


def _replace_string_in_dict(dict_input, old, new):
  dict_output = {}
  for key, values in dict_input.items():
    dict_output[key] = [value.replace(old, new) for value in values]
  return dict_output


def _get_isolated_targets(tests):
  return [t.target_name for t in tests if t.runs_on_swarming]


def _get_test_targets_from_config(targets_config, phase):
  if phase is None:
    return [t.target_name for t in targets_config.all_tests]
  if phase == 'rtti_no_sctp':
    return [t.target_name for t in targets_config.all_tests]
  return []


def _is_triggering_perf_tests(builder_id, builder_config):
  to_trigger = builder_config.builder_db.builder_graph[builder_id]
  return any(builders.BUILDERS_DB[b].perf_id for b in to_trigger)


def _is_cpp_file(file):
  return file.endswith('.cc') or file.endswith('.c') or file.endswith(
      '.cpp') or file.endswith('.h') or file.endswith('.hpp')


def _skip_include_cleaner(file, filter_list):
  for filtered_path in filter_list["excluded_paths"]:
    if file.startswith(filtered_path):
      return True
  return False


class WebRTCApi(recipe_api.RecipeApi):

  @property
  def revision(self):
    return self.m.chromium.build_properties.get('got_revision')

  @property
  def revision_cp(self):
    return self.m.chromium.build_properties.get('got_revision_cp')

  @property
  def revision_number(self):
    branch, number = self.m.commit_position.parse(self.revision_cp)
    assert branch.endswith('/main')
    return number

  def apply_bot_config(self, builder_id, builder_config):
    self.m.chromium_tests.configure_build(builder_config)
    if self.m.tryserver.is_tryserver:
      self.m.chromium.apply_config('trybot_flavor')

    if builders.BUILDERS_DB[builder_id].perf_id:
      assert self.m.chromium.c.BUILD_CONFIG == 'Release', (
          'Perf tests should only be run with Release builds.')
    if builders.BUILDERS_DB[builder_id].execution_mode == builder_spec.TEST:
      assert self.m.properties.get('parent_got_revision'), (
          'Testers should only be run with "parent_got_revision" property.')

  def determine_compilation_targets(
      self,
      source_dir: Path,
      build_dir: Path,
      builder_id,
      targets_config,
      phase,
  ):
    """ Returns the tests to run and the targets to compile."""
    bot_config = builders.BUILDERS_DB[builder_id]
    test_targets = _get_test_targets_from_config(targets_config, phase)

    # Perf testers only need the catapult protos.
    if bot_config.perf_id and bot_config.execution_mode == builder_spec.TEST:
      return test_targets, ['webrtc_dashboard_upload']

    # CI bots always build everything.
    if not self.m.tryserver.is_tryserver:
      return test_targets, ['all']

    # Trybots only build the targets that are affected by the patch.
    affected_files = self.m.chromium_checkout.get_files_affected_by_patch(
        relative_to=self.m.gclient.get_gerrit_patch_root(), cwd=source_dir)
    test_targets, compile_targets = self.m.filter.analyze(
        source_dir,
        build_dir,
        affected_files,
        test_targets,
        additional_compile_targets=['all'],
        mb_path=source_dir / 'tools_webrtc/mb',
        phase=phase)

    # Some trybots are used to calculate the binary size impact of the current
    # CL. These targets should always be built.
    for filename in bot_config.binary_size_files or []:
      compile_targets += [t for t in _BINARY_SIZE_TARGETS if t in filename]
    return test_targets, sorted(set(compile_targets))

  def should_download_video_quality_tools(self, builder_id, builder_config):
    if 'android' not in builder_id.builder.lower():
      return False
    return (builders.BUILDERS_DB[builder_id].perf_id or
            _is_triggering_perf_tests(builder_id, builder_config))

  def download_video_quality_tools(self, source_dir):
    with self.m.depot_tools.on_path():
      # Video quality tools
      args_tools = [source_dir / 'tools_webrtc/video_quality_toolchain/linux']
      script_tools = source_dir / 'tools_webrtc/download_tools.py'
      cmd_tools = ['vpython3', '-u', script_tools] + args_tools
      self.m.step('download video quality tools', cmd_tools)

      # AppRTC
      args_apprtc = [
          '--bucket=chromium-webrtc-resources', '--directory',
          source_dir / 'rtc_tools/testing'
      ]
      script_apprtc = self.m.depot_tools.download_from_google_storage_path
      cmd_apprtc = ['vpython3', '-u', script_apprtc] + args_apprtc
      self.m.step('download apprtc', cmd_apprtc)

      # Golang
      args_golang = [
          '--bucket=chromium-webrtc-resources', '--directory',
          source_dir / 'rtc_tools/testing/golang/linux'
      ]
      script_golang = self.m.depot_tools.download_from_google_storage_path
      cmd_golang = ['vpython3', '-u', script_golang] + args_golang
      self.m.step('download golang', cmd_golang)

  def should_generate_code_coverage(self, builder_id, builder_config):
    return builder_id.builder.lower() == 'linux_coverage'

  def setup_code_coverage_module(self, source_dir: Path, build_dir: Path):
    """Configure internal constants of the code_coverage module."""
    self.m.profiles.source_dir = source_dir
    self.m.code_coverage._use_clang_coverage = True
    self.m.code_coverage.source_dir = source_dir
    self.m.code_coverage.build_dir = build_dir

    # For presubmit, only instrument changed files.
    if self.m.tryserver.is_tryserver:
      affected_files = self.m.chromium_checkout.get_files_affected_by_patch(
          report_via_property=True)
      is_deps_only_change = affected_files == ["DEPS"]
      self.m.code_coverage.instrument(
          affected_files, is_deps_only_change=is_deps_only_change)

  def run_mb(self,
             source_dir: Path,
             build_dir: Path,
             builder_id,
             phase=None,
             tests=None):
    self.m.chromium.mb_gen(
        source_dir,
        build_dir,
        builder_id,
        phase=phase,
        mb_path=source_dir.joinpath('tools_webrtc', 'mb'),
        isolated_targets=_get_isolated_targets(tests or []))

  def isolate(self, build_dir: Path, builder_id, tests):
    if builders.BUILDERS_DB[builder_id].execution_mode == builder_spec.TEST:
      # The tests running on a 'tester' bot are isolated by the 'builder'.
      self.m.isolate.check_swarm_hashes(_get_isolated_targets(tests))
    else:
      self.m.isolate.isolate_tests(
          build_dir, targets=_get_isolated_targets(tests))

  def set_upload_build_properties(self, build_dir: Path, builder_id):
    experiment_prefix = 'Experimental' if self.m.runtime.is_experimental else ''
    bucketname = self.m.buildbucket.bucket_v1
    build_url = 'https://ci.chromium.org/p/%s/builders/%s/%s/%s' % (
        urllib.parse.quote(self.m.buildbucket.build.builder.project),
        urllib.parse.quote(bucketname), urllib.parse.quote(builder_id.builder),
        urllib.parse.quote(str(self.m.buildbucket.build.number)))
    # Don't overwrite existing chromium build_properties
    build_props = self.m.chromium.build_properties or {}
    build_props.update({
        'build_page_url': build_url,
        'bot': builders.BUILDERS_DB[builder_id].perf_id,
        'dashboard_url': _DASHBOARD_UPLOAD_URL,
        'commit_position': self.revision_number,
        'webrtc_git_hash': self.revision,
        'perf_dashboard_machine_group': experiment_prefix + _PERF_MACHINE_GROUP,
        'outdir': build_dir,
    })
    self.m.chromium.set_build_properties(build_props)

  def set_test_command_lines(
      self,
      source_dir: Path,
      build_dir: Path,
      builder_id,
      tests,
  ):
    if builders.BUILDERS_DB[builder_id].execution_mode != builder_spec.TEST:
      return self.m.chromium_tests.set_swarming_test_execution_info(
          source_dir,
          build_dir,
          tests,
          self.m.chromium_tests.find_swarming_command_lines('', build_dir),
          self.m.path.relpath(build_dir, source_dir),
      )

    # Tester builders only triggers swarming tests built on 'builder' bots
    # so the swarming command line needs to be retrieved from build
    # parameters.
    swarming_command_lines = _replace_string_in_dict(
        self.m.properties.get('swarming_command_lines'),
        'WILL_BE_ISOLATED_OUTDIR',
        'ISOLATED_OUTDIR',
    )
    # Tester builders run their tests in the parent builder out directory.
    relative_cwd = self.m.properties.get('swarming_command_lines_cwd')
    for test in tests:
      if test.runs_on_swarming:
        command_line = swarming_command_lines.get(test.target_name, [])
        if command_line:
          test.raw_cmd = command_line
          test.relative_cwd = relative_cwd

  def get_binary_sizes(self, files, build_dir):
    args = [
        '--base-dir',
        build_dir,
        '--output',
        self.m.json.output(),
        '--',
    ] + list(files)
    cmd = ['vpython3', '-u', self.resource('binary_sizes.py')] + args

    result = self.m.step(
        'get binary sizes',
        cmd,
        infra_step=True,
        step_test_data=self.test_api.example_binary_sizes)
    result.presentation.properties['binary_sizes'] = result.json.output

  def include_cleaner(self, source_dir: Path, build_dir: Path):
    INCLUDE_CLEANER = 'tools_webrtc/iwyu/apply_include_cleaner.py'
    FILTER_LIST = 'tools_webrtc/iwyu/iwyu-verifier-filter_list.json'
    SKIP_FOOTER = 'No-Iwyu'

    result = result_pb.RawResult(status=common_pb.SUCCESS)
    if SKIP_FOOTER in self.m.tryserver.get_footers():
      return result

    affected_files = self.m.chromium_checkout.get_files_affected_by_patch(
        report_via_property=True)
    filter_list = json.loads(
        self.m.file.read_text(
            'read filter_list',
            self.m.path.join(source_dir, FILTER_LIST),
            test_data='{"excluded_paths": ["skip.cc"]}'))
    self.m.step('remove compile_commands.json',
                ['rm', '-f', build_dir / 'compile_commands.json'])
    for f in affected_files:
      if not _is_cpp_file(f) or _skip_include_cleaner(
          f, filter_list) or not self.m.path.exists(source_dir / f):
        continue
      with self.m.context(cwd=source_dir):
        # First call to apply_include_cleaner generates compile_commands.json.
        step_result = self.m.step(
            'apply_include_cleaner.py ' + f,
            ['vpython3', '-u', INCLUDE_CLEANER, '-c', '-w', build_dir, f],
            raise_on_failure=False)
      if step_result.exc_result.retcode != 0:
        result = result_pb.RawResult(
            status=common_pb.FAILURE,
            summary_markdown='Run "' + INCLUDE_CLEANER +
            '" to fix this bot !<br>Add a "' + SKIP_FOOTER +
            ': [reason]" footer in the commit description to skip this bot.')
    return result

  def build_android_archive(self, source_dir: Path):
    # Build the Android .aar archive and upload it to Google storage (except for
    # trybots). This should only be run on a single bot or the archive will be
    # overwritten (and it's a multi-arch build so one is enough).
    build_script = source_dir / 'tools_webrtc/android/build_aar.py'
    args = ['--verbose']
    if self.m.tryserver.is_tryserver:
      # To benefit from incremental builds for speed.
      args.append('--build-dir=out/android-archive')

    cmd = ['vpython3', '-u', build_script] + args

    with self.m.context(cwd=source_dir):
      build_dir = source_dir / 'andriod-archive'
      cmd += ['--build-dir', build_dir, '--use-remoteexec']
      with self.m.siso.context():
        self.m.step('build android archive', cmd)
      self.m.file.rmtree('Remove android archive dir', build_dir)

    if not self.m.tryserver.is_tryserver and not self.m.runtime.is_experimental:
      self.m.gsutil.upload(
          source_dir / 'libwebrtc.aar',
          'chromium-webrtc',
          'android_archive/webrtc_android_%s.aar' % self.revision_number,
          args=['-a', 'public-read'],
          unauthenticated_url=True)

  def package_apprtcmobile(self, build_dir: Path, builder_id):
    # Zip and upload out/{Debug,Release}/apks/AppRTCMobile.apk
    apk_root = build_dir / 'apks'
    zip_path = self.m.path.start_dir / 'AppRTCMobile_apk.zip'

    pkg = self.m.zip.make_package(apk_root, zip_path)
    pkg.add_file(apk_root / 'AppRTCMobile.apk')
    pkg.zip('AppRTCMobile zip archive')

    apk_upload_url = 'client.webrtc/%s/AppRTCMobile_apk_%s.zip' % (
        builder_id.builder, self.revision_number)
    if not self.m.runtime.is_experimental:
      self.m.gsutil.upload(
          zip_path,
          _WEBRTC_GS_BUCKET,
          apk_upload_url,
          args=['-a', 'public-read'],
          unauthenticated_url=True)

  def run_tests(
      self,
      checkout_dir: Path,
      source_dir: Path,
      build_dir: Path,
      builder_id,
      tests,
  ):
    if not tests:
      return

    if builders.BUILDERS_DB[builder_id].perf_id:
      self.set_upload_build_properties(build_dir, builder_id)

    self.set_test_command_lines(source_dir, build_dir, builder_id, tests)
    test_runner = self.m.chromium_tests.create_test_runner(
        checkout_dir,
        source_dir,
        build_dir,
        tests,
        surface_invalid_results_as_infra_failure=True)
    test_failure_summary = test_runner()

    if self.m.code_coverage.using_coverage:
      self.m.code_coverage.process_coverage_data(tests)

    return test_failure_summary

  def trigger_child_builds(
      self,
      builder_id,
      builder_config,
      source_dir: Path,
      build_dir: Path,
      update_step,
  ):
    if _is_triggering_perf_tests(builder_id, builder_config):
      # Replace ISOLATED_OUTDIR by WILL_BE_ISOLATED_OUTDIR to prevent
      # the variable to be expanded by the builder instead of the tester.
      swarming_command_lines = _replace_string_in_dict(
          self.m.chromium_tests.find_swarming_command_lines(
              suffix='', build_dir=build_dir),
          'ISOLATED_OUTDIR',
          'WILL_BE_ISOLATED_OUTDIR',
      )
      relative_cwd = self.m.path.relpath(build_dir, source_dir)
      properties = {
          'swarming_command_lines': swarming_command_lines,
          'swarm_hashes': self.m.isolate.isolated_tests,
          'swarming_command_lines_cwd': relative_cwd,
      }
      self.m.chromium_tests.trigger_child_builds(
          builder_id, update_step, builder_config, properties,
          self.m.buildbucket.gitiles_commit)
