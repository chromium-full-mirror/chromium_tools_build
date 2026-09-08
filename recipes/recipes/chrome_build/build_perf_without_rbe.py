# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Recipe to measure build time.
   See also go/chrome-build-time
"""

import json

from recipe_engine import post_process
from recipe_engine.config_types import Path

from RECIPE_MODULES.build import chromium_types
from RECIPE_MODULES.build import chromium_tests_builder_config as ctbc

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import (
    builder_group,
    chromium,
    chromium_build_perf,
    chromium_checkout,
    chromium_tests,
    chromium_tests_builder_config,
    siso,
)
from RECIPE_MODULES.depot_tools import gsutil
from RECIPE_MODULES.recipe_engine import (
    buildbucket,
    context,
    file,
    json as json_module,
    path,
    platform,
    properties,
    raw_io,
    step,
    time,
)


@dataclass
class DEPS(RecipeScriptApi):
  buildbucket: buildbucket.API
  builder_group: builder_group.API
  chromium: chromium.API
  chromium_build_perf: chromium_build_perf.API
  chromium_checkout: chromium_checkout.API
  chromium_tests: chromium_tests.API
  chromium_tests_builder_config: chromium_tests_builder_config.API
  context: context.API
  file: file.API
  gsutil: gsutil.API
  json: json_module.API
  path: path.API
  platform: platform.API
  properties: properties.API
  raw_io: raw_io.API
  siso: siso.API
  step: step.API
  time: time.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  buildbucket: buildbucket.TEST_API
  chromium: chromium.TEST_API
  chromium_tests_builder_config: chromium_tests_builder_config.TEST_API
  context: context.TEST_API
  file: file.TEST_API
  json: json_module.TEST_API
  path: path.TEST_API
  raw_io: raw_io.TEST_API
  siso: siso.TEST_API
  step: step.TEST_API

# TODO: Create a new bucket to make the include analysis public.
_GS_BUCKET = 'chrome-goma-log'


def _compile(
    api: DEPS,
    source_dir: Path,
    build_dir: Path,
    target,
    resource_usage_output_file: Path,
    build_log: Path,
):
  api.chromium_build_perf.recreate_build_dir(
      source_dir, build_dir, remove_deps_cache=True)
  with api.context(cwd=build_dir):
    cmd = [
        'ninja',
        '-C',
        build_dir,
        '-v',
        target,
    ]
    step_result = api.siso.run_ninja(
        source_dir,
        cmd,
        stdout=api.raw_io.output(leak_to=build_log),
        resource_usage_output_file=resource_usage_output_file,
    )
    rusage = api.file.read_json(
        'read resource usage log',
        resource_usage_output_file,
        test_data={'ru_utime': '0:01.00'},
    )
    return step_result, rusage


def _analyze_includes(
    api: DEPS,
    target: str,
    source_dir: Path,
    staging_dir: Path,
    build_log: Path,
):
  with api.step.nest('analyze includes') as parent_step:
    result_js_file = staging_dir / 'include-analysis.js'
    cmd = [
        'python3',
        'tools/clang/scripts/analyze_includes.py',
        '--target=%s' % target,
        '--revision=%s' % api.buildbucket.build.input.gitiles_commit.id,
        '--json-out=%s' % result_js_file,
        build_log,
    ]
    with api.context(cwd=source_dir):
      api.step('analyze includes', cmd)

    # It starts with `data = ` to be included as JS file.
    # https://source.chromium.org/chromium/chromium/src/+/main:tools/clang/scripts/analyze_includes.py;l=438-440;drc=b9664d07c6b204b35a6e42141ba9581e261624fb
    test_js_data = 'data = ' + json.dumps({
        'target': target,
        'revision': 'abcd',
        'date': None,
        'files': [
            'a.cc',
            'a.h',
        ],
        'roots': [0],
        'includes': [[1]],
        'included_by': [[], [0]],
        'sizes': [10, 20],
        'tsizes': [30, 20],
    })
    js_data_raw = api.file.read_text(
        'read include analysis result', result_js_file, test_data=test_js_data)
    if js_data_raw.startswith('data = '):
      result_json = js_data_raw[len('data = '):]
    analysis_result = json.loads(result_json)

    # Read include-analysis.html and replace <script> tag with the JS data.
    analysis_html = api.file.read_text(
        'read include-analysis.html',
        source_dir / 'tools/clang/scripts/include-analysis.html')
    analysis_html = analysis_html.replace(
        '<script src="include-analysis.js"></script>',
        f'<script>{js_data_raw}</script>')
    archive_file = api.path.mkstemp()
    api.file.write_text('write archive file', archive_file, analysis_html)

    # Upload include-analysis.html to GCS.
    upload_path = 'chrome-includes-analysis/%s/%d/%s' % (
        api.buildbucket.builder_full_name,
        api.buildbucket.build.number,
        'include-analysis.html',
    )
    upload_result = api.gsutil.upload(
        archive_file,
        _GS_BUCKET,
        upload_path,
        link_name="include-analysis.html",
        metadata={'Content-Type': 'text/html'},
        name='upload include-analysis.html')

    archive_link = upload_result.presentation.links['include-analysis.html']
    analysis_result['archive_link'] = archive_link
    parent_step.links['include-analysis.html'] = archive_link
    parent_step.logs['analysis_result'] = api.json.dumps(
        analysis_result, indent=2)

    return analysis_result


def RunSteps(api: DEPS):
  # Set up a named cache so runhooks doesn't redownload everything on each run.
  solution_path = api.path.cache_dir / 'builder'
  api.file.ensure_directory('init cache if not exists', solution_path)

  builder_id = chromium_types.BuilderId.create_for_group(
      api.builder_group.for_current, api.buildbucket.builder_name)
  _, builder_config = api.chromium_tests_builder_config.lookup_builder(
      builder_id, use_try_db=False)
  api.chromium_tests.configure_build(builder_config)

  update_result = api.chromium_checkout.ensure_checkout()
  source_dir = update_result.source_root.path
  build_dir = api.chromium.default_build_dir(source_dir)
  api.chromium.ensure_toolchains(checkout_dir=update_result.checkout_dir)

  with api.context(cwd=solution_path):
    api.chromium.runhooks(source_dir, build_dir)

  staging_dir = api.path.mkdtemp()
  resource_usage_output_file = staging_dir / 'resource_usage.json'
  build_log = staging_dir / 'build_log'
  # Build target: chrome
  target = 'chrome'
  _, rusage = _compile(
      api,
      source_dir,
      build_dir,
      target,
      resource_usage_output_file,
      build_log,
  )

  include_analysis = _analyze_includes(api, target, source_dir, staging_dir,
                                       build_log)

  api.chromium_build_perf.upload_build_stats_to_bq(rusage, include_analysis)

  # Remove the out dir to reduce the builder cache size.
  api.file.rmtree('rmtree %s' % str(build_dir), str(build_dir))


def GenTests(api: TEST_DEPS):
  ctbc_api = api.chromium_tests_builder_config

  # Test data.
  builder = {
      'builder_group': 'fake-group',
      'builder': 'fake-builder',
  }

  yield api.test(
      'full_linux',
      api.chromium.ci_build(**builder),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_ci_builder(
              builder_spec=ctbc.BuilderSpec.create(
                  gclient_config='chromium',
                  chromium_config='chromium',
              ),
              **builder).assemble()),
      api.siso.properties(),
      api.post_process(post_process.StepCommandContains, 'compile',
                       ['/usr/bin/time']),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'build_failure',
      api.chromium.ci_build(**builder),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_ci_builder(
              builder_spec=ctbc.BuilderSpec.create(
                  gclient_config='chromium',
                  chromium_config='chromium',
              ),
              **builder).assemble()),
      api.siso.properties(),
      api.step_data('compile', retcode=1),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )
