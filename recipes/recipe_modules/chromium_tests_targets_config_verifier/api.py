# Copyright 2025 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import collections.abc
import difflib
import re

import attr
from google.protobuf import json_format

from recipe_engine import config_types, engine_types, recipe_api

from RECIPE_MODULES.build import chromium_tests_builder_config as ctbc
from RECIPE_MODULES.build.chromium_tests import (generators, steps,
                                                 targets_config as
                                                 targets_config_module)

from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb
from PB.recipe_engine import result as result_pb
from PB.recipe_modules.build.chromium_tests_builder_config import (
    properties as ctbc_properties_pb)

_CTBC_PROPERTY = '$build/chromium_tests_builder_config'


def _targets_spec_file_re(builder_config_directory: str) -> re.Pattern:
  """Get a regular expression that will match a targets spec file path.

  Returns:
    A pattern object that will match repo-root-relative paths that refer
    to targets spec files. The pattern captures the repo-root-relative
    path to the directory that contains configs for a single builder
    (not just the targets spec files).
  """
  return re.compile(f'^({re.escape(builder_config_directory)}/([^/]+)/[^/]+)'
                    r'/targets/[^/]+\.json')


def _result(
    status: common_pb.Status,
    header: str,
    elements: collections.abc.Iterable[str],
) -> result_pb.RawResult:
  summary = [header, '']
  summary.extend('* {}'.format(e) for e in elements)
  return result_pb.RawResult(status=status, summary_markdown='\n'.join(summary))


