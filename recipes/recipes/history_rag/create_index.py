# Copyright 2025 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Creates index for history-rag."""

from recipe_engine import post_process
from RECIPE_MODULES.build import chromium_tests_builder_config as ctbc

DEPS = [
    'build/chromium',
    'build/chromium_checkout',
    'build/chromium_tests',
    'build/chromium_tests_builder_config',
    'depot_tools/gsutil',
    'recipe_engine/buildbucket',
    'recipe_engine/cipd',
    'recipe_engine/context',
    'recipe_engine/file',
    'recipe_engine/json',
    'recipe_engine/path',
    'recipe_engine/raw_io',
    'recipe_engine/step',
    'recipe_engine/properties',
]

BASE_PKG_NAME = 'infra/history_rag'
MANIFEST_PKG_NAME = '%s/manifest' % BASE_PKG_NAME


def RunSteps(api):
  source_dir, revision = checkout_source_code(api)
  generate_blame_jsons(api, source_dir, revision)


def checkout_source_code(api):
  with api.step.nest('Checkout Chrome Source Code'):
    _, builder_config = api.chromium_tests_builder_config.lookup_builder()
    api.chromium_tests.configure_build(builder_config)
    update_result = api.chromium_checkout.ensure_checkout()
    checkout_dir = update_result.checkout_dir
    source_dir = update_result.source_root.path
    build_dir = api.chromium.default_build_dir(source_dir)
    api.chromium.ensure_toolchains(checkout_dir)
    api.chromium.runhooks(source_dir, build_dir, clobber=True)

    # TODO: Change this to got_revision for chrome
    revision = update_result.properties.get('got_v8_revision', 'fake_revision')

    return source_dir, revision


def generate_blame_jsons(api, source_dir, current_revision):
  with api.step.nest('Generate Blame JSONs'):
    baseline_pkg = get_baseline_package_info(api)
    if baseline_pkg:
      baseline_blame_jsons_dir = download_baseline_blame_jsons(
          api, baseline_pkg)
      fresh_blame_jsons_dir = generate_fresh_blame_jsons(
          api, source_dir, baseline_pkg)
      # Merge the fresh jsons into the baseline directory
      cmd = [
          'vpython3',
          api.resource('merge_blame_jsons.py'), baseline_blame_jsons_dir,
          fresh_blame_jsons_dir
      ]
      _ = api.step('merge fresh and baseline blame jsons', cmd)
      # Remove the .cipd metadata directory from the baseline after merging
      cipd_meta_dir = baseline_blame_jsons_dir / '.cipd'
      if api.path.exists(cipd_meta_dir):
        api.file.rmtree("Remove .cipd from merged set", cipd_meta_dir)
      merged_dir = baseline_blame_jsons_dir
    else:
      # No baseline, so the "merged" directory is just the fresh ones
      merged_dir = generate_fresh_blame_jsons(api, source_dir, None)

    # Upload the prepared directory
    pkg_name, instance_id = update_blame_jsons_cipd(api, merged_dir,
                                                    current_revision)

    # update manifest package to point to new cipd
    update_baseline_package_info(api, pkg_name, instance_id)


