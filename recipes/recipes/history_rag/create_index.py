# Copyright 2025 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Creates index for history-rag."""

import re
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

GCS_BUCKET_STAGING = 'historyrag-chrome-internal-staging'
GCS_BUCKET_PROD = 'historyrag-chrome-internal'
MIN_FILE_THRESHOLD = 100
MIN_COMMITS_THRESHOLD = 500


def get_destination_bucket(api):
  build = api.buildbucket.build
  if build.builder.bucket == 'ci' and build.builder.builder == 'linux-history-rag':
    return GCS_BUCKET_PROD
  return GCS_BUCKET_STAGING

# Configuration for Vertex AI
GOOGLE_CLOUD_PROJECT = 'skia-infra-corp'
GOOGLE_CLOUD_LOCATION = 'global'


def _get_manifest_pkg_name(submodule_name):
  return f'{BASE_PKG_NAME}/{submodule_name}/manifest'


def _get_file_blame_jsons_pkg_name(submodule_name):
  return f'{BASE_PKG_NAME}/{submodule_name}/file_blame_jsons'

def RunSteps(api):
  source_dir, _ = checkout_source_code(api)
  submodules = find_submodules(api, source_dir)
  failed_submodules = {}
  for submodule_name, submodule_info in submodules.items():
    submodule_dir = submodule_info['path']
    submodule_revision = submodule_info['revision']
    with api.step.nest(f'Processing {submodule_name}') as presentation:
      # process_submodule(api, submodule_name, submodule_dir, submodule_revision)
      try:
        process_submodule(api, submodule_name, submodule_dir,
                          submodule_revision)
      except api.step.StepFailure as e:
        failed_submodules[submodule_name] = f"StepFailure: {e}"
        presentation.status = api.step.FAILURE
        presentation.step_text = 'Finished with StepFailure'
        # Add error log to the current step's presentation
        presentation.logs['error'] = str(e).splitlines()

  if failed_submodules:
    summary = ["Processing completed with the following failures:"]
    for name, reason in failed_submodules.items():
      summary.append(f"  - {name}: {reason}")

    # Create a summary step for failures
    failure_summary_step = api.step('Submodule Failures Summary', cmd=None)
    failure_summary_step.presentation.logs['summary'] = summary
    failure_summary_step.presentation.status = api.step.FAILURE

    # Raise a StepFailure to make the build result non-successful
    raise api.step.StepFailure("One or more submodules failed to process.")


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


def find_submodules(api, source_dir):
  """
  Finds all configured submodules, their paths, and their current HEAD commits.

  Args:
    api: The recipe API object.
    source_dir: The root directory of the main git repository (e.g., chromium/src).

  Returns:
    A dictionary where keys are project names ('root' for the main repo,
    sanitized paths for submodules) and values are dictionaries:
    {'path': absolute_path, 'revision': head_commit_hash}.
  """
  with api.step.nest('Find all submodules'):
    submodules = {}

    # 1. Process the main repository
    step_result = api.step(
        'Get HEAD for root', ['git', '-C', source_dir, 'rev-parse', 'HEAD'],
        stdout=api.raw_io.output_text(),
        stderr=api.raw_io.output_text())
    root_head = step_result.stdout.strip()
    submodules['root'] = {'path': source_dir, 'revision': root_head}

    # 2. Process submodules from .gitmodules
    gitmodules_path = source_dir / '.gitmodules'
    content = api.file.read_text("Read .gitmodules", gitmodules_path)
    path_regex = re.compile(r'^\s*path\s*=\s*(.*)$', re.MULTILINE)
    submodule_entries = content.split('[submodule ')

    for entry in submodule_entries[1:]:
      match = path_regex.search(entry)
      if not match:
        continue
      submodule_path = match.group(1).strip()
      submodule_abs_path = source_dir / submodule_path
      submodule_name = re.sub(r'[^a-zA-Z0-9_-]+', '_', submodule_path)

      if not api.path.exists(submodule_abs_path):
        continue

      full_command = f"find {submodule_abs_path} -not -path '*/.*' -type f 2>/dev/null | wc -l"
      cmd = ['bash', '-c', full_command]
      step_result = api.step(
          f'Check if {submodule_name} has minimum number of files',
          cmd,
          stdout=api.raw_io.output_text(),
      )

      if int(step_result.stdout.strip()) < MIN_FILE_THRESHOLD:
        continue

      cmd = ['git', '-C', submodule_abs_path, 'rev-list', '--count', 'HEAD']
      step_result = api.step(
          f'Check if {submodule_name} has minimum number of commits',
          cmd,
          stdout=api.raw_io.output_text(),
      )
      if int(step_result.stdout.strip()) < MIN_COMMITS_THRESHOLD:
        continue

      step_result = api.step(
          f'Get HEAD for {submodule_name}',
          ['git', '-C', submodule_abs_path, 'rev-parse', 'HEAD'],
          stdout=api.raw_io.output_text(),
          stderr=api.raw_io.output_text())
      submodule_head = step_result.stdout.strip()

      submodules[submodule_name] = {
          'path': submodule_abs_path,
          'revision': submodule_head
      }
    return submodules


def process_submodule(api, submodule_name, submodule_dir, submodule_revision):
  """Runs the indexing pipeline for a single source directory (repo or submodule)."""
  file_blame_jsons_dir, file_blame_jsons_pkg_name, file_blame_jsons_pkg_id = (
      generate_file_blame_jsons(api, submodule_name, submodule_dir,
                                submodule_revision))
  commit_hash_jsons_dir = (
      generate_commit_hash_jsons(api, submodule_name, submodule_dir,
                                 file_blame_jsons_dir))
  # update manifest package to point to new CIPDs
  update_pointers_to_latest_CIPDs(api, submodule_name, submodule_revision,
                                  file_blame_jsons_pkg_name,
                                  file_blame_jsons_pkg_id)
  generate_topics(api, submodule_name, submodule_revision,
                  commit_hash_jsons_dir)
  # cleanup all generated files to avoid out of disk errors
  api.file.rmcontents('clean up work', api.path.cleanup_dir)


def generate_file_blame_jsons(api, submodule_name, submodule_dir,
                              current_revision):
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
    baseline_pkg = _get_baseline_package_info(api, submodule_name)
    if baseline_pkg:
      baseline_blame_jsons_dir = _download_baseline_file_blame_jsons(
          api, submodule_name, baseline_pkg)
      fresh_blame_jsons_dir = _generate_fresh_file_blame_jsons(
          api, submodule_name, submodule_dir, baseline_pkg)
      merged_dir = _merge_blame_jsons(api, baseline_blame_jsons_dir,
                                      fresh_blame_jsons_dir)
    else:
      # No baseline, so the "merged" directory is just the fresh ones
      merged_dir = _generate_fresh_file_blame_jsons(api, submodule_name,
                                                    submodule_dir, None)

    # Upload the prepared directory
    pkg_name_base = _get_file_blame_jsons_pkg_name(submodule_name)
    pkg_name, instance_id = _update_cipd_package(
        api,
        step_name='Upload File Blame JSONs to CIPD',
        package_name=f"{pkg_name_base}/{current_revision}",
        package_content_dir=merged_dir,
        package_description=f"File blame jsons for {submodule_name} at commit: {current_revision}"
    )

    return merged_dir, pkg_name, instance_id


def generate_commit_hash_jsons(api, submodule_name, submodule_dir,
                               file_blame_jsons_dir):
  """Generate commit_hash jsons from file_blame jsons.

  Creates one json file for each commit_hash in the jsons in
  `file_blame_jsons_dir`.

  Returns:
    The output directory containing the commit hash jsons.
  """
  with api.step.nest('Generate Commit Hash JSONs'):
    commit_hash_monolith_json_file = _collect_file_blame_jsons(
        api, submodule_name, submodule_dir, file_blame_jsons_dir)
    # The command appends commit level info with blame_hashes.json file
    # generated in the previous step and creates one JSON file per commit hash
    # in the output directory. Each file is named {commit_hash}.json
    # (e.g., a1b2c3d4e5f6.json).
    output_dir = api.path.cleanup_dir / f'{submodule_name}_commit_hash_jsons'
    cmd = [
        'vpython3',
        api.resource('git_data_processor.py'), 'fetch', '--source-dir',
        submodule_dir, '--output-dir', output_dir,
        commit_hash_monolith_json_file
    ]
    _ = api.step('Generate JSON per commit hash', cmd)

    return output_dir


