# Copyright 2025 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
from recipe_engine.post_process import StatusFailure, StatusSuccess

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


def RunSteps(api):
  # Ensure depot_tools is on PATH.
  with api.depot_tools.on_path():

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

      failed_files = []
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
            for line in step_result.stdout.splitlines():
              if 'ERROR -' in line:
                errors.append(line)
              elif 'WARNING -' in line:
                warnings.append(line)

          if errors:
            step.status = api.step.FAILURE
            step.logs['errors'] = errors
            failed_files.append(readme_rel_path)

          if warnings:
            step.logs['warnings'] = warnings

      # 6. Fail if issues found.
      if failed_files:
        raise api.step.StepFailure('Validation failed for: %s' % failed_files)


def GenTests(api):
  yield api.test(
      'basic_success',
      api.chromium.try_build(builder='linux-readme-validator'),
      api.tryserver.get_files_affected_by_patch(
          ['src/third_party/foo/README.chromium']) +
      api.step_data(
          'validate src/third_party/foo/README.chromium.run validator on src/third_party/foo/README.chromium',
          stdout=api.raw_io.output_text('Found 1 metadata files.\nDone.')),
      api.post_process(StatusSuccess),
  )

  yield api.test(
      'validation_failure',
      api.chromium.try_build(builder='linux-readme-validator'),
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
      'no_readme',
      api.chromium.try_build(builder='linux-readme-validator'),
      api.tryserver.get_files_affected_by_patch(['src/foo/bar.txt']),
      api.post_process(StatusSuccess),
  )

  yield api.test(
      'warnings_only',
      api.chromium.try_build(builder='linux-readme-validator'),
      api.tryserver.get_files_affected_by_patch(
          ['src/third_party/baz/README.chromium']) +
      api.step_data(
          'validate src/third_party/baz/README.chromium.run validator on src/third_party/baz/README.chromium',
          stdout=api.raw_io.output_text('Found 1 metadata files.\n'
                                        'WARNING - License file not found.\n'
                                        'Done.')),
      api.post_process(StatusSuccess),
  )
