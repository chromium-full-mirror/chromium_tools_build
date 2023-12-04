# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from . import commons
from abc import ABC, abstractmethod
from contextlib import contextmanager


class RollHandler(ABC):

  def __init__(self, module, autoroller_config):
    self.module = module
    self.api = module.m
    self.config = autoroller_config
    self.enabled = True
    self.add_new_files = False

  def roll(self):
    if self.enabled:
      with self.api.step.nest(f'Update {self.name()} deps') as step:
        with self.roll_contex():
          step.presentation.step_text = self.summary()
          self.abandon_active_cls()
          commons.discard_local_changes(self.api)
          changes = self.apply_changes()
          cl_link = commons.upload_cl(
              self.api,
              subject=self.get_subject(),
              upload_flags=self.upload_flags(),
              commit_msg_lines=self.commit_msg_lines(changes),
              bugs_label=self.config.get('bugs', None),
              add=self.add_new_files,
          )
          if cl_link:
            step.presentation.links['CL'] = cl_link
            self.module.summary.append(self.summary())

  @contextmanager
  def roll_contex(self):
    with self.api.context(
        cwd=self.api.path['checkout']), self.api.depot_tools.on_path():
      yield

  def abandon_active_cls(self):
    """Ensure no other active roll exists. If it does, abandon the old one."""
    target_config = self.config['target_config']

    commits = self.api.gerrit.get_changes(
        target_config['gerrit_base_url'],
        query_params=[
            ('project', target_config['project_name']),
            ('owner', target_config['account']),
            ('status', 'open'),
            ('subject', f'"{self.get_subject()}"'),
        ],
        limit=20,
        step_test_data=self.api.gerrit.test_api.get_empty_changes_response_data,
    )

    # Querying gerrit with a subject is not exact, so filter the results for
    # precise match.
    commits = [c for c in commits if c['subject'] == self.get_subject()]

    for commit in commits:
      self.api.gerrit.abandon_change(target_config['gerrit_base_url'],
                                     commit['_number'], 'stale roll')

      step_result = self.api.step('Previous roll failed', cmd=None)
      step_result.presentation.step_text = 'Notify sheriffs!'
      step_result.presentation.status = 'FAILURE'

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
    return []

  @abstractmethod
  def commit_msg_lines(self, changes):
    pass  # pragma: no cover
