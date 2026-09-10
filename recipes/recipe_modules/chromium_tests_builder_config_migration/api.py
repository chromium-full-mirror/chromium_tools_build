# Copyright 2022 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import abc
import collections
from collections.abc import Callable, Iterable, Mapping
import contextlib
import re

import attr

from recipe_engine import recipe_api

from RECIPE_MODULES.build import chromium_tests_builder_config as ctbc
from RECIPE_MODULES.build import proto_validation
from RECIPE_MODULES.build import chromium_types

from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb
from PB.recipe_engine import result as result_pb

from PB.recipe_modules.build.chromium_tests_builder_config_migration import (
  properties as properties_pb,
)

_VALIDATORS = proto_validation.Registry()


@_VALIDATORS.register(properties_pb.InputProperties)
def _validate_properties(message, ctx):
  operation = message.WhichOneof('operation')
  if operation:
    ctx.validate_field(message, operation)
  else:
    ctx.error('no operation is set')


@_VALIDATORS.register(properties_pb.GroupingsOperation)
def _validate_groupings_operation(message, ctx):
  ctx.validate_field(message, 'output_path')
  ctx.validate_repeated_field(message, 'builder_group_filters')


@_VALIDATORS.register(properties_pb.GroupingsOperation.BuilderGroupFilter)
def _validate_builder_group_filter(message, ctx):
  ctx.validate_field(message, 'builder_group_regex')


@_VALIDATORS.register(properties_pb.MigrationOperation)
def _validate_migration_operation(message, ctx):
  ctx.validate_repeated_field(message, 'builders_to_migrate')
  ctx.validate_field(message, 'output_path')


@_VALIDATORS.register(properties_pb.MigrationOperation.BuilderGroupAndName)
def _validate_builder_group_and_name(message, ctx):
  ctx.validate_field(message, 'builder_group')
  ctx.validate_field(message, 'builder')


class _OutputArgumentsFactory(abc.ABC):
  """Factory for outputting kwargs of a function call."""

  @abc.abstractmethod
  def set_raw_arg(self, name: str, value: str) -> None:
    """Set an argument to a raw value.

    x.set_raw_arg('foo', 'bar')
    results in
    foo = bar,
    """
    raise NotImplementedError()  # pragma: no cover

  def set_string_arg(self, name: str, value: str) -> None:
    """Set an argument to a string.

    x.set_string_arg('foo', 'bar')
    results in
    foo = "bar",
    """
    self.set_raw_arg(name, f'"{value}"')

  @abc.abstractmethod
  def set_raw_list_arg(self, name: str, args: Iterable[str]) -> None:
    """Set an argument to a list of raw values.

    x.set_raw_list_arg('foo', ['bar', 'baz'])
    results in
    foo = [
        bar,
        baz,
    ],
    """
    raise NotImplementedError()  # pragma: no cover

  def set_string_list_arg(self, name: str, values: Iterable[str]) -> None:
    """Set an argument to a list of strings.

    x.set_raw_list_arg('foo', ['bar', 'baz'])
    results in
    foo = [
        "bar",
        "baz",
    ],
    """
    self.set_raw_list_arg(name, (f'"{v}"' for v in values))

  @abc.abstractmethod
  def start_call_arg(
    self,
    name: str,
    expression: str,
  ) -> 'contextlib.AbstractContextManager[_OutputArgumentsFactory]':
    """Set an argument to a function call.

    The returned context manager takes care of opening and closing the
    function call. The context object associated with the context
    manager can be used to set the arguments of the function call.

    with x.create_struct('foo', 'bar') as y:
      y.set_raw_arg('baz', 1)
    results in
    foo = bar(
        baz = 1,
    ),
    """
    raise NotImplementedError()  # pragma: no cover


