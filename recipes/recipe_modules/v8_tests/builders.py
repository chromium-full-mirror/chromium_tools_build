# Copyright 2022 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

# Contains the bulk of the V8 builder configurations so they can be reused
# from multiple recipes.


from __future__ import annotations

from collections import defaultdict

from recipe_engine.engine_types import freeze
from .testing import V8Variant


class TestStepConfig:
  """Per-step test configuration."""

  def __init__(
    self,
    name,
    shards=1,
    suffix='',
    test_args=None,
    variants=None,
    swarming_dimensions=None,
    swarming_task_attrs=None,
  ):
    """Init per-step test configuration.

    Args:
      name: Test name used for keying testing.TEST_CONFIGS.
      shards: Number of swarming shards, default 1.
      suffix: Optional suffix for test name in UI.
      test_args: Optional list of extra args to the test driver.
      variants: Optional V8Variant object defining the testing variants to use.
      swarming_dimensions: Dict with extra swarming dimensions.
      swarming_task_attrs: Dict with extra swarming task attributes.
    """
    self.name = name
    self.shards = shards
    self.suffix = suffix
    self.test_args = list(test_args or [])
    self.variants = variants
    self.swarming_dimensions = swarming_dimensions or {}
    self.swarming_task_attrs = swarming_task_attrs or {}
    if not suffix and variants:
      # Disambiguate step names if a particular variant is specified and no
      # default suffix is provided.
      self.suffix = str(variants)

  def __str__(self):
    return '%s(%d) %s' % (self.name, self.shards, self.variants)

  @property
  def step_name_suffix(self):
    return ' - %s' % self.suffix if self.suffix else ''

  def pack(self):
    """Returns a serializable version of this object.

    This method is the counterpart to the method below.
    """
    return [
      self.name,
      self.shards,
      self.variants.pack() if self.variants else None,
      self.suffix,
      self.test_args,
      self.swarming_dimensions,
      self.swarming_task_attrs,
    ]

  @staticmethod
  def unpack(packed, swarming_dimensions=None, swarming_task_attrs=None):
    """Constructs a test-step config from a serialized version of this class.

    This method is the counterpart to the method ablve.

    Args:
      packed: An object to unpack as returned by pack() above.
      swarming_dimensions: Default swarming dimensions, flattened into every
          test config. Per-test dimensions will override these defaults.
      swarming_task_attrs: Default swarming attributes, flattened into every
          test config. Per-test attributes will override these defaults.
    """
    return TestStepConfig(
      name=packed[0],
      shards=int(packed[1]),
      variants=V8Variant.unpack(packed[2]) if packed[2] else None,
      suffix=packed[3],
      test_args=packed[4],
      swarming_dimensions=dict(
        (swarming_dimensions or {}), **(packed[5] or {})
      ),
      swarming_task_attrs=dict(
        (swarming_task_attrs or {}), **(packed[6] or {})
      ),
    )

  @staticmethod
  def from_python_literal(spec):
    """Constructs a test-step config from the V8-side pyl test spec."""
    variant = spec.get('variant')
    return TestStepConfig(
      name=spec['name'],
      swarming_dimensions=spec.get('swarming_dimensions'),
      swarming_task_attrs=spec.get('swarming_task_attrs'),
      shards=int(spec.get('shards', 1)),
      suffix=spec.get('suffix', ''),
      test_args=spec.get('test_args'),
      variants=V8Variant(variant) if variant else None,
    )