def get_baseline_package_info(api):
  """
  Fetches a pointer package, reads a manifest file, and returns the content.

  The pointer package 'infra/history_rag/manifest' is expected to contain
  a 'manifest.json' file specifying the actual data package and version.

  Returns:
    dict: The parsed content of manifest.json if successful.
    None: If the pointer package 'infra/history_rag/manifest' with ref 'latest'
          does not exist.

  Raises:
    api.step.StepFailure: If manifest.json is missing, malformed, or
                          lacks required keys ('package', 'version').
  """
  dest_path = api.path.cleanup_dir / 'manifest'
  # Step 1: Check if the manifest package exists at 'latest'
  with api.step.nest('Check for manifest package') as presentation:
    try:
      api.cipd.describe(MANIFEST_PKG_NAME, 'latest')
    except api.step.StepFailure:
      presentation.logs['info'] = [
          f"Manifest package {MANIFEST_PKG_NAME}:latest not found. Returning None."
      ]
      presentation.status = api.step.WARNING
      return None

  # Step 2: Download the pointer package
  with api.step.nest('Fetch manifest package from CIPD'):
    pkgs = api.cipd.EnsureFile()
    pkgs.add_package(MANIFEST_PKG_NAME, 'latest')
    api.cipd.ensure(dest_path, pkgs)

  manifest_file_path = dest_path / 'manifest.json'
  # Step 3: Check if manifest.json exists
  if not api.path.exists(manifest_file_path):
    raise api.step.StepFailure(
        f"Manifest file not found in CIPD package {MANIFEST_PKG_NAME}")

  # Step 4: Read and parse the JSON file
  manifest_data = api.file.read_json(
      'Read manifest.json',
      manifest_file_path,
      test_data={
          'package': 'infra/history_rag/default_test_pkg',
          'version': 'default-test-version-id'
      })

  package = manifest_data.get('package')
  version = manifest_data.get('version')
  if not package or not isinstance(package, str):
    raise api.step.StepFailure(
        "Key 'package' missing or not a string in manifest.json")
  if not version or not isinstance(version, str):
    raise api.step.StepFailure(
        "Key 'version' missing or not a string in manifest.json")
  expected_prefix = BASE_PKG_NAME + '/'
  if not package.startswith(expected_prefix):
    raise api.step.StepFailure(
        f"Invalid package name format: '{package}'. Expected to start with '{expected_prefix}'"
    )

  return manifest_data


def download_baseline_blame_jsons(api, baseline_package):
  dest_path = api.path.cleanup_dir / 'blame_jsons'
  with api.step.nest('Fetch baseline blame jsons'):
    pkgs = api.cipd.EnsureFile()
    pkgs.add_package(baseline_package['package'], baseline_package['version'])
    api.cipd.ensure(dest_path, pkgs)
  return dest_path


def generate_fresh_blame_jsons(api, source_dir, baseline_pkg):
  output_dir = api.path.cleanup_dir / 'output'
  cmd = ['vpython3', api.resource('git_data_processor.py'), 'blame']
  if baseline_pkg:
    baseline_commit = baseline_pkg['package'].split('/')[-1]
    cmd += ['--baseline-commit', baseline_commit]
  cmd += ['--source-dir', source_dir, '--output-dir', output_dir, 'v8']
  _ = api.step('Generate fresh blame index', cmd)
  return output_dir


def _extract_cipd_instance_id(api, step_result):
  """Extracts the instance ID from cipd create stdout."""
  if step_result.stdout:
    # A correct output looks like
    # Instance: infra/history_rag/revision:instance-id'
    for line in step_result.stdout.splitlines():
      if 'Instance:' in line and len(line.split(':')) == 3:
        instance_id = line.split(':')[2].strip()
        return instance_id
  return None


def update_blame_jsons_cipd(api, blame_json_dir, current_revision):
  package_name = f"{BASE_PKG_NAME}/{current_revision}"
  description = f"Blame jsons for chrome at commit: {current_revision}"

  create_cmd = [
      'cipd', 'create', '-name', package_name, '-in', blame_json_dir,
      '-install-mode', 'copy', '-metadata', f"description:{description}"
  ]

  create_step = api.step(
      name='Upload blame jsons to cipd',
      cmd=create_cmd,
      stdout=api.raw_io.output_text(),
      stderr=api.raw_io.output_text(),
  )

  instance_id = _extract_cipd_instance_id(api, create_step)

  if instance_id:
    api.step.active_result.presentation.logs['instance_id'] = instance_id
    api.step.active_result.presentation.links['Blame Indexes Package'] = (
        f"https://chrome-infra-packages.appspot.com/p/{package_name}/+/{instance_id}"
    )

    api.step(
        name=f'set cipd ref latest for {package_name}',
        cmd=[
            'cipd', 'set-ref', package_name, '-ref', 'latest', '-version',
            instance_id
        ])
  else:
    api.step.active_result.presentation.logs['cipd_stdout'] = create_step.stdout
    api.step.active_result.presentation.logs['cipd_stderr'] = create_step.stderr
    raise api.step.StepFailure(
        "CIPD instance ID not found in output for blame package")
  return package_name, instance_id


