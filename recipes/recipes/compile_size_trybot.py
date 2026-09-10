# Copyright 2024 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import re

from recipe_engine import post_process
from recipe_engine.config_types import Path
from PB.recipes.build.compile_size_trybot import InputProperties

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import binary_size, siso
from RECIPE_MODULES.recipe_engine import (
  buildbucket,
  context,
  file,
  path,
  properties,
  raw_io,
  step,
)


@dataclass
class DEPS(RecipeScriptApi):
  binary_size: binary_size.API
  buildbucket: buildbucket.API
  context: context.API
  file: file.API
  path: path.API
  properties: properties.API
  raw_io: raw_io.API
  siso: siso.API
  step: step.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  binary_size: binary_size.TEST_API
  raw_io: raw_io.TEST_API


PROPERTIES = InputProperties

BEFORE_LINE_RE = re.compile('Before: .* \((.*)\)')
DELTA_LINE_RE = re.compile('Delta: .* \((.*)\)')

DEFAULT_SIZE_THRESHOLD_MIB = 500


def RunSteps(api: DEPS, properties):

  def create_diffs(
    author,
    review_subject,
    review_url,
    source_dir: Path,
    size_config_json_name,
    before_dir,
    after_dir,
    results_path,
    staging_dir,
    affected_files=None,
    **kwargs,
  ):
    diff_script = (
      source_dir / 'tools/clang/scripts/compiler_inputs_size_diff.py'
    )
    cmd = [diff_script, before_dir / 'size.txt', after_dir / 'size.txt']
    result = api.step(
      name='Generate diffs',
      cmd=cmd,
      stdout=api.raw_io.output_text(leak_to=staging_dir / 'diff.txt'),
    )
    lines = result.stdout.splitlines()
    try:
      before_matches = BEFORE_LINE_RE.match(lines[0])
      delta_matches = DELTA_LINE_RE.match(lines[2])
      before = int(before_matches.group(1))
      delta = int(delta_matches.group(1))
      # Make the stats a markdown list.
      summary = '\n'.join(f'* {line}' for line in lines[:4])
    except (ValueError, IndexError, AttributeError) as e:
      raise api.step.InfraFailure(
        f'Failed to parse compile size delta report: {e}'
      )
    status_code = 0
    size_threshold_mib = api.properties.get(
      'size_threshold_mib', DEFAULT_SIZE_THRESHOLD_MIB
    )
    if delta >= size_threshold_mib * 1024 * 1024:
      status_code = 1
      summary = (
        f'Compile size check failed! Delta ({delta / 1024 / 1024:.2f} MiB) '
        + f'exceeds threshold ({size_threshold_mib:.2f} MiB)\n\n{summary}\n\n'
        + 'See the following document for more information:\n'
        + 'https://chromium.googlesource.com/chromium/src/+/main/docs/speed/binary_size/compile_size_builder.md'
      )
    write_results = api.file.write_json(
      'Write size results',
      results_path,
      {
        'archive_filenames': [],
        'links': [],
        'status_code': status_code,
        'summary': summary,
        'uncompressed': delta,
      },
    )
    if status_code:
      # We want to draw attention to this step so that people can find the
      # TU breakdown log. This would be better as a warning, i.e. with
      # crbug.com/40581344.
      write_results.presentation.status = api.step.FAILURE
    write_results.presentation.logs['compile_size_deltas.txt'] = lines
    write_results.presentation.properties['compile_size'] = {
      'before': before,
      'delta': delta,
    }

  def analyze_compile_size(
    source_dir: Path, build_dir: Path, staging_dir: Path
  ):
    # Get all commands required to build `chrome`.
    siso_query_commands_cmd = [
      api.siso.siso_path(source_dir),
      'query',
      'commands',
      '-C',
      str(build_dir),
      'chrome',
    ]
    # Get all dependencies stored in deps logs to build `chrome`.
    siso_query_deps_cmd = [
      api.siso.siso_path(source_dir),
      'query',
      'deps',
      '-C',
      str(build_dir),
    ]
    compiler_inputs_size_cmd = [
      source_dir / 'tools/clang/scripts/compiler_inputs_size.py',
      build_dir,
      staging_dir / 'commands.txt',
      staging_dir / 'deps.txt',
    ]
    with api.context(cwd=source_dir, infra_steps=True):
      api.step(
        'check siso version', [api.siso.siso_path(source_dir), 'version']
      )
      api.step(
        'Run siso query commands chrome',
        siso_query_commands_cmd,
        stdout=api.raw_io.output(leak_to=staging_dir / 'commands.txt'),
      )
      api.step(
        'Run siso query deps',
        siso_query_deps_cmd,
        stdout=api.raw_io.output(leak_to=staging_dir / 'deps.txt'),
      )
      api.step(
        'Measure compiler inputs size',
        compiler_inputs_size_cmd,
        stdout=api.raw_io.output(leak_to=staging_dir / 'size.txt'),
      )

  return api.binary_size.compare_size(
    chromium_config='chromium',
    chromium_apply_configs=['mb'],
    gclient_config='chromium',
    binary_size_footer='Compile-Size',
    diff_func=create_diffs,
    analysis_func=analyze_compile_size,
    analysis_warning_statuses={},
  )


def GenTests(api: TEST_DEPS):

  def check_sizes(check, steps, before=None, delta=None):
    diff_output_properties = steps['Write size results'].output_properties
    check(diff_output_properties['compile_size']['before'] == before)
    check(diff_output_properties['compile_size']['delta'] == delta)

  yield api.test(
    'big_delta',
    api.binary_size.build(),
    api.override_step_data(
      'Generate diffs',
      api.raw_io.stream_output_text(
        'Before: 10 GiB (10737418240)\nX\nDelta: 1 GiB (+1073741824)\n',
        stream='stdout',
      ),
    ),
    api.post_check(check_sizes, before=10737418240, delta=1073741824),
    api.expect_status('SUCCESS'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'negative_delta',
    api.binary_size.build(),
    api.override_step_data(
      'Generate diffs',
      api.raw_io.stream_output_text(
        'Before: 10 GiB (10737418240)\nX\nDelta: -1 GiB (-1073741824)\n',
        stream='stdout',
      ),
    ),
    api.post_check(check_sizes, before=10737418240, delta=-1073741824),
    api.expect_status('SUCCESS'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'fail_compile',
    api.binary_size.build(),
    api.override_step_data('compile (with patch)', retcode=1),
    api.expect_status('FAILURE'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'fail_siso_query',
    api.binary_size.build(),
    api.override_step_data('Run siso query deps', retcode=1),
    api.expect_status('INFRA_FAILURE'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'fail_delta_parsing',
    api.binary_size.build(),
    api.override_step_data(
      'Generate diffs',
      api.raw_io.stream_output_text('Some unparsable output', stream='stdout'),
    ),
    api.expect_status('INFRA_FAILURE'),
    api.post_process(post_process.DropExpectation),
  )
