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
    'recipe_engine/time',
    'recipe_engine/properties',
]

BASE_PKG_NAME = 'infra/history_rag/chrome'
MANIFEST_PKG_NAME = '%s/manifest' % BASE_PKG_NAME
FILE_BLAME_JSONS_PKG_NAME = '%s/file_blame_jsons' % BASE_PKG_NAME
COMMIT_HASH_JSONS_PKG_NAME = '%s/commit_hash_jsons' % BASE_PKG_NAME

GCS_BUCKET = 'historyrag-chrome-internal-staging'

# Configuration for Vertex AI
GOOGLE_CLOUD_PROJECT = 'skia-infra-corp'
GOOGLE_CLOUD_LOCATION = 'us-central1'

def RunSteps(api):
  source_dir, revision = checkout_source_code(api)
  file_blame_jsons_dir, file_blame_jsons_pkg_name, file_blame_jsons_pkg_id = (
      generate_file_blame_jsons(api, source_dir, revision))
  commit_hash_jsons_dir, commit_hash_jsons_pkg_name, commit_hash_jsons_pkg_id = (
      generate_commit_hash_jsons(api, source_dir, revision,
                                 file_blame_jsons_dir))
  # update manifest package to point to new CIPDs
  update_pointers_to_latest_CIPDs(api, revision, file_blame_jsons_pkg_name,
                                  file_blame_jsons_pkg_id,
                                  commit_hash_jsons_pkg_name,
                                  commit_hash_jsons_pkg_id)
  generate_topics(api, commit_hash_jsons_dir, revision)


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

    revision = update_result.properties.get('got_revision', 'fake_revision')

    return source_dir, revision


def generate_file_blame_jsons(api, source_dir, current_revision):
  """Generates a set of blame jsons for the source code.

  The output of this phase is a set of directories mimicking the file
  structure of the source directory, where each leaf node is a json file
  corresponding to a file in the source directory. Each json is a mapping like
  {
    "version": "0.1",
    "file_hash": "9d6fd29f55604702e62482488eb175a51e0a84d3",
    "lines": [
      "17e91cb8a3316c05775c566cabfa4b90c266f5f9",
      "...",
      "...",
    ]
  }
  Where `lines` correspond to line by line blame list for that file, and
  `file_hash` is the content hash of the file.

  Returns:
    merged_dir: The output directory containing the blame jsons.
    pkg_name: The name of the CIPD package containing the blame jsons.
    instance_id: The instance ID of the CIPD package containing the blame jsons.
  """
  with api.step.nest('Generate File Blame JSONs'):
    baseline_pkg = _get_baseline_package_info(api)
    if baseline_pkg:
      baseline_blame_jsons_dir = _download_baseline_file_blame_jsons(
          api, baseline_pkg)
      fresh_blame_jsons_dir = _generate_fresh_file_blame_jsons(
          api, source_dir, baseline_pkg)
      merged_dir = _merge_blame_jsons(api, baseline_blame_jsons_dir,
                                      fresh_blame_jsons_dir)
    else:
      # No baseline, so the "merged" directory is just the fresh ones
      merged_dir = _generate_fresh_file_blame_jsons(api, source_dir, None)

    # Upload the prepared directory
    pkg_name, instance_id = _update_cipd_package(
        api,
        step_name='Upload File Blame JSONs to CIPD',
        package_name=f"{FILE_BLAME_JSONS_PKG_NAME}/{current_revision}",
        package_content_dir=merged_dir,
        package_description=f"File blame jsons for chrome at commit: {current_revision}"
    )

    return merged_dir, pkg_name, instance_id


