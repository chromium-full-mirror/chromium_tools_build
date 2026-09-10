# Copyright 2022 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from collections.abc import Collection, Sequence, Set
import difflib

import attr
from google.protobuf import json_format

from recipe_engine import recipe_api
from recipe_engine.config_types import Path
from recipe_engine.engine_types import thaw

from RECIPE_MODULES.build import chromium_tests_builder_config as ctbc
from RECIPE_MODULES.build.chromium_types import BuilderId

from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb
from PB.recipe_engine import result as result_pb
from PB.recipe_modules.build.chromium_tests_builder_config import (
  properties as ctbc_properties_pb,
)

_CTBC_PROPERTY = '$build/chromium_tests_builder_config'


def _result(
  status: common_pb.Status,
  header: str,
  elements: Sequence[str],
) -> result_pb.RawResult:
  summary = [header, '']
  summary.extend(f'* {e}' for e in elements)
  return result_pb.RawResult(status=status, summary_markdown='\n'.join(summary))


class ChromiumTestsBuilderConfigVerifierApi(recipe_api.RecipeApi):
  def verify_builder_configs(
    self,
    repo_path: Path,
    properties_files_directory: str,
    dbs: Sequence[tuple[ctbc.BuilderDatabase, ctbc.TryDatabase | None]],
    try_buckets: Collection[str] = (),
  ) -> result_pb.RawResult:
    """Verify builder configs specified in properties files.

    For each affected properties file, if the
    $build/chromium_tests_builder_config property is set and has been
    changed, the resultant BuilderConfig will be compared against the
    BuilderConfig present in the databases, if any. If the
    BuilderConfigs do not compare equal, then a failing result will be
    returned.

    Args:
      * repo_path - The path to the repo containing the properties
        files.
      * properties_files_directory - The relative path within the repo
        to the directory containing the builder properties files. The
        relative path to the properties file from the builder config
        directory must be <bucket>/<builder>/properties.json.
      * dbs - The builder and try databases to look up the builder
        config in. BuilderConfig.lookup will be tried with successive
        pair until a BuilderConfig is returned which will be the
        BuilderConfig that the properties-based BuilderConfig will be
        compared against. If no BuilderConfig is found, then no
        verification is performed.
      * try_buckets - The names of buckets for which a lookup should
        occur in the try database. For all other buckets, the try
        database will not be used. This resolves the situation where a
        builder group is used for both CI and try builders. The bucket
        for each builder will be determined by the path to properties
        file, see properties_file_directory for more information.
    """
    assert self.m.tryserver.is_tryserver

    with self.m.step.nest('determine affected properties files'):
      with self.m.context(cwd=repo_path):
        affected_files = self.m.tryserver.get_files_affected_by_patch('')

      paths = self.m.file.glob_paths(
        'find builder properties files',
        repo_path,
        f'{properties_files_directory}/*/*/properties.json',
        include_hidden=True,
      )
      properties_files = set(self.m.path.relpath(p, repo_path) for p in paths)

      with self.m.context(cwd=repo_path / properties_files_directory):
        # Lists the files known to git at HEAD
        result = self.m.git(
          'ls-tree',
          '-r',
          'HEAD',
          '--name-only',
          stdout=self.m.raw_io.output_text(add_output_log=True),
        )
      files_at_head = set(
        f'{properties_files_directory}/{f}'
        for f in result.stdout.strip().splitlines()
      )

    futures = []
    for f in sorted(affected_files):
      if f not in properties_files:
        continue

      _, bucket, builder, _ = f.rsplit('/', 3)

      futures.append(
        self.m.futures.spawn_immediate(
          self._verify_builder_config,
          repo_path,
          files_at_head,
          f,
          bucket,
          builder,
          dbs,
          try_buckets,
          __name=f,
        )
      )

    self.m.futures.wait(futures)

    failures = [f.name for f in futures if not f.result()]
    if failures:
      return _result(
        status=common_pb.FAILURE,
        elements=failures,
        header='Could not verify the following files:',
      )

  def _verify_builder_config(
    self,
    repo_path: Path,
    files_at_head: Set[str],
    f: str,
    bucket: str,
    builder: str,
    dbs: Sequence[tuple[ctbc.BuilderDatabase, ctbc.TryDatabase | None]],
    try_buckets: Collection[str],
  ) -> bool:
    with self.m.step.nest(f'verify {f}') as presentation:

      def success(message: str) -> bool:
        presentation.step_text = '\n' + message
        return True

      def failure(message: str) -> bool:
        presentation.status = self.m.step.FAILURE
        presentation.step_text = '\n' + message
        return False

      properties = self.m.file.read_json(
        'read file at CL', repo_path / f, test_data={}, include_log=True
      )
      if _CTBC_PROPERTY not in properties:
        return success(f'{_CTBC_PROPERTY} is not set, nothing to verify')

      if f in files_at_head:
        with self.m.context(cwd=repo_path):
          result = self.m.git(
            'cat-file',
            f'HEAD:{f}',
            '--textconv',
            name='read file at HEAD',
            stdout=self.m.raw_io.output_text(),
          )
        result.presentation.logs[f.rsplit('/', 1)[-1]] = result.stdout
        prev_properties = self.m.json.loads(result.stdout)

        builder_config_property = properties[_CTBC_PROPERTY]
        prev_builder_config_property = prev_properties.get(_CTBC_PROPERTY)
        if builder_config_property == prev_builder_config_property:
          return success(f'{_CTBC_PROPERTY} is unchanged, nothing to verify')

      if 'builder_group' not in properties:
        return failure("builder_group property is not set, can't verify")

      builder_id = BuilderId.create_for_group(
        properties['builder_group'], builder
      )

      for builder_db, try_db in dbs:
        try:
          recipe_config = ctbc.BuilderConfig.lookup(
            builder_id,
            builder_db,
            try_db,
            use_try_db=bucket in try_buckets,
            default_retry_failed_shards=bucket in try_buckets,
          )
          break
        except ctbc.BuilderConfigException:
          pass
      else:
        return success('no recipe config exists, nothing to verify')

      ctbc_properties = ctbc_properties_pb.InputProperties()
      json_format.ParseDict(properties[_CTBC_PROPERTY], ctbc_properties)
      src_side_config = ctbc.proto.convert_builder_config(
        ctbc_properties.builder_config
      )

      diff = self._compare_builder_configs(recipe_config, src_side_config)
      if diff:
        presentation.logs['diff'] = diff
        return failure("builder configs differ, see 'diff' log for details")

      return success('src-side config matches recipe config')

  def _compare_builder_configs(
    self,
    recipe_config: ctbc.BuilderConfig,
    src_side_config: ctbc.BuilderConfig,
  ) -> Sequence[str] | None:
    """Compare recipe and src-side configs for equivalence.

    There are some differences that need to be massaged between recipe
    configs and src-side configs:
    1. The builder DB will be the entire static DB for recipe configs
      whereas it will only contain the relevant entries for src-side
      configs
    2. include_all_triggered_testers is not part of the proto since it
      will already be reflected in the entries being added by the
      src-side generator, in both cases,
      builder_ids_in_scope_for_testing will already reflect whether or
      not it was set
    3. simulation_platform is not part of the proto since it's only used
      in recipe tests

    A comparison can be performed directly between BuilderConfig
    objects, but removing the irrelevant fields from the diff would
    involve repetition, so instead convert the configs to a
    representative json format and compare that.

    Args:
      recipe_config: The BuilderConfig obtained from the static DBs.
      src_side_config: The BuilderConfig obtained from the properties
        file.

    Returns:
      A list of strings with the differences between the representative
      json if they differ. Otherwise, None.
    """

    def default_json_conversion(obj: object) -> object:
      # BuilderId is used as keys in BuilderDatabase, but keys in json can't be
      # arbitrary objects, so convert it to strings to match how they'll be
      # presented for BuilderDatabase
      if isinstance(obj, BuilderId):
        return str(obj)

      if attr.has(obj):
        # Don't recurse, otherwise other attrs types will get converted to dicts
        # and we won't be given the opportunity to make type-specific tweaks to
        # child objects.
        d = attr.asdict(obj, recurse=False)

        # Logically, the DB is just a mapping of builder ID to spec; the
        # builders_by_group field is redundant with the _db field, so just
        # present it as a dict mapping ID to spec. Keys in json can't be
        # arbitrary objects (and the json module uses the default callback for
        # dict keys), so convert the keys to strings.
        if isinstance(obj, ctbc.BuilderDatabase):
          return {
            str(builder_id): builder_spec
            for builder_id, builder_spec in d['_db'].items()
          }

        if isinstance(obj, ctbc.BuilderConfig):
          # bb_builder_id_by_builder_id will only be populated by
          # src-side configs
          d.pop('_bb_builder_id_by_builder_id', None)
          # include_all_triggered_testers will already be reflected in
          # builder_ids_in_scope_for_testing and it doesn't appear in the proto,
          # so set it to False so that it doesn't impact comparison
          d.pop('include_all_triggered_testers', None)
          # Make sure unordered collections are sorted for comparison
          d['builder_ids'] = sorted(d['builder_ids'])
          d['mirroring_try_builders'] = sorted(d['mirroring_try_builders'])
          # Use the effective value for builder_ids_in_scope_for_testing rather
          # than the private field
          d['builder_ids_in_scope_for_testing'] = sorted(
            obj.builder_ids_in_scope_for_testing
          )
          del d['_builder_ids_in_scope_for_testing']
          return d

        if isinstance(obj, ctbc.BuilderSpec):
          # Simulation platform is only used for running recipe tests, it
          # doesn't appear in the proto
          d.pop('simulation_platform', None)
          # It doesn't make sense to set HOST_ values to anything other than
          # what the default values should be, setting them src-side isn't even
          # supported
          chromium_config_kwargs = thaw(d['chromium_config_kwargs'])
          d['chromium_config_kwargs'] = chromium_config_kwargs
          for k in list(chromium_config_kwargs):
            if k.startswith('HOST_'):
              chromium_config_kwargs.pop(k)
          return d

        return d  # pragma: no cover

      raise TypeError(f'{obj!r} is not JSON serializable')  # pragma: no cover

    def convert_to_json(builder_config: ctbc.BuilderConfig) -> Sequence[str]:
      return self.m.json.dumps(
        builder_config,
        indent=2,
        default=default_json_conversion,
      ).splitlines()

    def normalize_recipe_config(
      builder_config: ctbc.BuilderConfig,
    ) -> ctbc.BuilderConfig:
      builder_db = builder_config.builder_db
      builders_by_group = {}
      examined = set()
      to_examine = list(builder_config.builder_ids_in_scope_for_testing)
      while to_examine:
        i = to_examine.pop()
        if i in examined:
          continue
        examined.add(i)

        builder_spec = builder_db[i]
        # Recipe configs can omit the parent builder group, which is treated as
        # the same builder group as the associated builder
        if builder_spec.parent_buildername:
          if not builder_spec.parent_builder_group:
            builder_spec = attr.evolve(
              builder_spec, parent_builder_group=i.group
            )

          # For testers, the parent won't appear in
          # builder_ids_in_scope_for_testing, but it's still needed in the DB
          # for some operations
          parent_id = BuilderId.create_for_group(
            builder_spec.parent_builder_group, builder_spec.parent_buildername
          )
          to_examine.append(parent_id)
        builders_by_group.setdefault(i.group, {})[i.builder] = builder_spec

      return attr.evolve(
        builder_config,
        builder_db=ctbc.BuilderDatabase.create(builders_by_group),
      )

    recipe_config_json = convert_to_json(normalize_recipe_config(recipe_config))
    src_side_config_json = convert_to_json(src_side_config)

    return list(
      difflib.unified_diff(
        recipe_config_json,
        src_side_config_json,
        fromfile='recipe builder config',
        tofile='src-side builder config',
        n=max(len(recipe_config_json), len(src_side_config_json)),
      )
    )