def _collect_file_blame_jsons(api, submodule_name, submodule_dir,
                              blame_json_dir):
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
  blame_hashes_file = api.path.cleanup_dir / f'{submodule_name}_blame_hashes.json'
  cmd = [
      'vpython3',
      api.resource('git_data_processor.py'), 'collect', '--source-dir',
      submodule_dir, '--output-file', blame_hashes_file, blame_json_dir
  ]
  _ = api.step('Collect Blame JSONs', cmd)
  return blame_hashes_file


def generate_topics(api, submodule_name, submodule_revision,
                    commit_hash_jsons_dir):
  with api.step.nest('Generate and Upload Topics'):
    with api.context(
        env={
            'GOOGLE_GENAI_USE_VERTEXAI': 'true',
            'GOOGLE_CLOUD_PROJECT': GOOGLE_CLOUD_PROJECT,
            'GOOGLE_CLOUD_LOCATION': GOOGLE_CLOUD_LOCATION,
        }):
      prepared_commit_file = api.path.cleanup_dir / f'{submodule_name}_prepared_commits.pkl'
      cmd = [
          'vpython3',
          api.resource('prepare_commits.py'), commit_hash_jsons_dir,
          '--output-file', prepared_commit_file
      ]
      _ = api.step('Prepare Commits', cmd)

      embeddings_file = api.path.cleanup_dir / f'{submodule_name}_embeddings.npz'
      cmd = [
          'vpython3',
          api.resource('generate_embeddings.py'), prepared_commit_file,
          '--output-file', embeddings_file
      ]
      _ = api.step('Generate Embeddings', cmd)

      cluster_json_file = api.path.cleanup_dir / f'{submodule_name}_clusters.json'
      cmd = [
          'vpython3',
          api.resource('cluster_topics.py'), embeddings_file,
          prepared_commit_file, '--output-file', cluster_json_file
      ]
      _ = api.step('Cluster Topics', cmd)

      summarized_topics_file = api.path.cleanup_dir / f'{submodule_name}_summarized_topics.json'
      cmd = [
          'vpython3',
          api.resource('summarize_topics.py'), cluster_json_file,
          '--output-file', summarized_topics_file, '--top-n', 5000, '--workers',
          20
      ]
      _ = api.step('Summarize Topics', cmd)

      topic_zip_file = api.path.cleanup_dir / f'{submodule_name}_topics.zip'
      cmd = [
          'vpython3',
          api.resource('group_and_package.py'), summarized_topics_file,
          '--output-file', topic_zip_file, '--gen-groups', 10, '--workers', 20
      ]
      _ = api.step('Group and Package', cmd)

      topic_dest_path = _get_topics_dest_path(api, submodule_name,
                                              submodule_revision)
      gcs_bucket = get_destination_bucket(api)
      api.gsutil.upload(
          source=topic_zip_file,
          bucket=gcs_bucket,
          dest=topic_dest_path,
          name=f'Upload topics for {submodule_revision}',
          link_name='GCS Topics File')
      api.step.active_result.presentation.links[
          'GCS Topics File'] = f"https://storage.cloud.google.com/{gcs_bucket}/{topic_dest_path}"


def _get_topics_dest_path(api, submodule_name, current_revision):
  """Generates the GCS destination path for the embeddings file."""
  now = api.time.utcnow()
  date_path = now.strftime('%Y/%m/%d')
  return f"embeddings/{date_path}/{current_revision}/{submodule_name}/topics.zip"


