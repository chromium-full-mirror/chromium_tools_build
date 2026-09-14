# Copyright 2019 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process
from google.protobuf import json_format
from PB.go.chromium.org.luci.common.proto.findings import (
  findings as findings_pb,
)

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import chromium, chromium_checkout
from RECIPE_MODULES.depot_tools import (
  gclient,
  gerrit,
  git,
  tryserver,
)
from RECIPE_MODULES.recipe_engine import (
  buildbucket,
  cipd,
  context,
  file,
  findings,
  json,
  path,
  platform,
  properties,
  raw_io,
  step,
)


@dataclass
class DEPS(RecipeScriptApi):
  buildbucket: buildbucket.API
  chromium: chromium.API
  chromium_checkout: chromium_checkout.API
  cipd: cipd.API
  context: context.API
  file: file.API
  findings: findings.API
  gclient: gclient.API
  gerrit: gerrit.API
  git: git.API
  json: json.API
  path: path.API
  platform: platform.API
  properties: properties.API
  raw_io: raw_io.API
  step: step.API
  tryserver: tryserver.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  buildbucket: buildbucket.TEST_API
  cipd: cipd.TEST_API
  file: file.TEST_API
  path: path.TEST_API
  raw_io: raw_io.TEST_API
  step: step.TEST_API
  tryserver: tryserver.TEST_API


def _RunMetricsAnalyzer(
  api: DEPS, src_dir, prev_dir, metrics_paths, patch_path, commit_message
):
  packages_dir = api.path.cleanup_dir / 'packages'
  test = bool(api.tryserver.get_footer('Tricium-Test'))
  pkg = 'infra/tricium/legacy_functions/metrics/linux-amd64'
  if test:
    pkg = 'experimental/tricium/legacy_functions/metrics/linux-amd64'

  with api.step.nest('load_' + ('test' if test else 'prod') + '_analyzer'):
    ensure_file = api.cipd.EnsureFile()
    ensure_file.add_package(pkg, version='latest')
    api.cipd.ensure(packages_dir, ensure_file)

  metrics = packages_dir / 'metrics'
  out_dir = api.path.cleanup_dir / 'out'
  enums_path = api.path.join('tools', 'metrics', 'histograms', 'enums.xml')
  api.step(
    'metrics',
    [
      metrics,
      '-input',
      src_dir,
      '-output',
      out_dir,
      '-previous',
      prev_dir,
      '-patch',
      patch_path,
      '-enums',
      enums_path,
      '-message',
      commit_message,
      '--',
    ]
    + metrics_paths,
  )

  # This is where the metrics analyzer should write all results to.
  out_file = out_dir / 'findings.out'

  findings = api.file.read_proto(
    'metrics_output', out_file, findings_pb.Findings, 'BINARY'
  )
  if findings.findings:
    for f in findings.findings:
      api.findings.populate_source_from_current_build(f.location)
    api.findings.upload_findings(findings.findings, step_name='upload findings')


def RunSteps(api: DEPS):
  assert api.tryserver.is_tryserver

  api.buildbucket.hide_current_build_in_gerrit()

  # Do not run if "Tricium-Skip-Metrics" is in the commit message footer.
  if bool(api.tryserver.get_footer('Tricium-Skip-Metrics')):
    return

  with api.chromium.chromium_layout():
    api.gclient.set_config('chromium')
    api.chromium.set_config('chromium')

    # Do not rebase the patch, so that the Tricium analyzer observes the correct
    # line numbers. Otherwise, line numbers would be relative to origin/main,
    # which will typically be synced to include changes subsequent to the actual
    # patch.
    api.chromium_checkout.ensure_checkout(gerrit_no_rebase_patch_ref=True)

    src_dir = api.chromium_checkout.source_dir
    with api.context(cwd=src_dir):
      # Do not analyze removed files.
      affected = [
        f
        for f in api.chromium_checkout.get_files_affected_by_patch()
        if api.path.exists(src_dir / f)
      ]

      metrics_filenames = {
        'histograms.xml',
        'fieldtrial_testing_config.json',
      }
      metrics_paths = [
        path
        for path in affected
        if api.path.basename(path) in metrics_filenames
      ]

      if not metrics_paths:
        api.step.empty(
          'no_metrics_paths',
          step_text=(
            'No files relevant to Tricium metrics analysis were changed'
          ),
        )
        return

      # Put last version of changed files in temporary directory.
      prev_dir = api.path.cleanup_dir.joinpath('previous', 'src')
      for path in metrics_paths:
        prev_dir_path = prev_dir / path
        api.file.ensure_directory(
          'create_directories', api.path.dirname(prev_dir_path)
        )
        # `git show` throws an error if the file doesn't exist. This could
        # happen when users just added a new histograms.xml. In this case,
        # we just need to touch an empty file as the placeholder.
        try:
          api.git(
            'show',
            'FETCH_HEAD~:' + path,
            stdout=api.raw_io.output(leak_to=prev_dir_path),
          )
        except Exception:
          api.step('touch an empty file', ['touch', prev_dir_path])

      # Get the diff itself, with paths formatted as Tricium analyzer expects.
      patch_path = api.path.cleanup_dir / 'tricium_generated_diff.patch'
      diff_arg_list = [
        'diff',
        'FETCH_HEAD~',
        'FETCH_HEAD',
        '--output=' + str(patch_path),
        '--',
      ] + metrics_paths
      api.git(*diff_arg_list)

      # Run the metrics analyzer.
      with api.step.nest('metrics'):
        _RunMetricsAnalyzer(
          api,
          src_dir,
          prev_dir,
          metrics_paths,
          patch_path,
          api.tryserver.get_change_description(),
        )


