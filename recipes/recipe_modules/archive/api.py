# Copyright 2013 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import base64
import re
import os
import typing
import urllib.parse

from . import manual_bisect_files

from google.protobuf import json_format

from PB.recipe_modules.build.archive.properties import (
  ArchiveData,
  InputProperties,
)
from PB.recipe_modules.build.ssci.properties import GeneratedSBOM

from recipe_engine import recipe_api
from recipe_engine.config_types import Path

# Regular expression to identify a Git hash.
GIT_COMMIT_HASH_RE = re.compile(r'[a-zA-Z0-9]{40}')
# The Google Storage metadata key for the full commit position.
GS_COMMIT_POSITION_KEY = 'Cr-Commit-Position'
# The Google Storage metadata key for the commit position number.
GS_COMMIT_POSITION_NUMBER_KEY = 'Cr-Commit-Position-Number'
# The Google Storage metadata key for the Git commit hash.
GS_GIT_COMMIT_KEY = 'Cr-Git-Commit'
# SBOM file extension.
SBOM_EXTENSION = '.spdx.json'

# The name of the manifest file that will be written to the archive's build
# directory to store metadata about the archive for use by ClusterFuzz.
MANIFEST_FILENAME = 'clusterfuzz_manifest.json'
# File that stores the build arguments like GN args.
ARGS_FILENAME = 'args.gn'