def generate_commit_hash_jsons(api, source_dir, current_revision,
                               file_blame_jsons_dir):
  """Generate commit_hash jsons from file_blame jsons.

  Creates one json file for each commit_hash in the jsons in
  `file_blame_jsons_dir`.

  Returns:
    The output directory containing the commit hash jsons.
  """
  with api.step.nest('Generate Commit Hash JSONs'):
    commit_hash_monolith_json_file = _collect_file_blame_jsons(
        api, source_dir, file_blame_jsons_dir)

    # The command appends commit level info with blame_hashes.json file
    # generated in the previous step and creates one JSON file per commit hash
    # in the output directory. Each file is named {commit_hash}.json
    # (e.g., a1b2c3d4e5f6.json).
    output_dir = api.path.cleanup_dir / 'commit_hash_jsons'
    cmd = [
        'vpython3',
        api.resource('git_data_processor.py'), 'fetch', '--source-dir',
        source_dir, '--output-dir', output_dir, commit_hash_monolith_json_file
    ]
    _ = api.step('Generate JSON per commit hash', cmd)

    # Upload the prepared directory
    pkg_name, instance_id = _update_cipd_package(
        api,
        step_name='Upload Commit Hash JSONs to CIPD',
        package_name=f"{COMMIT_HASH_JSONS_PKG_NAME}/{current_revision}",
        package_content_dir=output_dir,
        package_description=f"Commit hash jsons for chrome at commit: {current_revision}"
    )

    return output_dir, pkg_name, instance_id


def _collect_file_blame_jsons(api, source_dir, blame_json_dir):
  """Collects all blame hash jsons and converts to a monolith inverse map.

  This phase's main purpose is to "invert" the data from the blame_index.
  Instead of mapping file -> [commits], it scans all those individual JSON
  files and creates one large map of commit -> [files_and_lines].

  JSON Data Structure
  The file will contain a single JSON object where:
  Keys are the unique commit hashes found across all blame files. Values are
  objects detailing where that commit is blamed and the context of those lines.
  Here is a simplified example of the structure(--context=2):

  {
    "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2": {
      "repository": "v8",
      "files": {
        "src/foo.cc": [
          [10, "  // line 10 context"],
          [11, "  // line 11 context"],
          [12, "  if (some_condition) {"],
          [13, "    // line 13 context"],
          [14, "    // line 14 context"]
        ],
        "src/bar.h": [
          [41, "    // line 41 context"],
          [42, "    // line 42 context"],
          [43, "    int some_variable;"],
          [44, "    // line 44 context"],
          [45, "    // line 45 context"]
        ]
      }
    },
    "b7c8d9e0f1a2b7c8d9e0f1a2b7c8d9e0f1a2b7c8": {
      ...
    },
    ...
  }

  Where
  [<line_number>, <line_content>]: This is an array of pairs. The collect
  command reads the original source files and includes not only the exact line
  blamed on the commit but also `context` lines around it (`context` lines
  before and `context` lines after).
  """
  blame_hashes_file = api.path.cleanup_dir / 'blame_hashes.json'
  cmd = [
      'vpython3',
      api.resource('git_data_processor.py'), 'collect', '--source-dir',
      source_dir, '--output-file', blame_hashes_file, blame_json_dir
  ]
  _ = api.step('Collect Blame JSONs', cmd)
  return blame_hashes_file


