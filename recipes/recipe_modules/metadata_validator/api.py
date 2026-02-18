# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import re
import pathlib
import configparser

from recipe_engine.recipe_api import RecipeApi
from dataclasses import dataclass, field
from recipe_engine.post_process import StatusFailure, StatusSuccess
from PB.recipe_engine import result as result_pb
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb

SKIP_FOOTER = 'Metadata-Validate-Bypass'
BYPASS_TEXT = ('\n\n**To bypass this check, add '
               f'`{SKIP_FOOTER}: <REASON>` '
               'to your CL description.**')

CIPD_GCS_CAVEAT = ('\n\nLicenses stored in CIPD or GCS aren\'t detected '
                   'correctly')

BYPASSED_TEXT = '\n\n**Validation bypassed by footer.**'

RUN_LOCALLY_FOOTNOTE = (
    '\n\nTo run these checks locally, use '
    '`python3 <path_to_depot_tools>/metadata/scan.py <path/to/dependency>`')

METADATA_FILES = [
    'README.chromium',
    'README.angle',
    'README.pdfium',
    'README.crashpad',
    'README.skia',
    'README.swarming',
    'README.v8',
    'README.webrtc',
    'README.google',
    'README.libaom',
]

# Truncate the list of failures to keep the summary under
# Buildbucket's 4000 byte limit on the summary_markdown field.
MAX_SUMMARY = 4000

# This list is used to determine if scan.py should provide
# --is-open-source-project flag which allows more permissive license usage.
OPEN_SOURCE_HOSTS = frozenset([
    'chromium-review.googlesource.com',
    'pdfium-review.googlesource.com',
    'webrtc-review.googlesource.com',
    'angle-review.googlesource.com',
    'dawn-review.googlesource.com',
    'skia-review.googlesource.com',
    'swiftshader-review.googlesource.com',
])


@dataclass
class ValidationResult:
  errors: list[str] = field(default_factory=list)
  warnings: list[str] = field(default_factory=list)


