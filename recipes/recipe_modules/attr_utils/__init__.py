# Copyright 2020 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Utilities for declaring immutable attr types.

This module provides `attrs` as a replacement for `attr.s` that has the
following differences in behavior.
* The attribute default values are validated when the class is defined,
  rather than validation errors occurring when attempting to create on
  object that uses the defaults.
* Classes are created frozen, which prevents changing attribute values.
* Classes are created slotted by default, which prevents being able to
  assign to undeclared attributes.
* A more helpful exception message is provided when required attributes
  are not initialized.
* Parentheses must be used in the decorator even if no arguments are
  specified.

The `attrib` method is provided for defining attributes on a class. It
accepts an optional default and requires a constraint that can be
applied to the values of the attribute. The constraint controls the
acceptable values and converts them to a consistent form.

Constraints can be one of:
* A type - values must be instances of the type
* A tuple of types - values must be an instance of one of the types
* An AttributeConstraint - the constraint determines the acceptable
  values and any conversion that is applied before they are stored

The following constraints are provided:
* `enum` - Attribute values must be one of a fixed set of values.
* `sequence` - Attribute values must be iterable and will be converted
  to tuples. By default, no constraint is placed on the members of the
  sequence. Constraints can be added using the index operator, e.g.
  `sequence[int]` for a sequence of ints, `sequence[(str, Path)]` for a
  sequence of strs and Paths or `sequence[enum('x', 'y', 'z')]` for a
  sequence where each member is one of 'x', 'y' or 'z'.
* `set_` - Attribute values must be iterable and will be converted to
  frozensets. By default, no constraint is placed on the members of the
  set. Constraints can be added using the index operator, e.g.
  `set_[int]` for a set of ints, `set_[(str, Path)]` for a set of strs
  and Paths or `set_[enum('x', 'y', 'z')]` for a set where each member
  is one of 'x', 'y' or 'z'.
* `command_args` - Equivalent to `sequence` with the members
  constrained to be of types that can be part of the command line for a
  step.
* `mapping` - Attribute values must be mappings and will be converted to
  FrozenDicts. By default, no constraint is placed on the keys or values
  of the mapping. Constraints can be added using the index operator,
  e.g. `mapping[int, (str, Path)]` for a mapping from int to strs and
  Paths or `mapping[str, sequence[int]]` for a mapping from strs to
  sequences of ints. Ellipsis can be used in place of a constraint to
  avoid placing a constraint on the keys or values, e.g. `mapping[str,
  ...]` for a mapping from strs to any values or `mapping[..., int]` for
  a mapping from any keys to ints.
* `callable_` - Attribute values must be callable objects.

The treatment of the default is as follows:
* If `default` is not specified, the attribute is required: a value must
  be provided for the attribute when creating an object. In contrast to
  `attr.ib`, a required attribute can appear after an optional attribute
  or in a class that has a base with an optional attribute; the ordering
  of the arguments in `__init__` is not adjusted to account for this, so
  it may be necessary to specify it as a keyword argument if values are
  not being provided for all preceding optional attributes.
* If `default` is `None`, the attribute is optional: a value does not
  need to be provided for the attribute when creating an object. `None`
  is an acceptable value in addition to any other values allowed by the
  constraint and will be the default value.
* If `default` is not `None`, the attribute is optional: a value does
  not need to be provided for the attribute when creating an object.
  Values for the attribute will always conform to the definition; the
  attribute's default value will be used if provided the value `None`.

The following additional utilities are provided:
* `cached_property` - Like property, but will only be executed once.
* `FieldMapping` - Mixin to provide dict-like access to a class defined
  with `attrs`.