def generate_topics(api, commit_hash_jsons_dir, revision):
  with api.step.nest('Generate and Upload Topics'):
    with api.context(
        env={
            'GOOGLE_GENAI_USE_VERTEXAI': 'true',
            'GOOGLE_CLOUD_PROJECT': GOOGLE_CLOUD_PROJECT,
            'GOOGLE_CLOUD_LOCATION': GOOGLE_CLOUD_LOCATION,
        }):
      prepared_commit_file = api.path.cleanup_dir / 'prepared_commits.pkl'
      cmd = [
          'vpython3',
          api.resource('prepare_commits.py'), commit_hash_jsons_dir,
          '--output-file', prepared_commit_file
      ]
      _ = api.step('Prepare Commits', cmd)

      embeddings_file = api.path.cleanup_dir / 'embeddings.npz'
      cmd = [
          'vpython3',
          api.resource('generate_embeddings.py'), prepared_commit_file,
          '--output-file', embeddings_file
      ]
      _ = api.step('Generate Embeddings', cmd)

      cluster_json_file = api.path.cleanup_dir / 'clusters.json'
      cmd = [
          'vpython3',
          api.resource('cluster_topics.py'), embeddings_file,
          prepared_commit_file, '--output-file', cluster_json_file,
          '--min-cluster-size', 100
      ]
      _ = api.step('Cluster Topics', cmd)

      summarized_topics_file = api.path.cleanup_dir / 'summarized_topics.json'
      cmd = [
          'vpython3',
          api.resource('summarize_topics.py'), cluster_json_file,
          '--output-file', summarized_topics_file, '--top-n', 5000, '--workers',
          20
      ]
      _ = api.step('Summarize Topics', cmd)

      topic_zip_file = api.path.cleanup_dir / 'topics.zip'
      cmd = [
          'vpython3',
          api.resource('group_and_package.py'), summarized_topics_file,
          '--output-file', topic_zip_file, '--gen-groups', 10, '--workers', 20
      ]
      _ = api.step('Group and Package', cmd)

      topic_dest_path = _get_topics_dest_path(api, revision)
      api.gsutil.upload(
          source=topic_zip_file,
          bucket=GCS_BUCKET,
          dest=topic_dest_path,
          name=f'Upload topics for {revision}',
          link_name='GCS Topics File')
      api.step.active_result.presentation.links[
          'GCS Topics File'] = f"https://storage.cloud.google.com/{GCS_BUCKET}/{topic_dest_path}"


def _get_topics_dest_path(api, current_revision):
  """Generates the GCS destination path for the embeddings file."""
  now = api.time.utcnow()
  date_path = now.strftime('%Y/%m/%d')
  return f"embeddings/{date_path}/{current_revision}/topics.zip"