def update_baseline_package_info(api, package, version):
  manifest_content = {
      "package": package,
      "version": version,
  }
  temp_dir = api.path.mkdtemp(prefix='manifest_')
  manifest_file_path = temp_dir / 'manifest.json'
  api.file.write_json(
      name='Create manifest.json',
      dest=manifest_file_path,
      data=manifest_content,
  )
  create_cmd = [
      'cipd', 'create', '-name', MANIFEST_PKG_NAME, '-in', temp_dir,
      '-install-mode', 'copy'
  ]
  create_step = api.step(
      name='Upload new manifest to cipd',
      cmd=create_cmd,
      stdout=api.raw_io.output_text(),
      stderr=api.raw_io.output_text(),
  )
  instance_id = _extract_cipd_instance_id(api, create_step)
  if instance_id:
    api.step.active_result.presentation.logs[
        'manifest_instance_id'] = instance_id
    api.step.active_result.presentation.links['Manifest Package'] = (
        f"https://chrome-infra-packages.appspot.com/p/{MANIFEST_PKG_NAME}/+/{instance_id}"
    )
    api.step(
        name=f'Set cipd ref latest for {MANIFEST_PKG_NAME}',
        cmd=[
            'cipd', 'set-ref', MANIFEST_PKG_NAME, '-ref', 'latest', '-version',
            instance_id
        ])
  else:
    api.step.active_result.presentation.logs['cipd_stdout'] = create_step.stdout
    api.step.active_result.presentation.logs['cipd_stderr'] = create_step.stderr
    raise api.step.StepFailure(
        "CIPD instance ID not found in output for manifest package")


# Default builder config for tests
def builder_config_test_data(api):
  return api.chromium_tests_builder_config.ci_build(
      builder_group='fake-group',
      builder='fake-builder',
      revision='newrevision',
      builder_db=ctbc.BuilderDatabase.create({
          'fake-group': {
              'fake-builder':
                  ctbc.BuilderSpec.create(
                      chromium_config='chromium',
                      chromium_apply_config=['mb'],
                      gclient_config='chromium',
                  ),
          },
      }))

