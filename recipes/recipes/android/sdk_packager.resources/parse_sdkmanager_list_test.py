#!/usr/bin/env python3
# Copyright 2019 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import os
import sys
import unittest

THIS_DIR = os.path.dirname(__file__)

sys.path.insert(0, THIS_DIR)
import parse_sdkmanager_list


def _format_package_line(name, version, description=None, update_version=None):
  parts = [f'  {name:<88}']
  if update_version:
    parts.append(f'{version:<21}->        {update_version:<9}')
  else:
    parts.append(f'{version:<23}')
  if description:
    parts.append(f'{description:<80}')
  return ''.join(parts)


class PackageLineReTest(unittest.TestCase):

  def testValid(self):
    line = _format_package_line('emulator', '37.1.11', 'Android Emulator')
    m = parse_sdkmanager_list.PACKAGE_LINE_RE.match(line)
    self.assertTrue(m)
    self.assertEqual('emulator', m.group(1))
    self.assertEqual('37.1.11', m.group(2))
    self.assertIsNone(m.group(3))
    self.assertEqual('Android Emulator', m.group(4).strip())

  def testValidWithArrow(self):
    line = _format_package_line(
        'emulator', '31.2.10', 'Android Emulator', update_version='37.1.11')
    m = parse_sdkmanager_list.PACKAGE_LINE_RE.match(line)
    self.assertTrue(m)
    self.assertEqual('emulator', m.group(1))
    self.assertEqual('31.2.10', m.group(2))
    self.assertEqual('37.1.11', m.group(3))
    self.assertEqual('Android Emulator', m.group(4).strip())

  def testValidWithTabs(self):
    line = '\temulator\t37.1.11\tAndroid Emulator'
    m = parse_sdkmanager_list.PACKAGE_LINE_RE.match(line)
    self.assertTrue(m)
    self.assertEqual('emulator', m.group(1))
    self.assertEqual('37.1.11', m.group(2))
    self.assertEqual('Android Emulator', m.group(4).strip())

  def testValidWithTabsAndArrow(self):
    line = '\temulator\t31.2.10\t->\t37.1.11\tAndroid Emulator'
    m = parse_sdkmanager_list.PACKAGE_LINE_RE.match(line)
    self.assertTrue(m)
    self.assertEqual('emulator', m.group(1))
    self.assertEqual('31.2.10', m.group(2))
    self.assertEqual('37.1.11', m.group(3))
    self.assertEqual('Android Emulator', m.group(4).strip())

  def testValidWithVersionPlusCount(self):
    line = _format_package_line('platforms/android-34', '3.0.0 (+1)',
                                'Android SDK Platform 34')
    m = parse_sdkmanager_list.PACKAGE_LINE_RE.match(line)
    self.assertTrue(m)
    self.assertEqual('platforms/android-34', m.group(1))
    self.assertEqual('3.0.0', m.group(2))
    self.assertEqual('Android SDK Platform 34', m.group(4).strip())

  def testValidWithoutDescription(self):
    line = '  emulator  37.1.11'
    m = parse_sdkmanager_list.PACKAGE_LINE_RE.match(line)
    self.assertTrue(m)
    self.assertEqual('emulator', m.group(1))
    self.assertEqual('37.1.11', m.group(2))
    self.assertIsNone(m.group(4))

  def testValidWithArrowWithoutDescription(self):
    line = '  emulator  31.2.10  ->  37.1.11'
    m = parse_sdkmanager_list.PACKAGE_LINE_RE.match(line)
    self.assertTrue(m)
    self.assertEqual('emulator', m.group(1))
    self.assertEqual('31.2.10', m.group(2))
    self.assertEqual('37.1.11', m.group(3))
    self.assertIsNone(m.group(4))


