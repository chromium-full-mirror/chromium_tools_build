# Copyright 2022 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""A library for performing validation of protobufs.

To validate protobufs, import Registry, use its register method
to register validators and then call its validate method.

Example:
from RECIPE_MODULES.build import proto_validation

VALIDATORS = proto_validation.Registry()

@VALIDATORS.register(MyMessage)
def _validate_my_message(message, ctx):
  ctx.validate_field(message, 'foo')
  ctx.validate_field(message, 'nested_message', optional=True)

@VALIDATORS.register(MyNestedMessage)
def _validate_my_nested_message(message, ctx):
  ctx.validate_field(message, 'bar')

errors = VALIDATORS.validate(
    MyMessage(foo='x', nested_message=MyNestedMessage(bar='y')),
    '$build/my_module')
if errors:
  # Report errors
"""

import contextlib

from google.protobuf.message import Message


class Registry:
  """A type for registering validators and validating protobuf messages.
  """

  def __init__(self):
    self._validators_by_proto_type = {}

  def register(self, proto_type):
    """Decorate a function to register a validator.

    The decorated function must take two positional arguments:
    1. The protobuf message to validate.
    2. A Context instance. This provides the means to report errors and
      trigger validation of fields on the message.

    Example:
      VALIDATORS = Registry()

      @VALIDATORS.register(MyMessage)
      def validate_my_message(message, ctx):
        ...

    Args:
      proto_type: The protobuf class to register the validator for. When
        `validate` is called for a message of the type or if a validator
        function validates a field of the message type, the registered
        validator will be called.

    Returns:
      A decorator that will register the decorated function as the
      validator for `proto_type`.
    """
    assert issubclass(proto_type, Message), (
        'validators can only be registered for proto message types, got {!r}'
        .format(proto_type))

    def inner(f):
      assert proto_type not in self._validators_by_proto_type, (
          '{!r} is already registered as validator for {!r}'.format(
              self._validators_by_proto_type[proto_type], proto_type))
      self._validators_by_proto_type[proto_type] = f
      return f

    return inner

  def validate(self, message, location=None):
    """Validate a protobuf message.

    Args:
      message: The protobuf message to validate.
      location: The name of the location of the top-level message being
        validated.

    Returns:
      A list of validation errors for the message.
    """
    assert isinstance(message, Message), (
        'only proto message objects can be validated, got {!r}'.format(message))
    validator = self._validators_by_proto_type.get(type(message))
    if not validator:
      return []
    ctx = Context(self._validators_by_proto_type, location)
    validator(message, ctx)
    return ctx._errors


class Context:
  """A type for reporting errors and validating fields.

  Validator functions and callbacks will receive a Context
  instance. Any errors reported using the context will be included in
  the errors reported by the top-level validate call.

  It is invalid to attempt to use a Context instance once the function
  that it was passed to has returned.
  """

  def __init__(self, validators_by_proto_type, location):
    self._validators_by_proto_type = validators_by_proto_type
    self._location = location
    self._errors = []

  @property
  def location(self):
    """The location that refers to the message being validated."""
    return self._location

  def error(self, error):
    """Report a validation error."""
    self._errors.append(error)

  def get_field_location(self, field, index=None):
    """Get the location value of a field of the current message.

    Args:
      field: The name of the field.
      index: An optional index value for referring to the individual
        element of a repeated field.

    Returns:
      A string with the absolute location of the field.
    """
    location = ('{}.{}'.format(self._location, field)
                if self._location is not None else field)
    if index is not None:
      location = '{}[{!r}]'.format(location, index)
    return location

  def validate_field(self, message, field, optional=False):
    """Validate a field of the message.

    Report an error if a non-optional field is not set. If the field is
    set and is of message type, any registered validator for the message
    type will be called on the field value.

    Args:
      message: The protobuf message containing the field to validate.
      field: The name of the field to validate.
      optional: Iff true, the field will validate successfully if not
        set. For primitive types, a false-valued field will be
        considered unset.
    """
    if self._is_primitive(message, field):
      has_field = bool(getattr(message, field))
    else:
      has_field = message.HasField(field)
      if has_field:
        sub_message = getattr(message, field)
        validator = self._get_validator(sub_message)
        if validator:
          location = self.get_field_location(field)
          with self._sub_context(location) as sub_ctx:
            validator(sub_message, sub_ctx)

    if not has_field and not optional:
      self.error('{} is not set'.format(self.get_field_location(field)))

  def validate_repeated_field(self,
                              message,
                              field,
                              optional=False,
                              allow_default_primitives=False,
                              callback=None):
    """Validate a repeated field.

    Args:
      message: The message containing the repeated field to validate.
      field: The name of the field to validate.
      optional: Iff true, the field will validate successfully if no
        elements were specified.
      allow_default_primitives: Iff true, default-valued elements (0,
        empty string) will validate successfully. This has no effect on
        fields with message element types.
      callback: A callback that will be called for any elements that do
        not cause a validation error. The callback must be a function
        that takes two positional arguments:
        1. The validated element.
        2. A Context instance. The location will refer to the specific
          element.
    """
    value = getattr(message, field)
    if value:
      if self._is_primitive(message, field):
        validator = None
        if not allow_default_primitives:
          validator = self._validate_non_default_primitive
      else:
        validator = self._get_validator(value[0])

      for i, e in enumerate(value):
        location = self.get_field_location(field, i)
        with self._sub_context(location) as sub_ctx:
          valid = True
          if validator:
            validator(e, sub_ctx)
            if sub_ctx._errors:
              valid = False
          if valid and callback:
            callback(e, sub_ctx)

    elif not optional:
      self.error('{} is empty'.format(self.get_field_location(field)))

  @staticmethod
  def _is_primitive(message, field):
    # message_type will be None if the field is a primitive type rather
    # than a message
    return message.DESCRIPTOR.fields_by_name[field].message_type is None

  @staticmethod
  def _validate_non_default_primitive(value, ctx):
    if not value:
      ctx.error('{} is not set'.format(ctx.location))

  def _get_validator(self, message):
    return self._validators_by_proto_type.get(type(message))

  @contextlib.contextmanager
  def _sub_context(self, location):
    sub_ctx = Context(self._validators_by_proto_type, location)
    yield sub_ctx

    self._errors.extend(sub_ctx._errors)