def GenTests(api):
  # Basic test case: Baseline manifest exists and is valid.
  yield api.test(
      'basic_with_baseline',
      builder_config_test_data(api),
      api.properties(**{
          '$build/chromium_checkout': {
              'gclient_config': 'chromium',
          },
      }),
      # Mock cipd describe for manifest package - found
      api.step_data(
          'Generate Blame JSONs.Check for manifest package.cipd describe infra/history_rag/manifest',
          retcode=0),
      api.path.exists(api.path.cleanup_dir / 'manifest/manifest.json',
                      api.path.cleanup_dir / 'blame_jsons' / '.cipd'),
      # Mock manifest.json content
      api.step_data(
          'Generate Blame JSONs.Read manifest.json',
          api.file.read_json({
              'package': 'infra/history_rag/oldrevision',
              'version': 'baseline-version-id'
          })),
      # Mock cipd create for blame package
      api.step_data(
          'Generate Blame JSONs.Upload blame jsons to cipd',
          stdout=api.raw_io.output_text(
              'Instance: infra/history_rag/newrevision:new-blame-instance-id')),
      # Mock cipd create for manifest package
      api.step_data(
          'Generate Blame JSONs.Upload new manifest to cipd',
          stdout=api.raw_io.output_text(
              'Instance: infra/history_rag/manifest:new-manifest-instance-id')),
      api.post_process(post_process.MustRun,
                       'Checkout Chrome Source Code.gclient runhooks'),
      api.post_process(post_process.MustRun,
                       'Generate Blame JSONs.Check for manifest package'),
      api.post_process(post_process.MustRun,
                       'Generate Blame JSONs.Fetch manifest package from CIPD'),
      api.post_process(post_process.MustRun,
                       'Generate Blame JSONs.Read manifest.json'),
      api.post_process(post_process.MustRun,
                       'Generate Blame JSONs.Fetch baseline blame jsons'),
      api.post_process(post_process.MustRun,
                       'Generate Blame JSONs.Generate fresh blame index'),
      api.post_process(
          post_process.MustRun,
          'Generate Blame JSONs.merge fresh and baseline blame jsons'),
      api.post_process(post_process.MustRun,
                       'Generate Blame JSONs.Upload blame jsons to cipd'),
      api.post_process(post_process.MustRun,
                       'Generate Blame JSONs.Create manifest.json'),
      api.post_process(post_process.MustRun,
                       'Generate Blame JSONs.Upload new manifest to cipd'),
      api.post_process(
          post_process.MustRun,
          'Generate Blame JSONs.Set cipd ref latest for infra/history_rag/manifest'
      ),
      api.post_process(post_process.DropExpectation),
  )

  # Test case: No baseline manifest package exists.
  yield api.test(
      'no_baseline',
      builder_config_test_data(api),
      api.properties(**{
          '$build/chromium_checkout': {
              'gclient_config': 'chromium',
          },
      }),
      # Mock cipd describe for manifest package - not found
      api.step_data(
          'Generate Blame JSONs.Check for manifest package.cipd describe infra/history_rag/manifest',
          retcode=1),
      # Mock cipd create for blame package
      api.step_data(
          'Generate Blame JSONs.Upload blame jsons to cipd',
          stdout=api.raw_io.output_text(
              'Instance: infra/history_rag/newrevision:new-blame-instance-id')),
      # Mock cipd create for manifest package
      api.step_data(
          'Generate Blame JSONs.Upload new manifest to cipd',
          stdout=api.raw_io.output_text(
              'Instance: infra/history_rag/manifest:new-manifest-instance-id')),
      api.post_process(post_process.DoesNotRun,
                       'Generate Blame JSONs.Fetch manifest package from CIPD'),
      api.post_process(post_process.DoesNotRun,
                       'Generate Blame JSONs.Fetch baseline blame jsons'),
      api.post_process(
          post_process.DoesNotRun,
          'Generate Blame JSONs.merge fresh and baseline blame jsons'),
      api.post_process(post_process.MustRun,
                       'Generate Blame JSONs.Generate fresh blame index'),
      api.post_process(post_process.MustRun,
                       'Generate Blame JSONs.Upload blame jsons to cipd'),
      api.post_process(post_process.MustRun,
                       'Generate Blame JSONs.Create manifest.json'),
      api.post_process(post_process.MustRun,
                       'Generate Blame JSONs.Upload new manifest to cipd'),
      api.post_process(
          post_process.MustRun,
          'Generate Blame JSONs.Set cipd ref latest for infra/history_rag/manifest'
      ),
      api.post_process(post_process.DropExpectation),
  )

  # Test case: Baseline manifest exists, but manifest.json file is missing.
  yield api.test(
      'invalid_manifest_missing_file',
      builder_config_test_data(api),
      api.properties(**{
          '$build/chromium_checkout': {
              'gclient_config': 'chromium',
          },
      }),
      api.step_data(
          'Generate Blame JSONs.Check for manifest package.cipd describe infra/history_rag/manifest',
          retcode=0),
      api.post_process(post_process.StepFailure, 'Generate Blame JSONs'),
      api.post_process(post_process.DropExpectation),
      status='FAILURE')

  # Test case: Manifest.json is not valid JSON.
  yield api.test(
      'invalid_manifest_bad_json',
      builder_config_test_data(api),
      api.properties(**{
          '$build/chromium_checkout': {
              'gclient_config': 'chromium',
          },
      }),
      api.step_data(
          'Generate Blame JSONs.Check for manifest package.cipd describe infra/history_rag/manifest',
          retcode=0),
      api.path.exists(api.path.cleanup_dir / 'manifest/manifest.json',),
      api.step_data(
          'Generate Blame JSONs.Read manifest.json',
          api.file.read_json({}),
          retcode=1  # Simulate JSON parse failure
      ),
      api.post_process(post_process.StepException, 'Generate Blame JSONs'),
      api.post_process(post_process.DropExpectation),
      status='INFRA_FAILURE')

  # Test case: Manifest.json is missing the 'package' key.
  yield api.test(
      'invalid_manifest_missing_package_key',
      builder_config_test_data(api),
      api.properties(**{
          '$build/chromium_checkout': {
              'gclient_config': 'chromium',
          },
      }),
      api.step_data(
          'Generate Blame JSONs.Check for manifest package.cipd describe infra/history_rag/manifest',
          retcode=0),
      api.path.exists(api.path.cleanup_dir / 'manifest/manifest.json',),
      api.step_data('Generate Blame JSONs.Read manifest.json',
                    api.file.read_json({'version': 'some-version'})),
      api.post_process(post_process.StepFailure, 'Generate Blame JSONs'),
      api.post_process(post_process.DropExpectation),
      status='FAILURE')

  # Test case: Manifest.json has a package name with the wrong prefix.
  yield api.test(
      'invalid_manifest_bad_package_name',
      builder_config_test_data(api),
      api.properties(**{
          '$build/chromium_checkout': {
              'gclient_config': 'chromium',
          },
      }),
      api.step_data(
          'Generate Blame JSONs.Check for manifest package.cipd describe infra/history_rag/manifest',
          retcode=0),
      api.path.exists(api.path.cleanup_dir / 'manifest/manifest.json',),
      api.step_data(
          'Generate Blame JSONs.Read manifest.json',
          api.file.read_json({
              'package': 'wrong_prefix/oldrevision',
              'version': 'baseline-version-id'
          })),
      api.post_process(post_process.StepFailure, 'Generate Blame JSONs'),
      api.post_process(post_process.DropExpectation),
      status='FAILURE')

  # Test case: Manifest.json is missing the 'version' key.
  yield api.test(
      'invalid_manifest_missing_version',
      builder_config_test_data(api),
      api.properties(**{
          '$build/chromium_checkout': {
              'gclient_config': 'chromium',
          },
      }),
      api.step_data(
          'Generate Blame JSONs.Check for manifest package.cipd describe infra/history_rag/manifest',
          retcode=0),
      api.path.exists(api.path.cleanup_dir / 'manifest/manifest.json',),
      api.step_data(
          'Generate Blame JSONs.Read manifest.json',
          api.file.read_json({
              'package': f'{BASE_PKG_NAME}/oldrevision',
          })),
      api.post_process(post_process.StepFailure, 'Generate Blame JSONs'),
      api.post_process(post_process.DropExpectation),
      status='FAILURE')

  # Test case: CIPD upload for blame package fails to return instance ID.
  yield api.test(
      'cipd_upload_blame_fails',
      builder_config_test_data(api),
      api.properties(**{
          '$build/chromium_checkout': {
              'gclient_config': 'chromium',
          },
      }),
      api.step_data(
          'Generate Blame JSONs.Check for manifest package.cipd describe infra/history_rag/manifest',
          retcode=1),  # No baseline
      api.step_data(
          'Generate Blame JSONs.Upload blame jsons to cipd',
          stdout=api.raw_io.output_text('Something went wrong')),
      api.post_process(post_process.StepFailure, 'Generate Blame JSONs'),
      api.post_process(post_process.DropExpectation),
      status='FAILURE')

  # Test case: CIPD upload for manifest package fails to return instance ID.
  yield api.test(
      'cipd_upload_manifest_fails',
      builder_config_test_data(api),
      api.properties(**{
          '$build/chromium_checkout': {
              'gclient_config': 'chromium',
          },
      }),
      api.step_data(
          'Generate Blame JSONs.Check for manifest package.cipd describe infra/history_rag/manifest',
          retcode=1),  # No baseline
      api.step_data(
          'Generate Blame JSONs.Upload blame jsons to cipd',
          stdout=api.raw_io.output_text(
              'Instance: infra/history_rag/newrevision:new-blame-instance-id')),
      api.step_data(
          'Generate Blame JSONs.Upload new manifest to cipd',
          stdout=api.raw_io.output_text('Oh no, no instance ID here')),
      api.post_process(post_process.StepFailure, 'Generate Blame JSONs'),
      api.post_process(post_process.DropExpectation),
      status='FAILURE')