def _get_baseline_package_info(api):
  """
  Fetches a pointer package, reads a manifest file, and returns the content.

  The pointer package 'infra/history_rag/chrome/manifest' is expected to contain
  a 'manifest.json' file specifying the actual data package and version.

  Returns:
    dict: The parsed content of manifest.json if successful.
    None: If the pointer package 'infra/history_rag/chrome/manifest' with ref 'latest'
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
          'file_blame_jsons_package': 'infra/history_rag/default_test_pkg',
          'file_blame_jsons_version': 'default-test-version-id'
      })

  package = manifest_data.get('file_blame_jsons_package')
  version = manifest_data.get('file_blame_jsons_version')
  if not package or not isinstance(package, str):
    raise api.step.StepFailure(
        "Key 'file_blame_jsons_package' missing or not a string in manifest.json"
    )
  if not version or not isinstance(version, str):
    raise api.step.StepFailure(
        "Key 'file_blame_jsons_version' missing or not a string in manifest.json"
    )

  return manifest_data


def _download_baseline_file_blame_jsons(api, baseline_package):
  dest_path = api.path.cleanup_dir / 'baseline_blame_jsons'
  with api.step.nest('Fetch baseline blame jsons'):
    pkgs = api.cipd.EnsureFile()
    pkgs.add_package(baseline_package['file_blame_jsons_package'],
                     baseline_package['file_blame_jsons_version'])
    api.cipd.ensure(dest_path, pkgs)
  return dest_path


def _generate_fresh_file_blame_jsons(api, source_dir, baseline_pkg):
  output_dir = api.path.cleanup_dir / 'fresh_blame_jsons'
  cmd = ['vpython3', api.resource('git_data_processor.py'), 'blame']
  if baseline_pkg:
    baseline_commit = baseline_pkg['file_blame_jsons_package'].split('/')[-1]
    cmd += ['--baseline-commit', baseline_commit]
  cmd += ['--source-dir', source_dir, '--output-dir', output_dir, '.']
  _ = api.step('Generate fresh blame index', cmd)
  return output_dir


def _merge_blame_jsons(api, baseline_dir, fresh_dir):
  """Merge the fresh jsons into the baseline directory. """
  cmd = [
      'vpython3',
      api.resource('merge_blame_jsons.py'), baseline_dir, fresh_dir
  ]
  _ = api.step('Merge Fresh and Baseline Blame JSONs', cmd)
  # Remove the .cipd metadata directory from the baseline after merging
  cipd_meta_dir = baseline_dir / '.cipd'
  api.file.rmtree("Remove .cipd from merged set", cipd_meta_dir)
  return baseline_dir


def _extract_cipd_instance_id(api, step_result):
  """Extracts the instance ID from cipd create stdout."""
  if step_result.stdout:
    # A correct output looks like
    # Instance: infra/history_rag/.../...:instance-id'
    for line in step_result.stdout.splitlines():
      if 'Instance:' in line:
        instance_id = line.split(':')[-1].strip()
        return instance_id
  return None


def _update_cipd_package(api,
                         package_name,
                         package_content_dir,
                         package_description='',
                         step_name='Upload to CIPD'):
  create_cmd = [
      'cipd', 'create', '-name', package_name, '-in', package_content_dir,
      '-install-mode', 'copy', '-metadata', f"description:{package_description}"
  ]

  create_step = api.step(
      name=step_name,
      cmd=create_cmd,
      stdout=api.raw_io.output_text(),
      stderr=api.raw_io.output_text(),
  )

  instance_id = _extract_cipd_instance_id(api, create_step)

  if instance_id:
    api.step.active_result.presentation.logs['instance_id'] = instance_id
    api.step.active_result.presentation.links['CIPD Package'] = (
        f"https://chrome-infra-packages.appspot.com/p/{package_name}/+/{instance_id}"
    )

    api.step(
        name='Set CIPD ref latest',
        cmd=[
            'cipd', 'set-ref', package_name, '-ref', 'latest', '-version',
            instance_id
        ])
    return package_name, instance_id

  api.step.active_result.presentation.logs['cipd_stdout'] = create_step.stdout
  api.step.active_result.presentation.logs['cipd_stderr'] = create_step.stderr
  raise api.step.StepFailure(
      f"CIPD instance ID not found in output for {package_name}")


def update_pointers_to_latest_CIPDs(api, current_revision,
                                    file_blame_jsons_package,
                                    file_blame_jsons_version,
                                    commit_hash_jsons_package,
                                    commit_hash_jsons_version):
  with api.step.nest('Update Pointers to all JSON CIPD packages'):
    manifest_content = {
        "file_blame_jsons_package": file_blame_jsons_package,
        "file_blame_jsons_version": file_blame_jsons_version,
        "commit_hash_jsons_package": commit_hash_jsons_package,
        "commit_hash_jsons_version": commit_hash_jsons_version,
    }
    temp_dir = api.path.mkdtemp(prefix='manifest_')
    manifest_file_path = temp_dir / 'manifest.json'
    api.file.write_json(
        name='Create manifest.json',
        dest=manifest_file_path,
        data=manifest_content,
    )
    _update_cipd_package(
        api,
        step_name='Upload new manifest to cipd',
        package_name=MANIFEST_PKG_NAME,
        package_content_dir=temp_dir,
        package_description=f"Commit hash jsons for chrome at commit: {current_revision}"
    )

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
          'Generate File Blame JSONs.Check for manifest package.cipd describe infra/history_rag/chrome/manifest',
          retcode=0),
      api.path.exists(api.path.cleanup_dir / 'manifest/manifest.json',
                      api.path.cleanup_dir / 'blame_jsons' / '.cipd'),
      # Mock manifest.json content
      api.step_data(
          'Generate File Blame JSONs.Read manifest.json',
          api.file.read_json({
              'file_blame_jsons_package': 'infra/history_rag/oldrevision',
              'file_blame_jsons_version': 'baseline-version-id'
          })),
      # Mock cipd create for file blame json package
      api.step_data(
          'Generate File Blame JSONs.Upload File Blame JSONs to CIPD',
          stdout=api.raw_io.output_text(
              'Instance: infra/history_rag/file_blame_jsons/newrevision:new-blame-instance-id'
          )),
      # Mock cipd create for commit hash json package
      api.step_data(
          'Generate Commit Hash JSONs.Upload Commit Hash JSONs to CIPD',
          stdout=api.raw_io.output_text(
              'Instance: infra/history_rag/commit_hash_jsons/newrevision:new-blame-instance-id'
          )),
      # Mock cipd create for manifest package
      api.step_data(
          'Update Pointers to all JSON CIPD packages.Upload new manifest to cipd',
          stdout=api.raw_io.output_text(
              'Instance: infra/history_rag/chrome/manifest:new-manifest-instance-id'
          )),
      api.post_process(post_process.MustRun,
                       'Checkout Chrome Source Code.gclient runhooks'),
      api.post_process(post_process.MustRun,
                       'Generate File Blame JSONs.Check for manifest package'),
      api.post_process(
          post_process.MustRun,
          'Generate File Blame JSONs.Fetch manifest package from CIPD'),
      api.post_process(post_process.MustRun,
                       'Generate File Blame JSONs.Read manifest.json'),
      api.post_process(post_process.MustRun,
                       'Generate File Blame JSONs.Fetch baseline blame jsons'),
      api.post_process(post_process.MustRun,
                       'Generate File Blame JSONs.Generate fresh blame index'),
      api.post_process(
          post_process.MustRun,
          'Generate File Blame JSONs.Merge Fresh and Baseline Blame JSONs'),
      api.post_process(
          post_process.MustRun,
          'Generate File Blame JSONs.Upload File Blame JSONs to CIPD'),
      api.post_process(
          post_process.MustRun,
          'Generate Commit Hash JSONs.Generate JSON per commit hash'),
      api.post_process(
          post_process.MustRun,
          'Generate Commit Hash JSONs.Upload Commit Hash JSONs to CIPD'),
      api.post_process(
          post_process.MustRun,
          'Update Pointers to all JSON CIPD packages.Create manifest.json'),
      api.post_process(
          post_process.MustRun,
          'Update Pointers to all JSON CIPD packages.Upload new manifest to cipd'
      ),
      api.post_process(
          post_process.MustRun,
          'Update Pointers to all JSON CIPD packages.Set CIPD ref latest'),
      api.post_process(post_process.MustRun,
                       'Generate and Upload Topics.Prepare Commits'),
      api.post_process(post_process.MustRun,
                       'Generate and Upload Topics.Generate Embeddings'),
      api.post_process(post_process.MustRun,
                       'Generate and Upload Topics.Cluster Topics'),
      api.post_process(post_process.MustRun,
                       'Generate and Upload Topics.Summarize Topics'),
      api.post_process(post_process.MustRun,
                       'Generate and Upload Topics.Group and Package'),
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
          'Generate File Blame JSONs.Check for manifest package.cipd describe infra/history_rag/chrome/manifest',
          retcode=1),
      # Mock cipd create for file blame json package
      api.step_data(
          'Generate File Blame JSONs.Upload File Blame JSONs to CIPD',
          stdout=api.raw_io.output_text(
              'Instance: infra/history_rag/file_blame_jsons/newrevision:new-blame-instance-id'
          )),
      # Mock cipd create for commit hash json package
      api.step_data(
          'Generate Commit Hash JSONs.Upload Commit Hash JSONs to CIPD',
          stdout=api.raw_io.output_text(
              'Instance: infra/history_rag/commit_hash_jsons/newrevision:new-blame-instance-id'
          )),
      # Mock cipd create for manifest package
      api.step_data(
          'Update Pointers to all JSON CIPD packages.Upload new manifest to cipd',
          stdout=api.raw_io.output_text(
              'Instance: infra/history_rag/chrome/manifest:new-manifest-instance-id'
          )),
      api.post_process(
          post_process.DoesNotRun,
          'Generate File Blame JSONs.Fetch manifest package from CIPD'),
      api.post_process(post_process.DoesNotRun,
                       'Generate File Blame JSONs.Fetch baseline blame jsons'),
      api.post_process(
          post_process.DoesNotRun,
          'Generate File Blame JSONs.Merge Fresh and Baseline Blame JSONs'),
      api.post_process(post_process.MustRun,
                       'Generate File Blame JSONs.Generate fresh blame index'),
      api.post_process(
          post_process.MustRun,
          'Generate File Blame JSONs.Upload File Blame JSONs to CIPD'),
      api.post_process(
          post_process.MustRun,
          'Generate Commit Hash JSONs.Generate JSON per commit hash'),
      api.post_process(
          post_process.MustRun,
          'Generate Commit Hash JSONs.Upload Commit Hash JSONs to CIPD'),
      api.post_process(
          post_process.MustRun,
          'Update Pointers to all JSON CIPD packages.Create manifest.json'),
      api.post_process(
          post_process.MustRun,
          'Update Pointers to all JSON CIPD packages.Upload new manifest to cipd'
      ),
      api.post_process(
          post_process.MustRun,
          'Update Pointers to all JSON CIPD packages.Set CIPD ref latest'),
      api.post_process(post_process.MustRun,
                       'Generate and Upload Topics.Prepare Commits'),
      api.post_process(post_process.MustRun,
                       'Generate and Upload Topics.Generate Embeddings'),
      api.post_process(post_process.MustRun,
                       'Generate and Upload Topics.Cluster Topics'),
      api.post_process(post_process.MustRun,
                       'Generate and Upload Topics.Summarize Topics'),
      api.post_process(post_process.MustRun,
                       'Generate and Upload Topics.Group and Package'),
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
          'Generate File Blame JSONs.Check for manifest package.cipd describe infra/history_rag/chrome/manifest',
          retcode=0),
      api.post_process(post_process.StepFailure, 'Generate File Blame JSONs'),
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
          'Generate File Blame JSONs.Check for manifest package.cipd describe infra/history_rag/chrome/manifest',
          retcode=0),
      api.path.exists(api.path.cleanup_dir / 'manifest/manifest.json',),
      api.step_data(
          'Generate File Blame JSONs.Read manifest.json',
          api.file.read_json({}),
          retcode=1  # Simulate JSON parse failure
      ),
      api.post_process(post_process.StepException, 'Generate File Blame JSONs'),
      api.post_process(post_process.DropExpectation),
      status='INFRA_FAILURE')

  # Test case: Manifest.json is missing the 'file_blame_jsons_package' key.
  yield api.test(
      'invalid_manifest_missing_file_blame_jsons_package_key',
      builder_config_test_data(api),
      api.properties(**{
          '$build/chromium_checkout': {
              'gclient_config': 'chromium',
          },
      }),
      api.step_data(
          'Generate File Blame JSONs.Check for manifest package.cipd describe infra/history_rag/chrome/manifest',
          retcode=0),
      api.path.exists(api.path.cleanup_dir / 'manifest/manifest.json',),
      api.step_data(
          'Generate File Blame JSONs.Read manifest.json',
          api.file.read_json({'file_blame_jsons_version': 'some-version'})),
      api.post_process(post_process.StepFailure, 'Generate File Blame JSONs'),
      api.post_process(post_process.DropExpectation),
      status='FAILURE')

  # Test case: Manifest.json is missing the 'file_blame_jsons_version' key.
  yield api.test(
      'invalid_manifest_missing_version',
      builder_config_test_data(api),
      api.properties(**{
          '$build/chromium_checkout': {
              'gclient_config': 'chromium',
          },
      }),
      api.step_data(
          'Generate File Blame JSONs.Check for manifest package.cipd describe infra/history_rag/chrome/manifest',
          retcode=0),
      api.path.exists(api.path.cleanup_dir / 'manifest/manifest.json',),
      api.step_data(
          'Generate File Blame JSONs.Read manifest.json',
          api.file.read_json({
              'file_blame_jsons_package': f'{BASE_PKG_NAME}/oldrevision',
          })),
      api.post_process(post_process.StepFailure, 'Generate File Blame JSONs'),
      api.post_process(post_process.DropExpectation),
      status='FAILURE')

  # Test case: CIPD upload for file blame jsons fails to return instance ID.
  yield api.test(
      'cipd_upload_file_blame_jsons_fails_no_instance_id',
      builder_config_test_data(api),
      api.properties(**{
          '$build/chromium_checkout': {
              'gclient_config': 'chromium',
          },
      }),
      api.step_data(
          'Generate File Blame JSONs.Check for manifest package.cipd describe infra/history_rag/chrome/manifest',
          retcode=1),  # No baseline
      api.step_data(
          'Generate File Blame JSONs.Upload File Blame JSONs to CIPD',
          stdout=api.raw_io.output_text('Something went wrong')),
      api.post_process(post_process.StepFailure, 'Generate File Blame JSONs'),
      api.post_process(post_process.DropExpectation),
      status='FAILURE')

  # Test case: CIPD upload for commit jsons fails to return instance ID.
  yield api.test(
      'cipd_upload_commit_hash_jsons_fails_no_instance_id',
      builder_config_test_data(api),
      api.properties(**{
          '$build/chromium_checkout': {
              'gclient_config': 'chromium',
          },
      }),
      api.step_data(
          'Generate File Blame JSONs.Check for manifest package.cipd describe infra/history_rag/chrome/manifest',
          retcode=1),  # No baseline

      # Mock cipd create for file blame json package
      api.step_data(
          'Generate File Blame JSONs.Upload File Blame JSONs to CIPD',
          stdout=api.raw_io.output_text(
              'Instance: infra/history_rag/file_blame_jsons/newrevision:new-blame-instance-id'
          )),
      # Mock cipd create for commit hash json package
      api.step_data(
          'Generate Commit Hash JSONs.Upload Commit Hash JSONs to CIPD',
          stdout=api.raw_io.output_text('Something went wrong')),
      api.post_process(post_process.StepFailure, 'Generate Commit Hash JSONs'),
      api.post_process(post_process.DropExpectation),
      status='FAILURE')

  # Test case: CIPD upload for manifest package fails to return instance ID.
  yield api.test(
      'cipd_upload_manifest_fails_no_instance_id',
      builder_config_test_data(api),
      api.properties(**{
          '$build/chromium_checkout': {
              'gclient_config': 'chromium',
          },
      }),
      api.step_data(
          'Generate File Blame JSONs.Check for manifest package.cipd describe infra/history_rag/chrome/manifest',
          retcode=1),  # No baseline

      # Mock cipd create for file blame json package
      api.step_data(
          'Generate File Blame JSONs.Upload File Blame JSONs to CIPD',
          stdout=api.raw_io.output_text(
              'Instance: infra/history_rag/file_blame_jsons/newrevision:new-blame-instance-id'
          )),
      # Mock cipd create for commit hash json package
      api.step_data(
          'Generate Commit Hash JSONs.Upload Commit Hash JSONs to CIPD',
          stdout=api.raw_io.output_text(
              'Instance: infra/history_rag/commit_hash_jsons/newrevision:new-blame-instance-id'
          )),
      # Mock cipd create for manifest package
      api.step_data(
          'Update Pointers to all JSON CIPD packages.Upload new manifest to cipd',
          stdout=api.raw_io.output_text('Oh no, no instance ID here')),
      api.post_process(post_process.StepFailure,
                       'Update Pointers to all JSON CIPD packages'),
      api.post_process(post_process.DropExpectation),
      status='FAILURE')
