# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine.post_process import DropExpectation
from recipe_engine.recipe_api import Property

from RECIPE_MODULES.build.v8.v8version import (
    choose_revision_to_roll, largest_major_version, normalize_version)

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


def git_hash(num):
  return (40 * 'a' + str(num))[-40:]


def fake_date(hours=0):
  assert 0 <= hours < 24
  return f'Fri Jul 7 {hours:02d}:32:02 2023 +0000'


def ref_data(ref_tuples):
  return [
    f'refs/tags/{tag} {git_hash(num)} {fake_date(hours)}'
    for tag, num, hours in ref_tuples
  ]


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

  def test_largest_major_version(self):
    self.assertEqual(
        largest_major_version(['1.2.3.4', '1.3.1', '1.2.40']), (1, 3))

  def test_ref_data(self):
    """Demonstrates the ref test data."""
    refs = ref_data([
      ('11.7.1', 102, 11),
      ('11.7', 101, 3),
    ])
    expected = [
        'refs/tags/11.7.1 aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa102 '
        'Fri Jul 7 11:32:02 2023 +0000',
        'refs/tags/11.7 aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa101 '
        'Fri Jul 7 03:32:02 2023 +0000',
    ]
    self.assertEqual(refs, expected)

  def test_revision_to_roll_newer_release(self):
    refs = ref_data([
      ('11.6.219.9-pgo', 3, 6),
      ('11.6.219.9', 3, 6),
      ('11.7.10-pgo', 2, 5),
      ('11.7.10', 2, 5),
      ('11.7.9-pgo', 1, 0),
      ('11.7.9', 1, 0),
    ])
    self.assertEqual(
        choose_revision_to_roll(refs, '11.7.9'),
        (git_hash(2), f'found revision to roll: {git_hash(2)}'))

  def test_revision_to_roll_padded_version(self):
    refs = ref_data([
        ('11.7.10-pgo', 2, 5),
        ('11.7.10', 2, 5),
        ('11.7.456709-pgo', 1, 0),
        ('11.7.456709', 1, 0),
    ])
    with self.api.assertions.assertRaises(AssertionError):
      choose_revision_to_roll(refs, '11.7.456709')

  def test_revision_to_roll_nothing_new_padded_version(self):
    refs = ref_data([
        ('11.10.10-pgo', 3, 2),
        ('11.10.10', 3, 2),
        ('11.10.9.1-pgo', 2, 1),
        ('11.10.9.1', 2, 1),
        ('11.10.456709-pgo', 1, 0),
        ('11.10.456709', 1, 0),
        ('11.10.9-pgo', 1, 0),
        ('11.10.9', 1, 0),
    ])
    self.assertEqual(
        choose_revision_to_roll(refs, '11.10.10'),
        (None, 'found no newer revision than: 11.10.10'))

  def test_revision_to_roll_first(self):
    refs = ref_data([
      ('11.7.9-pgo', 3, 13),
      # Roll this as it has pgo data and is the newest.
      ('11.7.9', 3, 13),
      ('11.7.8-pgo', 2, 12),
      ('11.7.8', 2, 12),
      ('11.7.7-pgo', 1, 11),
      ('11.7.7', 1, 11),
    ])
    self.assertEqual(
        choose_revision_to_roll(refs, '11.7.4'),
        (git_hash(3), f'found revision to roll: {git_hash(3)}'))

  def test_revision_to_roll_first_no_pgo(self):
    refs = ref_data([
      ('11.7.9', 3, 13),
      ('11.7.8-pgo', 2, 12),
      # Roll this as the next newer has no pgo data yet.
      ('11.7.8', 2, 12),
      ('11.7.7-pgo', 1, 11),
      ('11.7.7', 1, 11),
    ])
    self.assertEqual(
        choose_revision_to_roll(refs, '11.7.4'),
        (git_hash(2), f'found revision to roll: {git_hash(2)}'))

  def test_revision_to_roll_new_patch(self):
    refs = ref_data([
      ('11.7.9-pgo', 4, 15),
      ('11.7.9', 4, 15),
      ('11.7.8.1-pgo', 3, 13),
      # Patch revision has priority as the next main revision is only 2 hours
      # newer.
      ('11.7.8.1', 3, 13),
      ('11.7.8-pgo', 2, 12),
      ('11.7.8', 2, 12),
      ('11.7.7-pgo', 1, 11),
      ('11.7.7', 1, 11),
    ])
    self.assertEqual(
        choose_revision_to_roll(refs, '11.7.8'),
        (git_hash(3), f'found revision to roll: {git_hash(3)}'))

  def test_revision_to_roll_new_patch_in_the_works(self):
    refs = ref_data([
      ('11.7.9-pgo', 4, 15),
      ('11.7.9', 4, 15),
      # Patch revision has priority, but is not ready yet due to missing pgo.
      # So we roll nothing.
      ('11.7.8.1', 3, 13),
      ('11.7.8-pgo', 2, 12),
      ('11.7.8', 2, 12),
      ('11.7.7-pgo', 1, 11),
      ('11.7.7', 1, 11),
    ])
    self.assertEqual(
        choose_revision_to_roll(refs, '11.7.8'),
        (None, f'waiting for pgo data for: {git_hash(3)}'))

  def test_revision_to_roll_old_patch(self):
    refs = ref_data([
      ('11.7.9-pgo', 4, 19),
      ('11.7.9', 4, 19),
      ('11.7.8.1-pgo', 3, 13),
      # This patch revision is too old, there's a new enough main revision
      # available with pgo data.
      ('11.7.8.1', 3, 13),
      ('11.7.8-pgo', 2, 12),
      ('11.7.8', 2, 12),
      ('11.7.7-pgo', 1, 11),
      ('11.7.7', 1, 11),
    ])
    self.assertEqual(
        choose_revision_to_roll(refs, '11.7.8'),
        (git_hash(4), f'found revision to roll: {git_hash(4)}'))

  def test_revision_to_roll_nothing_new(self):
    refs = ref_data([
      ('11.9.17.9-pgo', 5, 3),
      ('11.9.17.9', 5, 3),
      ('11.9.17.8-pgo', 4, 2),
      ('11.9.17.8', 4, 2),
      ('11.10.10-pgo', 3, 2),
      # Nothing newer than this, the rest above has newer timestamp,
      # but older version (i.e. beta).
      ('11.10.10', 3, 2),
      ('11.10.9.1-pgo', 2, 1),
      ('11.10.9.1', 2, 1),
      ('11.10.9-pgo', 1, 0),
      ('11.10.9', 1, 0),
    ])
    self.assertEqual(
        choose_revision_to_roll(refs, '11.10.10'),
        (None, 'found no newer revision than: 11.10.10'))

  def test_revision_to_roll_no_pgo(self):
    refs = ref_data([
      ('11.9.17.9-pgo', 5, 3),
      ('11.9.17.9', 5, 3),
      ('11.9.17.8-pgo', 4, 2),
      ('11.9.17.8', 4, 2),
      # This is a newer revision, but it has no pgo data ready yet.
      ('11.10.10', 3, 2),
      ('11.10.9.1-pgo', 2, 1),
      ('11.10.9.1', 2, 1),
      ('11.10.9-pgo', 1, 0),
      ('11.10.9', 1, 0),
    ])
    self.assertEqual(
        choose_revision_to_roll(refs, '11.10.9.1'),
        (None, 'found no newer revision than: 11.10.9.1'))

  def test_revision_to_roll_on_branch_day(self):
    refs = ref_data([
      ('11.8.1-pgo', 4, 15),
      ('11.8.1', 4, 15),
      # On branch day, patches of the previous major branch look like patched
      # rolls. This test ensures they are ignored as soon as a new major
      # version (here 11.8) appears.
      ('11.7.8.1', 3, 13),
      ('11.7.8-pgo', 2, 12),
      ('11.7.8', 2, 12),
    ])
    self.assertEqual(
        choose_revision_to_roll(refs, '11.7.8'),
        (git_hash(4), f'found revision to roll: {git_hash(4)}'))

  def test_revision_to_roll_errors(self):
    refs = ref_data([
      ('11.10', 2 ,1),
      ('11.9', 1, 0),
    ])
    with self.api.assertions.assertRaises(AssertionError):
      choose_revision_to_roll(refs, '11.7.8')


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
