# Copyright 2025 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
from recipe_engine.post_process import StatusFailure, StatusSuccess
from PB.recipe_engine import result as result_pb
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb

SKIP_FOOTER = 'Metadata-Validate-Bypass'
BYPASS_TEXT = ('\n\nTo `bypass this check, add '
               f'\'{SKIP_FOOTER}: \\<REASON\\>\' '
               'to your CL description.')

BYPASSED_TEXT = '\n\n<b>Validation bypassed by footer.</b>'

DEPS = [
    'chromium',
    'chromium_checkout',
    'depot_tools/bot_update',
    'depot_tools/depot_tools',
    'depot_tools/gclient',
    'depot_tools/tryserver',
    'recipe_engine/context',
    'recipe_engine/path',
    'recipe_engine/platform',
    'recipe_engine/properties',
    'recipe_engine/step',
    'recipe_engine/raw_io',
    'recipe_engine/file',
]


def format_validation_summary(all_results, bypass_validation):
  failure_message_lines = []

  # Determine if we have multiple files with issues
  multi_file = len(all_results) > 1

  for filename, res in all_results.items():
    # Only disambiguate filenames if multiple files involved.
    if multi_file:
      failure_message_lines.append(f'{filename}:')

    file_errors = res['errors']
    file_warnings = res['warnings']

    # Formatting logic
    messages = []
    if file_errors:
      messages.extend(file_errors)
    if file_warnings:
      messages.extend(file_warnings)

    if len(messages) == 1 and not multi_file:
      failure_message_lines.append(messages[0])
    else:
      failure_message_lines.extend([f'* {m}' for m in messages])

    if multi_file:
      failure_message_lines.append('')  # Spacer

  if not failure_message_lines:
    return None

  failure_msg = '\n'.join(failure_message_lines)
  if bypass_validation:
    failure_msg += BYPASSED_TEXT
  else:
    failure_msg += BYPASS_TEXT

  return failure_msg


def RunSteps(api):
  # Ensure depot_tools is on PATH.
  with api.depot_tools.on_path():

    # Check for bypass footer
    bypass_validation = bool(api.tryserver.get_footers(SKIP_FOOTER))
    if bypass_validation:
      api.step('Validation bypassed', cmd=None)

    # Set up standard chromium checkout paths.
    builder_dir = api.path.cache_dir / 'builder'
    source_dir = builder_dir / 'src'
    api.file.ensure_directory('ensure builder dir', builder_dir)

    api.chromium_checkout.set_paths(builder_dir, source_dir)

    # Update Chrome source in the builder cache.
    with api.context(cwd=builder_dir):
      api.gclient.set_config('chromium')
      api.chromium_checkout.ensure_checkout()

      files = api.chromium_checkout.get_files_affected_by_patch()
      readme_files = [f for f in files if str(f).endswith('README.chromium')]
      if not readme_files:
        api.step('no readme changes', cmd=None)
        return

      all_results = {}
      any_errors_found = False

      for readme_rel_path in readme_files:
        # scan.py expects a directory to scan.
        readme_dir = api.path.dirname(
            api.path.join(source_dir, readme_rel_path))
        with api.step.nest(f'validate {readme_rel_path}') as step:
          cmd = [
              'vpython3',
              api.depot_tools.root.joinpath('metadata', 'scan.py'),
              readme_dir,
          ]
          step_result = api.step(
              f'run validator on {readme_rel_path}',
              cmd,
              stdout=api.raw_io.output_text(add_output_log=True),
              raise_on_failure=False)

          # Check stdout/stderr for errors and warnings.
          errors = []
          warnings = []
          if step_result.stdout:
            # metadata/scan.py prints a summary after "Done.".
            # We only care about the detailed errors before that.
            output_body = step_result.stdout.split('Done.', 1)[0]
            for line in output_body.splitlines():
              line = line.strip()
              if 'ERROR -' in line:
                errors.append(line)
              elif 'WARNING -' in line:
                warnings.append(line)

          if errors or warnings:
            all_results[readme_rel_path] = {
                'errors': errors,
                'warnings': warnings
            }

          step.logs['debug_errors'] = errors
          step.logs['debug_warnings'] = warnings

          if errors:
            any_errors_found = True
            step.status = api.step.FAILURE
            if bypass_validation:
              step.status = api.step.WARNING

            step.logs['errors'] = errors
            step.step_text = '\n'.join(errors)

          if warnings:
            if step.status != api.step.FAILURE:
              step.status = api.step.WARNING
            step.logs['warnings'] = warnings
            step.step_text += '\n' + '\n'.join(warnings)

      # 6. Fail if issues found (and format nice message)
      failure_msg = format_validation_summary(all_results, bypass_validation)

      if not failure_msg:
        return  # No issues found

      step_res = api.step('Validation Issues', cmd=None)
      step_res.presentation.step_text = failure_msg

      if any_errors_found and not bypass_validation:
        raise api.step.StepFailure(failure_msg)

      return result_pb.RawResult(
          status=common_pb.SUCCESS, summary_markdown=failure_msg)


def GenTests(api):
  yield api.test(
      'basic_success',
      api.chromium.try_build(builder='linux-metadata-validator'),
      api.tryserver.get_files_affected_by_patch(
          ['src/third_party/foo/README.chromium']) +
      api.step_data(
          'validate src/third_party/foo/README.chromium.run validator on src/third_party/foo/README.chromium',
          stdout=api.raw_io.output_text('Found 1 metadata files.\nDone.')),
      api.post_process(StatusSuccess),
  )

  yield api.test(
      'validation_failure',
      api.chromium.try_build(builder='linux-metadata-validator'),
      api.tryserver.get_files_affected_by_patch(
          ['src/third_party/bar/README.chromium']) +
      api.step_data(
          'validate src/third_party/bar/README.chromium.run validator on src/third_party/bar/README.chromium',
          stdout=api.raw_io.output_text(
              'Found 1 metadata files.\n'
              'ERROR - Required field \'Name\' is missing.\n'
              'Done.')),
      api.expect_status('FAILURE'),
      api.post_process(StatusFailure),
  )

  yield api.test(
      'bypass_validation',
      api.chromium.try_build(builder='linux-metadata-validator'),
      api.tryserver.get_files_affected_by_patch(
          ['src/third_party/bar/README.chromium']) +
      api.tryserver.get_footers({SKIP_FOOTER: ['reason for bypass']}) +
      api.step_data(
          'validate src/third_party/bar/README.chromium.run validator on src/third_party/bar/README.chromium',
          stdout=api.raw_io.output_text(
              'Found 1 metadata files.\n'
              'ERROR - Required field \'Name\' is missing.\n'
              'Done.')),
      api.post_process(StatusSuccess),
  )

  yield api.test(
      'no_readme',
      api.chromium.try_build(builder='linux-metadata-validator'),
      api.tryserver.get_files_affected_by_patch(['src/foo/bar.txt']),
      api.post_process(StatusSuccess),
  )

  yield api.test(
      'warnings_only',
      api.chromium.try_build(builder='linux-metadata-validator'),
      api.tryserver.get_files_affected_by_patch(
          ['src/third_party/baz/README.chromium']) +
      api.step_data(
          'validate src/third_party/baz/README.chromium.run validator on src/third_party/baz/README.chromium',
          stdout=api.raw_io.output_text('Found 1 metadata files.\n'
                                        'WARNING - License file not found.\n'
                                        'Done.')),
      api.post_process(StatusSuccess),
  )

  yield api.test(
      'errors_and_warnings',
      api.chromium.try_build(builder='linux-readme-validator'),
      api.tryserver.get_files_affected_by_patch(
          ['src/third_party/mixed/README.chromium']) +
      api.step_data(
          'validate src/third_party/mixed/README.chromium.run validator on src/third_party/mixed/README.chromium',
          stdout=api.raw_io.output_text('Found 1 metadata files.\n'
                                        'ERROR - Critical issue.\n'
                                        'WARNING - Minor issue.\n'
                                        'Done.')),
      api.expect_status('FAILURE'),
      api.post_process(StatusFailure),
  )
  yield api.test(
      'multiple_readmes_with_issues',
      api.chromium.try_build(builder='linux-metadata-validator'),
      api.tryserver.get_files_affected_by_patch([
          'src/third_party/foo/README.chromium',
          'src/third_party/bar/README.chromium',
      ]) + api.step_data(
          'validate src/third_party/foo/README.chromium.run validator on src/third_party/foo/README.chromium',
          stdout=api.raw_io.output_text(
              'Found 1 metadata files.\n'
              'ERROR - Required field \'Name\' is missing.\n'
              'WARNING - License file not found.\n'
              'Done.')) +
      api.step_data(
          'validate src/third_party/bar/README.chromium.run validator on src/third_party/bar/README.chromium',
          stdout=api.raw_io.output_text(
              'Found 1 metadata files.\n'
              'ERROR - Required field \'Description\' is missing.\n'
              'Done.')),
      api.expect_status('FAILURE'),
      api.post_process(StatusFailure),
  )