class _OutputFactory(abc.ABC):
  """Factory for creating the migration output."""

  @abc.abstractmethod
  def edit_builder(
    self,
    builder_id: chromium_types.BuilderId,
  ) -> contextlib.AbstractContextManager[_OutputArgumentsFactory]:
    """Set arguments for a builder.

    The returned context manager takes care of associating arguments
    with the corresponding builder. The context object associated with
    the context manager can be used to set arguments in the builder
    definition.

    with x.edit_builder('foo', 'bar') as y:
      y.set_raw_arg('baz', 1)
    results in
    foo = bar(
        baz = 1,
    ),
    """
    raise NotImplementedError()  # pragma: no cover

  @abc.abstractmethod
  def write_output(self, file_api, step_name: str, output_path: str) -> None:
    """Write the final output to a file.

    Args:
      file_api: The file api.
      step_name: The name of the step for writing out the file.
      output_path: The path of the file to write the output to.
    """
    raise NotImplementedError()  # pragma: no cover


class _TextArgumentsFactory(_OutputArgumentsFactory):
  """An output arguments factory for text output."""

  INDENT = ' ' * 4

  def __init__(self, indent):
    super().__init__()
    self._lines = []
    self._indent = indent

  def set_raw_arg(self, name: str, value: str) -> None:
    self._lines.append(f'{self._indent}{name} = {value},')

  def set_raw_list_arg(self, name: str, args: Iterable[str]) -> None:
    self._lines.append(f'{self._indent}{name} = [')
    indent = self._indent + self.INDENT
    for a in args:
      self._lines.append(f'{indent}{a},')
    self._lines.append(f'{self._indent}],')

  @contextlib.contextmanager
  def start_call_arg(
    self,
    name: str,
    expression: str,
  ) -> contextlib.AbstractContextManager[_OutputArgumentsFactory]:
    args_factory = _TextArgumentsFactory(self._indent + self.INDENT)
    yield args_factory

    self._lines.append(f'{self._indent}{name} = {expression}(')
    self._lines.extend(args_factory._lines)
    self._lines.append(f'{self._indent}),')


class _TextFactory(_OutputFactory):
  """An output factory for text output.

  The resulting file will have text output for each builder to be
  migrated. The format of the output will be a line identifying the
  builder with builder_group:builder then indented lines containing the
  starlark snippets to be added to the builder's definition and then a
  blank line.

  For example:
  foo:foo-builder
      builder_spec = builder_config.builder_spec(
          gclient_config = builder_config.gclient_config(
              config = "chromium",
          ),
          chromium_config = builder_config.chromium_config(
              config = "chromium",
          ),
      ),

  tryserver.foo:foo-builder
      mirrors = [
          "ci/foo-builder",
      ],
  """

  def __init__(self):
    super().__init__()
    self._lines = []

  @contextlib.contextmanager
  def edit_builder(
    self,
    builder_id: chromium_types.BuilderId,
  ) -> contextlib.AbstractContextManager[_OutputArgumentsFactory]:
    args_factory = _TextArgumentsFactory(_TextArgumentsFactory.INDENT)
    yield args_factory

    self._lines.append(str(builder_id))
    self._lines.extend(args_factory._lines)
    self._lines.append('')

  def write_output(self, file_api, step_name: str, output_path: str) -> None:
    file_api.write_text(step_name, output_path, '\n'.join(self._lines))


class _JsonArgumentsFactory(_OutputArgumentsFactory):
  """An output arguments factory for json output."""

  def __init__(self):
    super().__init__()
    self._pieces = []

  def set_raw_arg(self, name: str, value: str) -> None:
    self._pieces.append(f'{name}={value},')

  def set_raw_list_arg(self, name: str, args: Iterable[str]) -> None:
    contents = ','.join(args)
    self.set_raw_arg(name, f'[{contents}]')

  @contextlib.contextmanager
  def start_call_arg(
    self,
    name: str,
    expression: str,
  ) -> 'contextlib.AbstractContextManager[_OutputArgumentsFactory]':
    args_factory = _JsonArgumentsFactory()
    yield args_factory

    self._pieces.append(f'{name}={expression}(')
    self._pieces.extend(args_factory._pieces)
    self._pieces.append('),')