class ParseSdkListTest(unittest.TestCase):

  def testSingleAvailablePackage(self):
    raw = '\n'.join([
        'Available packages:',
        _format_package_line('emulator', '37.1.11', 'Android Emulator'),
    ])
    result = parse_sdkmanager_list.ParseSdkList(raw)
    expected = {
        'available': [{
            'name': 'emulator',
            'description': 'Android Emulator',
            'version': '37.1.11',
        },],
        'installed': [],
    }
    self.assertEqual(expected, result)

  def testMultipleAvailablePackages(self):
    raw = '\n'.join([
        'Available packages:',
        _format_package_line('build-tools/36.0.0', '36.0.0',
                             'Android SDK Build-Tools 36'),
        _format_package_line('emulator', '37.1.11', 'Android Emulator'),
    ])
    result = parse_sdkmanager_list.ParseSdkList(raw)
    expected = {
        'available': [
            {
                'name': 'build-tools/36.0.0',
                'description': 'Android SDK Build-Tools 36',
                'version': '36.0.0',
            },
            {
                'name': 'emulator',
                'description': 'Android Emulator',
                'version': '37.1.11',
            },
        ],
        'installed': [],
    }
    self.assertEqual(expected, result)

  def testSingleInstalledPackage(self):
    raw = '\n'.join([
        'Installed packages:',
        _format_package_line('emulator', '37.1.11', 'Android Emulator'),
    ])
    result = parse_sdkmanager_list.ParseSdkList(raw)
    expected = {
        'available': [],
        'installed': [{
            'name': 'emulator',
            'description': 'Android Emulator',
            'version': '37.1.11',
        },],
    }
    self.assertEqual(expected, result)

  def testInstalledPackageWithUpdate(self):
    raw = '\n'.join([
        'Installed packages:',
        _format_package_line(
            'emulator', '31.2.10', 'Android Emulator',
            update_version='37.1.11'),
    ])
    result = parse_sdkmanager_list.ParseSdkList(raw)
    expected = {
        'available': [],
        'installed': [{
            'name': 'emulator',
            'description': 'Android Emulator',
            'version': '31.2.10',
        },],
    }
    self.assertEqual(expected, result)

  def testMultipleInstalledPackages(self):
    raw = '\n'.join([
        'Installed packages:',
        _format_package_line('build-tools/36.0.0', '36.0.0',
                             'Android SDK Build-Tools 36'),
        _format_package_line(
            'emulator', '31.2.10', 'Android Emulator',
            update_version='37.1.11'),
    ])
    result = parse_sdkmanager_list.ParseSdkList(raw)
    expected = {
        'available': [],
        'installed': [
            {
                'name': 'build-tools/36.0.0',
                'description': 'Android SDK Build-Tools 36',
                'version': '36.0.0',
            },
            {
                'name': 'emulator',
                'description': 'Android Emulator',
                'version': '31.2.10',
            },
        ],
    }
    self.assertEqual(expected, result)

  def testAvailableAndInstalledPackages(self):
    raw = '\n'.join([
        'Installed packages:',
        _format_package_line('build-tools/36.0.0', '36.0.0',
                             'Android SDK Build-Tools 36'),
        '',
        'Available packages:',
        _format_package_line('emulator', '37.1.11', 'Android Emulator'),
    ])
    result = parse_sdkmanager_list.ParseSdkList(raw)
    expected = {
        'available': [{
            'name': 'emulator',
            'description': 'Android Emulator',
            'version': '37.1.11',
        },],
        'installed': [{
            'name': 'build-tools/36.0.0',
            'description': 'Android SDK Build-Tools 36',
            'version': '36.0.0',
        },],
    }
    self.assertEqual(expected, result)

  def testPackageWithVersionPlusCount(self):
    raw = '\n'.join([
        'Available packages:',
        _format_package_line('extras/google/Android_Emulator_Hypervisor_Driver',
                             '2.2.0 (+1)',
                             'Android Emulator hypervisor driver (installer)'),
        _format_package_line('ndk-bundle', '22.1.7171670 (+13)', 'NDK'),
    ])
    result = parse_sdkmanager_list.ParseSdkList(raw)
    expected = {
        'available': [
            {
                'name': 'extras/google/Android_Emulator_Hypervisor_Driver',
                'description': 'Android Emulator hypervisor driver (installer)',
                'version': '2.2.0',
            },
            {
                'name': 'ndk-bundle',
                'description': 'NDK',
                'version': '22.1.7171670',
            },
        ],
        'installed': [],
    }
    self.assertEqual(expected, result)

  def testUpdatesIgnored(self):
    raw = '\n'.join([
        'Installed packages:',
        _format_package_line('emulator', '31.2.10', 'Android Emulator'),
        '',
        'Available packages:',
        _format_package_line('emulator', '37.1.11', 'Android Emulator'),
        '',
        'Available updates:',
        _format_package_line('emulator', '31.2.10', update_version='37.1.11'),
    ])
    result = parse_sdkmanager_list.ParseSdkList(raw)
    expected = {
        'available': [{
            'name': 'emulator',
            'description': 'Android Emulator',
            'version': '37.1.11',
        },],
        'installed': [{
            'name': 'emulator',
            'description': 'Android Emulator',
            'version': '31.2.10',
        },],
    }
    self.assertEqual(expected, result)

  def testUnrecognizedLinesIgnored(self):
    raw = '\n'.join([
        'Some banner text',
        'Installed packages:',
        _format_package_line('emulator', '37.1.11', 'Android Emulator'),
    ])
    result = parse_sdkmanager_list.ParseSdkList(raw)
    expected = {
        'available': [],
        'installed': [{
            'name': 'emulator',
            'description': 'Android Emulator',
            'version': '37.1.11',
        },],
    }
    self.assertEqual(expected, result)

  def testListAllFile(self):
    list_all_path = os.path.join(THIS_DIR, 'list_all.txt')
    if os.path.exists(list_all_path):
      with open(list_all_path) as f:
        result = parse_sdkmanager_list.ParseSdkList(f.read())
      self.assertEqual(6, len(result['installed']))
      self.assertEqual(615, len(result['available']))
      self.assertEqual('emulator', result['installed'][2]['name'])
      self.assertEqual('31.2.10', result['installed'][2]['version'])


if __name__ == '__main__':
  unittest.main(buffer=True)
