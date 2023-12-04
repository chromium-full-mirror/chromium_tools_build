# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from . import commons
from .handler_base import RollHandler
from collections import namedtuple


class SriptedRollsFactory:
  SupportedScript = namedtuple('SupportedScript', ['title', 'exe', 'args'])

  def __init__(self, autoroller_config):
    self.config = autoroller_config

  def supported(self):
    """Returns a dict of supported scripted rolls. The key is the script key
    and the value is a tuple of the title and the path elements to the script.
    """
    return {
        'puppeteer-core':
            SriptedRollsFactory.SupportedScript(
                'Puppeteer Core', 'scripts/deps/roll_front_end_third_party.py',
                ['puppeteer-core', 'puppeteer', 'lib/esm']),
        'puppeteer-replay':
            SriptedRollsFactory.SupportedScript(
                'Puppeteer Replay',
                'scripts/deps/roll_front_end_third_party.py',
                ['@puppeteer/replay', 'puppeteer-replay', 'lib']),
        'browser-protocol':
            SriptedRollsFactory.SupportedScript(
                'Browser Protocol', 'scripts/deps/roll_deps.py', [
                    '--ref', 'working-tree', '{{CHROMIUM_DIR}}',
                    '{{DEVTOOLS_DIR}}'
                ]),
        # Add more scripts here
    }

  def get_rollers(self, module):
    script_keys = self.config.get('scripted_rolls', [])
    return [
        ScriptedRollHandler(module, self.config,
                            self.supported()[key], key) for key in script_keys
    ]


class ScriptedRollHandler(RollHandler):

  def __init__(self, module, autoroller_config, script, key):
    super().__init__(module, autoroller_config)
    self.add_new_files = True
    self.script = script
    self.key = key
    self.updated = False

  def name(self):
    return self.script.title

  def apply_changes(self):
    args = [self.resolve_arg(a) for a in self.script.args]
    self.api.step(f'Run {self.name()} script', [
        'python3', '-u',
        self.api.path['checkout'].join(*self.script.exe.split('/')), *args
    ])

  def resolve_arg(self, arg):
    if arg == '{{CHROMIUM_DIR}}':
      return self.api.path['cache'].join('builder', 'src')
    if arg == '{{DEVTOOLS_DIR}}':
      return self.api.path['checkout']
    return arg

  def get_subject(self):
    return f'Roll {self.key}'

  def commit_msg_lines(self, _):
    return commons.commit_msg_lines_w_reviewes([
        'In case of failures or errors, reach out to someone from '
        'config/owner/RECORDER_OWNERS.',
        commons.roll_origin_line(self.api)
    ], self.config['reviewers'])

  def summary(self):
    return self.name()
