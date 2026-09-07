# Copyright 2022 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Test of proto validation."""

from __future__ import annotations

from recipe_engine import post_process

from RECIPE_MODULES.build.proto_validation import Registry

from PB.recipe_modules.build.proto_validation.tests import test_protos

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.recipe_engine import assertions


@dataclass
class DEPS(RecipeScriptApi):
  assertions: assertions.API


def RunSteps(api: DEPS):
  validators = Registry()

  def assert_valid(obj):
    errors = validators.validate(obj, '$test')
    api.assertions.assertFalse(errors)

  def assert_invalid(obj, *expected_errors):
    errors = validators.validate(obj, '$test')
    for e in expected_errors:
      api.assertions.assertIn(e, errors)

  # pylint: disable=unused-variable
  # The functions are called via indirection, so they don't end up used

  # Validating a message that has no registered validator returns no errors
  assert_valid(test_protos.EmptyMessage())

  # Calling error returns an error with the location filled in
  @validators.register(test_protos.BadMessage)
  def validate_bad_message(_, ctx):
    ctx.error('{} is a BadMessage'.format(ctx.location))

  assert_invalid(test_protos.BadMessage(), '$test is a BadMessage')

  # Validating a primitive field returns an error if the field is not
  # set and not optional
  @validators.register(test_protos.MessageWithRequiredPrimitive)
  def validate_message_with_required_primitive(message, ctx):
    ctx.validate_field(message, 'required')

  assert_invalid(test_protos.MessageWithRequiredPrimitive(),
                 '$test.required is not set')

  assert_valid(test_protos.MessageWithRequiredPrimitive(required='foo'))

  @validators.register(test_protos.MessageWithOptionalPrimitive)
  def validate_message_with_optional_primitive(message, ctx):
    ctx.validate_field(message, 'optional', optional=True)

  assert_valid(test_protos.MessageWithOptionalPrimitive())

  # Validating a message field returns an error if the field is not set
  # and not optional
  @validators.register(test_protos.MessageWithRequiredMessage)
  def validate_message_with_required_message(message, ctx):
    ctx.validate_field(message, 'required')

  assert_invalid(test_protos.MessageWithRequiredMessage(),
                 '$test.required is not set')

  assert_valid(
      test_protos.MessageWithRequiredMessage(
          required=test_protos.EmptyMessage()))

  @validators.register(test_protos.MessageWithOptionalMessage)
  def validate_message_with_optional_message(message, ctx):
    ctx.validate_field(message, 'optional', optional=True)

  assert_valid(test_protos.MessageWithOptionalMessage())

  # Validators are recursively called for nested message fields
  @validators.register(test_protos.MessageWithNestedMessage)
  def validate_message_with_nested_message(message, ctx):
    ctx.validate_field(message, 'nested')

  assert_invalid(
      test_protos.MessageWithNestedMessage(
          nested=test_protos.MessageWithRequiredMessage()),
      '$test.nested.required is not set')

  # Validating a repeated primitive field returns an error if the field is not
  # set and not optional
  @validators.register(test_protos.MessageWithRequiredRepeatedPrimitive)
  def validate_message_with_required_repeated_primitive(message, ctx):
    ctx.validate_repeated_field(message, 'required')

  assert_invalid(test_protos.MessageWithRequiredRepeatedPrimitive(),
                 '$test.required is empty')

  assert_invalid(
      test_protos.MessageWithRequiredRepeatedPrimitive(required=['']),
      '$test.required[0] is not set')

  assert_valid(
      test_protos.MessageWithRequiredRepeatedPrimitive(required=['foo']))

  @validators.register(test_protos.MessageWithOptionalRepeatedPrimitive)
  def validate_message_with_optional_repeated_primitive(message, ctx):
    ctx.validate_repeated_field(
        message, 'optional', optional=True, allow_default_primitives=True)

  assert_valid(test_protos.MessageWithOptionalRepeatedPrimitive())

  assert_valid(test_protos.MessageWithOptionalRepeatedPrimitive(optional=['']))

  # Validating a repeated message field returns an error if the field is not
  # set and not optional
  @validators.register(test_protos.MessageWithRequiredRepeatedMessage)
  def validate_message_with_required_repeated_message(message, ctx):
    ctx.validate_repeated_field(message, 'required')

  assert_invalid(test_protos.MessageWithRequiredRepeatedMessage(),
                 '$test.required is empty')

  assert_valid(
      test_protos.MessageWithRequiredRepeatedMessage(
          required=[test_protos.EmptyMessage()]))

  @validators.register(test_protos.MessageWithOptionalRepeatedMessage)
  def validate_message_with_optional_repeated_message(message, ctx):
    ctx.validate_repeated_field(message, 'optional', optional=True)

  assert_valid(test_protos.MessageWithOptionalRepeatedMessage())

  # Validators are recursively called for repeated nested message fields
  @validators.register(test_protos.MessageWithNestedRepeatedMessage)
  def validate_message_with_nested_repeated_message(message, ctx):
    ctx.validate_repeated_field(message, 'nested')

  assert_invalid(
      test_protos.MessageWithNestedRepeatedMessage(
          nested=[test_protos.MessageWithRequiredMessage()]),
      '$test.nested[0].required is not set')

  # Validating repeated field calls callback for valid elements
  @validators.register(test_protos.MessageWithRepeatedFieldRequiringCallback)
  def validate_message_with_repeated_field_requiring_callback(message, ctx):
    valid_values = []

    def callback(value, sub_ctx):
      valid_values.append('{}={}'.format(sub_ctx.location, value))
      sub_ctx.error('{}, valid so far: {}'.format(sub_ctx.location,
                                                  ', '.join(valid_values)))

    ctx.validate_repeated_field(message, 'values', callback=callback)

  assert_invalid(
      test_protos.MessageWithRepeatedFieldRequiringCallback(
          values=['', 'foo', '', 'bar', 'baz']),
      '$test.values[1], valid so far: $test.values[1]=foo',
      '$test.values[3], valid so far: $test.values[1]=foo, $test.values[3]=bar',
      ('$test.values[4], valid so far: '
       '$test.values[1]=foo, $test.values[3]=bar, $test.values[4]=baz'),
  )


def GenTests(api: RecipeTestApi):
  yield api.test(
      'full',
      api.post_process(post_process.DropExpectation),
  )