def _get_baseline_package_info(api, submodule_name):
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
  manifest_pkg_name = _get_manifest_pkg_name(submodule_name)
  dest_path = api.path.cleanup_dir / f'{submodule_name}_manifest'
  with api.step.nest('Check for manifest package') as presentation:
    try:
      api.cipd.describe(manifest_pkg_name, 'latest')
    except api.step.StepFailure:
      presentation.logs['info'] = [
          f"Manifest package {manifest_pkg_name}:latest not found. Returning None."
      ]
      presentation.status = api.step.WARNING
      return None

  with api.step.nest('Fetch manifest package from CIPD'):
    pkgs = api.cipd.EnsureFile()
    pkgs.add_package(manifest_pkg_name, 'latest')
    api.cipd.ensure(dest_path, pkgs)

  manifest_file_path = dest_path / 'manifest.json'
  if not api.path.exists(manifest_file_path):
    raise api.step.StepFailure(
        f"Manifest file not found in CIPD package {manifest_pkg_name}")

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


def _download_baseline_file_blame_jsons(api, submodule_name, baseline_package):
  dest_path = api.path.cleanup_dir / f'{submodule_name}_baseline_blame_jsons'
  with api.step.nest('Fetch baseline blame jsons'):
    pkgs = api.cipd.EnsureFile()
    pkgs.add_package(baseline_package['file_blame_jsons_package'],
                     baseline_package['file_blame_jsons_version'])
    api.cipd.ensure(dest_path, pkgs)
  return dest_path


def _generate_fresh_file_blame_jsons(api, submodule_name, submodule_dir,
                                     baseline_pkg):
  output_dir = api.path.cleanup_dir / f'{submodule_name}_fresh_blame_jsons'
  cmd = ['vpython3', api.resource('git_data_processor.py'), 'blame']
  if baseline_pkg:
    baseline_commit = baseline_pkg['file_blame_jsons_package'].split('/')[-1]
    cmd += ['--baseline-commit', baseline_commit]
  cmd += ['--source-dir', submodule_dir, '--output-dir', output_dir, '.']
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


