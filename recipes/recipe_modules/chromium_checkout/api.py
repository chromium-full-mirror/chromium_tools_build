# Copyright 2016 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import copy

from recipe_engine import recipe_api
from recipe_engine.config_types import Path

from RECIPE_MODULES.build.chromium_tests.resultdb import ResultDB
from RECIPE_MODULES.depot_tools import bot_update
from RECIPE_MODULES.depot_tools.gclient import api as gclient

from RECIPE_MODULES.depot_tools.tryserver.api import SubmodulePathsResult

from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb
from PB.go.chromium.org.luci.resultdb.proto.v1 import common as common_rdb_pb
from PB.go.chromium.org.luci.resultdb.proto.v1 import (
  invocation as invocation_pb,
)


class ChromiumCheckoutApi(recipe_api.RecipeApi):
  def __init__(self, input_properties, *args, **kwargs):
    super().__init__(*args, **kwargs)
    # We track if the default checkout dir was accessed so that we can detect
    # usage that would most likely be a mistake
    self._default_checkout_dir_accessed = False
    self._checkout_dir = None
    self._source_dir = None
    self._timeout = input_properties.timeout

  @property
  def timeout(self):
    return self._timeout

  @property
  def default_checkout_dir(self) -> Path:
    """The default location to perform a checkout."""
    self._default_checkout_dir_accessed = True
    return self.m.path.cache_dir / 'builder'

  _UNSET_USAGE_MESSAGE = (
    'call chromium_checkout.ensure_checkout, chromium_checkout.set_paths'
    ' or chromium_checkout.set_paths_from_update_result first'
  )

  @property
  def checkout_dir(self) -> Path:
    """The path to the directory where the checkout was performed.

    For a chromium checkout, this would be the parent of src.
    """
    if self._checkout_dir is None:
      raise ValueError(f'checkout_dir is not set, {self._UNSET_USAGE_MESSAGE}')
    return self._checkout_dir

  @property
  def source_dir(self) -> Path:
    """The path to the top level repo.

    For a chromium checkout, this would be the src directory.
    """
    if self._checkout_dir is None:
      raise ValueError(f'source_dir is not set, {self._UNSET_USAGE_MESSAGE}')
    return self._source_dir

  def _set_paths(self, checkout_dir: Path, source_dir: Path) -> None:
    assert self._checkout_dir is None and self._source_dir is None, (
      'paths have already been set'
    )
    assert checkout_dir in source_dir.parents, (
      'source_dir must be within checkout_dir'
    )
    # We expect that if someone accessed default_checkout_dir then checkout_dir
    # will end up set to default_checkout_dir. If that's not the case, we expect
    # it to be a mistake.
    if (
      self._default_checkout_dir_accessed
      and checkout_dir != self.default_checkout_dir
    ):
      raise ValueError(
        f'checkout_dir is being set to {checkout_dir} after'
        f' default_checkout_dir ({self.default_checkout_dir}) was accessed,'
        ' this indicates a likely mistake'
      )
    self._checkout_dir = checkout_dir
    self._source_dir = source_dir

  def set_paths(self, checkout_dir: Path, source_dir: str | Path) -> None:
    """Manually set the paths for the module.

    This is intended for uses cases where no checkout is performed. Eventually,
    all code should be switched to taking paths as arguments instead of relying
    on chromium_checkout.checkout_dir and chromium_checkout.source_dir, but this
    provided a migration path for removing api.path.checkout_dir until necessary
    changes to plumb paths through can be made.

    Args:
      checkout_dir: The path to where the directory that contains the "checked
        out" repos (for a chromium checkout, this will be the parent directory
        of src). After returning, api.chromium_checkout.checkout_dir will have
        this value.
      source_root: Either a str that gives the checkout_dir-relative path to the
        source directory or a Path which must be a subdirectory of checkout_dir.
        After returning, api.chromium_checkout.source_dir will have a Path
        pointing at this directory.
    """
    if isinstance(source_dir, str):
      source_dir = checkout_dir / source_dir
    self._set_paths(checkout_dir, source_dir)

  def set_paths_from_update_result(
    self, update_result: bot_update.Result
  ) -> None:
    """Manually set the paths for the module.

    This is intended for uses cases where the checkout is performed via
    bot_update.ensure_checkout. Eventually, all code should be switched to
    taking paths as arguments instead of relying on
    chromium_checkout.checkout_dir and chromium_checkout.source_dir, but this
    provided a migration path for removing api.path.checkout_dir until necessary
    changes to plumb paths through can be made.

    Args:
      update_result: The result from calling bot_update.ensure_checkout.
    """
    self._set_paths(update_result.checkout_dir, update_result.source_root.path)

  def get_files_affected_by_patch(
    self, relative_to=None, cwd=None, report_via_property=False
  ):
    """Returns list of POSIX paths of files affected by patch for "analyze".

    Paths are relative to `relative_to` which for analyze in Chromium should be
    'src/' (the default value). Paths always use '/' as a path delimiter, no
    matter the OS.
    """
    if not self.m.tryserver.gerrit_change:
      # There is no patch to begin with.
      return []
    patch_root = self.m.gclient.get_gerrit_patch_root()
    assert patch_root, (
      'local path is not configured for %s'
      % self.m.tryserver.gerrit_change_repo_url
    )
    cwd = cwd or self.checkout_dir / patch_root
    with self.m.context(cwd=cwd):
      files = self.m.tryserver.get_files_affected_by_patch(
        patch_root,
        report_files_via_property=(
          'affected_files' if report_via_property else None
        ),
      )
    return self.format_affected_file_paths(files, relative_to=relative_to)

  def get_files_affected_by_patch_with_submodules(
    self,
    relative_to=None,
    cwd=None,
    report_via_property=False,
    step_name_prefix='',
  ):
    """Returns SubmodulePathsResult containing POSIX paths of affected files and submodule metadata."""
    if not self.m.tryserver.gerrit_change:
      # There is no patch to begin with.
      return SubmodulePathsResult()
    patch_root = self.m.gclient.get_gerrit_patch_root()
    assert patch_root, (
      'local path is not configured for %s'
      % self.m.tryserver.gerrit_change_repo_url
    )
    cwd = cwd or self.checkout_dir / patch_root
    with self.m.context(cwd=cwd):
      submodule_paths_result = (
        self.m.tryserver.get_files_affected_by_patch_with_submodules(
          patch_root,
          report_files_via_property=(
            'affected_files' if report_via_property else None
          ),
          step_name_prefix=step_name_prefix,
        )
      )
    formatted_files = self.format_affected_file_paths(
      list(submodule_paths_result.affected_files), relative_to=relative_to
    )
    return SubmodulePathsResult(
      affected_files=formatted_files,
      unchecked_out_submodules=(
        submodule_paths_result.unchecked_out_submodules
      ),
      deleted_submodules=submodule_paths_result.deleted_submodules,
      new_submodules=submodule_paths_result.new_submodules,
      nested_submodules=submodule_paths_result.nested_submodules,
      unresolvable_submodules=(submodule_paths_result.unresolvable_submodules),
    )

  def format_affected_file_paths(self, files, relative_to=None):
    if relative_to is None:
      relative_to = 'src/'
    for i, path in enumerate(files):
      path = str(path)
      files[i] = self.m.path.relpath(path, relative_to)
      # self.m.path.relpath uses '\' if the machine running the recipe is a
      # windows machine. The results of this function are expected to have a '/'
      # delimiter no matter the OS. Fix this here.
      files[i] = files[i].replace('\\', '/')
    return files

  def ensure_checkout(
    self,
    *,
    timeout: int | None = None,
    **kwargs,
  ) -> bot_update.Result:
    """Wrapper for bot_update.ensure_checkout with chromium-specific additions.

    in contrast to bot_update.ensure_checkout, if api.context.cwd is None, the
    checkout will be performed in api.chromium_checkout.default_checkout_dir
    instead of api.path.start_dir.

    Args:
      timeout: Timeout in seconds for bot_update.ensure_checkout. If the timeout
        value is set on this module's properties, this will be ignored.
      **kwargs: Keyword arguments to forward on to bot_update.ensure_checkout.
        The following arguments have overridden defaults: * no_fetch_tags: True
    """
    kwargs.setdefault('no_fetch_tags', True)

    # Pass set_output_commit=False to bot_update to override default behavior.
    set_output_commit = kwargs.get('set_output_commit', False)
    kwargs['set_output_commit'] = False

    if self.m.tryserver.is_tryserver and self.m.tryserver.gerrit_change:
      footers = self.m.tryserver.get_footers() or {}
      cq_no_rebase = footers.get('Cq-No-Rebase', [])
      if any(v.strip().lower() == 'true' for v in cq_no_rebase):
        if self.m.cv.active and self.m.cv.run_mode != self.m.cv.DRY_RUN:
          raise self.m.step.StepFailure(
            'The Cq-No-Rebase footer is only supported for CQ dry runs, but '
            f'this build is part of a {self.m.cv.run_mode} run.'
          )
        if self.m.cv.active:
          self.m.cv.allow_reuse_for(self.m.cv.DRY_RUN)
        kwargs.setdefault('gerrit_no_rebase_patch_ref', True)

    timeout = int(self.timeout) if self.timeout else timeout

    if self.m.siso.enabled:
      self.m.siso.enable_download_remoteexec_cfg_hook()

    gclient_config = self.m.gclient.c
    with self.m.chromium_bootstrap.update_gclient_config(
      gclient_config
    ) as callback:
      self._report_gclient_config(gclient_config)

      with self.m.context(cwd=self.m.context.cwd or self.default_checkout_dir):
        # The step_name could be duplicated in a build. This is very unlikely
        # and in this worst case scenario the last step would simply overwrite
        # earlier ones.
        step_name = self.m.bot_update.step_name(
          kwargs.get('patch', True), kwargs.get('suffix', None)
        )

        step_tag = self.m.repro_instructions.tag_for_step(step_name)
        update_result = None
        try:
          update_result = self.m.bot_update.ensure_checkout(
            gclient_config=gclient_config,
            timeout=timeout,
            step_tags={'resultdb.instruction.id': step_tag},
            **kwargs,
          )
        finally:
          revision = (
            update_result.manifest.get('src', {}).get('revision', '')
            if update_result
            else ''
          )
          instruction = self._get_repro_instruction(gclient_config, revision)
          self.m.repro_instructions.create_step_instruction(
            step_tag,
            f'{step_name} instructions',
            remote_content=instruction,
            local_content=instruction,
          )
          self.m.repro_instructions.update_invocation_instructions()

      if set_output_commit and (
        out_commit := self._get_out_commit(update_result)
      ):
        self.m.buildbucket.set_output_gitiles_commit(out_commit)

      # TODO(crbug.com/395081730): Remove this prop plumbing.
      self.m.chromium.set_build_properties(update_result.properties)

      callback(update_result.manifest)

    if self._checkout_dir is None and self._source_dir is None:
      self.set_paths_from_update_result(update_result)
    else:
      # Allow checking out multiple times provided that the checkout is
      # performed in the same location and checks out the same repo
      checkout_dir = update_result.checkout_dir
      source_dir = update_result.source_root.path
      assert (
        self._checkout_dir == checkout_dir and self._source_dir == source_dir
      ), (
        'checkout performed with different paths, previously'
        f' checkout_dir={self._checkout_dir}, source_dir={self._source_dir},'
        ' attempting to set'
        f' checkout_dir={checkout_dir}, source_dir={source_dir}'
      )

    self.update_rdb_source_spec_invocation(
      gitiles_commit=update_result.out_commit
    )

    return update_result

  def _get_repro_instruction(self, gclient_config, src_revision) -> str:
    gclient_config = copy.deepcopy(gclient_config)
    # The instance is based on the builder bucket users should not match
    # the builder
    for solution in gclient_config.solutions:
      if 'rbe_instance' in solution.custom_vars:
        solution.custom_vars.pop('rbe_instance')

    # .gclient is python not json, this leads to problems like booleans becoming
    # true rather than True so it cannot be directly copy/pasted with as_jsonish
    json_python_replacements = {
      'true': 'True',
      'false': 'False',
      'null': 'None',
    }

    def replace_json(line):
      if ':' not in line:
        return line
      key, _, value = line.partition(':')
      stripped = value.strip(' ,')
      if stripped in json_python_replacements:
        value = value.replace(stripped, json_python_replacements[stripped])

      return f'{key}:{value}'

    filtered_config = {}
    jsonish_config = gclient_config.as_jsonish(include_hidden=True)
    for key in ('solutions', 'target_cpu', 'target_os'):
      filtered_config[key] = '\n'.join(
        replace_json(line)
        for line in self.m.json.dumps(jsonish_config[key], indent=2).split('\n')
      )

    filtered_config = '\n'.join(
      [f'{key} = {value}' for key, value in filtered_config.items()]
    )

    lines = []
    lines.append(
      'See the '
      '[docs](https://chromium.googlesource.com/chromium/src/+/HEAD/docs/get_the_code.md) '
      'for more platform specific update instructions.'
    )

    gclient_file_details = (
      '<details><summary>`.gclient` file for this update</summary>'
      f'<pre><code>{filtered_config}</code></pre></details>'
    )
    lines.append(gclient_file_details.replace(' ', '&nbsp;'))

    if src_revision:
      lines.append('To test at the same revision as this builder run:')
      lines.append(
        f'```git fetch; git checkout {src_revision}; gclient sync```'
      )
      lines.append('')

    lines.append('To test at the latest revision run:')
    lines.append('```git pull; gclient sync```')

    lines.append('')
    lines.append(
      '*This will run both bot update and runhook steps. To '
      'separate these instead run `gclient sync --nohooks` and '
      '`gclient runhooks` separately*'
    )
    return '<br/>'.join(lines)

  def _report_gclient_config(self, gclient_config):
    # We may need to update revision values to replace revision resolvers with
    # something that is json serializable
    gclient_config = copy.deepcopy(gclient_config)

    def _replace_revision_resolver(revision):
      if isinstance(revision, gclient.RevisionResolver):
        return "*{}*".format(type(revision).__name__)
      return revision

    gclient_config.revisions = {
      k: _replace_revision_resolver(v)
      for k, v in gclient_config.revisions.items()
    }

    for s in gclient_config.solutions:
      s.revision = _replace_revision_resolver(s.revision)

    step = self.m.step('gclient config', [])
    step.presentation.logs['config'] = self.m.json.dumps(
      gclient_config.as_jsonish(include_hidden=True), indent=2
    ).split('\n')

  def _get_out_commit(
    self, update_result: bot_update.Result
  ) -> common_pb.GitilesCommit | None:
    # If input commit ref is a tag, use it as output to display more
    # user-friendly information on Buildbucket UI.
    in_commit = self.m.buildbucket.gitiles_commit
    return (
      in_commit
      if in_commit and in_commit.ref.startswith('refs/tags/')
      else update_result.out_commit
    )

  def update_rdb_source_spec_invocation(self, gitiles_commit):
    """Update the rdb invocation to include the SourceSpec being used.

    Args:
      gitiles_commit: (buildbucket.GitilesCommit) gitiles commit position that
        has been checked out.
    """
    if not self.m.resultdb.enabled:
      return None

    with self.m.step.nest('set rdb sources'):
      if not gitiles_commit or not all(
        [
          gitiles_commit.host,
          gitiles_commit.project,
          gitiles_commit.id,
          gitiles_commit.ref,
          gitiles_commit.position,
        ]
      ):
        step_result = self.m.step.empty('missing gitiles commit info')
        # TODO(sshrimp): Remove excess debug info. The gitiles_commit might not
        # be set if it's not the primary checkout, however,
        # self.m.buildbucket.build.output will still contain an empty object
        # set this  property so we can check for all edge cases
        step_result.presentation.properties['failed_rdb_invocation_update'] = (
          True
        )
        step_result.presentation.logs['gitiles_commit'] = str(gitiles_commit)
        step_result.presentation.logs['gerrit_changes'] = str(
          self.m.buildbucket.build.input.gerrit_changes
        )
        return

      baseline_id = ResultDB.generate_baseline_id(
        self.m.buildbucket.build.builder.bucket, self.m.buildbucket.builder_name
      )
      self.m.resultdb.update_invocation(
        step_name='update invocation',
        is_source_spec_final=True,
        source_spec=invocation_pb.SourceSpec(
          sources=common_rdb_pb.Sources(
            gitiles_commit=common_rdb_pb.GitilesCommit(
              host=gitiles_commit.host,
              project=gitiles_commit.project,
              commit_hash=gitiles_commit.id,
              ref=gitiles_commit.ref,
              position=gitiles_commit.position,
            ),
            changelists=[
              common_rdb_pb.GerritChange(
                host=change.host,
                project=change.project,
                change=change.change,
                patchset=change.patchset,
              )
              for change in self.m.buildbucket.build.input.gerrit_changes
            ],
          )
        ),
        baseline_id=baseline_id,
      )
