# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import traceback

from . import commons
from abc import ABC, abstractmethod
from contextlib import contextmanager
from recipe_engine import recipe_api


GERRIT_BASE_URL = 'https://chromium-review.googlesource.com'


class RollHandler(ABC):
  def __init__(self, module, source_dir, autoroller_config):
    self.module = module
    self.api = module.m
    self.source_dir = source_dir
    self.config = autoroller_config
    self.add_new_files = False

  def roll(self, cl_manager):
    with self.api.step.nest(f'Update {self.name()} deps') as step:
      try:
        with self.roll_contex(self.source_dir):
          step.step_text = self.summary()
          create_new_cl = cl_manager.abandon_active_cls(self.get_subject())
          if not create_new_cl:
            self.api.step.empty('Found existing roll CL. Skipping.')
            return
          commons.discard_local_changes(self.api, self.source_dir)
          changes = self.apply_changes()
          commit_msg_lines = (
            self.commit_msg_lines(changes) + self.commit_msg_footers()
          )
          cl_link = cl_manager.upload_cl(
            subject=self.get_subject(),
            upload_flags=self.upload_flags(),
            commit_msg_lines=commit_msg_lines,
            add=self.add_new_files,
          )
          if cl_link:
            step.links['CL'] = cl_link
            self.module.summary.append(self.summary())
      except Exception:
        failed = self.api.step.empty('Roll failed')
        failed.presentation.status = self.api.step.FAILURE
        failed.presentation.logs['exception'] = traceback.format_exc()
        self.module.failures.append(self.name())

  @contextmanager
  def roll_contex(self, source_dir):
    with self.api.context(cwd=source_dir), self.api.depot_tools.on_path():
      yield

  @abstractmethod
  def name(self):
    pass  # pragma: no cover

  @abstractmethod
  def summary(self):
    pass  # pragma: no cover

  @abstractmethod
  def apply_changes(self):
    pass  # pragma: no cover

  @abstractmethod
  def get_subject(self):
    pass  # pragma: no cover

  def upload_flags(self):
    return ['--dry-run']

  @abstractmethod
  def commit_msg_lines(self, changes):
    pass  # pragma: no cover

  def commit_msg_footers(self):
    footers = self.config.get('commit_msg_footers', [])
    return [''] + footers if footers else []


class DummyRollHandler(RollHandler):
  def __init__(self, module, source_dir, autoroller_config):
    super().__init__(module, source_dir, autoroller_config)
    self.add_new_files = True

  def name(self):
    return 'dummy'

  def summary(self):
    return 'dummy'

  def apply_changes(self):
    if self.api.properties.get('u_no_pass', False):
      raise recipe_api.InfraFailure('dummy failure')

  def get_subject(self):
    return 'dummy'

  def commit_msg_lines(self, _):
    return ['dummy']

  def commit_msg_footers(self):
    return ['', 'Key: dummy']