def update_pointers_to_latest_CIPDs(api, submodule_name, current_revision,
                                    file_blame_jsons_package,
                                    file_blame_jsons_version):
  with api.step.nest('Update Pointers to all JSON CIPD packages'):
    manifest_content = {
        "file_blame_jsons_package": file_blame_jsons_package,
        "file_blame_jsons_version": file_blame_jsons_version,
    }
    temp_dir = api.path.mkdtemp(prefix=f'{submodule_name}_manifest_')
    manifest_file_path = temp_dir / 'manifest.json'
    api.file.write_json(
        name='Create manifest.json',
        dest=manifest_file_path,
        data=manifest_content,
    )
    manifest_pkg_name = _get_manifest_pkg_name(submodule_name)
    _update_cipd_package(
        api,
        step_name='Upload new manifest to cipd',
        package_name=manifest_pkg_name,
        package_content_dir=temp_dir,
        package_description=f"Manifest for {submodule_name} at commit: {current_revision}"
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


# Test config corresponding to prod builder
def prod_builder_config_test_data(api):
  return api.chromium_tests_builder_config.ci_build(
      builder_group='fake-group',
      builder='linux-history-rag',
      revision='newrevision',
      builder_db=ctbc.BuilderDatabase.create({
          'fake-group': {
              'linux-history-rag':
                  ctbc.BuilderSpec.create(
                      chromium_config='chromium',
                      chromium_apply_config=['mb'],
                      gclient_config='chromium',
                  ),
          },
      }))


def StepCommandContainsSubstrings(check, step_odict, step, substrings):
  """Assert that a step's command contained the given substring

  Args:
    step (str) - The name of the step to check the command of.
    substring (str) - The expected substring of an argument. If any of
      the commandline element contained this substring, the check is success.
  """

  def found_in_commandline(substring):
    return any(substring in arg for arg in step_odict[step].cmd)

  check(
      'command line for step %s contained %s as substrings' %
      (step, substrings), all(found_in_commandline(s) for s in substrings))

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
      # Mock find HEAD for root
      api.step_data(
          'Find all submodules.Get HEAD for root',
          stdout=api.raw_io.output_text('newrevision')),
      # Mock cipd describe for manifest package - found
      api.step_data(
          'Processing root.Generate File Blame JSONs.Check for manifest package.cipd describe infra/history_rag/chrome/root/manifest',
          retcode=0),
      api.path.exists(api.path.cleanup_dir / 'root_manifest' / 'manifest.json'),
      # Mock manifest.json content
      api.step_data(
          'Processing root.Generate File Blame JSONs.Read manifest.json',
          api.file.read_json({
              'file_blame_jsons_package':
                  'infra/history_rag/chrome/root/file_blame_jsons/oldrevision',
              'file_blame_jsons_version':
                  'baseline-version-id'
          })),
      # Mock cipd create for file blame json package
      api.step_data(
          'Processing root.Generate File Blame JSONs.Upload File Blame JSONs to CIPD',
          stdout=api.raw_io.output_text(
              'Instance: infra/history_rag/chrome/root/file_blame_jsons/newrevision:new-blame-instance-id'
          )),
      # Mock cipd create for manifest package
      api.step_data(
          'Processing root.Update Pointers to all JSON CIPD packages.Upload new manifest to cipd',
          stdout=api.raw_io.output_text(
              'Instance: infra/history_rag/chrome/root/manifest:new-manifest-instance-id'
          )),
      api.post_process(post_process.MustRun,
                       'Checkout Chrome Source Code.gclient runhooks'),
      api.post_process(
          post_process.MustRun,
          'Processing root.Generate File Blame JSONs.Check for manifest package'
      ),
      api.post_process(
          post_process.MustRun,
          'Processing root.Generate File Blame JSONs.Fetch manifest package from CIPD'
      ),
      api.post_process(
          post_process.MustRun,
          'Processing root.Generate File Blame JSONs.Read manifest.json'),
      api.post_process(
          post_process.MustRun,
          'Processing root.Generate File Blame JSONs.Fetch baseline blame jsons'
      ),
      api.post_process(
          post_process.MustRun,
          'Processing root.Generate File Blame JSONs.Generate fresh blame index'
      ),
      api.post_process(
          post_process.MustRun,
          'Processing root.Generate File Blame JSONs.Merge Fresh and Baseline Blame JSONs'
      ),
      api.post_process(
          post_process.MustRun,
          'Processing root.Generate File Blame JSONs.Upload File Blame JSONs to CIPD'
      ),
      api.post_process(
          post_process.MustRun,
          'Processing root.Generate Commit Hash JSONs.Generate JSON per commit hash'
      ),
      api.post_process(
          post_process.MustRun,
          'Processing root.Update Pointers to all JSON CIPD packages.Create manifest.json'
      ),
      api.post_process(
          post_process.MustRun,
          'Processing root.Update Pointers to all JSON CIPD packages.Upload new manifest to cipd'
      ),
      api.post_process(
          post_process.MustRun,
          'Processing root.Update Pointers to all JSON CIPD packages.Set CIPD ref latest'
      ),
      api.post_process(
          post_process.MustRun,
          'Processing root.Generate and Upload Topics.Prepare Commits'),
      api.post_process(
          post_process.MustRun,
          'Processing root.Generate and Upload Topics.Generate Embeddings'),
      api.post_process(
          post_process.MustRun,
          'Processing root.Generate and Upload Topics.Cluster Topics'),
      api.post_process(
          post_process.MustRun,
          'Processing root.Generate and Upload Topics.Summarize Topics'),
      api.post_process(
          post_process.MustRun,
          'Processing root.Generate and Upload Topics.Group and Package'),
      api.post_process(
          StepCommandContainsSubstrings,
          'Processing root.Generate and Upload Topics.gsutil Upload topics for newrevision',
          ['gs://historyrag-chrome-internal-staging/']),
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
      # Mock find HEAD for root
      api.step_data(
          'Find all submodules.Get HEAD for root',
          stdout=api.raw_io.output_text('newrevision')),
      # Mock cipd describe for manifest package - not found
      api.step_data(
          'Processing root.Generate File Blame JSONs.Check for manifest package.cipd describe infra/history_rag/chrome/root/manifest',
          retcode=1),
      # Mock cipd create for file blame json package
      api.step_data(
          'Processing root.Generate File Blame JSONs.Upload File Blame JSONs to CIPD',
          stdout=api.raw_io.output_text(
              'Instance: infra/history_rag/chrome/root/file_blame_jsons/newrevision:new-blame-instance-id'
          )),
      # Mock cipd create for manifest package
      api.step_data(
          'Processing root.Update Pointers to all JSON CIPD packages.Upload new manifest to cipd',
          stdout=api.raw_io.output_text(
              'Instance: infra/history_rag/chrome/root/manifest:new-manifest-instance-id'
          )),
      api.post_process(
          post_process.DoesNotRun,
          'Processing root.Generate File Blame JSONs.Fetch manifest package from CIPD'
      ),
      api.post_process(
          post_process.DoesNotRun,
          'Processing root.Generate File Blame JSONs.Fetch baseline blame jsons'
      ),
      api.post_process(
          post_process.DoesNotRun,
          'Processing root.Generate File Blame JSONs.Merge Fresh and Baseline Blame JSONs'
      ),
      api.post_process(
          post_process.MustRun,
          'Processing root.Generate File Blame JSONs.Generate fresh blame index'
      ),
      api.post_process(
          post_process.MustRun,
          'Processing root.Generate File Blame JSONs.Upload File Blame JSONs to CIPD'
      ),
      api.post_process(
          post_process.MustRun,
          'Processing root.Generate Commit Hash JSONs.Generate JSON per commit hash'
      ),
      api.post_process(
          post_process.MustRun,
          'Processing root.Update Pointers to all JSON CIPD packages.Create manifest.json'
      ),
      api.post_process(
          post_process.MustRun,
          'Processing root.Update Pointers to all JSON CIPD packages.Upload new manifest to cipd'
      ),
      api.post_process(
          post_process.MustRun,
          'Processing root.Update Pointers to all JSON CIPD packages.Set CIPD ref latest'
      ),
      api.post_process(
          post_process.MustRun,
          'Processing root.Generate and Upload Topics.Prepare Commits'),
      api.post_process(
          post_process.MustRun,
          'Processing root.Generate and Upload Topics.Generate Embeddings'),
      api.post_process(
          post_process.MustRun,
          'Processing root.Generate and Upload Topics.Cluster Topics'),
      api.post_process(
          post_process.MustRun,
          'Processing root.Generate and Upload Topics.Summarize Topics'),
      api.post_process(
          post_process.MustRun,
          'Processing root.Generate and Upload Topics.Group and Package'),
      api.post_process(
          StepCommandContainsSubstrings,
          'Processing root.Generate and Upload Topics.gsutil Upload topics for newrevision',
          ['gs://historyrag-chrome-internal-staging/']),
      api.post_process(post_process.DropExpectation),
  )

  # Test case: Prod builder uses prod gcs bucket to upload topics zip.
  yield api.test(
      'prod_builder_uses_prod_gcs_bucket',
      prod_builder_config_test_data(api),
      api.properties(**{
          '$build/chromium_checkout': {
              'gclient_config': 'chromium',
          },
      }),
      # Mock find HEAD for root
      api.step_data(
          'Find all submodules.Get HEAD for root',
          stdout=api.raw_io.output_text('newrevision')),
      # Mock cipd describe for manifest package - not found
      api.step_data(
          'Processing root.Generate File Blame JSONs.Check for manifest package.cipd describe infra/history_rag/chrome/root/manifest',
          retcode=1),
      # Mock cipd create for file blame json package
      api.step_data(
          'Processing root.Generate File Blame JSONs.Upload File Blame JSONs to CIPD',
          stdout=api.raw_io.output_text(
              'Instance: infra/history_rag/chrome/root/file_blame_jsons/newrevision:new-blame-instance-id'
          )),
      # Mock cipd create for manifest package
      api.step_data(
          'Processing root.Update Pointers to all JSON CIPD packages.Upload new manifest to cipd',
          stdout=api.raw_io.output_text(
              'Instance: infra/history_rag/chrome/root/manifest:new-manifest-instance-id'
          )),
      api.post_process(
          post_process.MustRun,
          'Processing root.Generate and Upload Topics.gsutil Upload topics for newrevision'
      ),
      api.post_process(
          StepCommandContainsSubstrings,
          'Processing root.Generate and Upload Topics.gsutil Upload topics for newrevision',
          ['gs://historyrag-chrome-internal/']),
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
          'Processing root.Generate File Blame JSONs.Check for manifest package.cipd describe infra/history_rag/chrome/root/manifest',
          retcode=0),
      api.post_process(post_process.StepFailure,
                       'Processing root.Generate File Blame JSONs'),
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
          'Processing root.Generate File Blame JSONs.Check for manifest package.cipd describe infra/history_rag/chrome/root/manifest',
          retcode=0),
      api.path.exists(api.path.cleanup_dir / 'root_manifest' /
                      'manifest.json',),
      api.step_data(
          'Processing root.Generate File Blame JSONs.Read manifest.json',
          api.file.read_json({}),
          retcode=1  # Simulate JSON parse failure
      ),
      api.post_process(post_process.StepException,
                       'Processing root.Generate File Blame JSONs'),
      api.post_process(post_process.DropExpectation),
      status='FAILURE')

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
          'Processing root.Generate File Blame JSONs.Check for manifest package.cipd describe infra/history_rag/chrome/root/manifest',
          retcode=0),
      api.path.exists(api.path.cleanup_dir / 'root_manifest' /
                      'manifest.json',),
      api.step_data(
          'Processing root.Generate File Blame JSONs.Read manifest.json',
          api.file.read_json({'file_blame_jsons_version': 'some-version'})),
      api.post_process(post_process.StepFailure,
                       'Processing root.Generate File Blame JSONs'),
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
          'Processing root.Generate File Blame JSONs.Check for manifest package.cipd describe infra/history_rag/chrome/root/manifest',
          retcode=0),
      api.path.exists(api.path.cleanup_dir / 'root_manifest' /
                      'manifest.json',),
      api.step_data(
          'Processing root.Generate File Blame JSONs.Read manifest.json',
          api.file.read_json({
              'file_blame_jsons_package': f'{BASE_PKG_NAME}/oldrevision',
          })),
      api.post_process(post_process.StepFailure,
                       'Processing root.Generate File Blame JSONs'),
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
          'Processing root.Generate File Blame JSONs.Check for manifest package.cipd describe infra/history_rag/chrome/root/manifest',
          retcode=1),  # No baseline
      api.step_data(
          'Processing root.Generate File Blame JSONs.Upload File Blame JSONs to CIPD',
          stdout=api.raw_io.output_text('Something went wrong')),
      api.post_process(post_process.StepFailure,
                       'Processing root.Generate File Blame JSONs'),
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
          'Processing root.Generate File Blame JSONs.Check for manifest package.cipd describe infra/history_rag/chrome/root/manifest',
          retcode=1),  # No baseline

      # Mock cipd create for file blame json package
      api.step_data(
          'Processing root.Generate File Blame JSONs.Upload File Blame JSONs to CIPD',
          stdout=api.raw_io.output_text(
              'Instance: infra/history_rag/chrome/root/file_blame_jsons/newrevision:new-blame-instance-id'
          )),
      # Mock cipd create for manifest package
      api.step_data(
          'Processing root.Update Pointers to all JSON CIPD packages.Upload new manifest to cipd',
          stdout=api.raw_io.output_text('Oh no, no instance ID here')),
      api.post_process(
          post_process.StepFailure,
          'Processing root.Update Pointers to all JSON CIPD packages'),
      api.post_process(post_process.DropExpectation),
      status='FAILURE')

  def src_dir():
    # Get the base checkout directory from the test API and append 'src'
    return api.chromium_checkout.default_checkout_dir / 'src'

  gitmodules_content = """
    [submodule "good_sub"]
        path = good_sub
        url = https://example.com/good_sub.git
    [submodule "too_few_files"]
        path = too_few_files
        url = https://example.com/too_few_files.git
    [submodule "too_few_commits"]
        path = too_few_commits
        url = https://example.com/too_few_commits.git
    [submodule "nonexistent"]
        path = nonexistent
        url = https://example.com/nonexistent.git
    [submodule "no_path]
        url = ...
    """

  yield api.test(
      'submodule_discovery',
      builder_config_test_data(api),
      api.properties(**{
          '$build/chromium_checkout': {
              'gclient_config': 'chromium',
          },
      }),
      # Mock find HEAD for root
      api.step_data(
          'Find all submodules.Get HEAD for root',
          stdout=api.raw_io.output_text('newrevision')),
      # Mock .gitmodules existence and content
      api.path.exists(src_dir() / '.gitmodules'),
      api.step_data('Find all submodules.Read .gitmodules',
                    api.file.read_text(gitmodules_content)),
      # Mock existence of submodule directories on disk
      api.path.exists(src_dir() / 'good_sub',
                      src_dir() / 'too_few_files',
                      src_dir() / 'too_few_commits'),
      # Mock steps for min files check
      api.step_data(
          'Find all submodules.Check if good_sub has minimum number of files',
          stdout=api.raw_io.output_text(str(MIN_FILE_THRESHOLD))),
      api.step_data(
          'Find all submodules.Check if too_few_files has minimum number of files',
          stdout=api.raw_io.output_text(str(MIN_FILE_THRESHOLD - 1))),
      api.step_data(
          'Find all submodules.Check if too_few_commits has minimum number of files',
          stdout=api.raw_io.output_text(str(MIN_FILE_THRESHOLD))),
      # Mock steps for min commits check
      api.step_data(
          'Find all submodules.Check if good_sub has minimum number of commits',
          stdout=api.raw_io.output_text(str(MIN_COMMITS_THRESHOLD))),
      api.step_data(
          'Find all submodules.Check if too_few_commits has minimum number of commits',
          stdout=api.raw_io.output_text(str(MIN_COMMITS_THRESHOLD - 1))),
      # Mock find HEAD for good_sub
      api.step_data(
          'Find all submodules.Get HEAD for good_sub',
          stdout=api.raw_io.output_text('newrevision_good_sub')),
      # --- Mock for root processing ---
      api.step_data(
          'Processing root.Generate File Blame JSONs.Check for manifest package.cipd describe infra/history_rag/chrome/root/manifest',
          retcode=1),
      api.step_data(
          'Processing root.Generate File Blame JSONs.Upload File Blame JSONs to CIPD',
          stdout=api.raw_io.output_text('Instance: ...')),
      api.step_data(
          'Processing root.Update Pointers to all JSON CIPD packages.Upload new manifest to cipd',
          stdout=api.raw_io.output_text('Instance: ...')),

      # --- Mock steps for "good_sub" ---
      api.step_data(
          'Processing good_sub.Generate File Blame JSONs.Check for manifest package.cipd describe infra/history_rag/chrome/good_sub/manifest',
          retcode=1),
      api.step_data(
          'Processing good_sub.Generate File Blame JSONs.Upload File Blame JSONs to CIPD',
          stdout=api.raw_io.output_text('Instance: ...')),
      api.step_data(
          'Processing good_sub.Update Pointers to all JSON CIPD packages.Upload new manifest to cipd',
          stdout=api.raw_io.output_text('Instance: ...')),
      api.post_process(post_process.MustRun, 'Processing root'),
      api.post_process(post_process.MustRun, 'Processing good_sub'),
      api.post_process(post_process.DoesNotRun,
                       'Processing path_to_too_few_files'),
      api.post_process(post_process.DoesNotRun, 'Processing too_few_commits'),
      api.post_process(post_process.DoesNotRun, 'Processing nonexistent'),
      api.post_process(post_process.DropExpectation),
  )