class TestSpec:
  """Represents a V8-side test specification with extra tests to run on a
  set of builders.

  The builder set is comprised of the parent builder and all triggered child
  builders.
  """

  def __init__(self):
    self._test_spec = {}

  def update(self, other_test_spec):
    self._test_spec.update(other_test_spec._test_spec)

  def as_properties_dict_single(self):
    """Convenience method - like above, but assuming exactly one test-spec
    entry.
    """
    assert len(self._test_spec) >= 1, 'No tests specified'
    assert len(self._test_spec) <= 1, 'Ambiguous tester names in spec'
    return self.as_properties_dict(next(iter(self._test_spec.keys())))

  def as_properties_dict(self, buildername):
    """Packs a test spec and returns it as a properties dict to be passed to
    another builder.

    This method is the counterpart to the method below.

    Args:
      buildername: The name of the builder to which the property is passed. The
          spec will contain the data for that builder only.
    Returns: A dict to be used to update recipe properties of the triggered
        child builders.
    """
    builder_spec = self._test_spec.get(buildername, {})
    packed_spec = {
      'swarming_dimensions': builder_spec.get('swarming_dimensions', {}),
      'swarming_task_attrs': builder_spec.get('swarming_task_attrs', {}),
      'tests': [t.pack() for t in builder_spec.get('tests', [])],
    }
    if packed_spec['tests']:
      return {'parent_test_spec': packed_spec}
    return {}

  @staticmethod
  def from_properties_dict(properties):
    """Unpacks a test spec provided by another builder via properties.

    This method is the counterpart to the method above.

    Returns: A list of TestStepConfig objects representing extra test.
    """
    # Swarming dimensions and properties are flattened into each test config.
    builder_spec = properties.get('parent_test_spec', {})
    swarming_dimensions = builder_spec.get('swarming_dimensions', {})
    swarming_task_attrs = builder_spec.get('swarming_task_attrs', {})
    return [
      TestStepConfig.unpack(
        packed_spec, swarming_dimensions, swarming_task_attrs
      )
      for packed_spec in builder_spec.get('tests', [])
    ]

  @staticmethod
  def from_python_literal(full_test_spec, builderset):
    """Constructs a filtered test spec from the raw V8-side pyl.

    Args:
      full_test_spec: Full unfiltered test spec from python literal.
      builderset: Iterable with builder names (comprising the current builder
          and all its triggered testers).
    Returns:
      A test spec dict (buildername->builder spec dict) filtered by the
      builders in the given builderset. The tests in each builder spec are
      defined by TestStepConfig objects.
    """
    result = TestSpec()
    for buildername in builderset:
      builder_spec = full_test_spec.get(buildername)
      if builder_spec:
        result._test_spec[buildername] = {
          'swarming_dimensions': builder_spec.get('swarming_dimensions', {}),
          'swarming_task_attrs': builder_spec.get('swarming_task_attrs', {}),
          'tests': [
            TestStepConfig.from_python_literal(t)
            for t in builder_spec.get('tests', [])
          ],
        }
    return result

  def log_lines(self):
    """Readable representation of this test spec for recipe log lines.

    Returns: List of strings.
    """
    log = []
    for builder, builder_spec in sorted(self._test_spec.items()):
      log.append(builder)
      for test in builder_spec['tests']:
        log.append('  ' + str(test))
    return log

  def get_tests(self, buildername):
    """Get all TestStepConfig objects filtered by `buildername`.

    The swarming dimensions and properties are flattened into each test-step
    config. Per-test dimensions and properties have precedence.
    """
    builder_spec = self._test_spec.get(buildername, {})
    swarming_dimensions = builder_spec.get('swarming_dimensions', {})
    swarming_task_attrs = builder_spec.get('swarming_task_attrs', {})
    # Pack/unpack each test spec in order to flatten the swarming dimensions
    # and properties into each object.
    return [
      TestStepConfig.unpack(
        test_spec.pack(), swarming_dimensions, swarming_task_attrs
      )
      for test_spec in builder_spec.get('tests', [])
    ]

  def get_all_test_names(self):
    """Get all test names for all tests of all builders."""
    return [
      test.name
      for builder_spec in self._test_spec.values()
      for test in builder_spec.get('tests', [])
    ]


# Empty test spec, usable as null object.
EmptyTestSpec = TestSpec()