class MetadataValidatorApi(RecipeApi):

  @property
  def _working_dir(self):
    return self.m.path.cleanup_dir / 'gerrit_readme_checkout'

  def _write_to_working_dir(self, rel_path, content):
    """Writes content to the working directory, mirroring the relative path."""
    dest_path = self._working_dir / rel_path
    self.m.file.ensure_directory(f'Ensure cache dir for {rel_path}',
                                 dest_path.parent)
    return self.m.file.write_text(f'Write {rel_path} to cache', dest_path,
                                  content)

  def extract_license_paths(self, readme_content):
    """
    Parses README.chromium content to find 'License File:' lines
    and extract the file paths.
    """
    license_files = set()
    license_file_re = re.compile(r'License File:\s*(.+)', re.IGNORECASE)
    for line in readme_content.splitlines():
      if match := license_file_re.match(line):
        for substr in match.group(1).split(','):
          if path := substr.strip():
            license_files.add(path)
    return list(license_files)

  def format_validation_summary(self, all_results):
    failure_message_lines = []

    # Determine if we have multiple files with issues.
    multi_file = len(all_results) > 1

    for filename, res in all_results.items():
      # Only disambiguate filenames if multiple files involved.
      if multi_file:
        failure_message_lines.append(f'{filename}:')

      file_errors = res.errors
      file_warnings = res.warnings

      # Formatting logic.
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

    footer_text = RUN_LOCALLY_FOOTNOTE + BYPASS_TEXT

    # Prepend the cipd/gcs caveat.
    if "License" in failure_msg:
      footer_text = CIPD_GCS_CAVEAT + footer_text

    if len(failure_msg) + len(footer_text) > MAX_SUMMARY:
      trunc = '\n\n... (truncated)'
      # Truncate content leaving room for truncation msg and footer.
      allowed_len = MAX_SUMMARY - len(trunc) - len(footer_text)
      failure_msg = failure_msg[:allowed_len] + trunc

    return failure_msg + footer_text

  def get_files_map(self, change):
    """Extract files_map from change object.

    Returns:
      dict: {file_path: {'status': 'A'|'M'|'D', ...}, ...}
            where status is 'A' (added), 'M' (modified), or 'D' (deleted).
    """
    current_revision = change.get('current_revision')
    return change.get('revisions', {}).get(current_revision,
                                           {}).get('files', {})

  def is_file_deleted(self, change, file_path):
    """Check if a file is deleted in the given change.

    Returns:
      bool: True if the file is marked as deleted.
    """
    if file_desc := self.get_files_map(change).get(file_path):
      return file_desc.get('status') == 'D'
    return False

  def fetch_gerrit_file_content(self,
                                file_path,
                                change,
                                accept_statuses=None) -> str | None:
    """Fetch file content using Gitiles API.

    Returns:
      The content of the file as a string, or None if fetch failed or skipped.
    """
    if accept_statuses is None:
      accept_statuses = [200]
    cl = self.m.tryserver.gerrit_change
    if self.is_file_deleted(change, file_path):
      self.m.step.empty(
          f'Not fetching {file_path} because it is deleted in the CL')
      return None

    # TODO(b/483862822): Remove this once url paths are properly encoded.
    if " " in file_path:
      self.m.step.empty(
          f'Not fetching {file_path} because it contains spaces, see '
          'b/483862822')
      return None

    # TODO(b/484171134): Support downloading .md files.
    if ".md" in file_path:
      self.m.step.empty(
          f'Not fetching {file_path} because it contains .md, see b/484171134')
      return None

    # Construct Gitiles repo URL from Gerrit host/project.
    gitiles_host = cl.host.replace('-review.googlesource.com',
                                   '.googlesource.com')
    repo_url = f'https://{gitiles_host}/{cl.project}'

    return self.m.gitiles.download_file(
        repo_url,
        file_path,
        branch=change.get('current_revision', 'HEAD'),
        step_name=f'Fetch {file_path}',
        attempts=5,
        accept_statuses=accept_statuses)

  def submodule_path_map(self,
                         change,
                         child_path_filter=None) -> dict[str, str]:
    """Returns a map from submodule path to project url.

    Args:
      change: The Gerrit change object.
      child_path_filter: Optional filter for submodule paths that are parents of
        this path. E.g. if the child_path_filter is
        `third_party/foo/bar/cats.png`, then the filter will only allow subsets
        of 'third_party/foo/bar' to be returned.

    Returns:
      dict[str, str]: A map from submodule path to submodule url.
    """
    submodule_map = {}
    # .gitmodules might not exist in the CL.
    git_modules = self.fetch_gerrit_file_content(
        '.gitmodules', change, accept_statuses=[200, 400, 404])
    if not git_modules:
      self.m.step.empty('No .gitmodules file found')
      return submodule_map

    parser = configparser.ConfigParser()
    parser.read_string(git_modules)

    # Use start_dir to avoid the "mismatched bases" error
    temp_modules = self.m.path.start_dir / 'temp_gitmodules'
    self.m.file.write_text('write .gitmodules for parsing', temp_modules,
                           git_modules)

    # We use --get-regexp to find the unique names without parsing the line.
    with self.m.context(cwd=self.m.path.start_dir):
      res = self.m.git(
          'config',
          '--file',
          temp_modules,
          '--get-regexp',
          r'submodule\..*\.path',
          stdout=self.m.raw_io.output_text(),
          name='list submodules')

    if not res.stdout:
      return submodule_map

    for line in res.stdout.splitlines():
      if not line:
        continue

      # Git output for --get-regexp is: <full_key> <value>
      # Example: 'submodule.my.path.with.dots.path third_party/path'.
      path_key, path_val = line.split(' ', 1)
      if child_path_filter and not child_path_filter.startswith(path_val):
        continue
      url_key = path_key.rsplit('.path', 1)[0] + '.url'

      with self.m.context(cwd=self.m.path.start_dir):
        url_val = self.m.git(
            'config',
            '--file',
            temp_modules,
            '--get',
            url_key,
            stdout=self.m.raw_io.output_text()).stdout.strip()

      if path_val and url_val:
        submodule_map[path_val] = url_val
    return submodule_map

  def fetch_submodule_license(self, license_path, readme_content, change):
    """Fetch license file from the submodule repo using Gitiles.

    This searches for the submodule root by finding the submodule path in the
    .gitmodules file closest to the license path. A custom gitiles call is made
    to fetch the license file from the submodule repo.

    Returns:
      str | None: The content of the license file as a string,
      or None if fetch failed or skipped.
    """
    p_license = pathlib.PurePosixPath(license_path)
    submodules = self.submodule_path_map(change, child_path_filter=license_path)
    # We start from the direct parent.
    submodule_dir = ""
    for parent in p_license.parents:
      parent_str = str(parent)
      if parent_str == '.':
        continue
      if parent_str in submodules:
        submodule_dir = parent_str
        break
    if not submodule_dir:
      self.m.step.empty('No submodule root found for license path')
      return None

    url = submodules.get(submodule_dir, '')
    self.m.step.empty(f'{submodule_dir} - submodules', step_text=f"{url}")

    # The submodule_dir is a gitlink; fetch its content to get the SHA1.
    content = self.fetch_gerrit_file_content(submodule_dir, change)
    if not content or not re.fullmatch(r'[0-9a-fA-F]{40}', content.strip()):
      self.m.step.empty(f'Failed to fetch SHA1 for submodule {submodule_dir}')
      return None
    submodule_rev = content.strip()

    # Found submodule at 'parent_str'.
    # Calculate path relative to submodule root.
    rel_path = str(p_license.relative_to(submodule_dir))

    # TODO(b/483862822): Remove this once url paths are properly encoded.
    if " " in rel_path:
      self.m.step.empty(
          f'Not fetching {submodule_dir} : {rel_path} because it contains '
          'spaces, see b/483862822')
      return None

    # TODO(b/484171134): Support downloading .md files.
    if ".md" in rel_path:
      self.m.step.empty(
          f'Not fetching {submodule_dir} : {rel_path} because it contains .md, '
          'see b/484171134')
      return None
    step_name = f'Fetch license from submodule {submodule_dir} : {rel_path}'
    return self.m.gitiles.download_file(
        url,
        rel_path,
        branch=submodule_rev,
        step_name=step_name,
        accept_statuses=[200, 400, 404])

  def get_changes(self):
    """Helper to get the set of affected files in the current CL.

    Returns:
      list: A list of change objects (dicts).
    """
    cl = self.m.tryserver.gerrit_change
    host = cl.host
    change_id = cl.change
    gerrit_url = f'https://{host}'
    return self.m.gerrit.get_changes(
        gerrit_url,
        query_params=[('change', str(change_id))],
        o_params=['CURRENT_COMMIT', 'CURRENT_FILES', 'CURRENT_REVISION'],
        limit=1)

  def is_metadata_file(self, file_path):
    """Check if the given file path is a recognized metadata file.

    Returns:
      bool: True if the file path ends with a known metadata filename.
    """
    for metadata_file in METADATA_FILES:
      if file_path.endswith(metadata_file):
        return True
    return False

  def get_affected_READMEs(self, change):
    """Get README.chromium files that are modified or added in the CL.

    Returns:
      set: Paths to README.chromium files that are not deleted in the change.
    """
    affected_file_set = set()
    for fpath, finfo in self.get_files_map(change).items():
      # Only process READMEs that have not been deleted.
      if self.is_metadata_file(fpath) and finfo.get('status') != 'D':
        affected_file_set.add(fpath)
    return sorted(affected_file_set)

  def resolve_license_path(self, readme_path, license_rel_path):
    """Resolves the license path relative to the README file.

    Args:
      readme_path (str):
        The path to the README file (e.g., 'third_party/foo/README.chromium').
      license_rel_path (str):
        The license path string found in the README.

    Returns:
      pathlib.PurePosixPath:
        The resolved path (relative to the repo root).
    """
    # Metadata files use POSIX path syntax, and root directory
    # refers to the repository root.
    p_license = pathlib.PurePosixPath(license_rel_path)
    if p_license.is_absolute():
      # Strip leading slashes (handles '/LICENSE' and '//LICENSE').
      return pathlib.PurePosixPath(license_rel_path.lstrip('/'))

    # Resolve relative to the README's directory.
    p_readme = pathlib.PurePosixPath(readme_path)
    return p_readme.parent.joinpath(p_license)

  def fetch_and_write_licenses(self, file_path, readme_content, change):
    """Fetch and cache license files referenced in README content.

    Parses the README for "License File:" entries, resolves relative paths
    based on the README's directory, fetches each license from gerrit, and
    writes them to the working directory. Also creates copies under the README
    directory for absolute-path licenses to assist the validator.
    """
    license_paths_relative = self.extract_license_paths(readme_content)

    for lic_path_str in license_paths_relative:
      resolved_path = self.resolve_license_path(file_path, lic_path_str)
      resolved_path_str = str(resolved_path)

      license_content = self.fetch_gerrit_file_content(
          resolved_path_str, change, accept_statuses=[200, 400, 404])
      if license_content is None and not self.is_file_deleted(
          change, resolved_path_str):
        # If Gerrit fetch failed (e.g. 404 because file is in a submodule),
        # try Gitiles fallback.
        license_content = self.fetch_submodule_license(resolved_path_str,
                                                       readme_content, change)

      if license_content is not None:
        self._write_to_working_dir(resolved_path_str, license_content)

  def _is_internal_host(self, host):
    """Checks if the Gerrit host is internal (not in the allowlist)."""
    return host not in OPEN_SOURCE_HOSTS

  def validate_readmes(self, readme_paths):
    """Validate a single README file and return validation results.

    Args:
      readme_paths: List of README paths to validate.

    Returns:
      dict[str, ValidationResult]: A dictionary mapping readme paths to their
      validation results.
    """
    readme_dir_abs = self._working_dir

    json_out = self.m.json.output()
    all_results = {}

    with self.m.step.nest('Validate') as step:
      cl = self.m.tryserver.gerrit_change
      cmd = [
          'vpython3',
          self.m.depot_tools.root.joinpath('metadata', 'scan.py'),
          readme_dir_abs,
          '--json-summary',
          json_out,
      ]

      if not self._is_internal_host(cl.host):
        cmd.append('--is-open-source-project')
      else:
        self.m.step.empty(
            f'Enforcing reciprocal license check for host {cl.host}')

      step_res = self.m.step('Run validator', cmd, raise_on_failure=False)

      json_output = step_res.json.output or {}
      for filepath, issues in json_output.get('files', {}).items():
        # Only process the requested files.
        if filepath not in readme_paths:
          continue

        errors = []
        warnings = []
        for issue in issues:
          msg = issue['message']
          if ".md" in msg:
            continue
          if issue.get('fatal', False):
            errors.append(msg)
            step.status = self.m.step.FAILURE
          else:
            warnings.append(msg)
            if step.status != self.m.step.FAILURE:
              step.status = self.m.step.WARNING

        if errors or warnings:
          all_results[filepath] = ValidationResult(
              errors=errors, warnings=warnings)

      return all_results

  def validate(self):

    # Check for bypass footer.
    bypass_validation = bool(self.m.tryserver.get_footer(SKIP_FOOTER))
    if bypass_validation:
      self.m.step('Validation bypassed', cmd=None)
      return result_pb.RawResult(
          status=common_pb.SUCCESS, summary_markdown=BYPASSED_TEXT)

    changes = self.get_changes()
    assert len(changes) == 1, f'Expected 1 change, got {len(changes)}'
    change = changes[0]

    with self.m.step.nest('Fetch affected metadata files'):
      # Ensure working dir exists to satisfy scan.py
      self.m.file.ensure_directory('Ensure working dir', self._working_dir)

      readme_files = self.get_affected_READMEs(change)
      if not readme_files:
        self.m.step('no readme changes', cmd=None)
        return

      for file_path in readme_files:
        with self.m.step.nest(f'Processing {file_path}'):
          readme_content = self.fetch_gerrit_file_content(file_path, change)
          if readme_content is not None:
            self._write_to_working_dir(file_path, readme_content)
            self.fetch_and_write_licenses(file_path, readme_content, change)

    all_results = self.validate_readmes(sorted(readme_files))

    # Fail if issues found (and format nice message).
    failure_msg = self.format_validation_summary(all_results)
    self.m.step.empty('Validation Issues', step_text=failure_msg)

    if not failure_msg:
      return

    any_errors_found = any(res.errors for res in all_results.values())
    if any_errors_found:
      raise self.m.step.StepFailure(failure_msg)

    return result_pb.RawResult(
        status=common_pb.SUCCESS, summary_markdown=failure_msg)