"""

from __future__ import annotations

import collections.abc
import sys

import attr
from attr import converters, validators

from recipe_engine.config_types import Path
from recipe_engine.engine_types import FrozenDict, freeze
from recipe_engine.util import Placeholder

_NOTHING = object()


def _instance_of(type_, name_qualifier=''):
  """Replacement for validators.instance_of that allows for modifying the name.

  This allows for more helpful error messages when referring to subsidiary
  portions of values (e.g. keys of a dict).
  """

  if isinstance(type_, tuple):
    type_qualifier = 'one of '
  else:
    type_qualifier = ''

  def inner(obj, attribute, value):
    if not isinstance(value, type_):
      raise TypeError(
        "{name_qualifier}'{name}' must be {type_qualifier}{type!r} "
        '(got {value!r} that is a {actual!r}).'.format(
          name=attribute.name,
          name_qualifier=name_qualifier,
          type=type_,
          type_qualifier=type_qualifier,
          actual=type(value),
          value=value,
        )
      )

  return inner


def _attrib(default, constraint):
  if default is None:
    validator = validators.optional(constraint.validate)
    converter = converters.optional(constraint.convert)
  elif default is _NOTHING:

    def validator(obj, attribute, value):
      if value is _NOTHING:
        raise TypeError(
          "No value provided for required attribute '{name}'".format(
            name=attribute.name
          )
        )
      constraint.validate(obj, attribute, value)

    converter = constraint.convert
  else:
    default = constraint.convert(default)
    validator = constraint.validate

    def converter(x):
      return constraint.convert(converters.default_if_none(default)(x))

  return attr.ib(default=default, validator=validator, converter=converter)


class AttributeConstraint:
  """A constraint to be applied to the values of an attrib.

  This allows for defining a common validation and conversion operation
  to be applied to multiple attributes.
  """

  def validate(self, obj, attribute, value):
    """Validate a value for an attrib.

    Args:
      * obj - The object that the attrib is being set on.
      * attribute - The attr.ib defining the field.
      * value - The provided value.
    """

  def convert(self, value):
    """Convert a provided value to an immutable value to store."""
    return value

  @staticmethod
  def from_callables(validator=None, converter=None):
    """Create a constraint from validator and/or converter callables."""
    validator = validator or (lambda obj, attribute, value: None)
    converter = converter or (lambda value: value)

    class _CallableDelegatingAttributeConstraint(AttributeConstraint):
      def validate(self, obj, attribute, value):
        validator(obj, attribute, value)

      def convert(self, value):
        return converter(value)

    return _CallableDelegatingAttributeConstraint()


def _normalize_constraint(
  constraint, constraint_id, name_qualifier='', allow_ellipsis=False
):
  """Converts an input constraint to an AttributeConstraint.

  Args:
    * constraint - The provided constraint. One of a type, a tuple of
      types or an AttributeConstraint. If allow_ellipsis is True,
      constraint can also be Ellipsis to indicate a no-op constraint.
    * constraint_id - The name of the parameter the constraint is being
      normalized for.
    * name_qualifier - A prefix to apply to the attribute name to
      provide more precise error messages, e.g. 'keys of ' to identify
      constraint validation errors on keys of an attribute.
    * allow_ellipsis - Allows ellipsis to be passed for constraint to
      produce a no-op constraint.
  """
  # Already have a constraint object, return it
  if isinstance(constraint, AttributeConstraint):
    return constraint

  # If Ellipsis is allow and constraint is Ellipsis, use a no-op constraint
  if allow_ellipsis and constraint is Ellipsis:
    return AttributeConstraint()

  # At this point, constraint should be a type or a tuple of types
  if isinstance(constraint, tuple):
    non_type_members = [e for e in constraint if not isinstance(e, type)]
    if non_type_members:
      raise TypeError(
        'All members of constraint must be types, got {}'.format(
          non_type_members
        )
      )
  elif not isinstance(constraint, type):
    allowed = ['a type', 'a tuple of types', 'an AttributeConstraint']
    if allow_ellipsis:
      allowed.append('Ellipsis')
    message = '{} must be one of {} or {}, got {}'.format(
      constraint_id, ', '.join(allowed[:-1]), allowed[-1], constraint
    )
    raise TypeError(message)

  return AttributeConstraint.from_callables(
    validator=_instance_of(constraint, name_qualifier)
  )


def attrib(constraint, default=_NOTHING):
  """Declare an immutable attribute.

  Arguments:
    * constraint - A constraint that defines the values that can be
      provided and/or converts values to the form to store them in. Must
      be provided in one of the following forms:
      * An AttributeConstraint
      * A type
      * A tuple containing types
    * default - The default value of the attribute. See module
      documentation for description of the default behavior.
  """
  return _attrib(default, _normalize_constraint(constraint, 'constraint'))


def enum(values):
  """A constraint allowing only specific values."""
  return AttributeConstraint.from_callables(
    validator=validators.in_(tuple(values))
  )


@attr.s(frozen=True, slots=True)
class _Sequence(AttributeConstraint):
  _member_constraint = attr.ib()

  @classmethod
  def create(cls, member_constraint=Ellipsis):
    member_constraint = _normalize_constraint(
      member_constraint,
      constraint_id='member_constraint',
      name_qualifier='members of ',
      allow_ellipsis=True,
    )
    return cls(member_constraint)

  def validate(self, obj, attribute, value):
    validator = validators.deep_iterable(
      iterable_validator=_instance_of(tuple),
      member_validator=self._member_constraint.validate,
    )
    validator(obj, attribute, value)

  def convert(self, value):
    try:
      itr = iter(value)
    except TypeError:
      # Let the validator provide a more helpful exception message
      return value
    return tuple(self._member_constraint.convert(x) for x in itr)


class _UnparameterizedSequence(_Sequence):
  @staticmethod
  def __getitem__(member_constraint):
    return _Sequence.create(member_constraint)


sequence = _UnparameterizedSequence.create()


# The set of allowed types should be kept in sync with the types allowed by
# _validate_cmd_list in
# https://source.chromium.org/chromium/infra/infra/+/main:recipes-py/recipe_modules/step/api.py
command_args = sequence[(int, str, Path, Placeholder)]


@attr.s(frozen=True, slots=True)
class _Set(AttributeConstraint):
  _member_constraint = attr.ib()

  @classmethod
  def create(cls, member_constraint=Ellipsis):
    member_constraint = _normalize_constraint(
      member_constraint,
      constraint_id='member_constraint',
      name_qualifier='members of ',
      allow_ellipsis=True,
    )
    return cls(member_constraint)

  def validate(self, obj, attribute, value):
    validator = validators.deep_iterable(
      iterable_validator=_instance_of(frozenset),
      member_validator=self._member_constraint.validate,
    )
    validator(obj, attribute, value)

  def convert(self, value):
    try:
      itr = iter(value)
    except TypeError:
      # Let the validator provide a more helpful exception message
      return value
    return frozenset(self._member_constraint.convert(x) for x in itr)


class _UnparameterizedSet(_Set):
  @staticmethod
  def __getitem__(member_constraint):
    return _Set.create(member_constraint)


set_ = _UnparameterizedSet.create()


@attr.s(frozen=True, slots=True)
class _Mapping(AttributeConstraint):
  _key_constraint = attr.ib()
  _value_constraint = attr.ib()

  @classmethod
  def create(cls, key_constraint=Ellipsis, value_constraint=Ellipsis):
    key_constraint = _normalize_constraint(
      key_constraint,
      constraint_id='key_constraint',
      name_qualifier='keys of ',
      allow_ellipsis=True,
    )
    value_constraint = _normalize_constraint(
      value_constraint,
      constraint_id='value_constraint',
      name_qualifier='values of ',
      allow_ellipsis=True,
    )
    return cls(key_constraint, value_constraint)

  def validate(self, obj, attribute, value):
    validator = validators.deep_mapping(
      mapping_validator=_instance_of(FrozenDict),
      key_validator=self._key_constraint.validate,
      value_validator=self._value_constraint.validate,
    )
    validator(obj, attribute, value)

  def convert(self, value):
    try:
      itr = value.items()
    except AttributeError:
      # Let the validator provide a more helpful exception message
      return value
    return freeze(
      {
        self._key_constraint.convert(k): self._value_constraint.convert(v)
        for k, v in itr
      }
    )


class _UnparameterizedMapping(_Mapping):
  @staticmethod
  def __getitem__(constraints):
    # __getitem__ is interesting: for multiple parameters, they actually
    # get packed up into a tuple
    assert isinstance(constraints, tuple), (
      'constraints must be specified for both keys and values, '
      'use ... for no constraint'
    )
    assert len(constraints) == 2, (
      'expected exactly 2 constraints (keys and values), got {} {}'.format(
        len(constraints), constraints
      )
    )
    return _Mapping.create(*constraints)


mapping = _UnparameterizedMapping.create()


callable_ = AttributeConstraint.from_callables(
  validator=validators.is_callable()
)


def cached_property(getter):
  """Decorator for a method to create a property calculated only once.

  The decorator should be applied to a method on a class created using
  the attrs decorator. The method must take zero arguments. The first
  time the property is accessed, the getter will be called to compute
  the value. The value is frozen so that all data accessible via the
  containing class is immutable. All subsequent access of the property
  will get the same value.

  The decorator saves the cost of computing values that are expensive
  but also communicates to a reader of the code that for a given object
  the value of the property will not change.

  The value returned by the getter should not include any mutable state
  in its computation so that the value is dependent only on the value of
  the containing object. Otherwise, it becomes harder to reason about
  what the value will be/what the state was that led to the returned
  value.
  """

  cache = {}

  class CachedProperty:
    def __get__(self, obj, objtype=None):
      """Descriptor for computing cached properties.

      See https://docs.python.org/3/howto/descriptor.html for a
      description of how descriptors in python work (information is
      applicable to python2).
      """
      del objtype
      # Used if someone attempts to access the class attribute
      if obj is None:  # pragma: no cover
        return self
      not_set = object()
      value = cache.get(obj, not_set)
      if value is not_set:
        value = freeze(getter(obj))
        cache[obj] = value
      return value

  return CachedProperty()


def attrs(slots=True, **kwargs):
  """A replacement for attr.s that provides some additional conveniences.

  The following conveniences are provided:
  * The attribute default values are validated when the class is defined, rather
    than validation errors occurring when attempting to create on object that
    uses the defaults.
  * Classes are created frozen, which prevents changing attribute values.
  * Classes are created slotted by default, which prevents being able to assign
    to undeclared attributes.
  """

  # Implementation note: avoid using inheritance to provide any functionality as
  # it can interfere with super calls for staticmethods and classmethods that
  # modify signatures
  def inner(cls):
    cls = attr.s(frozen=True, slots=slots, **kwargs)(cls)
    for a in attr.fields(cls):
      if a.validator is not None and a.default is not _NOTHING:
        try:
          a.validator(None, a, a.default)
        except Exception as e:
          message = 'default for {}'.format(e)
          raise type(e)(message).with_traceback(sys.exc_info()[2])

    return cls

  return inner


class FieldMapping(collections.abc.Mapping):
  """Mixin to give attrs-types dict-like access.

  An attrs-type that inherits from this mixin can be treated like a
  mapping. The mapping will have keys for each of the attrs fields that
  are not None, with the key being the name of the field.

  This mixin eases transitioning away from raw-dicts to attrs types.
  """

  def __getitem__(self, key):
    if key in attr.fields_dict(type(self)):
      value = getattr(self, key)
      if value is not None:
        return value
    raise KeyError(key)

  def _non_none_attrs(self):
    for k, v in attr.asdict(self).items():
      if v is not None:
        yield k

  def __iter__(self):
    return self._non_none_attrs()

  def __len__(self):
    return sum(1 for a in self._non_none_attrs())


from .api import AttrUtilsApi as API
