#!/usr/bin/env vpython3
# Copyright 2022 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import argparse
import os
import sys
import unittest

from pyfakefs.fake_filesystem_unittest import TestCase

SCRIPTS_DIR = os.path.normpath(os.path.join(__file__, '..', '..'))

sys.path.append(SCRIPTS_DIR)

import generate_groupings


class ParseError(Exception):
  pass


class ArgumentParser(argparse.ArgumentParser):
  """Test version of ArgumentParser

  This behaves the same as argparse.ArgumentParser except that the error
  method raises an instance of ParseError rather than printing output
  and exiting. This simplifies testing for error conditions and puts the
  actual error information in the traceback for unexpectedly failing
  tests.
  """

  def error(self, message):
    raise ParseError(message)


class GenerateGroupingsUnitTest(TestCase):
  def setUp(self):
    self.setUpPyfakefs()

  def test_parse_args_empty(self):
    args = generate_groupings.parse_args(
      ['foo', 'bar', 'baz'], parser_type=ArgumentParser
    )

    self.assertEqual(args.func, generate_groupings.generate_groupings)
    self.assertEqual(
      args.groupings_dir, generate_groupings.DEFAULT_GROUPINGS_DIR
    )
    self.assertCountEqual(args.projects, ['foo', 'bar', 'baz'])

  def test_parse_args_validate(self):
    args = generate_groupings.parse_args(
      ['--validate', 'foo'], parser_type=ArgumentParser
    )

    self.assertEqual(args.func, generate_groupings.validate_groupings)

  def test_parse_args_groupings_dir(self):
    args = generate_groupings.parse_args(
      ['--groupings-dir', 'fake-groupings-dir', 'foo'],
      parser_type=ArgumentParser,
    )

    self.assertEqual(args.groupings_dir, 'fake-groupings-dir')

  def test_generate_groupings(self):
    args = generate_groupings.parse_args(
      ['--groupings-dir', '/fake-groupings-dir', 'foo', 'bar', 'baz'],
      parser_type=ArgumentParser,
    )
    calls = []

    def generator(project, output_path):
      calls.append((project, output_path))

    generate_groupings.generate_groupings(args, groupings_generator=generator)

    self.assertCountEqual(
      calls,
      [
        ('foo', '/fake-groupings-dir/foo.json'),
        ('bar', '/fake-groupings-dir/bar.json'),
        ('baz', '/fake-groupings-dir/baz.json'),
      ],
    )

  def test_validate_groupings_failure(self):
    args = generate_groupings.parse_args(
      ['--groupings-dir', '/fake-groupings-dir', 'foo', 'bar', 'baz'],
      parser_type=ArgumentParser,
    )
    self.fs.create_file(
      '/fake-groupings-dir/foo.json', contents='"contents for foo"'
    )
    self.fs.create_file(
      '/fake-groupings-dir/bar.json', contents='"old contents for bar"'
    )

    def generator(project, output_path):
      with open(output_path, 'w') as f:
        f.write(f'"contents for {project}"')

    with self.assertRaises(generate_groupings.ValidationException) as caught:
      generate_groupings.validate_groupings(args, groupings_generator=generator)

    self.assertCountEqual(caught.exception.projects, ['bar', 'baz'])

  def test_validate_groupings_success(self):
    args = generate_groupings.parse_args(
      ['--groupings-dir', '/fake-groupings-dir', 'foo', 'bar', 'baz'],
      parser_type=ArgumentParser,
    )
    self.fs.create_file(
      '/fake-groupings-dir/foo.json', contents='"contents for foo"'
    )
    self.fs.create_file(
      '/fake-groupings-dir/bar.json', contents='"contents for bar"'
    )
    self.fs.create_file(
      '/fake-groupings-dir/baz.json', contents='"contents for baz"'
    )

    def generator(project, output_path):
      with open(output_path, 'w') as f:
        f.write(f'"contents for {project}"')

    generate_groupings.validate_groupings(args, groupings_generator=generator)


if __name__ == '__main__':
  unittest.main()
