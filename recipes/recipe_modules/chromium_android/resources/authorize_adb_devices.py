#!/usr/bin/env python3
# Copyright 2015 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Push host key onto Android devices using public userdebug key.

If the device has the chrome-infrastructure private adb key on it, use
ADB_VENDOR_KEYS to push the host-specific private key onto the devices that are
not authorized.
"""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
import tempfile

ADB_KEYS_PATH = '/data/misc/adb/adb_keys'


def GetCmdOutput(cmd, env=None):
  if env:
    logging.debug('%s (env=%s)' % (' '.join(cmd), env))
  else:
    logging.debug(' '.join(cmd))
  env = {} if not env else env
  env['HOME'] = os.environ['HOME']
  stdout, stderr = subprocess.Popen(
    cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env
  ).communicate()
  output = stdout.decode('utf-8') + stderr.decode('utf-8')
  return output


def PushHostPubkey(device, adb, env, public_keys_set):
  GetCmdOutput([adb, 'devices'], env)
  GetCmdOutput([adb, '-s', device, 'root'], env)
  adb_ls_output = GetCmdOutput(
    [adb, '-s', device, 'shell', 'ls', ADB_KEYS_PATH], env
  )
  # If adb_keys file exists on device add its contents to the public keys set.
  if adb_ls_output == ADB_KEYS_PATH:
    dev_keys = GetCmdOutput(
      [adb, '-s', device, 'shell', 'cat', ADB_KEYS_PATH], env
    ).splitlines()
    public_keys_set.update(dev_keys)

  with tempfile.NamedTemporaryFile() as f:
    f.write(''.join('%s\n' % pk for pk in public_keys_set))
    f.flush()
    GetCmdOutput([adb, '-s', device, 'push', f.name, ADB_KEYS_PATH], env)


def GetUnauthorizedDevices(adb):
  output = GetCmdOutput([adb, 'devices']).splitlines()
  return [line.split('\t')[0] for line in output if '\tunauthorized' in line]


def main(argv):
  parser = argparse.ArgumentParser()
  parser.add_argument('--adb-path', help='Path to adb binary.', default='adb')
  parser.add_argument(
    '--adb-keys-dir',
    help='Point to directory that contains adb keys.',
    default=os.path.join(os.environ['HOME'], '.android'),
  )
  parser.add_argument(
    '-v',
    '--verbose',
    action='store_true',
    help='turn on extra debugging information',
  )

  options = parser.parse_args()

  logging.basicConfig(level=logging.DEBUG if options.verbose else logging.INFO)

  dir_contents = os.listdir(options.adb_keys_dir)
  adb_path = options.adb_path
  private_key_files = [
    os.path.join(options.adb_keys_dir, key)
    for key in dir_contents
    if key.endswith('adbkey')
  ]
  public_key_files = [
    os.path.join(options.adb_keys_dir, key)
    for key in dir_contents
    if key.endswith('adbkey.pub')
  ]
  public_keys_set = set()
  for public_key_file in public_key_files:
    with open(public_key_file, 'r') as f:
      public_keys_set.add(f.read().strip())

  # Kill server and find unauthorized devices without ADB_VENDOR_KEYS
  GetCmdOutput([adb_path, 'kill-server']).splitlines()
  unauthorized_devices = GetUnauthorizedDevices(adb_path)
  logging.debug('Unauthorized devices: %s' % unauthorized_devices)

  # Kill server launched with ADB_VENDOR_KEYS
  GetCmdOutput([adb_path, 'kill-server']).splitlines()
  env = (
    {'ADB_VENDOR_KEYS': ':'.join(private_key_files)}
    if private_key_files
    else {}
  )
  for device in unauthorized_devices:
    logging.debug('Attempting to authorize device %s' % device)
    PushHostPubkey(device, adb_path, env, public_keys_set)


if __name__ == '__main__':
  sys.exit(main(sys.argv))