class _JsonBuilderFactory(_OutputArgumentsFactory):
  """An output arguments factory for builder args for json output.

  Builder arguments are handled specially for json output since
  buildozer will take the name of the argument as one of the parameters
  to its commands rather than it being part of the value to be set, as
  is the case for arguments to any further-nested function calls.
  """

  def __init__(self):
    super().__init__()
    self._edits = {}

  def set_raw_arg(self, name: str, value: str) -> None:
    self._edits[name] = value

  def set_raw_list_arg(self, name: str, args: Iterable[str]) -> None:
    self.set_raw_arg(name, f'[{",".join(args)}]')

  @contextlib.contextmanager
  def start_call_arg(
    self,
    name: str,
    expression: str,
  ) -> contextlib.AbstractContextManager[_OutputArgumentsFactory]:
    args_factory = _JsonArgumentsFactory()
    yield args_factory

    args = ''.join(args_factory._pieces)
    self.set_raw_arg(name, f'{expression}({args})')


class _JsonFactory(_OutputFactory):
  """An output factory for json output.

  The resulting file will be json with a list of dictionaries. Each
  dictionary will have builder_group and builder keys with corresponding
  string values that identify the builder and an edit key with a dict
  value that has the arguments to be set for the builder.

  For example:
  [
    {
      "builder_group": "foo",
      "builder": "foo-builder",
      "edits": {
        "builder_spec": "builder_config.builder_spec(gclient_config=builder_config.gclient_config(config=\"chromium\",),chromium_config=builder_config.chromium_config(config=\"chromium\",),)",
      }
    },
    {
      "builder_group": "tryserver.foo",
      "builder": "foo-builder",
      "edits": {
        "mirrors": "[\"ci/foo-builder\",]"
      }
    }
  ]
  """

  def __init__(self):
    super().__init__()
    self._builders = []

  @contextlib.contextmanager
  def edit_builder(
    self,
    builder_id: chromium_types.BuilderId,
  ) -> contextlib.AbstractContextManager[_OutputArgumentsFactory]:
    builder_factory = _JsonBuilderFactory()
    yield builder_factory

    self._builders.append(
      {
        "builder_group": builder_id.group,
        "builder": builder_id.builder,
        "edits": builder_factory._edits,
      }
    )

  def write_output(self, file_api, step_name: str, output_path: str) -> None:
    file_api.write_json(step_name, output_path, self._builders, indent=2)


class _Grouping:
  def __init__(self):
    self.builder_ids = set()
    self.blockers = set()

  def merge(self, other):
    self.builder_ids.update(other.builder_ids)
    self.blockers.update(other.blockers)


def _failure(summary):
  return result_pb.RawResult(
    status=common_pb.INFRA_FAILURE, summary_markdown=summary
  )


class BlockerCategory(abc.ABC):
  @abc.abstractmethod
  def get_blocker(
    self,
    builder_id: chromium_types.BuilderId,
    builder_spec: ctbc.BuilderSpec,
  ) -> str | None:
    raise NotImplementedError()  # pragma: no cover


