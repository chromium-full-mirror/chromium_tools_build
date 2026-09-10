# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from .cl import GerritCL


def just_fail(api, roller, cl, title=None, message=None):
  failure_step = api.step.empty(title or f"Roller '{roller.name}' failed")
  if message:
    failure_step.presentation.step_text = message
  failure_step.presentation.status = api.step.FAILURE
  failure_step.presentation.links.update(cl.presentation_links)
  roller.has_failure = True


def just_pass(api, roller, cl):
  pass


def mark_as_reported(api, roller, cl):
  cl.set_tag("rw_reported", 'Mark as reported')


class Roller:
  def __init__(self, roller_dict, api):
    # Get rid of ugly key name
    self.api = api
    self.host = roller_dict.pop('review-host')

    # Default values
    self.has_failure = False
    self.criteria = []
    self.failure_recovery = ['mark_as_reported', 'just_fail']
    self.gs_bucket = 'devtools-internal-screenshots'
    self.subject = self.project = self.account = None

    # Overwrite defaults with values from the config
    self.__dict__.update(roller_dict)

  def watch_criteria(self):
    return [
      ('project', self.project),
      ('owner', self.account),
    ] + [term.split(':') for term in self.criteria]

  def find_open_cls(self):
    gerrit_response = self.api.gerrit.get_changes(
      f'https://{self.host}',
      query_params=[
        ('status', 'open'),
        ('-hashtag', 'rw_reported'),
      ]
      + self.watch_criteria(),
      o_params=['LABELS', 'CURRENT_REVISION', 'DOWNLOAD_COMMANDS'],
      limit=20,
      step_test_data=self.api.gerrit.test_api.get_empty_changes_response_data,
      name='Find open CLs',
    )
    cls = [GerritCL(cl, self.host, self.api) for cl in gerrit_response]
    return [cl for cl in cls if cl.subject_matches(self.subject)]
