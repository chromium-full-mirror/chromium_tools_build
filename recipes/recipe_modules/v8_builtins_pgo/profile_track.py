# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from abc import ABC, abstractmethod
from functools import cached_property


class BaseProfileTrack(ABC):
  """
  A track is the process of compiling, generating and uploading the profile
  for a single commit on a track (architecture × platform). This process has
  multiple discrete steps. We want to run these steps in parallel for each
  versions and each track.

  This class manages the state of this process by collecting handlers and
  properties from every step and passes it to the next one.
  """

  def __init__(self, api, track, profiling_pool):
    self.api = api
    self.track = track
    self.profiling_pool = profiling_pool
    self.compilator_handler = None
    self.compilator_properties = None
    self.original_cas_digest = None
    self.profile_task = None
    self.exception = None

  @cached_property
  @abstractmethod
  def id(self):
    pass  # pragma: no cover

  @cached_property
  @abstractmethod
  def name(self):
    pass  # pragma: no cover

  @cached_property
  def profile_dir(self):
    return self.api.path.mkdtemp(self.id)

  @cached_property
  def profile_out_file(self):
    shard_output_dir = self.profile_task.get_task_shard_output_dirs()[0]
    return self.profile_dir / shard_output_dir / 'pgo.profile'

  def find_original_cas_digest(self, comp_props):
    self.compilator_properties = comp_props
    hashes = self.compilator_properties['swarm_hashes']
    self.original_cas_digest = hashes['d8_pgo']

  @cached_property
  def test_spec(self):
    return self.compilator_properties['parent_test_spec']

  @cached_property
  def swarming_dimensions(self):
    dimensions = self.test_spec['swarming_dimensions']
    dimensions.update({'pool': self.profiling_pool})
    return dimensions

  @cached_property
  def swarming_task_attrs(self):
    return self.test_spec['swarming_task_attrs']

  @property
  def presentation(self):
    if self.exception:
      return f'❌ {self.name} Failure: {self.exception}'

    return f'✓ {self.name}'

  @cached_property
  @abstractmethod
  def compilator_kwargs(self):
    pass  # pragma: no cover


class VersionProfileTrack(BaseProfileTrack):
  def __init__(self, api, track, profiling_pool, version, revision):
    self.version = '%d.%d.%d.%d' % version
    self.revision = revision
    super().__init__(api, track, profiling_pool)

  @cached_property
  def id(self):
    return f'v{self.version}_{self.track}'

  @cached_property
  def name(self):
    return f'{self.version} {self.track}'

  @cached_property
  def remote_profile_path(self):
    return f'by-version/{self.version}/{self.track}.profile'

  def get_profile_url(self, bucket):
    return f'https://storage.googleapis.com/{bucket}/{self.remote_profile_path}'

  @cached_property
  def compilator_kwargs(self):
    return {'revision': self.revision, 'gerrit_changes': []}


class RevisionProfileTrack(BaseProfileTrack):
  def __init__(self, api, track, profiling_pool, revision):
    self.revision = revision
    super().__init__(api, track, profiling_pool)

  @cached_property
  def id(self):
    return f'r{self.revision}_{self.track}'

  @cached_property
  def name(self):
    return f'{self.revision[:8]} {self.track}'

  @cached_property
  def compilator_kwargs(self):
    return {'revision': self.revision, 'gerrit_changes': []}


class ChangeProfileTrack(BaseProfileTrack):
  def __init__(self, api, track, profiling_pool, change):
    self.change = change
    super().__init__(api, track, profiling_pool)

  @cached_property
  def id(self):
    return f'c{self.change.change}_{self.change.patchset}_{self.track}'

  @cached_property
  def name(self):
    return (
      f'crrev.com/c/{self.change.change}/{self.change.patchset} {self.track}'
    )

  @cached_property
  def compilator_kwargs(self):
    return {'gerrit_changes': [self.change]}