def _migrate_builder_spec(
  builder_spec: ctbc.BuilderSpec,
  builder_factory: _OutputArgumentsFactory,
) -> None:
  with builder_factory.start_call_arg(
    'builder_spec',
    'builder_config.builder_spec',
  ) as spec_fact:
    if builder_spec.execution_mode == ctbc.TEST:
      spec_fact.set_raw_arg(
        'execution_mode', 'builder_config.execution_mode.TEST'
      )

    with spec_fact.start_call_arg(
      'gclient_config',
      'builder_config.gclient_config',
    ) as gc_fact:
      gc_fact.set_string_arg('config', builder_spec.gclient_config)
      if configs := builder_spec.gclient_apply_config:
        gc_fact.set_string_list_arg('apply_configs', configs)

    with spec_fact.start_call_arg(
      'chromium_config',
      'builder_config.chromium_config',
    ) as cc_fact:
      cc_fact.set_string_arg('config', builder_spec.chromium_config)
      if configs := builder_spec.chromium_apply_config:
        cc_fact.set_string_list_arg('apply_configs', configs)
      for k, v in builder_spec.chromium_config_kwargs.items():
        if k == 'BUILD_CONFIG':
          cc_fact.set_raw_arg(
            'build_config', f'builder_config.build_config.{v.upper()}'
          )
        elif k == 'TARGET_ARCH':
          cc_fact.set_raw_arg(
            'target_arch', f'builder_config.target_arch.{v.upper()}'
          )
        elif k == 'TARGET_BITS':
          cc_fact.set_raw_arg('target_bits', str(v))
        elif k == 'TARGET_PLATFORM':
          cc_fact.set_raw_arg(
            'target_platform', f'builder_config.target_platform.{v.upper()}'
          )
        elif k in ('TARGET_CROS_BOARDS', 'CROS_BOARDS_WITH_QEMU_IMAGES'):
          cc_fact.set_string_list_arg(k.lower(), v.split(':'))

    if config := builder_spec.android_config:
      with spec_fact.start_call_arg(
        'android_config',
        'builder_config.android_config',
      ) as ac_fact:
        ac_fact.set_string_arg('config', config)
        if configs := builder_spec.android_apply_config:
          ac_fact.set_string_list_arg('apply_configs', configs)

    if android_version_file := builder_spec.android_version:
      spec_fact.set_string_arg('android_version_file', android_version_file)

    if builder_spec.clobber:
      spec_fact.set_raw_arg('clobber', 'True')

    if builder_spec.serialize_tests:
      spec_fact.set_raw_arg('run_tests_serially', 'True')

    if builder_spec.perf_isolate_upload:
      spec_fact.set_raw_arg('perf_isolate_upload', 'True')

    if builder_spec.expose_trigger_properties:
      spec_fact.set_raw_arg('expose_trigger_properties', 'True')

    if skylab_gs_bucket := builder_spec.skylab_gs_bucket:
      with spec_fact.start_call_arg(
        'skylab_upload_location',
        'builder_config.skylab_upload_location',
      ) as sul_fact:
        sul_fact.set_string_arg('gs_bucket', skylab_gs_bucket)
        if skylab_gs_extra := builder_spec.skylab_gs_extra:
          sul_fact.set_string_arg('gs_extra', skylab_gs_extra)

    if builder_spec.cf_archive_build:
      with spec_fact.start_call_arg(
        'clusterfuzz_archive',
        'builder_config.clusterfuzz_archive',
      ) as ca_fact:
        ca_fact.set_string_arg('gs_bucket', builder_spec.cf_gs_bucket)
        if gs_acl := builder_spec.cf_gs_acl:
          ca_fact.set_string_arg('gs_acl', gs_acl)
        ca_fact.set_string_arg(
          'archive_name_prefix', builder_spec.cf_archive_name
        )
        if archive_subdir := builder_spec.cf_archive_subdir_suffix:
          ca_fact.set_string_arg('archive_subdir', archive_subdir)


_DEFAULT_TRY_SPEC = ctbc.TrySpec.create_for_single_mirror(
  'unused-group', 'unused-builder'
)
_SETTINGS_ATTRS = tuple(
  a.name
  for a in attr.fields(ctbc.TrySpec)
  # There is no default for mirrors, so we don't compare against that.
  if a.name not in ('mirrors')
)


