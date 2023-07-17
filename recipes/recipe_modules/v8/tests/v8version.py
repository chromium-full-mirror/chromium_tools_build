# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine.post_process import DropExpectation
from recipe_engine.recipe_api import Property

from RECIPE_MODULES.build.v8.v8version import (
    choose_revision_to_roll, normalize_version)

DEPS = [
    'recipe_engine/assertions',
    'recipe_engine/properties',
]

PROPERTIES = {
    'class_name': Property(kind=str),
    'test_name': Property(kind=str),
}


def git_hash(num):
  return (40 * 'a' + str(num))[-40:]


def ref_data(ref_tuples):
  return [f'refs/tags/{tag} {git_hash(num)}' for tag, num in ref_tuples]


class VersionUtilsTest:
  def __init__(self, api):
    self.api = api

  def assertEqual(self, actual, expected):
    self.api.assertions.assertEqual(actual, expected)

  def test_normalize_version_tuples(self):
    self.assertEqual(normalize_version((1,)), (1, 0, 0, 0))
    self.assertEqual(normalize_version((3, 1, 1)), (3, 1, 1, 0))
    self.assertEqual(normalize_version((3, 1, 1, 9)), (3, 1, 1, 9))
    self.assertEqual(normalize_version((3, '1', '1')), (3, 1, 1, 0))

  def test_normalize_version_error(self):
    with self.api.assertions.assertRaises(AssertionError):
      normalize_version(1.3)
    with self.api.assertions.assertRaises(AssertionError):
      normalize_version('11.6.4.9.8')
    with self.api.assertions.assertRaises(AssertionError):
      normalize_version(())
    with self.api.assertions.assertRaises(ValueError):
      normalize_version('')

  def test_normalize_version_strings(self):
    self.assertEqual(normalize_version('11.6.176'), (11, 6, 176, 0))
    self.assertEqual(normalize_version('11.6'), (11, 6, 0, 0))
    self.assertEqual(normalize_version('11.6.4.9'), (11, 6, 4, 9))

  def test_revision_to_roll_newer_release(self):
    refs = ref_data([
      ('11.6.219.9-pgo', 3),
      ('11.7.10-pgo', 2),
      ('11.7.9-pgo', 1),
    ])
    self.assertEqual(
        choose_revision_to_roll(refs, '11.7.9'),
        (git_hash(2), f'found revision to roll: {git_hash(2)}'))

  def test_revision_to_roll_first(self):
    refs = ref_data([
      ('11.7.9-pgo', 3),
      ('11.7.8-pgo', 2),
      ('11.7.7-pgo', 1),
    ])
    self.assertEqual(
        choose_revision_to_roll(refs, '11.7.4'),
        (git_hash(3), f'found revision to roll: {git_hash(3)}'))

  def test_revision_to_roll_patch(self):
    refs = ref_data([
      ('11.7.9-pgo', 4),
      ('11.7.8.1-pgo', 3),
      ('11.7.8-pgo', 2),
      ('11.7.7-pgo', 1),
    ])
    # TODO(https://crbug.com/1445862): Fix this and roll 11.7.8.1.
    self.assertEqual(
        choose_revision_to_roll(refs, '11.7.8'),
        (git_hash(4), f'found revision to roll: {git_hash(4)}'))

  def test_revision_to_roll_nothing_new(self):
    refs = ref_data([
      ('11.9.17.9-pgo', 5),
      ('11.9.17.8-pgo', 4),
      ('11.10.10-pgo', 3),
      ('11.10.9.1-pgo', 2),
      ('11.10.9-pgo', 1),
    ])
    self.assertEqual(
        choose_revision_to_roll(refs, '11.10.10'),
        (None, 'found no newer revision than: 11.10.10'))

  def test_revision_to_roll_errors(self):
    refs = ref_data([
      ('11.10.9.1', 2),
      ('11.10.9', 1),
    ])
    with self.api.assertions.assertRaises(AssertionError):
      choose_revision_to_roll(refs, '11.7.8')


def RunSteps(api, class_name, test_name):
  test_cls = globals()[class_name]
  test_suite = test_cls(api)

  getattr(test_suite, test_name)()


def GenTests(api):
  for class_name, cls in globals().items():
    if not class_name.endswith('Test'):
      continue

    for method_name in dir(cls):
      if not method_name.startswith('test_'):
        continue

      yield (api.test(f'{class_name}.{method_name}') +
             api.properties(class_name=class_name, test_name=method_name) +
             api.post_process(DropExpectation))
