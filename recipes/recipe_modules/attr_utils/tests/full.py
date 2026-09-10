# Copyright 2020 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import attr

from recipe_engine import post_process
from recipe_engine.config_types import Path
from recipe_engine.engine_types import FrozenDict
from recipe_engine.util import Placeholder

from RECIPE_MODULES.build.attr_utils import (
  FieldMapping,
  attrib,
  attrs,
  cached_property,
  callable_,
  command_args,
  enum,
  mapping,
  sequence,
  set_,
)

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.recipe_engine import assertions, path


@dataclass
class DEPS(RecipeScriptApi):
  assertions: assertions.API
  path: path.API


def RunSteps(api: DEPS):
  # attrib *********************************************************************
  with api.assertions.assertRaises(TypeError) as caught:
    attrib(1)
  message = (
    'constraint must be one of a type, a tuple of types '
    'or an AttributeConstraint, got 1'
  )
  api.assertions.assertEqual(str(caught.exception), message)

  with api.assertions.assertRaises(TypeError) as caught:
    attrib((str, 1, 'x'))
  message = "All members of constraint must be types, got [1, 'x']"
  api.assertions.assertEqual(str(caught.exception), message)

  @attr.s(frozen=True)
  class AttribTest:
    required = attrib(str)
    optional = attrib(str, default=None)
    default = attrib(str, default='default')
    multi_typed = attrib((str, int), default=None)

  # test requires arguments for attributes with no defaults
  with api.assertions.assertRaises(TypeError) as caught:
    AttribTest()

  message_fragment = "No value provided for required attribute 'required'"
  api.assertions.assertIn(message_fragment, str(caught.exception))

  # test validation of attribute type
  with api.assertions.assertRaises(TypeError) as caught:
    AttribTest(required=1)
  message = "'required' must be {} (got 1 that is a {}).".format(str, int)
  api.assertions.assertEqual(str(caught.exception), message)

  # test value and defaults
  x = AttribTest(required='required')
  api.assertions.assertEqual(x.required, 'required')
  api.assertions.assertIsNone(x.optional)
  api.assertions.assertEqual(x.default, 'default')

  # test None value allowed for attribute with None default
  x = AttribTest(required='required', optional=None)
  api.assertions.assertIsNone(x.optional)

  # test None value for attribute with non-None default results in default
  x = AttribTest(required='required', default=None)
  api.assertions.assertEqual(x.default, 'default')

  # required after optional handling *******************************************
  @attr.s(frozen=True)
  class RequiredTest:
    optional = attrib(str, default=None)
    required = attrib(str)

  with api.assertions.assertRaises(TypeError) as caught:
    RequiredTest()

  message = "No value provided for required attribute 'required'"
  api.assertions.assertEqual(str(caught.exception), message)

  # test successful validation
  x = RequiredTest(required='required')
  api.assertions.assertEqual(x.required, 'required')

  # enum ***********************************************************************
  @attr.s(frozen=True)
  class EnumTest:
    value = attrib(enum([1, 2, 3]))

  # test validation of attribute value
  with api.assertions.assertRaises(ValueError) as caught:
    EnumTest(value=4)
  message = "'value' must be in (1, 2, 3) (got 4)"
  api.assertions.assertEqual(caught.exception.args[0], message)

  # test successful validation
  x = EnumTest(value=1)
  api.assertions.assertEqual(x.value, 1)

  # sequence *******************************************************************
  @attr.s(frozen=True)
  class SequenceTest:
    value = attrib(sequence)
    typed = attrib(sequence[str], default=None)

  # test validation of attribute value
  with api.assertions.assertRaises(TypeError) as caught:
    SequenceTest(value=1)
  message = "'value' must be {} (got 1 that is a {}).".format(tuple, int)
  api.assertions.assertEqual(str(caught.exception), message)

  # test validation of element types
  with api.assertions.assertRaises(TypeError) as caught:
    SequenceTest(value=[1, 2, 3], typed=[4, 5, 6])
  message = "members of 'typed' must be {} (got 4 that is a {}).".format(
    str, int
  )
  api.assertions.assertEqual(str(caught.exception), message)

  # test successful validation
  x = SequenceTest(value=[1, 2, 3], typed=['4', '5', '6'])
  api.assertions.assertEqual(x.value, (1, 2, 3))
  api.assertions.assertEqual(x.typed, ('4', '5', '6'))

  # set_ ***********************************************************************
  @attr.s(frozen=True)
  class SetTest:
    value = attrib(set_)
    typed = attrib(set_[str], default=None)

  # test validation of attribute value
  with api.assertions.assertRaises(TypeError) as caught:
    SetTest(value=1)
  message = "'value' must be {} (got 1 that is a {}).".format(frozenset, int)
  api.assertions.assertEqual(str(caught.exception), message)

  # test validation of element types
  with api.assertions.assertRaises(TypeError) as caught:
    SetTest(value=[1, 2, 3], typed=[4, 5, 6])
  message = "members of 'typed' must be {} (got 4 that is a {}).".format(
    str, int
  )
  api.assertions.assertEqual(str(caught.exception), message)

  # test successful validation
  x = SetTest(value=[1, 2, 3, 1], typed=['4', '5', '6', '4'])
  api.assertions.assertEqual(x.value, frozenset((1, 2, 3)))
  api.assertions.assertEqual(x.typed, frozenset(('4', '5', '6')))

  # command_args ***************************************************************
  @attr.s(frozen=True)
  class CommandArgsTest:
    args = attrib(command_args)

  # test validation of element types
  with api.assertions.assertRaises(TypeError) as caught:
    CommandArgsTest([[]])

  api.assertions.assertEqual(
    str(caught.exception),
    (
      "members of 'args' must be one of ({}, {}, {}, {}) "
      "(got [] that is a {}).".format(int, str, Path, Placeholder, list)
    ),
  )

  # test that all valid argument types can be passed
  args = [0, 'x', api.path.start_dir, Placeholder('fake-placeholder')]
  x = CommandArgsTest(args)
  api.assertions.assertEqual(x.args, tuple(args))

  # mapping ********************************************************************
  with api.assertions.assertRaises(TypeError) as caught:
    mapping[str, 1]  # pylint: disable=pointless-statement
  message = (
    'value_constraint must be one of a type, a tuple of types, '
    'an AttributeConstraint or Ellipsis, got 1'
  )
  api.assertions.assertEqual(str(caught.exception), message)

  @attr.s(frozen=True)
  class MappingTest:
    typed = attrib(mapping[str, int], default={})
    key_typed = attrib(mapping[str, ...], default={})
    value_typed = attrib(mapping[..., int], default={})
    untyped = attrib(mapping, default={})

  # test validation of attribute value
  with api.assertions.assertRaises(TypeError) as caught:
    MappingTest(typed=1)
  message = "'typed' must be {} (got 1 that is a {}).".format(FrozenDict, int)
  api.assertions.assertEqual(str(caught.exception), message)

  # test validation of types of mapping keys
  with api.assertions.assertRaises(TypeError) as caught:
    MappingTest(typed={1: 1})
  message = "keys of 'typed' must be {} (got 1 that is a {}).".format(str, int)
  api.assertions.assertEqual(str(caught.exception), message)

  # test validation of types of mapping values
  with api.assertions.assertRaises(TypeError) as caught:
    MappingTest(typed={'a': 'a'})
  message = "values of 'typed' must be {} (got 'a' that is a {}).".format(
    int, str
  )
  api.assertions.assertEqual(str(caught.exception), message)

  # test successful validation
  x = MappingTest(
    typed={'1': 1, '2': 2},
    key_typed={'3': 3, '4': '4'},
    value_typed={'5': 5, 6: 6},
    untyped={
      '7': 7,
      8: '8',
    },
  )
  api.assertions.assertEqual(x.typed, FrozenDict({'1': 1, '2': 2}))
  api.assertions.assertEqual(x.key_typed, FrozenDict({'3': 3, '4': '4'}))
  api.assertions.assertEqual(x.value_typed, FrozenDict({'5': 5, 6: 6}))
  api.assertions.assertEqual(x.untyped, FrozenDict({'7': 7, 8: '8'}))

  # callable_ ******************************************************************
  def test_callback():
    pass  # pragma: no cover

  @attr.s(frozen=True)
  class CallableTest:
    callback = attrib(callable_)

  # test validation of attribute value
  with api.assertions.assertRaises(TypeError) as caught:
    CallableTest(callback='x')
  message = "'callback' must be callable (got 'x' that is a {}).".format(str)
  api.assertions.assertEqual(str(caught.exception), message)

  # test successful validation
  x = CallableTest(callback=test_callback)
  api.assertions.assertEqual(x.callback, test_callback)

  # attrs **********************************************************************
  with api.assertions.assertRaises(TypeError) as caught:

    @attrs()
    class AttrsTest:
      x = attrib(str, default=1)

  message = "default for 'x' must be {} (got 1 that is a {}).".format(str, int)
  api.assertions.assertEqual(str(caught.exception), message)

  @attrs()
  class AttrsTest:
    x = attrib(str, default='bar')

  x = AttrsTest()
  api.assertions.assertEqual(x.x, 'bar')

  # FieldMapping ***************************************************************
  @attrs()
  class FieldMappingTest(FieldMapping):
    x = attrib(str, default=None)
    y = attrib(str, default=None)

  x = FieldMappingTest()
  with api.assertions.assertRaises(KeyError):
    _ = x['x']
  api.assertions.assertEqual(dict(x), {})
  api.assertions.assertEqual(len(x), 0)

  x = FieldMappingTest(x='foo', y='bar')
  api.assertions.assertEqual(x['x'], 'foo')
  api.assertions.assertEqual(dict(x), {'x': 'foo', 'y': 'bar'})
  api.assertions.assertEqual(len(x), 2)

  # cached_property ************************************************************
  calls = []

  @attrs()
  class CachedPropertyTest:
    x = attrib(str)

    @cached_property
    def y(self):
      calls.append(1)
      return self.x.upper()

    @classmethod
    def create(cls, **kwargs):
      return cls(**kwargs)

  # Test that the getter is executed only once
  x = CachedPropertyTest('foo')
  api.assertions.assertEqual(len(calls), 0)

  api.assertions.assertEqual(x.y, 'FOO')
  api.assertions.assertEqual(len(calls), 1)

  api.assertions.assertEqual(x.y, 'FOO')
  api.assertions.assertEqual(len(calls), 1)

  # Test that the property cannot be set
  with api.assertions.assertRaises(AttributeError):
    x.y = 'bar'

  # Test that the inheritance hierarchy is not affected by attrs/cached_property
  @attrs()
  class InheritedCachedPropertyTest(CachedPropertyTest):
    z = attrib(str)

    @cached_property
    def w(self):
      return self.z.upper()  # pragma: no cover

    @classmethod
    def create(cls, **kwargs):
      return super(InheritedCachedPropertyTest, cls).create(x='foo', **kwargs)

  # If attrs uses inheritance to manage cached properties, then the
  # class hierarchy will be:
  # * attrs-created InheritedCachedPropertyTest
  # * InheritedCachedPropertyTest
  # * attrs-created CachedPropertyTest
  # * CachedPropertyTest
  # This causes InheritedCachedPropertyTest.create to be called twice, resulting
  # in a TypeError due to multiple values for keyword argument 'x'
  x = InheritedCachedPropertyTest.create(z='bar')
  api.assertions.assertEqual(x.x, 'foo')


def GenTests(api: RecipeTestApi):
  yield api.test(
    'full',
    api.post_process(post_process.DropExpectation),
  )
