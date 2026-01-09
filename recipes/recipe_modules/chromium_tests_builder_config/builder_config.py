# Copyright 2016 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import attr
import inspect
import itertools
import traceback

from .builder_spec import BuilderSpec, COMPILE_AND_TEST, TEST
from .builder_db import BuilderDatabase
from .try_spec import TryDatabase, ALWAYS, NEVER

from RECIPE_MODULES.build.chromium_types import BuilderId
from RECIPE_MODULES.build.attr_utils import (attrib, attrs, cached_property,
                                             enum, mapping, sequence)

from PB.go.chromium.org.luci.buildbucket.proto import (builder_common as
                                                       builder_common_pb)


class BuilderConfigException(Exception):
  """Exception indicating an attempt to create an invalid BuilderConfig."""


class _BuilderSpecProperty:
  """A non-data descriptor that delegates attributes to BuilderSpecs.

  This descriptor must be attached to the BuilderConfig class. The value
  for the attribute will be read from each of the BuilderSpecs that are
  wrapped by the BuilderConfig instance and an exception will be raised
  if there is more than one value.

  A descriptor is being used instead of overriding __getattr__ because
  the presence of a __getattr__ method on the type results in errors in
  property methods being hidden because python will fall back to calling
  __getattr__ if a property raises an exception.
  """

  def __init__(self, a):
    self._attr = a

  def __get__(self, obj, objtype=None):
    del objtype
    if obj is None:
      return self  # pragma: no cover
    per_builder_values = {}
    for builder_id in obj.builder_ids:
      builder_spec = obj.builder_db[builder_id]
      value = getattr(builder_spec, self._attr)
      per_builder_values[builder_id] = value
    values = list(set(per_builder_values.values()))
    if len(values) != 1:
      message = ['Inconsistent value for {!r}:'.format(self._attr)]
      message.extend(
          '{!r}: {!r}'.format(k, v) for k, v in per_builder_values.items())
      raise ValueError('\n  '.join(message))
    return values[0]


def delegate_to_builder_spec(builder_spec_class):
  """A decorator to delegate attribute and property access to specs.

  The decorator can be applied to a BuilderConfig subclass and takes a
  BuilderSpec subclass. For each attr Attribute or descriptor on the
  class, if the attribute's name is not a python magic name (begins and
  ends with double-underscore), a non-data descriptor will be added to
  the BuilderConfig subclass that will delegate to the wrapped builder
  specs and ensure a consistent value between them.
  """
  assert issubclass(builder_spec_class, BuilderSpec)

  def delegate(cls):
    for a in attr.fields_dict(builder_spec_class):
      setattr(cls, a, _BuilderSpecProperty(a))
    for a in dir(builder_spec_class):
      if a.startswith('__') and a.endswith('__'):
        continue
      val = getattr(builder_spec_class, a)
      if inspect.ismethod(val):
        continue
      if hasattr(val, '__get__'):
        setattr(cls, a, _BuilderSpecProperty(a))
    return cls

  return delegate


@attrs()
class BuildbucketBuilderId:
  """An immutable representation of buildbucket BuilderID

  Proto messages are not hashable, so can't be frozen, this type
  provides the same information as the buildbucket proto type while
  being able to appear in immutable types.
  """

  project = attrib(str)
  bucket = attrib(str)
  builder = attrib(str)

  @classmethod
  def from_proto(cls, proto: builder_common_pb.BuilderID):
    return cls(
        project=proto.project,
        bucket=proto.bucket,
        builder=proto.builder,
    )

  def to_proto(self) -> builder_common_pb.BuilderID:
    return builder_common_pb.BuilderID(
        project=self.project,
        bucket=self.bucket,
        builder=self.builder,
    )