class ChromiumTestsTargetsConfigVerifierApi(recipe_api.RecipeApi):

  def verify_target_configs(
      self,
      gclient_config_name: str,
      builder_config_directory: str,
      precommit_buckets: collections.abc.Collection[str],
  ) -> result_pb.RawResult | None:
    self.m.gclient.set_config(gclient_config_name)

    checkout_root = self.m.path.cache_dir / 'builder'
    with self.m.context(cwd=checkout_root):
      update_result = self.m.bot_update.ensure_checkout(patch=True)

    repo_path = update_result.source_root.path

    with self.m.step.nest('determine affected targets spec files'):
      with self.m.context(cwd=repo_path):
        affected_files = set(self.m.tryserver.get_files_affected_by_patch(''))

    fake_precommit_details = generators.PrecommitDetails()
    precommit_details_by_builder_dir = {}

    targets_spec_file_re = _targets_spec_file_re(builder_config_directory)
    for f in affected_files:
      if match := targets_spec_file_re.match(f):
        builder_dir = match.group(1)
        properties_file = f'{builder_dir}/properties.json'
        # The builder is either being deleted or switched to not being
        # bootstrapped. The latter case should be rare and wouldn't be easy to
        # distinguish, so just assume a deletion and don't try to verify the
        # builder.
        if (properties_file in affected_files and
            not self.m.path.exists(repo_path / properties_file)):
          continue
        bucket = match.group(2)
        precommit_details_by_builder_dir[builder_dir] = (
            fake_precommit_details if bucket in precommit_buckets else None)

    if not precommit_details_by_builder_dir:
      self.m.step.empty('all affected builders are being deleted')
      return None

    starlark_config_by_builder_dir = {}
    with self.m.step.nest('get patched targets configs'):
      for builder_dir, precommit_details in (
          precommit_details_by_builder_dir.items()):
        starlark_config = self._get_starlark_config(
            builder_dir,
            repo_path,
            checkout_root,
            precommit_details,
        )
        if starlark_config:
          starlark_config_by_builder_dir[builder_dir] = starlark_config

    if not starlark_config_by_builder_dir:
      return None

    with self.m.context(cwd=checkout_root):
      self.m.bot_update.deapply_patch(update_result)

    failures = []
    for builder_dir, starlark_config in starlark_config_by_builder_dir.items():
      precommit_details = precommit_details_by_builder_dir[builder_dir]
      if not self._verify_target_configs(
          builder_dir,
          repo_path,
          precommit_details,
          starlark_config,
      ):
        failures.append(builder_dir)
    if failures:
      return _result(
          status=common_pb.FAILURE,
          elements=failures,
          header='verification failed for the following builder directories:')

  def _get_builder_config(
      self,
      builder_dir: str,
      repo_path: config_types.Path,
  ) -> ctbc.BuilderConfig | None:
    properties_json_path = repo_path.joinpath(builder_dir, 'properties.json')
    if not self.m.path.exists(properties_json_path):
      return None

    properties = self.m.file.read_json(
        'read properties file',
        properties_json_path,
        test_data={},
        include_log=True,
    )

    if _CTBC_PROPERTY not in properties:
      return None

    ctbc_properties = ctbc_properties_pb.InputProperties()
    json_format.ParseDict(properties[_CTBC_PROPERTY], ctbc_properties)
    return ctbc.proto.convert_builder_config(ctbc_properties.builder_config)

  def _get_targets_config(
      self,
      step_name: str,
      builder_config: ctbc.BuilderConfig,
      source_dir: config_types.Path,
      targets_spec_dir: config_types.Path,
      precommit_details: generators.PrecommitDetails | None,
  ) -> targets_config_module.TargetsConfig:
    with self.m.step.nest(step_name):
      return self.m.chromium_tests.create_targets_config(
          builder_config,
          got_revisions={},
          source_dir=source_dir,
          build_dir=None,
          targets_spec_dir=targets_spec_dir,
          precommit_details=precommit_details,
          scripts_compile_targets_fn=lambda source_dir, build_dir: {},
      )

  def _get_starlark_config(
      self,
      builder_dir: str,
      repo_path: config_types.Path,
      checkout_root: config_types.Path,
      precommit_details: generators.PrecommitDetails | None,
  ) -> targets_config_module.TargetsConfig | None:
    with self.m.step.nest(builder_dir) as presentation:
      builder_config = self._get_builder_config(builder_dir, repo_path)
      if not builder_config:
        skip_reason = (
            "builder doesn't have bootstrapped builder config, can't verify")
      elif not builder_config.targets_spec_directory:
        skip_reason = (f'targets_spec_directory is not set in {_CTBC_PROPERTY},'
                       ' nothing to verify')
      else:
        if builder_config.targets_spec_directory_relative_to_source_dir:
          targets_spec_dir = repo_path / builder_config.targets_spec_directory
        else:
          targets_spec_dir = checkout_root / builder_config.targets_spec_directory
        return self._get_targets_config(
            'get starlark targets config',
            builder_config,
            repo_path,
            targets_spec_dir,
            precommit_details,
        )

      presentation.step_text = '\n' + skip_reason
      return None

  def _verify_target_configs(
      self,
      builder_dir: str,
      repo_path: config_types.Path,
      precommit_details: generators.PrecommitDetails | None,
      starlark_config: targets_config_module.TargetsConfig,
  ) -> bool:
    with self.m.step.nest(f'verify {builder_dir}') as presentation:

      def success(message: str) -> bool:
        presentation.step_text = '\n' + message
        return True

      builder_config = self._get_builder_config(builder_dir, repo_path)
      if not builder_config:
        return success(
            "builder didn't have bootstrapped builder config without patch,"
            " can't verify")

      if builder_config.targets_spec_directory:
        return success("builder is already using tests in starlark")

      self.m.chromium.verify_config = False
      chromium_config = self.m.chromium.make_config(
          builder_config.chromium_config,
          **builder_config.chromium_config_kwargs)
      for c in builder_config.chromium_apply_config:
        self.m.chromium.apply_config(c, chromium_config)

      targets_spec_dir = repo_path / chromium_config.targets_spec_dir

      # If someone adds a builder with a new builder group and doesn't set tests
      # in the initial CL, then a subsequent CL that sets the tests would
      # encounter the pyl-generated targets spec file not existing without the
      # patch. So if any of the targets spec files don't exist, assume that the
      # builder wasn't running tests before and there's no need to verify
      # anything.
      spec_files_exist = [
          self.m.path.exists(targets_spec_dir / spec_file)
          for spec_file in builder_config.targets_spec_files.values()
      ]
      if not any(spec_files_exist):
        return success('none of the targets spec files for the builder exists,'
                       " assuming it hasn't been running tests")
      if not all(spec_files_exist):
        raise self.m.step.InfraFailure(
            'unexpected situation: not all of the targets spec files'
            f' exist without patch for {builder_dir}')

      pyl_config = self._get_targets_config(
          'get pyl targets config',
          builder_config,
          repo_path,
          targets_spec_dir,
          precommit_details,
      )

      # If the builder isn't building targets or running tests without the patch,
      # then just allow targets for the builder to be newly set
      if not pyl_config.compile_only_targets and not pyl_config.all_tests:
        return success("builder wasn't running any tests before")

      diff = self._compare_targets_configs(pyl_config, starlark_config)
      if not diff:
        return success('starlark config matches pyl config')

      presentation.logs['diff'] = diff
      presentation.status = self.m.step.FAILURE
      presentation.step_text = (
          "\nbuilder configs differ, see 'diff' log for details")
      return False

  def _compare_targets_configs(
      self,
      pyl_config: targets_config_module.TargetsConfig,
      starlark_config: targets_config_module.TargetsConfig,
  ) -> collections.abc.Sequence[str] | None:

    def default_json_conversion(obj: object) -> object:
      if attr.has(obj):
        # Don't recurse, otherwise other attrs types will get converted to dicts
        # and we won't be given the opportunity to make type-specific tweaks to
        # child objects.
        d = attr.asdict(obj, recurse=False)

        # We really only care about the targets since the same builder config is
        # passed to both TargetsConfigs, so just return those
        if isinstance(obj, targets_config_module.TargetsConfig):
          return {
              str(builder_id): targets
              for builder_id, targets in d['_targets_by_builder_id'].items()
          }

        if isinstance(obj, steps.AbstractTestSpec):
          d2 = {'_type': type(obj).__name__}
          d2.update(d)
          return d2

        return d

      # We don't care about the state of tests, just that they have equivalent
      # specs
      if isinstance(obj, steps.TestWrapper):
        return obj._wrapper_spec
      if isinstance(obj, steps.Test):
        return obj.spec

      if isinstance(obj, config_types.Path):
        return str(obj)

      if isinstance(obj, engine_types.FrozenDict):
        return engine_types.thaw(obj)

      raise TypeError(f'{obj!r} is not JSON serializable')  # pragma: no cover

    def convert_to_json(
        targets_config: targets_config_module.TargetsConfig,
    ) -> collections.abc.Sequence[str]:
      return self.m.json.dumps(
          targets_config,
          indent=2,
          sort_keys=False,
          default=default_json_conversion,
      ).splitlines()

    pyl_config_json = convert_to_json(pyl_config)
    starlark_config_json = convert_to_json(starlark_config)

    return list(
        difflib.unified_diff(
            pyl_config_json,
            starlark_config_json,
            fromfile='pyl targets config',
            tofile='starlark targets config',
            n=max(len(pyl_config_json), len(starlark_config_json)),
        ))
