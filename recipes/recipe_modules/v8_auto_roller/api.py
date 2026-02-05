# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from functools import cached_property

from recipe_engine import recipe_api

from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb2
from PB.recipe_engine import result as result_pb2

from .cl_manager import CLManager, PatchPriorityCLManager
from .commons import discard_local_changes
from .deps_handlers import (TrustedRollHandler, UntrustedRollHandler,
                            get_dep_updates)
from .chrome_handler import CfTPinRollHandler
from .handler_base import DummyRollHandler
from .test262_handler import Test262ImportHandler
from .script_handlers import get_rollers

BASE_URL = 'https://chromium.googlesource.com/'

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
    self.failures = []

  def setup_target(
      self,
      solution_name,
      target_url,
      requires_chromium_checkout=False,
  ):
    with self.m.step.nest('Setup'):
      gclient_config = self.m.gclient.make_config()
      soln = gclient_config.solutions.add()
      soln.name = solution_name
      soln.url = target_url
      soln.revision = 'HEAD'

      self.m.gclient.c = gclient_config
      if requires_chromium_checkout:
        self.m.gclient.apply_config('chromium')

      # Allow rolling all os deps.
      self.m.gclient.c.target_os.add('android')
      self.m.gclient.c.target_os.add('win')

      # NOTE: Besides the name, this actually does a checkout of the first
      # solution defined in gclient (autoroller_config -> target_config ->
      # solution_name), and might be something else, e.g. devtools-frontend.
      update_result = self.m.v8.checkout(
          ignore_input_commit=True, set_output_commit=False)

      # Some builders require a chromium checkout. If that's not required,
      # rollers usually need chromium's DEPS file. We store it at the same
      # location as a checkout to avoid further tweakings of the process.
      if not requires_chromium_checkout:
        self._download_chromium_deps_file()

      return update_result

  def _download_chromium_deps_file(self):
    revision = self.m.gerrit.get_gerrit_branch(
        'https://chromium-review.googlesource.com/',
        'chromium/src',
        'refs/heads/main',
        step_test_data=lambda: self.m.json.test_api.output({
            'ref': 'refs/heads/main',
            'revision': 'deadbeef',
        }),
    )
    deps = self.m.gitiles.download_file(
        'https://chromium.googlesource.com/chromium/src',
        'DEPS',
        revision,
        step_test_data=lambda: self.m.gitiles.test_api.make_encoded_file(''),
    )
    chromium_path = self.m.v8.checkout_root / 'src'
    self.m.file.ensure_directory('ensure chromium cache dir', chromium_path)

    chromium_deps_file = chromium_path / 'DEPS'
    self.m.file.write_text('Store src/DEPS', chromium_deps_file, deps)

  def build_cl_manager(self,
                       source_dir,
                       bugs=None,
                       patched_cl_has_priority=False,
                       cc=None):
    cls = PatchPriorityCLManager if patched_cl_has_priority else CLManager
    return cls(self.m, source_dir, bugs, cc=cc)

  def report_result(self):
    result = result_pb2.RawResult()
    result.status = common_pb2.FAILURE if self.failures else common_pb2.SUCCESS
    if self.summary:
      result.summary_markdown = 'Updated ' + ', '.join(self.summary) + '.'
    if self.failures:
      result.summary_markdown += 'Failed to update ' + ', '.join(
        self.failures) + '.'
    return result

  def regular_roll(self, autoroller_config, cl_manager, source_dir):
    with self.m.step.nest('Find updated deps') as step_presentation:
      discard_local_changes(self.m, source_dir)
      trusted_updates, untrusted_updates = get_dep_updates(
          self.m, step_presentation, autoroller_config)

    TrustedRollHandler(self, source_dir, autoroller_config,
                       trusted_updates).roll(cl_manager)
    UntrustedRollHandler(self, source_dir, autoroller_config,
                         untrusted_updates).roll(cl_manager)

  def cft_pin_roll(self, autoroller_config, cl_manager, source_dir):
    CfTPinRollHandler(self, source_dir, autoroller_config).roll(cl_manager)

  def test262_roll(self, autoroller_config, cl_manager, source_dir):
    Test262ImportHandler(self, source_dir, autoroller_config).roll(cl_manager)

  def scripted_rolls(self,
                     autoroller_config,
                     cl_manager,
                     source_dir,
                     scripted_keys=None):
    scripted_rollers = get_rollers(self, source_dir, autoroller_config,
                                   scripted_keys or [])
    if scripted_rollers:
      with self.m.step.nest('Scripted rolls'):
        for roller in scripted_rollers:
          roller.roll(cl_manager)

  @cached_property
  def service_account(self):
    output_prefix = "Logged in as "
    step_result = self.m.step(
        'get login info', ['luci-auth', 'info'],
        infra_step=True,
        stdout=self.m.raw_io.output_text(),
        step_test_data=lambda: self.m.raw_io.test_api.stream_output_text(
            f'{output_prefix}account@example.com.', stream='stdout'))
    first_line = step_result.stdout.splitlines()[0]
    extracted_email = first_line[len(output_prefix):-1]
    return extracted_email

  def dummy_roll(self, cl_manager, source_dir):
    return DummyRollHandler(self, source_dir, None).roll(cl_manager)