def _migrate_try_spec(
  builder_id: chromium_types.BuilderId,
  try_spec: ctbc.TrySpec,
  builder_factory: _OutputArgumentsFactory,
) -> None:
  mirrors = []
  for m in try_spec.mirrors:
    if m.builder_id != builder_id and m.builder_id not in mirrors:
      mirrors.append(m.builder_id)
    if m.tester_id and m.tester_id != builder_id and m.tester_id not in mirrors:
      mirrors.append(m.tester_id)

  if mirrors:
    builder_factory.set_string_list_arg(
      'mirrors', (f'ci/{m.builder}' for m in mirrors)
    )

  if any(
    getattr(try_spec, a) != getattr(_DEFAULT_TRY_SPEC, a)
    for a in _SETTINGS_ATTRS
  ):
    with builder_factory.start_call_arg(
      'try_settings',
      'builder_config.try_settings',
    ) as ts_fact:
      if try_spec.include_all_triggered_testers:
        ts_fact.set_raw_arg('include_all_triggered_testers', 'True')

      if try_spec.is_compile_only:
        ts_fact.set_raw_arg('is_compile_only', 'True')

      if analyze_names := try_spec.analyze_names:
        ts_fact.set_string_list_arg('analyze_names', analyze_names)

      if not try_spec.retry_failed_shards:
        ts_fact.set_raw_arg('retry_failed_shards', 'False')

      if not try_spec.retry_without_patch:
        ts_fact.set_raw_arg('retry_without_patch', 'False')

      if (rts_condition := try_spec.regression_test_selection) != ctbc.NEVER:
        with ts_fact.start_call_arg(
          'rts_config',
          'builder_config.rts_config',
        ) as rc_fact:
          rc_fact.set_raw_arg(
            'condition', f'builder_config.rts_condition.{rts_condition.upper()}'
          )