@delegate_to_builder_spec(BuilderSpec)
@attrs()
class BuilderConfig:
  """"Static" configuration for a builder.

  BuilderConfig provides access to information defined entirely in
  recipes; for a given recipe version BuilderConfig information will be
  the same for all builds of a given builder.

  BuilderConfig wraps multiple builder specs and provides the means for
  getting values in a manner that ensures they are compatible between
  all of the wrapped specs. BuilderConfig overrides attribute access so
  that attempting to access any attribute that is defined on the specs
  returns the value on the specs, raising an exception if the value is
  inconsistent between the specs. The builders that have their specs
  wrapped are returned by `builder_ids`.

  In addition to wrapping the BuilderSpecs for some builders, it can
  also record other builders as being in scope for testing (e.g. a
  tester triggered by a builder whose spec is wrapped). The builders
  that are in scope for testing will not have their specs wrapped, but
  will have their targets spec files included when accessing
  `targets_spec_files`.
  """

  builder_db = attrib(BuilderDatabase)

  builder_ids = attrib(sequence[BuilderId])
  _builder_ids_in_scope_for_testing = attrib(sequence[BuilderId])

  # A mapping of chromium BuilderId to BuildbucketBuilderId. This will
  # only be populated for src-side builder configs
  _bb_builder_id_by_builder_id = attrib(
      mapping[BuilderId, BuildbucketBuilderId], default={})

  # The try builders that mirror the builder that this BuilderConfig
  # wraps
  mirroring_try_builders = attrib(sequence[BuilderId], default=())

  # The path to the directory where the targets spec files are read from,
  # relative to the top-level repository (e.g.
  # infra/config/generated/builders/try/linux-rel/targets)
  #
  # This is used by builders that specify their tests in starlark
  targets_spec_directory = attrib(str, default=None)

  # TODO(gbeaty) The following fields are copied from TrySpec (with some
  # changed defaults), but if all builders are switched to using the
  # module properties (once available) then TrySpec could be removed.

  # Whether or not all testers triggered by builders in builder_ids should be
  # considered in scope for testing
  include_all_triggered_testers = attrib(bool, default=True)
  # Whether the try builder is compile only or not
  is_compile_only = attrib(bool, default=False)
  # Additional names to add when analyzing the change to determine affected
  # targets
  analyze_names = attrib(sequence[str], default=())
  # Additional relative path strings that are excluded when analyzing the
  # change to determine affected targets. The paths should be relative to
  # the root dir of the chromium/src checkout.
  additional_exclusions = attrib(sequence[str], default=())
  # Whether or not failed shards of tests should be retried
  retry_failed_shards = attrib(bool, default=True)
  # Whether or not infra failed shards of tests should be retried
  retry_invalid_shards = attrib(bool, default=False)
  # Whether or not failed test suites should be retried without patch
  retry_without_patch = attrib(bool, default=True)
  # Conditions where skipping a subset of tests is allowed
  regression_test_selection = attrib(enum([ALWAYS, NEVER]), default=NEVER)

  @classmethod
  def create(cls,
             builder_db,
             builder_ids,
             builder_ids_in_scope_for_testing=None,
             step_api=None,
             **kwargs):
    """Create a BuilderConfig instance.

    Args:
      * builder_db - The BuilderDatabase containing the builders
        identified by `builder_ids` and
        `builder_ids_in_scope_for_testing`.
      * builder_ids - The IDs of the builders to wrap the builder specs
        for.
      * builder_ids_in_scope_for_testing - The IDs of the builders to
        only collect targets and tests from. Any IDs present in
        `builder_ids` will not be present in the final value.
      * step_api - Optional step API. If provided, in the event that a
        BuilderConfigException would be raised, an infra failing step
        will be created with the details instead.
      * kwargs - Any additional arguments to initialize fields of the
        BuilderConfig.

    Returns:
      A BuilderConfig instance. The specs for the builders in
      `builder_ids` will be wrapped. The testers in
      `builder_ids_in_scope_for_testing` will be in scope for testing.
      If `include_all_triggered_testers` is true, then any testers
      triggered by the wrapped builders will be in scope for testing.

    Raises:
      * BuilderConfigException if there isn't configuration matching all
        of builders in mirrors and step_api is None.
      * InfraFailure if there isn't configuration matching all of
        builders in mirrors and step_api is not None.
    """
    builder_ids_in_scope_for_testing = builder_ids_in_scope_for_testing or []

    try:
      if not builder_ids:
        raise BuilderConfigException('No builder IDs specified')
      for b in itertools.chain(builder_ids, builder_ids_in_scope_for_testing):
        if not b.group in builder_db.builders_by_group:
          raise BuilderConfigException(
              "No configuration present for group '{}'".format(b.group))
        if not b in builder_db:
          raise BuilderConfigException(
              "No configuration present for builder '{}' in group '{}'".format(
                  b.builder, b.group))
    except BuilderConfigException as e:
      if step_api is not None:
        result = step_api(str(e), [])
        result.presentation.status = step_api.EXCEPTION
        result.presentation.logs['details'] = traceback.format_exc()
        step_api.raise_on_failure(result)
      raise

    return cls(
        builder_db,
        builder_ids=builder_ids,
        builder_ids_in_scope_for_testing=builder_ids_in_scope_for_testing,
        **kwargs)

  @classmethod
  def lookup(cls,
             builder_id,
             builder_db,
             try_db=None,
             use_try_db=True,
             step_api=None,
             default_retry_failed_shards=True):
    """Create a BuilderConfig by looking up a builder.

    Args:
      * builder_id - The ID of the builder to look up.
      * builder_db - The BuilderDatabase that the builder will be looked
        up in.
      * try_db - An optional TryDatabase that the builder will be looked
        up in.
      * step_api - Optional step API. If provided, in the event that a
        BuilderConfigException would be raised, an infra failing step
        will be created with the details instead.
      * default_retry_failed_shards - Specifies the default value for
        retry_failed_shards if not defined in TrySpec.

    Returns:
      A BuilderConfig instance for the associated builder. If try_db was
      provided and has an entry for the builder, the BuilderConfig will
      use the associated TrySpec, see the documentation for `create` for
      specific details. Otherwise, the BuilderConfig will wrap the
      builder spec for the provided builder ID and any triggered testers
      will be in scope for testing.

    Raises:
      * BuilderConfigException if there isn't configuration matching the
        indicated builder and step_api is None.
      * InfraFailure if there isn't configuration matching the indicated
        builder and step_api is not None.
    """
    assert isinstance(builder_db, BuilderDatabase), (
        'Expected BuilderDatabase for builder_db, got {}'.format(
            type(builder_db)))
    assert try_db is None or isinstance(try_db, TryDatabase), \
        'Expected TryDatabase for try_db, got {}'.format(type(try_db))

    kwargs = {}

    try_spec = None
    if try_db and use_try_db:
      try_spec = try_db.get(builder_id)

    if try_spec is None:
      kwargs['builder_ids'] = [builder_id]
      kwargs['retry_failed_shards'] = default_retry_failed_shards

      if try_db:

        def is_builder_mirrored(spec):
          for mirror in spec.mirrors:
            if builder_id in (mirror.builder_id, mirror.tester_id):
              return True
            if (spec.include_all_triggered_testers and
                builder_id in builder_db.builder_graph[mirror.builder_id]):
              return True
          return False

        kwargs['mirroring_try_builders'] = sorted([
            try_id for try_id, spec in try_db.items()
            if is_builder_mirrored(spec)
        ])
    else:
      try_spec_kwargs = attr.asdict(try_spec, recurse=False)
      mirrors = try_spec_kwargs.pop('mirrors')
      kwargs.update(try_spec_kwargs)
      kwargs['builder_ids'] = {m.builder_id for m in mirrors}
      kwargs['builder_ids_in_scope_for_testing'] = {
          m.tester_id for m in mirrors if m.tester_id
      }

    # Create the builder config first to ensure all of the necessary builders
    # exist
    builder_config = cls.create(builder_db, step_api=step_api, **kwargs)

    # If there is a try spec, verify that it correctly labels builders as
    # builders or testers. We only care if there is a try spec because otherwise
    # the builder config is for a single LUCI builder which is allowed to be
    # either a builder or tester.
    try:
      if try_spec:
        for b in builder_config.builder_ids_in_scope_for_testing:
          builder_spec = builder_db[b]
          if b in builder_config.builder_ids:
            if builder_spec.execution_mode != COMPILE_AND_TEST:
              raise BuilderConfigException(
                  "try builder '{}' specifies '{}' as a builder,"
                  ' but it has execution mode {}, it must be {}'.format(
                      builder_id, b, builder_spec.execution_mode,
                      COMPILE_AND_TEST))
          # This branch will be executed for builders that are specified as
          # testers in the try mirrors
          else:
            if builder_spec.execution_mode != TEST:
              raise BuilderConfigException(
                  "try builder '{}' specifies '{}' as a tester,"
                  ' but it has execution mode {}, it must be {}'.format(
                      builder_id, b, builder_spec.execution_mode, TEST))
    except BuilderConfigException as e:
      if step_api is not None:
        step_api.empty(
            str(e),
            status=step_api.EXCEPTION,
            log_name='details',
            log_text=traceback.format_exc())
      raise

    return builder_config

  @cached_property
  def builder_ids_in_scope_for_testing(self):
    """The builder IDs that tests and targets should be built for."""
    ids = set(self.builder_ids)
    ids.update(self._builder_ids_in_scope_for_testing)
    if self.include_all_triggered_testers:
      ids = self.builder_db.builder_graph.get_transitive_closure(ids)
    return ids

  def get_buildbucket_builder_id(
      self,
      builder_id: BuilderId,
  ) -> BuildbucketBuilderId:
    """Get the BuildbucketBuilderId for a chromium BuilderId.

    Returns:
      The BuildbucketBuilderID for the builder if the config contains
      the necessary mapping (i.e. it was specified src-side) and the
      given chromium BuilderId is wrapped by the config. Otherwise,
      None.
    """
    return self._bb_builder_id_by_builder_id.get(builder_id)

  @cached_property
  def targets_spec_files(self):
    groups = set(builder_id.group
                 for builder_id in self.builder_ids_in_scope_for_testing)
    return {g: '{}.json'.format(g) for g in groups}
