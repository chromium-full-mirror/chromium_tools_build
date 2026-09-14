# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from .build import BBBuild

from functools import cached_property

from PB.go.chromium.org.luci.buildbucket.proto.builds_service import (
  BuildPredicate,
)


class GerritCL:
  def __init__(self, cl_dict, host, api):
    self.host = host
    self.api = api
    self.project = self.revisions = self.subject = None

    # Ger rid of ugly key name
    self.number = cl_dict.pop('_number')

    self.__dict__.update(cl_dict)

  def cq_blocking_builds(self):
    return [build for build in self.builds if build.is_cq_build()]

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
    return any(build.has_failed() for build in self.cq_blocking_builds())

  @property
  def as_query_dict(self):
    return dict(
      host=self.host,
      change=self.number,
      patchset=self.last_patch_number,
      project=self.project,
    )

  @property
  def full_path(self):
    return f'https://{self.host}/c/{self.short_path}'

  @property
  def short_path(self):
    return f'{self.project}/+/{self.number}'

  @property
  def presentation_links(self):
    return {self.short_path: self.full_path}

  @cached_property
  def builds(self):
    bb_builds = self.api.buildbucket.search(
      BuildPredicate(
        gerrit_changes=[self.as_query_dict],
        include_experimental=True,
      ),
      limit=100,
      fields=['tags,status'],
      report_build=False,
    )
    return [BBBuild(b) for b in bb_builds]

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