class ChromiumTestsBuilderConfigMigrationApi(recipe_api.RecipeApi):
  def __call__(
    self,
    properties: properties_pb.InputProperties,
    builder_db: ctbc.BuilderDatabase,
    try_db: ctbc.TryDatabase,
    *,
    additional_blocker_categories: Iterable[BlockerCategory] | None = None,
  ) -> result_pb.RawResult | None:
    errors = _VALIDATORS.validate(properties)
    if errors:
      summary = [
        'The following errors were found with the input properties:',
        '',
      ]
      summary.extend(errors)
      return result_pb.RawResult(
        status=common_pb.INFRA_FAILURE, summary_markdown='\n'.join(summary)
      )

    handlers_by_operation = {
      'groupings_operation': self._groupings_operation,
      'migration_operation': self._migration_operation,
    }
    operation = properties.WhichOneof('operation')
    handler = handlers_by_operation[operation]

    blocker_categories = additional_blocker_categories or []

    return handler(
      getattr(properties, operation), builder_db, try_db, blocker_categories
    )

  def _groupings_operation(
    self,
    groupings_operation: properties_pb.GroupingsOperation,
    builder_db: ctbc.BuilderDatabase,
    try_db: ctbc.TryDatabase,
    blocker_categories: Iterable[BlockerCategory],
  ) -> None:
    builder_group_filters = []
    for f in reversed(groupings_operation.builder_group_filters):
      regex = re.compile('^{}$'.format(f.builder_group_regex))
      include = not f.exclude
      builder_group_filters.append((regex, include))

    def builder_filter(builder_id):
      builder_group = builder_id.group
      for regex, include in builder_group_filters:
        if regex.match(builder_group):
          return include
      return False

    groupings_by_builder_id = self._compute_groupings(
      builder_db, try_db, blocker_categories, builder_filter=builder_filter
    )

    output_path = self.m.path.abspath(groupings_operation.output_path)

    json = {}
    for b, grouping in groupings_by_builder_id.items():
      grouping_json = {
        'builders': [str(b2) for b2 in sorted(grouping.builder_ids)],
      }
      if grouping.blockers:
        grouping_json['blockers'] = sorted(grouping.blockers)
      json[str(b)] = grouping_json
    output = self.m.json.dumps(json, indent=2, separators=(',', ': '))

    # Include log during the tests so that the expectation file is easier to
    # review
    self.m.file.write_text(
      'groupings', output_path, output, include_log=self._test_data.enabled
    )

  def _migration_operation(
    self,
    migration_operation: properties_pb.MigrationOperation,
    builder_db: ctbc.BuilderDatabase,
    try_db: ctbc.TryDatabase,
    blocker_categories: Iterable[BlockerCategory],
  ) -> result_pb.RawResult | None:
    groupings_by_builder_id = self._compute_groupings(
      builder_db, try_db, blocker_categories
    )

    to_migrate = set()
    for b in migration_operation.builders_to_migrate:
      builder_id = chromium_types.BuilderId.create_for_group(
        b.builder_group, b.builder
      )
      grouping = groupings_by_builder_id.get(builder_id)
      if grouping is None:
        return _failure("unknown builder '{}'".format(builder_id))
      if grouping.blockers:
        return _failure(
          "The grouping for '{}'"
          " cannot be migrated for the following reasons:{}".format(
            builder_id,
            "".join("\n  {}".format(b) for b in sorted(grouping.blockers)),
          )
        )
      to_migrate.update(grouping.builder_ids)

    output_factory = (
      _JsonFactory() if migration_operation.json_output else _TextFactory()
    )

    for builder_id in sorted(to_migrate):
      with output_factory.edit_builder(builder_id) as builder_factory:
        builder_spec = builder_db.get(builder_id)
        if builder_spec is not None:
          _migrate_builder_spec(builder_spec, builder_factory)

        try_spec = try_db.get(builder_id)
        if try_spec is not None:
          _migrate_try_spec(builder_id, try_spec, builder_factory)

    output_path = self.m.path.abspath(migration_operation.output_path)
    output_factory.write_output(self.m.file, 'src-side snippets', output_path)

  def _compute_groupings(
    self,
    builder_db: ctbc.BuilderDatabase,
    try_db: ctbc.TryDatabase,
    blocker_categories: Iterable[BlockerCategory],
    *,
    builder_filter: Callable[[chromium_types.BuilderId], bool] = None,
  ) -> Mapping[chromium_types.BuilderId, _Grouping]:
    builder_filter = builder_filter or (lambda _: True)

    groupings_by_builder_id = collections.defaultdict(_Grouping)

    def check_included(related_ids, step_name):
      included_by_builder_id = {}
      for related_id in related_ids:
        included_by_builder_id[related_id] = builder_filter(related_id)

      included = set(included_by_builder_id.values())
      if len(included) == 1:
        return next(iter(included))

      self.m.step.empty(
        step_name,
        status=self.m.step.INFRA_FAILURE,
        log_name='mismatched_inclusion',
        log_text=[
          '{} ignored: {}'.format(builder_id, included)
          for builder_id, included in included_by_builder_id.items()
        ],
      )

    def update_groupings(builder_id, related_ids, blockers=None):
      grouping = groupings_by_builder_id[builder_id]
      grouping.builder_ids.add(builder_id)
      if blockers:
        grouping.blockers.update(blockers)
      for related_id in related_ids:
        related_grouping = groupings_by_builder_id[related_id]
        related_grouping.builder_ids.add(related_id)
        grouping.merge(related_grouping)
        for connected_id in related_grouping.builder_ids:
          groupings_by_builder_id[connected_id] = grouping

    for builder_id, child_ids in builder_db.builder_graph.items():
      included = check_included(
        [builder_id] + sorted(child_ids),
        'invalid children for {}'.format(builder_id),
      )
      if included:
        builder_spec = builder_db[builder_id]
        blockers = []
        for c in blocker_categories:
          if (blocker := c.get_blocker(builder_id, builder_spec)) is not None:
            blockers.append(blocker)
        update_groupings(builder_id, child_ids, blockers)

    for try_id, try_spec in try_db.items():
      mirrored_ids = []
      for mirror in try_spec.mirrors:
        mirrored_ids.append(mirror.builder_id)
        if mirror.tester_id:
          mirrored_ids.append(mirror.tester_id)
      included = check_included(
        [try_id] + mirrored_ids,
        'invalid mirroring configuration for {}'.format(try_id),
      )
      if included:
        update_groupings(try_id, mirrored_ids)

    return groupings_by_builder_id
