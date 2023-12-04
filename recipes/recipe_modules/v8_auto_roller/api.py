# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import recipe_api

from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb2
from PB.recipe_engine import result as result_pb2

from .commons import discard_local_changes
from .deps_handlers import (TrustedRollHandler, UntrustedRollHandler,
                            handle_failed_deps, get_dep_updates)
from .chrome_handler import CfTPinRollHandler
from .test262_handler import Test262ImportHandler
from .script_handlers import SriptedRollsFactory

BASE_URL = 'https://chromium.googlesource.com/'
GERRIT_BASE_URL = 'https://chromium-review.googlesource.com'


def set_defaults(autoroller_config):
  autoroller_config.setdefault('regular_deps_roller', True)
  autoroller_config.setdefault('roll_test262', False)
  autoroller_config.setdefault('roll_chromium_pin', False)
  autoroller_config.setdefault('scripted_rolls', [])
  target_config = autoroller_config['target_config']
  target_config.setdefault('gerrit_base_url', GERRIT_BASE_URL)
  target_config.setdefault('base_url', BASE_URL)


class V8AutoRoller(recipe_api.RecipeApi):
  """General purpose module for rolling dependencies in Chromium satelite
  projects (V8, DevTools).

  In this context, a dependency is any resource that needs to be updated, copied
  or re-generated in order to keep the project in sync with Chromium and other
  external sources of truth.
  """


  def __init__(self, *args, **kwargs):
    super().__init__(*args, **kwargs)
    self.summary = []


  def setup(self, autoroller_config):
    set_defaults(autoroller_config)

    with self.m.step.nest('Setup'):
      target_config = autoroller_config['target_config']

      gclient_config = self.m.gclient.make_config()
      soln = gclient_config.solutions.add()
      soln.name = target_config['solution_name']
      soln.url = target_config['base_url'] + target_config['project_name']
      soln.revision = 'HEAD'

      self.m.gclient.c = gclient_config
      self.m.gclient.apply_config('chromium')

      # Allow rolling all os deps.
      self.m.gclient.c.target_os.add('android')
      self.m.gclient.c.target_os.add('win')

      # NOTE: Besides the name, this actually does a checkout of the first
      # solution defined in gclient (autoroller_config -> target_config ->
      # solution_name), and might be something else, e.g. devtools-frontend.
      self.m.v8.checkout(ignore_input_commit=True, set_output_commit=False)


  def report_result(self):
    result = result_pb2.RawResult()
    result.status = common_pb2.SUCCESS
    if self.summary:
      result.summary_markdown = 'updated ' + ', '.join(self.summary)
    return result

  def regular_roll(self, autoroller_config):
    if autoroller_config['regular_deps_roller']:
      with self.m.step.nest('Find updated deps'):
        discard_local_changes(self.m)
        trusted_updates, untrusted_updates, failed = get_dep_updates(
            self.m, autoroller_config)

      TrustedRollHandler(self, autoroller_config, trusted_updates).roll()
      UntrustedRollHandler(self, autoroller_config, untrusted_updates).roll()

      with self.m.step.nest('Check failed deps'):
        handle_failed_deps(self.m, failed)

  def cft_pin_roll(self, autoroller_config):
    CfTPinRollHandler(self, autoroller_config).roll()

  def test262_roll(self, autoroller_config):
    Test262ImportHandler(self, autoroller_config).roll()

  def scripted_rolls(self, autoroller_config):
    if autoroller_config['scripted_rolls']:
      with self.m.step.nest('Scripted rolls'):
        factory = SriptedRollsFactory
        for roller in factory(autoroller_config).get_rollers(self):
          roller.roll()
