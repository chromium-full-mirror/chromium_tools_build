# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.


class ProfileTrack:
  """
  A track is the process of compiling, generating and uploading the profile
  for a single commit on a track (architecture × platform). This process has
  multiple discrete steps. We want to run these steps in parallel for each
  versions and each track.

  This class manages the state of this process by collecting handlers and
  properties from every step and passes it to the next one.
  """

  def __init__(self, version, track, revision) -> None:
    self.version = ('%d.%d.%d.%d' % version) if version else None
    self.track = track
    self.revision = revision
    self.compilator_handler = None
    self.compilator_properties = None
    self.original_cas_digest = None
    self.profile_dir = None
    self.profile_task = None
    self.exception = None

  @property
  def profile_out_file(self):
    shard_output_dir = self.profile_task.get_task_shard_output_dirs()[0]
    return self.profile_dir / shard_output_dir / 'pgo.profile'

  def find_original_cas_digest(self, comp_props):
    self.compilator_properties = comp_props
    hashes = self.compilator_properties['swarm_hashes']
    self.original_cas_digest = hashes['d8_pgo']

  @property
  def test_spec(self):
    return self.compilator_properties['parent_test_spec']

  @property
  def swarming_dimensions(self):
    dimensions = self.test_spec['swarming_dimensions']
    dimensions.update({'pool': 'chromium.tests'})
    return dimensions

  @property
  def swarming_task_attrs(self):
    return self.test_spec['swarming_task_attrs']

  @property
  def name(self):
    return f'{self.version or self.revision[:8]} {self.track}'

  @property
  def presentation(self):
    result = '❌' if self.exception else '✓'
    if self.version:
      result += f' {self.version}'

    result += f' {self.revision} {self.track}'

    if self.exception:
      result += f' Failure: {self.exception}'

    return result

  @property
  def remote_profile_path(self):
    assert self.version, 'The track has no version assigned. No remote profile path exists.'
    return f'by-version/{self.version}/{self.track}.profile'

  def get_profile_url(self, bucket):
    return f'https://storage.googleapis.com/{bucket}/{self.remote_profile_path}'