class ArchiveApi(recipe_api.RecipeApi):
  """Chromium specific module for zipping, uploading and downloading build
  artifacts implemented as a wrapper around zip_build.py script.

  If you need to upload or download build artifacts (or any other files) for
  something other than Chromium flavor, consider using 'zip' + 'gsutil' or
  'isolate' modules instead.
  """

  def __init__(self, props, **kwargs):
    super().__init__(**kwargs)

    # This input property is populated by the global property $build/archive.
    self._default_config = props

  def zip_and_upload_build(
    self,
    step_name,
    target,
    source_dir: Path,
    *,
    build_url=None,
    build_revision=None,
    package_dsym_files=False,
    exclude_files=None,
    exclude_perf_test_files=False,
    update_properties=None,
    store_by_hash=True,
    platform=None,
    **kwargs,
  ):
    """Returns a step invoking zip_build.py to zip up a Chromium build.
    If build_url is specified, also uploads the build."""
    args = [
      '--target',
      target,
      '--gsutil-py-path',
      self.m.depot_tools.gsutil_py_path,
      '--staging-dir',
      self.m.path.cache_dir / 'cs',
      '--src-dir',
      source_dir,
    ]
    if 'build_archive_url' in self.m.properties:
      args.extend(
        [
          '--use-build-url-name',
          '--build-url',
          self.m.properties['build_archive_url'],
        ]
      )
    elif build_url:
      args.extend(['--build-url', build_url])
    if build_revision:
      args.extend(['--build_revision', build_revision])
    if package_dsym_files:
      args.append('--package-dsym-files')
    if exclude_files:
      args.extend(['--exclude-files', exclude_files])
    if 'gs_acl' in self.m.properties:
      args.extend(['--gs-acl', self.m.properties['gs_acl']])
    if exclude_perf_test_files and platform:
      include_bisect_file_list = manual_bisect_files.CHROME_REQUIRED_FILES.get(
        platform
      )
      include_bisect_strip_list = manual_bisect_files.CHROME_STRIP_LIST.get(
        platform
      )
      include_bisect_whitelist = manual_bisect_files.CHROME_WHITELIST_FILES.get(
        platform
      )
      if include_bisect_file_list:
        inclusions = ','.join(include_bisect_file_list)
        args.extend(['--include-files', inclusions])
      if include_bisect_strip_list:
        strip_files = ','.join(include_bisect_strip_list)
        args.extend(['--strip-files', strip_files])
      if include_bisect_whitelist:
        args.extend(['--whitelist', include_bisect_whitelist])
      args.extend(['--exclude-extra'])

      # If update_properties is passed in and store_by_hash is False,
      # we store it with commit position number instead of a hash
      if update_properties and not store_by_hash:
        commit_position = self._get_commit_position(update_properties, None)
        _, cp_number = self.m.commit_position.parse(commit_position)
        args.extend(['--build_revision', cp_number])

    args.extend(['--json-urls', self.m.json.output()])

    kwargs['step_test_data'] = lambda: self.test_api.m.json.output(
      {
        'storage_url': 'gs://zip_build.example.com/output.zip',
        'zip_url': 'https://storage.cloud.google.com/zip_build.example.com/output.zip',
      }
    )
    cmd = [
      'python3',
      self.repo_resource('recipes', 'zip_build.py'),
    ] + args
    result = self.m.step(step_name, cmd, infra_step=True, **kwargs)
    urls = result.json.output
    if 'storage_url' in urls:
      result.presentation.links['download'] = urls['storage_url']
    if 'zip_url' in urls:
      result.presentation.properties['build_archive_url'] = urls['zip_url']
    return result

  def _get_commit_position(self, update_properties, primary_project):
    """Returns the commit position of the project (or the specified primary
    project).
    """
    if primary_project:
      key = 'got_%s_revision_cp' % primary_project
    else:
      key = 'got_revision_cp'
    return update_properties.get(
      key, update_properties.get('got_src_revision_cp')
    )

  def _get_git_commit(self, update_properties, primary_project):
    """Returns: (str/None) the git commit hash for a given project.

    Attempts to identify the git commit hash for a given project. If
    'primary_project' is None, or if there is no git commit hash for the
    specified primary project, the checkout-wide commit hash will be used.

    If none of the candidate configurations are present, the value None will be
    returned.
    """
    if primary_project:
      commit = update_properties.get('got_%s_revision' % primary_project)
      if commit:
        assert GIT_COMMIT_HASH_RE.match(commit), commit
        return commit

    commit = update_properties.get('got_revision')
    if commit:
      assert GIT_COMMIT_HASH_RE.match(commit), commit
    return commit

  def _get_comparable_upload_path_for_sort_key(self, branch, number):
    """Returns a sortable string corresponding to the commit position."""
    if branch and branch not in ('refs/heads/master', 'refs/heads/main'):
      branch = branch.replace('/', '_')
      return '%s-%s' % (branch, number)
    return str(number)

  def _collect_runtime_deps(
    self,
    compile_targets: list[str],
    source_dir: Path,
    build_dir: Path,
    archive_root: Path,
  ) -> list[str]:
    """Collects the necessary runtime dependencies for the compile targets by
    reading the `.runtime_deps` file for each target in the `build_dir`.

    Returns the sorted set (list) of runtime dependencies with paths relative
    to the archive_root.

    Raises:
      file.Error: If any target's `.runtime_deps` file does not exist in the
        `build_dir`.
    """
    runtime_deps = set()

    with self.m.context(cwd=source_dir):
      with self.m.step.nest(
        'collect runtime deps for compile targets'
      ) as step_result:
        for target in compile_targets:
          target_name = target.removesuffix('.exe')
          deps_file = build_dir / f'{target_name}.runtime_deps'
          deps_content = self.m.file.read_text(
            f'read {target_name}.runtime_deps',
            deps_file,
            test_data=f'./{target}\n'
            f'./{target_name}_dependency\n'
            '../../testing/data/fuzzer_seed.txt',
          )
          deps = deps_content.splitlines()
          runtime_deps.update(
            # relpath() resolves paths relative to archive_root and normalizes
            # the paths (removes trailing slashes on directory dependencies).
            self.m.path.relpath(build_dir / dep, archive_root)
            for dep in deps
          )
          # Archive the .runtime_deps file itself, as it is needed by
          # ClusterFuzz to do target unpacking for coverage-guided fuzzers.
          runtime_deps.add(self.m.path.relpath(deps_file, archive_root))
        runtime_deps = sorted(runtime_deps)
        step_result.logs['paths_to_archive'] = runtime_deps
    return runtime_deps

  def clusterfuzz_archive_targets(
    self,
    source_dir: Path,
    archive_root: Path,
    update_properties,
    gs_bucket,
    compile_targets: list[str],
    build_dir: Path,
    *,
    archive_path: str,
    archive_schema_version: int = 1,
    fuzz_targets: list[str] | None = None,
    **kwargs,
  ) -> None:
    """Wrapper for clusterfuzz_archive that collects runtime deps for GN
    targets.

    This handles the dependency discovery, archiving targets and dependencies,
    adding needed files for ClusterFuzz, and delegates to the main
    clusterfuzz_archive function.

    Args:
      compile_targets: List of compiled targets with .runtime_deps files. The
        output archive will contain all these targets' outputs, all their
        runtime dependencies, the respective `.runtime_deps` files, and nothing
        more.
      build_dir: The absolute path to the build output directory.
      archive_path: Path prefix to use for the uploaded zip archive.
      archive_schema_version: Optional int to set metadata in
        `MANIFEST_FILENAME`. Default is 1.
      fuzz_targets: Optional list of fuzz target names within `build_dir`.
        These are converted to paths relative to the `archive_root`. This is
        intended for coverage-guided fuzzers so ClusterFuzz does not waste time
        discovering fuzzer paths and running non-fuzzer binaries.
      ... (other args are passed to clusterfuzz_archive)
    """
    if not compile_targets:
      raise ValueError("compile_targets must be provided and non-empty")

    runtime_deps = self._collect_runtime_deps(
      compile_targets, source_dir, build_dir, archive_root
    )
    args_file_path = build_dir / ARGS_FILENAME
    args_file = self.m.path.relpath(args_file_path, archive_root)
    paths_to_archive = [MANIFEST_FILENAME, args_file]
    if not self.m.tryserver.is_tryserver:
      # Trybots do not download remote-compiled binaries, so the files should
      # not be zipped, otherwise we encounter "No such file" errors.
      paths_to_archive.extend(runtime_deps)

    manifest_dict = {
      'archive_schema_version': archive_schema_version,
    }

    if fuzz_targets is not None:
      manifest_dict['fuzz_targets'] = [
        self.m.path.relpath(build_dir / target, archive_root)
        for target in fuzz_targets
      ]

    self.m.file.write_json(
      'write archive manifest',
      archive_root / MANIFEST_FILENAME,
      manifest_dict,
    )

    self.clusterfuzz_archive(
      source_dir=source_dir,
      archive_root=archive_root,
      update_properties=update_properties,
      gs_bucket=gs_bucket,
      archive_path=archive_path,
      paths_to_archive=paths_to_archive,
      **kwargs,
    )

  # TODO(518016282): Make this method private (e.g. rename to
  # `_clusterfuzz_archive`) once all builders have switched to
  # `clusterfuzz_archive_targets`.
  def clusterfuzz_archive(
    self,
    source_dir: Path,
    archive_root: Path,
    update_properties,
    gs_bucket,
    *,
    archive_path: str,
    paths_to_archive: list[str] | None = None,
    gs_acl=None,
    primary_project=None,
    sortkey_datetime=None,
  ) -> None:
    """Archives and uploads a build to google storage.

    The build is filtered by a list of file exclusions and then zipped. It is
    uploaded to google storage with some metadata about the commit position
    and revision attached.

    The zip file is named:
    `<archive_path>-<sort-key>[-experimental].zip`
    Where:
    - `<sort-key>` is either `sortkey_datetime` formatted as `YYYYMMDDHHMM`,
      or the commit position (formatted as `<branch>-<commit-position-number>`
      for non-main branches, or `<commit-position-number>` for main/master).
    - `-experimental` is appended if running in experimental mode.

    The archive is uploaded to `gs://<gs_bucket>/<subdir>/` where `<subdir>`
    is the directory component of `archive_path` (or root of bucket if none).

    Args:
      source_dir: The path to the top-level repo.
      archive_root: The absolute path of the directory to set as the root of
                    the archive. e.g., [cache]/builder/src/out/Release or
                    [cache]/builder/src.
      update_properties: The properties from the bot_update step (containing
                         commit information)
      gs_bucket: Name of the google storage bucket to upload to
      archive_path: Path prefix within the bucket to use for the uploaded zip
                    archive (e.g. 'linux-release/foo-linux-release').
                    A `-<sort-key>` suffix (and optional `-experimental`
                    suffix) will be appended to the basename of `archive_path`
                    along with the `.zip` extension.
      paths_to_archive: Optional list of dependency paths to include in the
                        archive, relative to archive_root. If included, it
                        will skip discovering paths to zip and use the
                        provided list.
      gs_acl: ACL used for the file on google storage
      primary_project: Optional project name for specifying the revision of the
                       checkout
      sortkey_datetime: If set, the api will use this datetime as the sortable
                        key path in the archive name, instead of trying to infer
                        it from the commit information.  This will be formatted
                        as YYYYMMDDHHMM.
    """

    gs_metadata = {}
    if sortkey_datetime is not None:
      sortkey_path = sortkey_datetime.strftime('%Y%m%d%H%M')
    else:
      commit_position = self._get_commit_position(
        update_properties, primary_project
      )
      cp_ref, cp_number = self.m.commit_position.parse(commit_position)
      sortkey_path = self._get_comparable_upload_path_for_sort_key(
        cp_ref, cp_number
      )
      gs_metadata[GS_COMMIT_POSITION_NUMBER_KEY] = cp_number
      if commit_position:
        gs_metadata[GS_COMMIT_POSITION_KEY] = commit_position
    build_git_commit = self._get_git_commit(update_properties, primary_project)
    staging_dir = self.m.path.cleanup_dir / 'cs'
    self.m.file.ensure_directory('create staging_dir', staging_dir)

    lzma_sdk_args = []
    can_fetch_7zip = self.m.platform.is_mac or (
      self.m.platform.is_linux
      and self.m.platform.arch == "intel"
      and self.m.platform.bits == 64
    )
    if self.m.path.exists(source_dir) and self.m.platform.is_win:
      lzma_sdk_args = [source_dir / 'third_party/lzma_sdk/bin/win64']
    elif can_fetch_7zip:
      cipd_pkg = 'infra/3pp/tools/7z/${platform}'
      lzma_sdk_args = [
        os.path.dirname(str(self.m.cipd.ensure_tool(cipd_pkg, 'latest')))
      ]

    if paths_to_archive:
      zip_file_list = paths_to_archive
    else:
      llvm_tools_to_copy = ['llvm-symbolizer', 'sancov']
      llvm_bin_dir = source_dir / 'third_party/llvm-build/Release+Asserts/bin'
      ext = '.exe' if self.m.platform.is_win else ''

      for tool in llvm_tools_to_copy:
        tool_src = self.m.path.join(llvm_bin_dir, tool + ext)
        tool_dst = self.m.path.join(archive_root, tool + ext)

        if not self.m.path.exists(tool_src):
          continue

        try:
          self.m.file.copy('Copy ' + tool, tool_src, tool_dst)
        except self.m.step.StepFailure:  # pragma: no cover
          # On some builds, it appears that a soft/hard link of llvm-symbolizer
          # exists in the archive root, which causes shutil.copy to raise an
          # exception. Either way, this shouldn't cause the whole build to fail.
          pass

      if not self.m.platform.is_win:
        llvm_lib_dir = source_dir / 'third_party/llvm-build/Release+Asserts/lib'
        libstdcplusplus_lib = 'libstdc++.so.6'
        libstdcplusplus_lib_src = self.m.path.join(
          llvm_lib_dir, libstdcplusplus_lib
        )
        libstdcplusplus_lib_dst = self.m.path.join(
          archive_root, libstdcplusplus_lib
        )
        if self.m.path.exists(libstdcplusplus_lib_src):
          try:
            self.m.file.copy(
              'Copy ' + libstdcplusplus_lib,
              libstdcplusplus_lib_src,
              libstdcplusplus_lib_dst,
            )
          except self.m.step.StepFailure:  # pragma: no cover
            # On some builds, it appears that a soft/hard link of libstdc++.so.6
            # exists in the archive root, which causes shutil.copy to raise an
            # exception. Either way, this shouldn't cause the whole build to fail.
            pass

      # Build the list of files to archive.
      cmd = [
        'python3',
        self.resource('filter_build_files.py'),
        '--dir',
        archive_root,
        '--platform',
        self.m.platform.name,
        '--output',
        self.m.json.output(),
      ]
      filter_result = self.m.step(
        'filter archive_root',
        cmd,
        infra_step=True,
        step_test_data=lambda: self.m.json.test_api.output(['file1', 'file2']),
      )

      zip_file_list = filter_result.json.output

    assert archive_path, 'archive_path must be provided'
    zip_file_base_name = os.path.basename(archive_path)
    subdir = os.path.dirname(archive_path)

    # `zip_file_base_name` is the file name minus the `.zip` extension, as
    # expected by `zip_archive.py`.
    zip_file_base_name += f'-{sortkey_path}'
    if self.m.runtime.is_experimental:
      zip_file_base_name += '-experimental'

    cmd = [
      'python3',
      self.resource('zip_archive.py'),
      '--output-dir',
      staging_dir,
      '--archive-name',
      zip_file_base_name,
      '--json-file-list',
      self.m.json.input(zip_file_list),
      '--file-relative-dir',
      archive_root,
      '--no-root-dir',
    ]
    if len(lzma_sdk_args) > 0:
      cmd.extend(['--lzma-sdk-dir'] + lzma_sdk_args)
    self.m.step('zipping', cmd, infra_step=True)

    zip_file_name = f'{zip_file_base_name}.zip'
    zip_file = staging_dir / zip_file_name

    if build_git_commit:
      gs_metadata[GS_GIT_COMMIT_KEY] = build_git_commit

    gs_args = []
    if gs_acl:
      gs_args.extend(['-a', gs_acl])
    # Only upload to GCS from CI bots, for ClusterFuzz to consume.
    if not self.m.tryserver.is_tryserver:
      self.m.gsutil.upload(
        zip_file,
        gs_bucket,
        "/".join([subdir, zip_file_name]),
        args=gs_args,
        metadata=gs_metadata,
        use_retry_wrapper=False,
      )
    self.m.file.remove(zip_file_name, zip_file)

  def download_and_unzip_build(
    self,
    step_name,
    target,
    build_url,
    source_dir: Path,
    *,
    build_revision=None,
    build_archive_url=None,
    **kwargs,
  ):
    """Returns a step invoking extract_build.py to download and unzip
    a Chromium build."""
    args = [
      '--gsutil-py-path',
      self.m.depot_tools.gsutil_py_path,
      '--target',
      target,
      '--src-dir',
      source_dir,
    ]
    if build_archive_url:
      args.extend(['--build-archive-url', build_archive_url])
    else:
      args.extend(['--build-url', build_url])
      if build_revision:
        args.extend(['--build_revision', build_revision])

    cmd = [
      'python3',
      self.repo_resource('recipes', 'extract_build.py'),
    ] + args
    self.m.step(step_name, cmd, infra_step=True, **kwargs)

  # FIXME(machenbach): This is currently used by win64 builders as well, which
  # have win32 in their archive names, which is confusing.
  def legacy_platform_name(self):
    """Replicates the behavior of PlatformName() in chromium_utils.py."""
    if self.m.platform.is_win:
      return 'win32'
    return self.m.platform.name

  def _legacy_url(self, is_download, gs_bucket_name, extra_url_components):
    """Computes a build_url suitable for uploading a zipped Chromium
    build to Google Storage.

    The reason this is named 'legacy' is that there are a large number
    of dependencies on the exact form of this URL. The combination of
    zip_build.py, extract_build.py, bot_utils.py, and runtest.py
    require that:

    * The platform name be exactly one of 'win32', 'mac', or 'linux'
    * The upload URL only name the directory on GS into which the
      build goes (zip_build.py computes the name of the file)
    * The download URL contain the unversioned name of the zip archive
    * The revision on the builder and tester machines be exactly the
      same

    There were too many dependencies to tease apart initially, so this
    function simply emulates the form of the URL computed by the
    underlying scripts.

    extra_url_components, if specified, should be a string without a
    trailing '/' which is inserted in the middle of the URL.

    The builder_name, or parent_buildername, is always automatically
    inserted into the URL.

    If build is running in experimental mode (see recipe_engine.runtime module),
    then 'experimental/' is prepended to path inside bucket automatically. This
    protects production builds from intererence from experimentation.
    """

    result = 'gs://' + gs_bucket_name
    if self.m.runtime.is_experimental:
      result += '/experimental'
    if extra_url_components:
      result += '/' + extra_url_components
    if is_download:
      result += (
        '/'
        + self.m.properties['parent_buildername']
        + '/'
        + 'full-build-'
        + self.legacy_platform_name()
        + '.zip'
      )
    else:
      result += '/' + self.m.buildbucket.builder_name
    return result

  def legacy_upload_url(self, gs_bucket_name, extra_url_components=None):
    """Returns a url suitable for uploading a Chromium build to Google
    Storage.

    extra_url_components, if specified, should be a string without a
    trailing '/' which is inserted in the middle of the URL.

    The builder_name, or parent_buildername, is always automatically
    inserted into the URL."""
    return self._legacy_url(False, gs_bucket_name, extra_url_components)

  def legacy_download_url(self, gs_bucket_name, extra_url_components=None):
    """Returns a url suitable for downloading a Chromium build from
    Google Storage.

    extra_url_components, if specified, should be a string without a
    trailing '/' which is inserted in the middle of the URL.

    The builder_name, or parent_buildername, is always automatically
    inserted into the URL."""
    return self._legacy_url(True, gs_bucket_name, extra_url_components)

  def _create_tar_archive_for_upload(
    self,
    archive_data: InputProperties.archive_datas,
    build_dir: typing.Text,
    files: typing.List[typing.Text],
    directories: typing.List[typing.Text],
    compression: typing.Text = 'gz',
    compression_level: typing.Optional[int] = None,
  ) -> None:
    """Adds files and dirs to a tar.gz file to be uploaded.

    Args:
      archive_data: An instance of
                    archive/properties.proto:InputProperties.archive_datas.
      build_dir: The absolute path to the build output directory.
      files: List of files to include. Paths are relative to |build_dir|.
      directories: List of directories to include. Paths are relative to
                   |build_dir|.
      compression: compression algorithm, allowed values are gz/bz2/zstd, and
                   default value is gz.
      compression_level: The compression level for zstd, integer between 1 and
                         20. This parameters has no effect for gz or bz2.

    Returns:
      Absolute path to the archive file or None if files and dirs do not exist
      and skip_empty_source is enabled.
    """
    suffix = {'gz': 'gz', 'zstd': 'zst'}[compression]
    tmp_dir = self.m.path.mkdtemp()
    output = tmp_dir / 'artifact.tar.{}'.format(suffix)
    pkg = self.m.tar.make_package(
      build_dir, output, compression, compression_level
    )

    for f in files:
      pkg.add_file(build_dir / f)
    for directory in directories:
      pkg.add_directory(build_dir / directory)

    try:
      pkg.tar('Create tar.{} archive'.format(suffix))
    except Exception:
      # Don't fail the build if there are no files to compress, and
      # skip_empty_source is enabled. Return an empty path.
      if archive_data.skip_empty_source:
        return None

    return output

  def _create_zip_archive_for_upload(
    self, archive_data, build_dir, files, directories
  ):
    """Adds files and directories to a zip file to be uploaded.

    Args:
      archive_data: An instance of
                    archive/properties.proto:InputProperties.archive_datas.
      build_dir: The absolute path to the build output directory.
      files: List of files to include. Paths are relative to |build_dir|.
      directories: List of directories to include. Paths are relative to
                   |build_dir|.

    Returns:
      Absolute path to the archive file or None if files and dirs do not exist
      and skip_empty_source is enabled.
    """
    # Create a temporary directory to hold the zipped archive.
    temp_dir = self.m.path.mkdtemp()
    output_path = temp_dir / 'artifact.zip'
    package = self.m.zip.make_package(build_dir, output_path)

    for f in files:
      package.add_file(build_dir / f)
    for directory in directories:
      package.add_directory(build_dir / directory)

    # An exception will be raised if there's an error, so we can assume that
    # this step succeeds.
    try:
      package.zip('Create generic archive')
    except Exception:
      # Don't fail the build if there are no files to compress, and
      # skip_empty_source is enabled. Return an empty path.
      if archive_data.skip_empty_source:
        return None

    return output_path

  def _get_canary_milestone(self):
    base_name = '/'.join(['chrome', 'VERSION'])

    def step_test_data():
      contents = '\n'.join(['MAJOR=91', 'MINOR=0', 'BUILD=4458', 'PATCH=0'])
      response_data = base64.b64encode(contents.encode('utf-8')).decode('ascii')
      return self.m.json.test_api.output(
        {
          'value': response_data,
        }
      )

    contents = self.m.gitiles.download_file(
      "https://chromium.googlesource.com/chromium/src.git/",
      base_name,
      branch='refs/heads/main',
      step_name='fetch milestone_branch',
      step_test_data=step_test_data,
    )
    canary_milestone = int(contents.split('\n')[0].split('=')[1])

    return canary_milestone

  def _get_milestone_position(self, source_dir: Path) -> str:
    canary_milestone = self._get_canary_milestone()
    milestone = int(self.m.chromium.get_version(source_dir)['MAJOR'])
    position = canary_milestone - milestone

    return "canary" if position == 0 else "canary-%s" % position

  def get_channel_name(self, source_dir: Path) -> str:
    """Get the current branch's channel name.

    Args:
      source_dir: The path to the top-level repo.

    Returns:
      The string of channel's name: it can be 'canary', 'beta', 'stable'
      'legacy<milestone>' or no return with an empty step.
    """
    canary_milestone = self._get_canary_milestone()
    milestone = int(self.m.chromium.get_version(source_dir)['MAJOR'])

    # Compare the milestone of latest Chromium with the current build to
    # determine the channel.
    if milestone == canary_milestone:
      return 'canary'
    if milestone + 1 == canary_milestone:
      return 'beta'
    if milestone + 2 == canary_milestone:
      return 'stable'
    if milestone + 10 >= canary_milestone:
      # Channel name for old milestones set to legacy.
      return 'legacy%s' % milestone
    self.m.step.empty(  # pragma: no cover
      'Unknown channel',
      status=self.m.step.FAILURE,
      step_text='Can not find channel for milestone: %s' % milestone,
    )

  def _evaluate_condition(self, source_dir: Path, condition):
    """Evaluates a condition from the following `when` placeholder. See
    _replace_placeholders.

    Returns:
      A boolean value to indicate the evaluated result of the condition.
    """
    if condition == 'is_canary':
      return self.get_channel_name(source_dir) == 'canary'
    self.m.step.empty(
      'Unknown condition',
      status=self.m.step.FAILURE,
      step_text=condition + ' can not be evaluated',
    )

  def _replace_placeholders(
    self,
    source_dir: Path,
    update_properties,
    custom_vars,
    input_str,
  ):
    # Evaluate the condition placeholder formatting like:
    # {% text when condition %}.
    # The text can contain other placeholders as well; the condition is
    # evaluated via _evaluate_condition function.
    # If the condition is evaluated to True, the entire placeholder will be
    # replaced into the text, otherwise it will be replaced into an empty
    # string.
    # The condition is currently used by Fuchsia archive to attach a ref when
    # the builder is running on canary. E.g.
    # {% m{%milestone}_fuchsia_ready when is_canary %}.
    for placeholder, value, condition in re.findall(
      '({%\s(.*?)\swhen\s(.*?)\s%})', input_str
    ):
      if self._evaluate_condition(source_dir, condition):
        input_str = input_str.replace(
          placeholder,
          self._replace_placeholders(
            source_dir, update_properties, custom_vars, value
          ),
        )
      else:
        input_str = input_str.replace(placeholder, '')

    position_placeholder = '{%position%}'
    if position_placeholder in input_str:
      commit_position = self._get_commit_position(update_properties, None)
      if not commit_position:
        self.m.step.empty(
          'Missing position placeholder',
          status=self.m.step.FAILURE,
          step_text=(
            'got_revision_cp or got_src_revision_cp is needed to populate '
            'the {%position%} placeholder'
          ),
        )
      _, position = self.m.commit_position.parse(commit_position)
      input_str = input_str.replace(position_placeholder, str(position))

    milestone_position_placeholder = '{%milestone_position%}'
    if milestone_position_placeholder in input_str:
      milestone_position = self._get_milestone_position(source_dir)
      input_str = input_str.replace(
        milestone_position_placeholder, milestone_position
      )

    channel_placeholder = '{%channel%}'
    if channel_placeholder in input_str:
      channel = self.get_channel_name(source_dir)
      input_str = input_str.replace(channel_placeholder, channel)

    arch_placeholder = '{%arch%}'
    if arch_placeholder in input_str:
      if (
        self.m.chromium.c.TARGET_ARCH == 'arm'
        and self.m.chromium.c.TARGET_BITS in (64, 32)
      ):
        arch = 'arm' + str(self.m.chromium.c.TARGET_BITS)
      elif (
        self.m.chromium.c.TARGET_ARCH == 'intel'
        and self.m.chromium.c.TARGET_BITS == 64
      ):
        arch = 'amd64'
      else:  # pragma: no cover
        self.m.step.empty(
          'Unresolved placeholder',
          status=self.m.step.FAILURE,
          step_text='Unsupported value for arch placeholder: %s-%d'
          % (self.m.chromium.c.TARGET_ARCH, self.m.chromium.c.TARGET_BITS),
        )
      input_str = input_str.replace(arch_placeholder, arch)

    commit_placeholder = '{%commit%}'
    if commit_placeholder in input_str:
      commit = self._get_git_commit(update_properties, None)
      if not commit:
        self.m.step.empty(
          'Missing commit placeholder',
          status=self.m.step.FAILURE,
          step_text=(
            'got_revision is needed to populate the {%commit%} placeholder'
          ),
        )
      input_str = input_str.replace(commit_placeholder, commit)

    timestamp_placeholder = '{%timestamp%}'
    if timestamp_placeholder in input_str:
      timestamp = str(self.m.time.utcnow().strftime('%Y%m%d%H%M%S'))
      input_str = input_str.replace(timestamp_placeholder, timestamp)

    chromium_version_placeholder = '{%chromium_version%}'
    if chromium_version_placeholder in input_str:
      version = self.m.chromium.get_version(source_dir)
      value = "%s.%s.%s.%s" % (
        version['MAJOR'],
        version['MINOR'],
        version['BUILD'],
        version['PATCH'],
      )
      input_str = input_str.replace(chromium_version_placeholder, value)

    builder_name_placeholder = '{%builder_name%}'
    if builder_name_placeholder in input_str:
      builder_name = self.m.buildbucket.builder_name
      input_str = input_str.replace(builder_name_placeholder, builder_name)

    build_number_placeholder = '{%build_number%}'
    if build_number_placeholder in input_str:
      build_number = str(self.m.buildbucket.build.number)
      input_str = input_str.replace(build_number_placeholder, build_number)

    milestone_placeholder = '{%milestone%}'
    if milestone_placeholder in input_str:
      input_str = input_str.replace(
        milestone_placeholder, self.m.chromium.get_version(source_dir)['MAJOR']
      )

    if custom_vars:
      for placeholder, key in re.findall('({%(.*?)%})', input_str):
        if key in custom_vars:
          input_str = input_str.replace(placeholder, custom_vars[key])
        else:
          self.m.step.empty(
            'Unresolved placeholder',
            status=self.m.step.FAILURE,
            step_text=placeholder + ' can not be resolved',
          )

    return input_str

  def _deconstruct_version(self, version):
    """Breaks version down into list of parts."""
    segments = []
    for segment in version.strip().split('.'):
      segments.append(int(segment))
    return segments

  def _read_source_side_archive_spec(self, source_side_archive_spec_path):
    if not self.m.path.exists(source_side_archive_spec_path):
      self.m.step.empty(
        'Could not find specified archive config',
        status=self.m.step.INFRA_FAILURE,
        step_text=f'{source_side_archive_spec_path} archive spec does not exist.',
      )
    source_side_archive_spec = self.m.file.read_json(
      'read archive spec (%s)'
      % self.m.path.basename(source_side_archive_spec_path),
      source_side_archive_spec_path,
      test_data={},
    )
    self.m.step.active_result.presentation.step_text = (
      'path: %s' % source_side_archive_spec_path
    )
    return source_side_archive_spec

  def _get_source_side_archive_spec(self, checkout_dir: Path, spec_path):
    source_side_archive_spec_path = checkout_dir.joinpath(*spec_path)
    archive_spec = self._read_source_side_archive_spec(
      source_side_archive_spec_path
    )
    return archive_spec

  def _get_archive_config(self, checkout_dir: Path, config):
    if config is None:
      config = self._default_config

    if not config.source_side_spec_path:
      return config

    source_side_archive_spec = self._get_source_side_archive_spec(
      checkout_dir, config.source_side_spec_path
    )
    if source_side_archive_spec:
      source_side_config = json_format.ParseDict(
        source_side_archive_spec, InputProperties(), ignore_unknown_fields=True
      )
      if config.verify_paths_only:
        source_side_config.verify_paths_only = True
      return source_side_config
    return config

  def _validate_paths(self, name, archive_data, base_path, paths):
    """Checks all paths for existence.

    Raises an error if at least one path is missing. If skip_empty_sources is
    set, return existing paths instead.
    """
    # In test mode, all paths exist by default and do not need to be mocked.
    if self._test_data.enabled:
      # These paths are used for testing the validation step
      if not ('missing-file.json' in paths or 'missing-dir' in paths):
        return paths

    with self.m.step.nest('Validate %s' % name) as presentation:
      valid = []
      missing = []
      for path in paths:
        path_exists = self.m.path.exists(self.m.path.join(base_path, path))
        (missing, valid)[path_exists].append(path)

      if missing:
        msg = 'The following %s are missing: %s' % (name, ', '.join(missing))
        presentation.step_text = msg

        if not archive_data.skip_empty_source:
          raise recipe_api.StepFailure('Missing %s' % name)

    return valid

  def _expand_archive_files(self, base_path, file_globs):
    """Expand the archive files from file_globs"""
    expanded_files = set()
    for filename in file_globs:
      for f in self.m.file.glob_paths(
        'expand file globs',
        base_path,
        filename,
        test_data=('glob1.txt', 'glob2.txt'),
      ):
        # Turn the returned Path object back into a string relative to
        # base_path.
        assert base_path.base == f.base
        assert base_path in f.parents
        common_pieces = f.pieces[len(base_path.pieces) :]
        expanded_files.add(os.path.sep.join(common_pieces))
    return expanded_files

  def _verify_gcs_archive_paths(
    self, checkout_dir: Path, build_dir: Path, archive_data
  ):
    base_path = build_dir
    if archive_data.base_dir:
      base_path = checkout_dir / archive_data.base_dir

    expanded_files = set(archive_data.files)
    expanded_files |= self._expand_archive_files(
      base_path, archive_data.file_globs
    )
    self._validate_paths('files', archive_data, base_path, expanded_files)
    self._validate_paths(
      'directories', archive_data, base_path, list(archive_data.dirs)
    )

  def _verify_archive_paths(
    self,
    checkout_dir: Path,
    build_dir: Path,
    *,
    archive_config: InputProperties = None,
  ):
    """Verify the existence of the paths in archive config."""
    with self.m.step.nest('Verify Archive Paths', status='last'):
      for archive_data in archive_config.archive_datas:
        self._verify_gcs_archive_paths(checkout_dir, build_dir, archive_data)
      if archive_config.cipd_archive_datas:
        raise self.m.step.StepFailure(
          'verify_paths_only only applies to archive_datas '
          'but cipd_archive_datas specified.'
        )

  def generic_archive(
    self,
    checkout_dir: Path,
    source_dir: Path,
    build_dir: Path,
    update_properties,
    *,
    custom_vars=None,
    config=None,
    report_artifacts=False,
    use_hardlink=False,
    generate_sboms=False,
  ):
    """Archives one or multiple packages to google cloud storage, CIPD or npm.

    The exact configuration of the archive is specified by InputProperties. See
    archive/properties.proto.

    Args:
      checkout_dir: The directory where the checkout was performed.
      source_dir: The path to the top-level repo.
      build_dir: The absolute path to the build output directory, e.g.
                 [cache]/builder/src/out/Release
      update_properties: The properties from the bot_update step (containing
                         commit information).
      custom_vars: Dict of custom string substitution for gcs paths.
                   E.g. custom_vars={'chrome_version':'1.2.3.4'}, then
                   gcs_path='gcs/{%chrome_version%}/path' will be replaced to
                   'gcs/1.2.3.4/path'.
      config: An instance of archive/properties.proto:InputProperties.
              DEPRECATED: If None, this will default to the global property
              $build/archive.
      report_artifacts: A boolean flag to enable artifact reporting. This is
                        set by recipe that uses this module.
      use_hardlink: A boolean for creating hardlinks instead of copying files
                    on preparing a temporary directory for archives. This flag
                    also enables the batch file operations via resource script,
                    to avoid too many steps in the build.
      generate_sboms: A Boolean for enabling SBOM generation and reporting, set
                      by the recipe using this module.

    Returns:
      A dictionary that stores custom_vars and update_properties, as well as
      the following keys:
        gcs: A list of dictionaries of files and their respective upload
             destination urls.
        cipd: A dictionary containing information about unused references for
              each package.
        npm: A list of dictionaries with the name, version and registry url of
             each npm package.

    """
    upload_results = {}
    upload_results['cipd'] = {}
    upload_results['gcs'] = []
    upload_results['npm'] = []
    upload_results['sbom'] = {}
    upload_results['update_properties'] = update_properties
    upload_results['custom_vars'] = custom_vars

    archive_config = self._get_archive_config(checkout_dir, config)

    if (
      not archive_config.archive_datas
      and not archive_config.cipd_archive_datas
      and not archive_config.npm_archive_datas
    ):
      return upload_results

    # Trybuilders using $build/archive (config is None) or a source-side spec
    # may only verify archive paths (unlike explicit in-memory configs).
    if (
      config is None or config.source_side_spec_path
    ) and self.m.tryserver.is_tryserver:
      assert archive_config.verify_paths_only, (
        'verify_paths_only must be True when configuring archives on '
        'trybuilders'
      )

    if archive_config.verify_paths_only:
      self._verify_archive_paths(
        checkout_dir, build_dir, archive_config=archive_config
      )
      return upload_results

    with self.m.step.nest('Generic Archiving Steps', status='last'):
      for archive_data in archive_config.archive_datas:
        if not archive_data.only_upload_on_tests_success:
          gcs_uploads = self.gcs_archive(
            checkout_dir,
            source_dir,
            build_dir,
            update_properties,
            archive_data,
            custom_vars=custom_vars,
            generate_sboms=generate_sboms,
            report_artifacts=report_artifacts,
            use_hardlink=use_hardlink,
          )
          upload_results['gcs'].append(gcs_uploads)
      for cipd_archive_data in archive_config.cipd_archive_datas:
        upload_results['cipd'].update(
          self.cipd_archive(
            source_dir,
            build_dir,
            update_properties,
            custom_vars,
            cipd_archive_data,
            report_artifacts=report_artifacts,
          )
        )
      for npm_archive_data in archive_config.npm_archive_datas:
        upload_results['npm'].append(
          self.npm_archive(
            source_dir,
            build_dir,
            update_properties,
            custom_vars,
            npm_archive_data,
          )
        )
    return upload_results

  def generic_archive_after_tests(
    self,
    checkout_dir: Path,
    source_dir: Path,
    build_dir: Path,
    *,
    config=None,
    upload_results=None,
    test_success=False,
  ):
    """Additional archiving steps after tests run.

    For google cloud storage packages, they will only be uploaded in this step
    if test_success is True and only_upload_on_tests_success is set to True,

    For CIPD packages, if test_success is True then refs will be added for each
    package with only_set_refs_on_tests_success set to True.

    Args:
      checkout_dir: The path to where the checkout was performed.
      source_dir: The path to the top-level repo.
      upload_results: The upload results from generic_archive.

    For information about other args see generic_archive.
    """
    if not upload_results or not test_success:
      return

    archive_config = self._get_archive_config(checkout_dir, config)

    if (
      not archive_config.archive_datas and not archive_config.cipd_archive_datas
    ):
      return

    if archive_config.verify_paths_only:
      return

    with self.m.step.nest('Generic Archiving Steps After Tests'):
      for archive_data in archive_config.archive_datas:
        if archive_data.only_upload_on_tests_success:
          self.gcs_archive(
            checkout_dir,
            source_dir,
            build_dir,
            upload_results['update_properties'],
            archive_data,
            custom_vars=upload_results['custom_vars'],
          )
      if upload_results['cipd']:
        for pkg in upload_results['cipd']:
          self.m.cipd.set_ref(
            package_name=pkg,
            version=upload_results['cipd'][pkg]['instance'],
            refs=upload_results['cipd'][pkg]['refs'],
          )

  def batch_copy(self, src, dst, file_list):
    input_file_list = self.m.path.mkstemp()
    self.m.file.write_text(
      'Write file list to copy',
      input_file_list,
      (os.linesep).join(sorted(file_list)),
    )
    self.m.step(
      'Copy files to a temp folder',
      [
        'vpython3',
        self.resource('batch.py'),
        'copy',
        '--des-dir',
        dst,
        '--base-dir',
        src,
        '--input-file-list',
        input_file_list,
      ],
      infra_step=True,
    )

  def gcs_archive(
    self,
    checkout_dir: Path,
    source_dir: Path,
    build_dir: Path,
    update_properties,
    archive_data,
    *,
    custom_vars=None,
    generate_sboms=False,
    report_artifacts=False,
    use_hardlink=False,
  ):
    """Archives a single package to google cloud storage.

    The exact configuration of the archive is specified by InputProperties. See
    archive/properties.proto.

    Args:
      checkout_dir: The directory where the checkout was performed.
      source_dir: The path to the top-level repo.
      build_dir: The absolute path to the build output directory, e.g.
                 [cache]/builder/src/out/Release
      update_properties: The properties from the bot_update step (containing
                         commit information).
      archive_data: An instance of
                    archive/properties.proto:InputProperties.archive_datas.
      custom_vars: Dict of custom string substitution for gcs paths.
                   E.g. custom_vars={'chrome_version':'1.2.3.4'}, then
                   gcs_path='gcs/{%chrome_version%}/path' will be replaced to
                   'gcs/1.2.3.4/path'.
      generate_sboms: A Boolean for enabling SBOM generation and reporting, set
                      by the recipe using this module.
      report_artifacts: A boolean flag to enable artifact reporting.
      use_hardlink: See generic_archive().
    """

    def _sanitize_gcs_path(gcs_path, file_path):
      gcs = gcs_path.split('/')
      f = file_path.split('/')
      return (
        '/'.join([x for x in gcs if x]) + '/' + '/'.join([x for x in f if x])
      )

    base_path = build_dir
    if archive_data.base_dir:
      base_path = checkout_dir / archive_data.base_dir

    # Perform dynamic configuration from placeholders, if necessary.
    gcs_path = self._replace_placeholders(
      source_dir, update_properties, custom_vars, archive_data.gcs_path
    )

    gcs_bucket = self._replace_placeholders(
      source_dir, update_properties, custom_vars, archive_data.gcs_bucket
    )

    experimental = self.m.runtime.is_experimental
    if experimental:
      gcs_bucket += "/experimental"

    gcs_args = []
    expanded_files = set(archive_data.files)
    expanded_files |= self._expand_archive_files(
      base_path, archive_data.file_globs
    )
    expanded_files = set(
      self._validate_paths('files', archive_data, base_path, expanded_files)
    )

    # Copy all files to a temporary directory. Keeping the structure.
    # This directory will be used for archiving.
    temp_dir = self.m.path.mkdtemp()
    if archive_data.root_permission_override:
      self.m.step(
        'Update temporary folder permissions',
        [
          'chmod',
          archive_data.root_permission_override,
          str(temp_dir),
        ],
        infra_step=True,
      )

    if use_hardlink:
      self.batch_copy(base_path, temp_dir, expanded_files)
    else:
      created = set()
      for filename in sorted(expanded_files):
        tmp_file_path = self.m.path.join(temp_dir, filename)
        tmp_file_dir = self.m.path.dirname(tmp_file_path)
        if not str(tmp_file_dir) in created:
          self.m.file.ensure_directory(
            'Create temp dir %s' % os.path.dirname(filename), tmp_file_dir
          )
          created.add(str(tmp_file_dir))
        self.m.file.copy(
          "Copy file %s" % filename,
          self.m.path.join(base_path, filename),
          tmp_file_path,
        )

    updated_dirs = self._validate_paths(
      'directories', archive_data, base_path, list(archive_data.dirs)
    )

    for directory in updated_dirs:
      self.m.file.copytree(
        "Copy folder %s" % directory,
        self.m.path.join(base_path, directory),
        self.m.path.join(temp_dir, directory),
        symlinks=True,
        hardlink=use_hardlink,
        allow_override=True,
      )

    # Starting here, we will only need to care about the temporary folder
    # which holds the files. So reset the base_path to temp_dir.
    base_path = temp_dir

    for rename_file in archive_data.rename_files:
      expanded_files.remove(rename_file.from_file)

      # Support placeholder replacement for file renames.
      new_filename = self._replace_placeholders(
        source_dir, update_properties, custom_vars, rename_file.to_file
      )
      expanded_files.add(new_filename)
      self.m.file.move(
        "Move file",
        self.m.path.join(base_path, rename_file.from_file),
        self.m.path.join(base_path, new_filename),
      )

    root_rename = None
    for rename_dir in archive_data.rename_dirs:
      # Renaming the archive root is a special case which would affect other
      # renames, so save it until all the other renames are finished.
      if rename_dir.from_dir == '.':
        root_rename = rename_dir
        continue

      # Support placeholder replacement for renames.
      new_dirname = self._replace_placeholders(
        source_dir, update_properties, custom_vars, rename_dir.to_dir
      )

      move_from_path = self.m.path.join(base_path, rename_dir.from_dir)
      for idx, dirname in enumerate(updated_dirs):
        if dirname == rename_dir.from_dir or dirname.startswith(
          rename_dir.from_dir + self.m.path.sep
        ):
          updated_dirs[idx] = dirname.replace(
            rename_dir.from_dir, rename_dir.to_dir, 1
          )

      moved_files = {}
      for fn in expanded_files:
        if fn.startswith(rename_dir.from_dir + self.m.path.sep):
          moved_files[fn] = fn.replace(
            rename_dir.from_dir, rename_dir.to_dir, 1
          )
      expanded_files = expanded_files.difference(moved_files.keys())
      expanded_files = expanded_files.union(moved_files.values())

      self.m.file.move(
        "Move dir: '%s'->'%s'" % (rename_dir.from_dir, new_dirname),
        move_from_path,
        self.m.path.join(base_path, new_dirname),
      )

    if root_rename:
      # Handle special case of adding a prefix path to the archive dir (i.e.
      # moving the archive to a subdir of itself).
      # The archive dir is temporarily moved to a new path because you can't
      # actually move a dir into a subdir of itself.
      new_dirname = self._replace_placeholders(
        source_dir, update_properties, custom_vars, root_rename.to_dir
      )
      move_from_path = self.m.path.mkdtemp().joinpath(
        self.m.path.basename(base_path)
      )
      self.m.file.move("Prep archive root move", base_path, move_from_path)
      self.m.file.move(
        "Move dir: '%s'->'%s'" % (root_rename.from_dir, new_dirname),
        move_from_path,
        self.m.path.join(base_path, new_dirname),
      )
      # All files and need to be prefixed with the new root.
      expanded_files = set(
        self.m.path.join(new_dirname, fn) for fn in expanded_files
      )
      updated_dirs = [self.m.path.join(new_dirname, d) for d in updated_dirs]

    expanded_files = sorted(expanded_files)

    # Get map of local file path to upload -> destination file path in GCS
    # bucket.
    uploads = {}
    if archive_data.archive_type == ArchiveData.ARCHIVE_TYPE_FILES:
      if archive_data.dirs:
        self.m.step.empty(
          'ARCHIVE_TYPE_FILES does not support dirs',
          status=self.m.step.FAILURE,
          step_text=(
            'archive_data properties with |archive_type| '
            'ARCHIVE_TYPE_FILES must have empty |dirs|'
          ),
        )
      uploads = {
        base_path / f: _sanitize_gcs_path(gcs_path, f) for f in expanded_files
      }
    elif archive_data.archive_type == ArchiveData.ARCHIVE_TYPE_FLATTEN_FILES:
      if archive_data.dirs:
        self.m.step.empty(
          'ARCHIVE_TYPE_FLATTEN_FILES does not support dirs',
          status=self.m.step.FAILURE,
          step_text=(
            'archive_data properties with |archive_type| '
            'ARCHIVE_TYPE_FLATTEN_FILES must have empty |dirs|'
          ),
        )
      uploads = {
        base_path / f: _sanitize_gcs_path(gcs_path, self.m.path.basename(f))
        for f in expanded_files
      }
    elif archive_data.archive_type == ArchiveData.ARCHIVE_TYPE_TAR_GZ:
      archive_file = self._create_tar_archive_for_upload(
        archive_data, base_path, expanded_files, updated_dirs, 'gz'
      )
      if archive_file:
        uploads = {archive_file: gcs_path}
    elif archive_data.archive_type == ArchiveData.ARCHIVE_TYPE_TAR_ZSTD:
      compression_level = None
      if archive_data.tar_zstd_params.compression_level:
        compression_level = archive_data.tar_zstd_params.compression_level
      archive_file = self._create_tar_archive_for_upload(
        archive_data,
        base_path,
        expanded_files,
        updated_dirs,
        'zstd',
        compression_level=compression_level,
      )
      if archive_file:
        uploads = {archive_file: gcs_path}
    elif archive_data.archive_type == ArchiveData.ARCHIVE_TYPE_RECURSIVE:
      if not archive_data.dirs:
        self.m.step.empty(
          'ARCHIVE_TYPE_RECURSIVE does not support empty dirs',
          status=self.m.step.FAILURE,
          step_text=(
            'archive_data properties with |archive_type| '
            'ARCHIVE_TYPE_RECURSIVE must specify |dirs|'
          ),
        )
      uploads = {base_path / d: gcs_path for d in updated_dirs}
      gcs_args += ['-R']
    elif archive_data.archive_type == ArchiveData.ARCHIVE_TYPE_SQUASHFS:
      archive_file = self.m.path.mkdtemp().joinpath('image.squash')
      algorithm = None
      compression_level = None
      block_size = None
      if (
        archive_data.squashfs_params.algorithm == 'zstd'
        or archive_data.squashfs_algorithm == 'zstd'
      ):
        if archive_data.squashfs_params.algorithm:
          algorithm = archive_data.squashfs_params.algorithm
        else:
          algorithm = archive_data.squashfs_algorithm
        # We just set compression_level to 22(highest).
        compression_level = 22
      if archive_data.squashfs_params.compression_level:
        compression_level = archive_data.squashfs_params.compression_level
      if archive_data.squashfs_params.block_size:
        block_size = archive_data.squashfs_params.block_size
      self.m.squashfs.mksquashfs(
        base_path, archive_file, algorithm, compression_level, block_size
      )
      uploads = {archive_file: gcs_path}
    else:
      archive_file = self._create_zip_archive_for_upload(
        archive_data, base_path, expanded_files, updated_dirs
      )
      if archive_file:
        uploads = {archive_file: gcs_path}

    # Build SBOMs for artifacts that require them.
    generated_sboms = {}
    if (
      generate_sboms
      and archive_data.HasField('requires_sbom')
      and archive_data.archive_type
      in (
        ArchiveData.ARCHIVE_TYPE_ZIP,
        ArchiveData.ARCHIVE_TYPE_FILES,
        ArchiveData.ARCHIVE_TYPE_FLATTEN_FILES,
      )
    ):
      # TODO(b/356745797): Remove try/except once SBOM generation
      # is stable.
      try:
        sbom_config = archive_data.requires_sbom
        if len(sbom_config.gn_targets) > 0:
          sbom_artifact_dict = {}
          for f, d in uploads.items():
            sbom_artifact_dict[self.m.path.basename(d)] = f

          sboms = self.m.ssci.generate_sbom_for_artifacts(
            artifacts=sbom_artifact_dict,
            source_dir=source_dir,
            build_dir=build_dir,
            gn_targets=sbom_config.gn_targets,
          )

          for f, sbom in sboms.items():
            sbom_gcs_path = _sanitize_gcs_path(
              gcs_path, self.m.path.basename(f)
            )
            if archive_data.archive_type == ArchiveData.ARCHIVE_TYPE_ZIP:
              sbom_gcs_path = gcs_path + SBOM_EXTENSION
            generated_sboms[sbom.sbom_path] = sbom_gcs_path
      except Exception as e:
        skip_sbom = self.m.step.empty(
          'skip SBOM generation because of an exception'
        )
        skip_sbom.presentation.step_text = f'exception raised: {e}'

    # Report artifacts that require provenance.
    if (
      archive_data.requires_provenance
      and not archive_data.archive_type == ArchiveData.ARCHIVE_TYPE_RECURSIVE
    ):
      for path, gcs_path in generated_sboms.items():
        # TODO(b/356745797): Remove try/except once SBOM generation
        # is stable.
        try:
          # Report SBOM artifacts for provenance generation.
          # SBOM's must be reported before their artifact counterpart.
          spdx_hash = self.m.file.file_hash(path, test_data='spdxbeef')
          # Need to report full destination path of the artifact.
          if report_artifacts:
            artifact_path = str(path).removesuffix(SBOM_EXTENSION)
            artifact_hash = self.m.file.file_hash(
              sbom_artifact_dict[self.m.path.basename(artifact_path)],
              test_data='deadbeef',
            )

            self.m.bcid_reporter.report_sbom(
              spdx_hash, 'gs://%s/%s' % (gcs_bucket, gcs_path), artifact_hash
            )

        except Exception as e:
          skip_sbom = self.m.step.empty(
            'skip SBOM reporting because of an exception'
          )
          skip_sbom.presentation.step_text = f'exception raised: {e}'

      for path, gcs_path in uploads.items():
        # Report all other artifacts for provenance generation.
        file_hash = self.m.file.file_hash(path, test_data='deadbeef')
        # Need to report full destination path of the artifact.
        if report_artifacts:
          self.m.bcid_reporter.report_gcs(
            file_hash, 'gs://%s/%s' % (gcs_bucket, gcs_path)
          )

    # Upload the generated SBOMs the same way the rest of the
    # artifacts are uploaded.
    uploads.update(generated_sboms)

    if archive_data.prevent_overwrites:
      gcs_args.append('-n')

    for file_path in uploads.keys():
      self.m.gsutil.upload(
        file_path,
        bucket=gcs_bucket,
        dest=uploads[file_path],
        args=gcs_args,
        name="upload {}".format(str(uploads[file_path])),
      )

    if archive_data.HasField('latest_upload'):
      if (
        not archive_data.latest_upload.gcs_file_content
        or not archive_data.latest_upload.gcs_path
      ):
        self.m.step.empty(
          (
            'latest_upload.gcs_path or latest_upload.gcs_file_content'
            ' not declared'
          ),
          status=self.m.step.FAILURE,
          step_text=(
            'Both latest_gcs_path and '
            'latest_gcs_file_content must be non-empty.'
          ),
        )

      latest_path = self._replace_placeholders(
        source_dir,
        update_properties,
        custom_vars,
        archive_data.latest_upload.gcs_path,
      )
      content = self._replace_placeholders(
        source_dir,
        update_properties,
        custom_vars,
        archive_data.latest_upload.gcs_file_content,
      )

      if archive_data.latest_upload.gcs_bucket:
        latest_gcs_bucket = self._replace_placeholders(
          source_dir,
          update_properties,
          custom_vars,
          archive_data.latest_upload.gcs_bucket,
        )
      else:
        latest_gcs_bucket = gcs_bucket

      if '{%chromium_version%}' in archive_data.latest_upload.gcs_file_content:
        file_name = self.m.path.basename(latest_path)
        dest_path = self.m.path.mkdtemp().joinpath(file_name)

        try:
          self.m.gsutil.download(
            bucket=latest_gcs_bucket, source=latest_path, dest=dest_path
          )
          last_version = self.m.file.read_text(
            'Read in last version', dest_path, test_data='1.2.3.4'
          )
        except Exception:
          last_version = '0.0.0.0'

        last_versions = self._deconstruct_version(last_version)
        new_versions = self._deconstruct_version(content)

        for last, new in zip(last_versions, new_versions):
          if last > new:
            content = last_version
            break
          if new > last:
            break

      content_ascii = content.encode('ascii', 'ignore')
      temp_dir = self.m.path.mkdtemp()
      output_file = temp_dir / 'latest.txt'
      self.m.file.write_text('Write latest file', output_file, content_ascii)
      self.m.gsutil.upload(
        output_file,
        bucket=latest_gcs_bucket,
        dest=latest_path,
        name="upload {}/{}".format(latest_gcs_bucket, latest_path),
      )

    # Generates a REVISIONS file
    if archive_data.HasField('revisions_file'):
      pattern = re.compile('^got_.*revision(_cp)?$')
      cp_pattern = re.compile('{#(\d*)}')
      content = {}
      for key, val in update_properties.items():
        if re.search(pattern, key):
          if key == 'got_v8_revision':
            content['v8_revision_git'] = val
          elif key == 'got_revision_cp':
            cp = re.search(cp_pattern, update_properties[key])
            content['chromium_revision'] = cp.group(1)
          elif key == 'got_v8_revision_cp':
            cp = re.search(cp_pattern, update_properties[key])
            content["v8_revision"] = cp.group(1)
          content[key] = val
      content_json = self.m.json.dumps(content)

      temp_dir = self.m.path.mkdtemp()
      output_file = temp_dir / 'revisions.txt'
      self.m.file.write_text('Write REVISIONS file', output_file, content_json)
      revisions_path = self._replace_placeholders(
        source_dir,
        update_properties,
        custom_vars,
        archive_data.revisions_file.gcs_path,
      )
      self.m.gsutil.upload(
        output_file,
        bucket=gcs_bucket,
        dest=revisions_path,
        name="upload {}".format(revisions_path),
      )

    return uploads

  def _replace_placeholders_in_list(
    self,
    source_dir: Path,
    update_properties,
    custom_vars,
    values,
  ):
    ret = []
    for value in values:
      value = self._replace_placeholders(
        source_dir, update_properties, custom_vars, value
      )
      if value:
        ret.append(value)
    return ret

  def _replace_placeholders_in_dict(
    self,
    source_dir: Path,
    update_properties,
    custom_vars,
    values,
  ):
    ret = dict(values)
    for key in ret:
      ret[key] = self._replace_placeholders(
        source_dir, update_properties, custom_vars, ret[key]
      )
    return {k: v for k, v in ret.items() if v}

  def cipd_archive(
    self,
    source_dir: Path,
    build_dir: Path,
    update_properties,
    custom_vars,
    cipd_archive_data,
    *,
    report_artifacts=False,
  ):
    """Archives packages to CIPD.

    Args:
      build_dir: The absolute path to the build output directory, e.g.
                 [cache]/builder/src/out/Release
      update_properties: The properties from the bot_update step (containing
                         commit information).
      custom_vars: Dict of custom string substitution for value used in
                   pkg_vars and tags.
      cipd_archive_data: An instance of archive/properties.proto:
                         InputProperties.cipd_archive_datas.
    """
    refs = self._replace_placeholders_in_list(
      source_dir, update_properties, custom_vars, cipd_archive_data.refs
    )
    tags = self._replace_placeholders_in_dict(
      source_dir, update_properties, custom_vars, cipd_archive_data.tags
    )
    pkg_vars = self._replace_placeholders_in_dict(
      source_dir, update_properties, custom_vars, cipd_archive_data.pkg_vars
    )

    compression_level = None
    if cipd_archive_data.HasField('compression'):
      compression_level = cipd_archive_data.compression.compression_level
    verification_timeout = None
    if cipd_archive_data.HasField('verification'):
      verification_timeout = cipd_archive_data.verification.verification_timeout

    pkg_refs = refs
    if cipd_archive_data.only_set_refs_on_tests_success:
      pkg_refs = None

    upload_results = {}
    for yaml_file in cipd_archive_data.yaml_files:
      pkg_def = build_dir / yaml_file
      create_from_yaml = self.m.cipd.create_from_yaml
      if report_artifacts:
        create_from_yaml = self.m.bcid_reporter.create_from_yaml
      create_results = create_from_yaml(
        pkg_def=pkg_def,
        refs=pkg_refs,
        tags=tags,
        pkg_vars=pkg_vars,
        compression_level=compression_level,
        verification_timeout=verification_timeout,
      )
      if cipd_archive_data.only_set_refs_on_tests_success:
        # Store info needed for setting refs through calling
        # generic_archive_after_tests.
        upload_results[create_results[0]] = {
          'refs': refs,
          'instance': create_results[1],
        }
    return upload_results

  def npm_archive(
    self,
    source_dir: Path,
    build_dir: Path,
    update_properties,
    custom_vars,
    npm_archive_data,
  ):
    """Packages and publishes an npm package to an npm registry / OSS Exit Gate.

    A published npm version can never be reused. So experimental and try builds
    only do a dry run, and a version that is already in the registry is not
    published again (e.g. when a build is retried).

    Args:
      source_dir: Path to the source repository root (e.g. chromium/src).
      build_dir: Path to the build output directory (e.g. out/Release).
      update_properties: The properties from the bot_update step.
      custom_vars: Dict of custom string substitutions.
      npm_archive_data: An instance of archive/properties.proto: NpmArchiveData.

    Returns:
      A dict with the name, version and registry url of the package.
    """
    package_name = npm_archive_data.package_name
    step_name = f'publish {package_name} to npm'
    with self.m.step.nest(step_name):
      registry_url = self._replace_placeholders(
        source_dir,
        update_properties,
        custom_vars,
        npm_archive_data.registry_url,
      )
      dist_tag = self._replace_placeholders(
        source_dir,
        update_properties,
        custom_vars,
        npm_archive_data.dist_tag,
      )
      manifest_bucket = self._replace_placeholders(
        source_dir,
        update_properties,
        custom_vars,
        npm_archive_data.manifest_bucket,
      )
      manifest_path = self._replace_placeholders(
        source_dir,
        update_properties,
        custom_vars,
        npm_archive_data.manifest_path,
      )
      dry_run_only = (
        self.m.runtime.is_experimental or self.m.tryserver.is_tryserver
      )

      # 1. Ensure Node.js & npm via CIPD
      nodejs_version = npm_archive_data.nodejs_version or 'version:3@26.8.2'
      npm_exe = 'npm.cmd' if self.m.platform.is_win else 'bin/npm'
      npm_bin = self.m.cipd.ensure_tool(
        'infra/3pp/tools/nodejs/${platform}',
        nodejs_version,
        executable_path=npm_exe,
      )

      # 2. Setup clean package directory
      pkg_dir = self.m.path.cleanup_dir / f'{package_name}-npm'
      self.m.file.rmtree('clean package dir', pkg_dir)
      self.m.file.ensure_directory('ensure package dir', pkg_dir)

      # 3. Copy root files from package_source_dir
      package_source_dir = source_dir
      if npm_archive_data.package_source_dir:
        package_source_dir = source_dir.joinpath(
          *npm_archive_data.package_source_dir.split('/')
        )
      for root_file in npm_archive_data.package_root_files:
        src_file = package_source_dir / root_file
        dest_file = pkg_dir / root_file
        self.m.file.ensure_directory(
          f'ensure dir for {root_file}', dest_file.parent
        )
        self.m.file.copy(f'copy {root_file}', src_file, dest_file)

      # 4. Copy build artifacts according to artifact_mappings
      for src_rel, dest_rel in npm_archive_data.artifact_mappings.items():
        src_path = build_dir.joinpath(*src_rel.split('/'))
        dest_path = pkg_dir.joinpath(*dest_rel.split('/'))
        self.m.file.ensure_directory(
          f'ensure dir for {dest_rel}', dest_path.parent
        )
        self.m.file.copytree(f'copy {src_rel}', src_path, dest_path)

      # 5. Copy extra source/checkout files according to file_mappings
      for file_mapping in npm_archive_data.file_mappings:
        src_rel = file_mapping.from_file
        dest_rel = self._replace_placeholders(
          source_dir,
          update_properties,
          custom_vars,
          file_mapping.to_file,
        )
        src_path = source_dir.joinpath(*src_rel.split('/'))
        dest_path = pkg_dir.joinpath(*dest_rel.split('/'))
        self.m.file.ensure_directory(
          f'ensure dir for {dest_rel}', dest_path.parent
        )
        self.m.file.copy(f'copy {dest_rel}', src_path, dest_path)

      # 6. Prune unwanted outputs
      for pattern in npm_archive_data.pruned_outputs:
        self.m.file.rmglob(f'prune {pattern}', pkg_dir, pattern)

      with self.m.context(cwd=pkg_dir, env_prefixes={'PATH': [npm_bin.parent]}):
        # 7. Set package version (converting 4-part Chrome version to SemVer)
        if npm_archive_data.version:
          package_version = self._replace_placeholders(
            source_dir,
            update_properties,
            custom_vars,
            npm_archive_data.version,
          )
          if '-' not in package_version and package_version.count('.') == 3:
            base, patch = package_version.rsplit('.', 1)
            package_version = f'{base}-{patch}'
          self.m.step(
            'npm version',
            [
              npm_bin,
              'version',
              package_version,
              '--no-git-tag-version',
              '--allow-same-version',
            ],
          )
        else:
          package_version = self.m.file.read_json(
            'read package.json',
            pkg_dir / 'package.json',
            test_data={'version': '1.0.0'},
          )['version']

        # 8. Authenticate to the registry. npm never packs .npmrc, and
        # include_log=False keeps the token out of the step logs.
        token = self.m.service_account.default().get_access_token(
          scopes=['https://www.googleapis.com/auth/cloud-platform']
        )
        registry = urllib.parse.urlsplit(registry_url)
        registry_path = registry.path.rstrip('/')
        self.m.file.write_text(
          'write .npmrc',
          pkg_dir / '.npmrc',
          f'//{registry.netloc}{registry_path}/:_authToken="{token}"\n',
          include_log=False,
        )

        # 9. Publish to registry
        publish_cmd = [
          npm_bin,
          'publish',
          '--registry',
          registry_url,
          '--access',
          'public',
        ]
        if dist_tag:
          publish_cmd.extend(['--tag', dist_tag])
        if self._is_npm_version_published(
          npm_bin, package_name, package_version, registry_url
        ):
          self.m.step.empty(
            'skip npm publish',
            step_text=f'{package_name}@{package_version} is already published',
          )
        else:
          self.m.step('npm publish (dry run)', publish_cmd + ['--dry-run'])
          if dry_run_only:
            self.m.step.empty(
              'skip npm publish',
              step_text='experimental and try builds only do a dry run',
            )
          else:
            self.m.step('npm publish', publish_cmd)

      # 10. Upload publishing manifest to GCS if configured
      if manifest_bucket and manifest_path and not dry_run_only:
        manifest_content = (
          self.m.json.dumps(
            {
              'publish_all': False,
              'publishing_groups': [{'packages': [{'name': package_name}]}],
            },
            indent=2,
          )
          + '\n'
        )
        manifest_file = pkg_dir / 'publishing_manifest.json'
        self.m.file.write_text(
          'write publishing manifest', manifest_file, manifest_content
        )
        self.m.gsutil.upload(
          manifest_file,
          manifest_bucket,
          manifest_path,
          name='upload publishing manifest to OSS Exit Gate',
        )

      return {
        'package_name': package_name,
        'version': package_version,
        'registry_url': registry_url,
      }

  def _is_npm_version_published(
    self,
    npm_bin: Path,
    package_name: str,
    package_version: str,
    registry_url: str,
  ) -> bool:
    """Returns whether the package version is already in the registry.

    `npm view` fails for a package that doesn't exist yet and prints nothing
    for a version that doesn't exist. Both mean "not published". Other failures
    (e.g. missing permissions) make the following `npm publish` steps fail.
    """
    result = self.m.step(
      'check if version is already published',
      [
        npm_bin,
        'view',
        f'{package_name}@{package_version}',
        'version',
        '--registry',
        registry_url,
      ],
      stdout=self.m.raw_io.output_text(),
      ok_ret='any',
      step_test_data=lambda: self.m.raw_io.test_api.stream_output_text(''),
    )
    return (result.stdout or '').strip() == package_version
