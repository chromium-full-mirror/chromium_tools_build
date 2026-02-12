# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from . import commons
from .handler_base import RollHandler
from collections import namedtuple


SupportedScript = namedtuple(
    'SupportedScript', ['title', 'exe', 'args', 'message', 'bot_commit'],
    defaults=[None, None, None, None, False])
"""A dict of supported scripted rolls. The key is the script key and the value
is a tuple of the title and the path elements to the script.
"""
SUPPORTED_SCRIPTS = {
    'puppeteer-core':
        SupportedScript(
            'Puppeteer Core', 'scripts/deps/roll_front_end_third_party.py',
            ['puppeteer-core', 'puppeteer', 'lib/esm'],
            'In case of failures or errors, reach out to someone from '
            'config/owner/RECORDER_OWNERS.'),
    'puppeteer-replay':
        SupportedScript(
            'Puppeteer Replay', 'scripts/deps/roll_front_end_third_party.py',
            ['@puppeteer/replay', 'puppeteer-replay', 'lib'],
            'In case of failures or errors, reach out to someone from '
            'config/owner/RECORDER_OWNERS.'),
    'browser-protocol & CfT':
        SupportedScript(
            'Browser Protocol & CfT', 'scripts/deps/roll_deps.py', [
                '--ref', 'CfT', '{{CHROMIUM_DIR}}', '{{DEVTOOLS_DIR}}',
                '--update-node', '--output', '{{OUTPUT_JSON}}'
            ], 'Rolling CfT pin toghether with browser-protocol files: '
            'https://chromium.googlesource.com/chromium/src/+log/{old_revision}..{new_revision}\n'
            'In case of failures or errors, reach out to someone from '
            'config/owner/COMMON_OWNERS.', True),
    # Add more scripts here
}


def get_rollers(api_module, source_dir, autoroller_config, script_keys):
  return [
      ScriptedRollHandler(
          api_module,
          source_dir,
          autoroller_config,
          SUPPORTED_SCRIPTS[key],
          key,
      ) for key in script_keys
  ]


class ScriptedRollHandler(RollHandler):

  def __init__(self, module, source_dir, autoroller_config, script, key):
    super().__init__(module, source_dir, autoroller_config)
    self.add_new_files = True
    self.script = script
    self.key = key
    self.updated = False
    self.output_file = None

  def upload_flags(self):
    flags = ['--dry-run']

    if self.script.bot_commit:
      flags += ['--set-bot-commit']

    return flags

  def name(self):
    return self.script.title

  def apply_changes(self):
    args = [self.resolve_arg(a) for a in self.script.args]
    self.api.step(f'Run {self.name()} script',
                  ['python3', '-u', self.source_dir / self.script.exe, *args])

  def resolve_arg(self, arg):
    if arg == '{{CHROMIUM_DIR}}':
      return self.api.path.cache_dir.joinpath('builder', 'src')
    if arg == '{{DEVTOOLS_DIR}}':
      return self.source_dir
    if arg == '{{OUTPUT_JSON}}':
      self.output_file = self.api.path.cleanup_dir.joinpath('roll_output.json')
      return self.output_file
    return arg

  def get_subject(self):
    return f'Roll {self.key}'

  def commit_msg_lines(self, _):
    message = self.script.message
    if self.output_file and self.api.path.exists(self.output_file):
      output_properties = self.api.file.read_json('Read roll output',
                                                  self.output_file)
      message = message.format(**output_properties)
    return commons.commit_msg_lines_w_reviewes(
        [message, commons.roll_origin_line(self.api)],
        self.config.get('manual_roll_reviewers'))

  def summary(self):
    return self.name()
