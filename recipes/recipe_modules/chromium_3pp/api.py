# Copyright 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""API to create 3pp-based packaged in chromium."""

from __future__ import annotations

from recipe_engine import recipe_api


class Chromium3ppApi(recipe_api.RecipeApi):

  def __init__(self, properties, **kwargs):
    super().__init__(**kwargs)

    self._package_prefix = properties.package_prefix
    self._package_paths_to_build = properties.package_paths_to_build
    self._platform = properties.platform
    self._force_build = properties.force_build
    self._preprocess = properties.preprocess
    self._gclient_config = properties.gclient_config
    self._gclient_apply_config = properties.gclient_apply_config

    # If local_checkout_dir exist, it will be used instead of a fresh checkout.
    # This is used to test creating and building new 3pp packages locally.
    self._checkout_path = properties.local_checkout_dir

  def prepare(self):
    """Sets up a chromium 3pp run.

    This includes:
     * Setting up the given configs.
     * setting up the checkout w/ bot_update
    """
    if self._checkout_path:
      # Avoid running gclient or ensure_checkout so that local changes are not
      # overwritten. This must be done in the experimental repo.
      assert self.m.runtime.is_experimental, (
          'When local_checkout_dir is defined, is_experimental is required. '
          'See https://crrev.com/c/3749816 for context.')
      original_path_str = self._checkout_path
      self._checkout_path = self.m.path.abs_to_path(self._checkout_path)
      assert self.m.path.exists(self._checkout_path / '.gclient'), (
          'The chromium_3pp recipe expects local_checkout_dir to be the dir '
          'that contains .gclient. You probably need to pass in the parent '
          f'dir instead of: {original_path_str}')
    else:
      self.m.gclient.set_config(self._gclient_config)
      for c in self._gclient_apply_config:
        self.m.gclient.apply_config(c)
      self.m.chromium_checkout.ensure_checkout()
      self._checkout_path = self.m.chromium_checkout.checkout_dir

  def _get_git_diff(self, name, staged_only=False):
    """Get the local git diff.

    Args:
      * name: The name of the step.
      * staged_only: A boolean indicating if return only the staged diff or not.

    Returns:
      The StepResult from the git step.
    """

    args = [
        'diff',
        '--diff-filter=d',  # exclude deleted paths
        '--name-only',
    ]
    if staged_only:
      args.append('--cached')

    return self.m.git(
        *args, name=name, stdout=self.m.raw_io.output_text(add_output_log=True))

  def execute(self):
    """Run the chromium_3pp steps.

    The steps include:
      * Return early if it is a tryjob but has no 3pp changes.
      * Retrieve the 3pp packages to build.
      * Run any specified preprocess.
      * Confirm the preprocess is no-op.
      * Build the 3pp packages, and upload them to CIPD if applicable.
    """
    if self._package_prefix:
      # Cast package_prefix to str since its type is unicode, but
      # set_package_prefix expects a str
      self.m.support_3pp.set_package_prefix(str(self._package_prefix))
    self.m.support_3pp.set_source_cache_prefix('3pp_sources')

    package_paths_to_build = set(self._package_paths_to_build)

    if self.m.tryserver.is_tryserver:
      self.m.support_3pp.set_experimental(True)

      # Analyze if the patch contains 3pp related changes
      # and return early if it does not.
      with self.m.context(cwd=self._checkout_path / 'src'):
        # Files from patch are under staged state so use "--cached" to only
        # show the staged changes.
        staged_diff_result = self._get_git_diff('Analyze', staged_only=True)
      file_paths = staged_diff_result.stdout.splitlines()
      for file_path in file_paths:
        # `git diff` always returns posix style separators ('/'):
        file_dirs = file_path.split('/')
        if '3pp' in file_dirs:
          index = file_dirs.index('3pp')
          package_paths_to_build.add(self.m.path.sep.join(file_dirs[:index]))

      if package_paths_to_build:
        staged_diff_result.presentation.logs['package_paths_to_build'] = sorted(
            package_paths_to_build)
      else:
        step_result = self.m.step('No 3pp related changes', cmd=None)
        return

    if is_report_bcid := not (self.m.tryserver.is_tryserver or
                              self.m.runtime.is_experimental):
      self.m.bcid_reporter.report_stage("start")

    # Special preprocess steps for scripts that auto-generate 3pp PB files.
    if self._preprocess:
      for process in self._preprocess:
        # Replace the placeholder {CHECKOUT} with the actual value
        process_args = [
            arg.format(CHECKOUT=self._checkout_path) for arg in process.cmd
        ]
        self.m.step('Preprocessing %s' % process.name, process_args)

      # Fail if there are unexpected (i.e. not part of the CL under test)
      # changes related to 3pp.
      # This is to prevent the preprocess steps above from making unexpected
      # changes to 3pp files.
      with self.m.context(cwd=self._checkout_path / 'src'):
        unstaged_diff_result = self._get_git_diff('Confirm no-op')

      unexpected_3pp_files = []
      for file_path in unstaged_diff_result.stdout.splitlines():
        if '3pp' in file_path.split(self.m.path.sep):
          unexpected_3pp_files.append(file_path)
      if unexpected_3pp_files:
        failure_text = 'Unexpected 3pp changes:\n'
        failure_text += '\n'.join(unexpected_3pp_files)
        raise self.m.step.StepFailure(failure_text)

    with self.m.step.nest('Load all packages'):
      self.m.support_3pp.load_packages_from_path(
          self._checkout_path / 'src',
          glob_pattern='**/3pp/3pp.pb',
          check_dup=True)

    cipd_pkg_names_to_build = set()

    for package_path in package_paths_to_build:
      with self.m.step.nest('Load to-build packages from %s' % package_path):
        for pattern in ('**/3pp/3pp.pb', './3pp/3pp.pb'):
          cipd_pkg_names_to_build.update(
              self.m.support_3pp.load_packages_from_path(
                  self._checkout_path.joinpath('src', *package_path.split('/')),
                  glob_pattern=pattern,
                  check_dup=False))

    _, unsupported = self.m.support_3pp.ensure_uploaded(
        # Note that when empty, all known packages will be built.
        packages=cipd_pkg_names_to_build,
        platform=self._platform,
        force_build=self.m.tryserver.is_tryserver or self._force_build,
    )

    if unsupported:
      step_name = 'Unsupported packages'
      step_result = self.m.step(step_name, cmd=None)
      step_result.presentation.step_text = '\n'.join(unsupported)

    if is_report_bcid:
      self.m.bcid_reporter.report_stage("upload-complete")
