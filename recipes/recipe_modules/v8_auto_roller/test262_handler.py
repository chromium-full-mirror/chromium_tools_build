# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from . import commons
from .handler_base import RollHandler

CREDS_NAME = 'test262-import-export'
KMS_CRYPTO_KEY = (
  'projects/v8-infra/locations/global/keyRings/%s/cryptoKeys/default'
  % CREDS_NAME
)
V8_TEST262_ROLLS_META_BUG = 'v8:7834'


class Test262ImportHandler(RollHandler):
  def __init__(self, api, source_dir, autoroller_config):
    super().__init__(api, source_dir, autoroller_config)
    self.import_range = None

  def name(self):
    return 'test262 import'

  def apply_changes(self):
    creds = self.api.path.cleanup_dir.joinpath(CREDS_NAME + '.json')
    self.api.cloudkms.decrypt(
      KMS_CRYPTO_KEY,
      self.module.repo_resource(
        'recipes', 'recipes', 'v8', 'assets', CREDS_NAME
      ),
      creds,
    )
    checkout_root = self.api.path.cache_dir / 'builder'
    chromium_path = checkout_root / 'src'
    blink_tools_path = chromium_path.joinpath('third_party', 'blink', 'tools')

    v8_path = checkout_root / 'v8'

    script = v8_path.joinpath('test', 'test262', 'tools', 'import.py')

    with self.api.context(cwd=v8_path), self.api.depot_tools.on_path():
      self.api.v8.git_output('branch', '-D', 'test262_import', ok_ret='any')
      self.api.v8.git_output('new-branch', 'test262_import')
      last_test262_revision = self.api.gclient(
        'get test262 revision',
        ['getdep', '-r', 'test/test262/data'],
        stdout=self.api.raw_io.output_text(),
      ).stdout.strip()

      self.run_import_script(
        creds,
        blink_tools_path,
        script,
        step_name='Import Test262 changes into V8.',
        extra_args=['--phase=PREBUILD'],
      )

      output_lines = self.run_import_script(
        creds,
        blink_tools_path,
        script,
        step_name='Update Test262 status file.',
        extra_args=[
          '--phase=POSTBUILD',
          '--v8-test262-last-revision',
          last_test262_revision,
          # TODO(liviurau): #2 Pass the failures collected at #1 to the
          # script.
          # '--test262-failure-file', failure_file,
        ],
      )
      assert output_lines, 'Step should have output at least one line.'
      self.import_range = output_lines[-1]

  def get_subject(self):
    return '[test262] Roll test262'

  def summary(self):
    return self.name()

  def run_import_script(
    self, creds, blink_tools_path, script, step_name, extra_args
  ):
    args = [
      '--credentials-json',
      creds,
      '--blink-tools-path',
      blink_tools_path,
    ] + extra_args
    return self.api.v8.vpython(
      step_name, script, args, stdout=self.api.raw_io.output_text()
    ).stdout.splitlines()

  def commit_msg_lines(self, _):
    return commons.commit_msg_lines_w_reviewes(
      [
        self.import_range,
        commons.roll_origin_line(self.api),
        'no-export: true',
      ],
      self.config.get('manual_roll_reviewers'),
    )

  def upload_flags(self):
    return ['--set-bot-commit', '--use-commit-queue']