def GenTests(api: TEST_DEPS):

  def build_with_patch(
    affected_files,
    include_diff=True,
    auto_exist_files=True,
    skip_footer=False,
    test_footer=False,
  ):
    test_data = api.buildbucket.try_build()

    footer_json = {}
    if skip_footer:
      footer_json['Tricium-Skip-Metrics'] = [True]
    if test_footer:
      footer_json['Tricium-Test'] = [True]
    test_data += api.tryserver.get_footers(footer_json)

    if include_diff:
      test_data += api.step_data(
        'git diff to analyze patch',
        api.raw_io.stream_output('\n'.join(affected_files)),
      )

    if auto_exist_files:
      test_data += api.path.exists(
        *[
          api.path.cache_dir.joinpath('builder', 'src', x)
          for x in affected_files
        ]
      )

    return test_data

  yield api.test(
    'no_files',
    build_with_patch(affected_files=[]),
    api.post_process(post_process.DoesNotRun, 'metrics'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'no_analysis_non_xml',
    build_with_patch(affected_files=['some/file.txt']),
    api.post_process(post_process.DoesNotRun, 'metrics'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'no_analysis_xml',
    build_with_patch(affected_files=['some/file.xml']),
    api.post_process(post_process.DoesNotRun, 'metrics'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'no_analysis_skip_footer',
    build_with_patch(
      affected_files=['some/test/test2/histograms.xml'],
      skip_footer=True,
      include_diff=False,
    ),
    api.post_process(post_process.DoesNotRun, 'bot_update'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'removed_file',
    build_with_patch(
      affected_files=['some/test/test2/histograms.xml'], auto_exist_files=False
    ),
    api.post_process(post_process.DoesNotRun, 'metrics'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'test_version_if_footer',
    build_with_patch(
      affected_files=['some/test/test2/histograms.xml'], test_footer=True
    ),
    api.step_data(
      'metrics.metrics_output', api.file.read_proto(findings_pb.Findings())
    ),
    api.post_process(post_process.DoesNotRun, 'metrics.load_prod_analyzer'),
    api.post_process(post_process.StepSuccess, 'metrics.load_test_analyzer'),
    api.post_process(post_process.StepSuccess, 'metrics'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'analyze_xml_live',
    build_with_patch(affected_files=['some/test/test2/histograms.xml']),
    api.step_data(
      'metrics.metrics_output',
      api.file.read_proto(
        findings_pb.Findings(
          findings=[
            findings_pb.Finding(
              category="chromium_metrics",
              message="Removed",
              severity_level=findings_pb.Finding.SEVERITY_LEVEL_ERROR,
              location=findings_pb.Location(
                file_path="testdata/src/test/histograms.xml"
              ),
            ),
          ]
        )
      ),
    ),
    api.post_process(post_process.DoesNotRun, 'metrics.load_test_analyzer'),
    api.post_process(post_process.StepSuccess, 'metrics.load_prod_analyzer'),
    api.post_process(post_process.StepSuccess, 'metrics'),
    api.post_process(post_process.MustRun, 'metrics.upload findings'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'show_file_path_not_found_but_succeed',
    build_with_patch(affected_files=['some/test/test2/histograms.xml']),
    # Simulate a file missing error, this could happen if users add a new file
    # Make sure the exception is captured and the analyzer shouldn't fail.
    api.step_data('git show', retcode=128),
    api.step_data(
      'metrics.metrics_output',
      api.file.read_proto(
        findings_pb.Findings(
          findings=[
            findings_pb.Finding(
              category="chromium_metrics",
              message="Removed",
              severity_level=findings_pb.Finding.SEVERITY_LEVEL_ERROR,
              location=findings_pb.Location(
                file_path="testdata/src/test/histograms.xml"
              ),
            ),
          ]
        )
      ),
    ),
    api.post_process(post_process.DoesNotRun, 'metrics.load_test_analyzer'),
    api.post_process(post_process.StepSuccess, 'metrics.load_prod_analyzer'),
    api.post_process(post_process.StepSuccess, 'metrics'),
    api.post_process(post_process.MustRun, 'metrics.upload findings'),
    api.post_process(post_process.DropExpectation),
  )
