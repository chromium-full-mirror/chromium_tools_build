# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from functools import cached_property

CV_RUN_STATUS_FAILED = 66


class GerritCL:
  def __init__(self, cl_dict, host, api):
    self.host = host
    self.api = api
    self.project = self.revisions = self.subject = None

    # Ger rid of ugly key name
    self.number = cl_dict.pop('_number')

    self.__dict__.update(cl_dict)

  @property
  def last_patch(self):
    return list(self.revisions.values())[0]

  @property
  def last_patch_number(self):
    return self.last_patch['_number']

  @property
  def git_url(self):
    git_host = self.host.replace('-review', '')
    return f'https://{git_host}/{self.project}'

  @property
  def git_ref(self):
    return self.last_patch['fetch']['http']['ref']

  def subject_matches(self, subject):
    return (not subject) or (subject == self.subject)

  def has_blocking_failures(self):
    return bool(self.failed_cv_run)

  @cached_property
  def failed_cv_run(self):
    search_project = self.project.split('/')[1]
    search_cl = (self.host, self.number)
    cv_runs = self.api.change_verifier.search_runs(search_project, search_cl)

    if not cv_runs:
      self.api.step.empty('No CV runs found')
      return None
    last_cv_run = cv_runs[0]
    if last_cv_run.mode not in ['FULL_RUN', 'DRY_RUN']:
      self.api.step.empty(
        f'Last CV run mode is unsupported: {last_cv_run.mode}.'
      )
      return None
    if not last_cv_run.end_time.ToSeconds():
      self.api.step.empty('Last CV run has not finished yet.')
      return None
    if last_cv_run.status != CV_RUN_STATUS_FAILED:
      self.api.step.empty('Last CV run did not fail.')
      return None
    if last_cv_run.cls[0].patchset != self.last_patch_number:
      self.api.step.empty('Last CV run was not for the latest patchset.')
      return None
    self.api.step.empty('Last CV run failed.')
    return last_cv_run

  @property
  def full_path(self):
    return f'https://{self.host}/c/{self.short_path}'

  @property
  def short_path(self):
    return f'{self.project}/+/{self.number}'

  @property
  def presentation_links(self):
    return {self.short_path: self.full_path}

  def set_tag(self, tag_name, step_name):
    self.api.gerrit.call_raw_api(
      host=f'https://{self.host}',
      path=f'/changes/{self.number}/hashtags',
      method='POST',
      body={"add": [tag_name]},
      accept_statuses=[200, 201],
      name=step_name,
    )

  def add_backlink_comment(self):
    self.api.gerrit.call_raw_api(
      name='add comment',
      method='POST',
      host=f'https://{self.host}',
      path=f'/changes/{self.number}/revisions/current/review',
      body={"message": f'Patch created at {self.api.buildbucket.build_url()}'},
    )

  def prepare_local_checkout(self):
    with self.api.step.nest('Prepare local checkout'):
      api = self.api.v8
      api.git_output('clone', self.git_url, '.')
      api.git_output('fetch', self.git_url, self.git_ref)
      api.git_output('checkout', '-b', 'work-branch', 'FETCH_HEAD')
      api.git_output('cl', 'issue', self.number)
