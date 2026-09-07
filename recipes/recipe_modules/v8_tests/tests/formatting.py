# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine.post_process import DropExpectation
from recipe_engine.recipe_api import Property

from RECIPE_MODULES.build.v8_tests import formatting

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.recipe_engine import assertions, properties


@dataclass
class DEPS(RecipeScriptApi):
  assertions: assertions.API
  properties: properties.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  properties: properties.TEST_API

PROPERTIES = {
    'class_name': Property(kind=str),
    'test_name': Property(kind=str),
}

class FormattingTest:
  def __init__(self, api):
    self.api = api

  def assertEqual(self, actual, expected):
    self.api.assertions.assertEqual(actual, expected)

  def test_sizes(self):
    size = lambda v: formatting.size({'x': v}, 'x')
    self.assertEqual(size(0), '0.0 B')
    self.assertEqual(size(1024), '1.0 kB')
    self.assertEqual(size(1.2 * 1024), '1.2 kB')
    self.assertEqual(size(1024 * 1024), '1.0 MB')
    self.assertEqual(size(123.1 * 1024 * 1024 * 1024), '123.1 GB')

  def test_specific_sizes(self):
    self.assertEqual(formatting.rss({'max_rss': 1023}), '1023.0 B')
    self.assertEqual(formatting.vms({'max_vms': 1023}), '1023.0 B')

  def test_duration(self):
    duration = lambda v: formatting.duration({'duration': v})
    self.assertEqual(duration(0), '00:00:000')
    self.assertEqual(duration(23.001), '00:23:001')
    self.assertEqual(duration(1200.103), '20:00:103')

  def test_details(self):
    test = {
      'name': 'foo',
      'flags': ['-a', '-b'],
      'command': 'run -a -b foo',
      'variant': 'default',
    }
    self.assertEqual(
        formatting.test_details(test, 'max_rss', '1.2 kB'),
        [
          'Test: foo',
          'Flags: -a -b',
          'Command: run -a -b foo',
          'Variant: default',
          'max_rss: 1.2 kB',
          '',
        ]
    )

  def test_top_tests(self):
    test_list = [
      {
        'name': 'foo',
        'flags': ['-a', '-b'],
        'command': 'run -a -b foo',
        'variant': 'default',
        'marked_heavy': False,
        'max_rss': 1.2 * 1024,
      },
      {
        'name': 'bar',
        'flags': ['-a', '-b'],
        'command': 'run -a -b bar',
        'variant': 'stress',
        'marked_heavy': True,
        'max_rss': 1.1 * 1024,
      },
    ]
    self.assertEqual(
        formatting.top_tests(
            test_list, formatting.rss, 'Maximum RSS', 'marked_heavy'),
        [
          '1.2 kB foo *',
          '1.1 kB bar',
          '',
          'Details:',
          '',
          'Test: foo',
          'Flags: -a -b',
          'Command: run -a -b foo',
          'Variant: default',
          'Maximum RSS: 1.2 kB',
          '',
          'Test: bar',
          'Flags: -a -b',
          'Command: run -a -b bar',
          'Variant: stress',
          'Maximum RSS: 1.1 kB',
          '',
        ]
    )


def RunSteps(api: DEPS, class_name, test_name):
  test_cls = globals()[class_name]
  test_suite = test_cls(api)

  getattr(test_suite, test_name)()


def GenTests(api: TEST_DEPS):
  for class_name, cls in globals().items():
    if not class_name.endswith('Test'):
      continue

    for method_name in dir(cls):
      if not method_name.startswith('test_'):
        continue

      yield (api.test(f'{class_name}.{method_name}') +
             api.properties(class_name=class_name, test_name=method_name) +
             api.post_process(DropExpectation))
