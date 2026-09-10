# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import datetime


def size(test, key):
  def rec(value, units):
    if value < 1024 or units[0] == 'PB':
      return f'{value:.{1}f} {units[0]}'
    return rec(value / 1024, units[1:])

  return rec(float(test.get(key, 0)), ['B', 'kB', 'MB', 'GB', 'TB', 'PB'])


def rss(test):
  return size(test, 'max_rss')


def vms(test):
  return size(test, 'max_vms')


def duration(test):
  delta = datetime.timedelta(seconds=test['duration'])
  time = (datetime.datetime.min + delta).time()
  return time.strftime('%M:%S:') + '%03i' % int(time.microsecond / 1000)


def test_details(test, format_key, format_value):
  return [
    f'Test: {test["name"]}',
    f'Flags: {" ".join(test["flags"])}',
    f'Command: {test["command"]}',
    f'Variant: {test["variant"]}',
    f'{format_key}: {format_value}',
    '',
  ]


def top_tests(test_list, format_fun, format_key, status_key):
  lines = []
  for test in test_list:
    suffix = ' *' if test.get(status_key) is False else ''
    lines.append(f'{format_fun(test)} {test["name"]}{suffix}')

  # Execution details.
  lines.extend(['', 'Details:', ''])
  for test in test_list:
    lines.extend(test_details(test, format_key, format_fun(test)))
  return lines
