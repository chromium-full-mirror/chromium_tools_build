# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from . import commons
from .handler_base import RollHandler

CFT_LKGR_URL = 'https://googlechromelabs.github.io/chrome-for-testing/last-known-good-versions.json'
CFT_PIN_CL_SUBJECT = 'Update Chrome (for Testing) PIN'
CHROME_VAR = 'chrome'


class CfTPinRollHandler(RollHandler):
  """Chrome for Testing pin roller."""

  def name(self):
    return 'chromium pin'

  def summary(self):
    return f'1 {self.name()}'

  def apply_changes(self):
    current_value = self.current_raw_value(self.api)
    new_value = self.get_latest_version(self.api)
    needs_update = self.should_roll(current_value, new_value)
    if needs_update:
      self.api.gclient(
        f'set {CHROME_VAR} deps', ['setdep', f'--var={CHROME_VAR}={new_value}']
      )
      return f'Chromium pin updated to {new_value}'
    return None

  def current_raw_value(self, api):
    try:
      step_result = api.gclient(
        f'get {CHROME_VAR} deps',
        ['getdep', f'--var={CHROME_VAR}'],
        stdout=api.raw_io.output_text(''),
      )
      # The first line contains the commit position number. Strip the rest.
      return step_result.stdout.strip().splitlines()[0].strip()
    except Exception:
      api.step.empty(f'Failed get dep {CHROME_VAR}')
      return None  # Ensure no roll attempt

  def get_latest_version(self, api):
    return api.url.get_json(
      CFT_LKGR_URL,
      step_name=f'check latest {CHROME_VAR}',
      default_test_data={'channels': {'Canary': {'version': '123.0.4500.6'}}},
    ).output['channels']['Canary']['version']

  def version_tuple(self, version):
    return tuple(map(int, version.split('.')))

  def should_roll(self, current, latest):
    return current and (
      self.version_tuple(current) < self.version_tuple(latest)
    )

  def get_subject(self):
    return CFT_PIN_CL_SUBJECT

  def upload_flags(self):
    return ['--set-bot-commit', '--use-commit-queue']

  def commit_msg_lines(self, changes):
    lines = []
    if changes:
      lines.append(changes)
    lines.append(commons.roll_origin_line(self.api))
    return lines
