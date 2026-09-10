# Copyright 2020 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""API for sending requests to ChromiumDash."""

from __future__ import annotations

from recipe_engine import recipe_api


class ChromiumDashApi(recipe_api.RecipeApi):
  URL = 'https://chromiumdash.appspot.com'
  RELEASE_ENDPOINT = 'fetch_releases'
  COMMIT_ENDPOINT = 'fetch_commits'
  MILESTONE_ENDPOINT = 'fetch_milestones'
  RELEASE_CHANNELS = ('Beta', 'Stable', 'Dev', 'Canary', 'Extended')
  VALID_PLATFORMS = ('Android', 'Mac', 'Linux', 'Windows', 'iOS')

  def _get_json(
    self, endpoint, url_args, step_name=None, default_test_data=None
  ):
    """Helper method to fetch json data from chromiumdash.

    Args:
      endpoint: Chromiumdash endpoint to send request to.
      url_args: Query paramaters that will be encoded into the chromiumdash url.
      step_name: Name of the step.
      default_test_data: Default test data.

    Returns: Response from the chromiumdash endpoint in JSON format."""

    url = (
      self.m.url.join(self.URL, endpoint)
      + ('?' + self.m.url.urlencode(url_args))
      if url_args
      else ''
    )
    return self.m.url.get_json(
      url, step_name=step_name, default_test_data=default_test_data
    ).output

  def fetch_commit_info(self, commit_hash, step_name=None):
    """Fetch commit information from chromiumdash.

    Args:
      commit_hash: Commit hash which is being queried.

    Returns: Response containing commit information."""

    default_test_data = {
      'deployment': {'beta': '84.0.4107.90'},
      'repo': 'chromium',
      'commit_type': 'commit',
    }
    return self._get_json(
      self.COMMIT_ENDPOINT,
      {'commit': commit_hash},
      step_name,
      default_test_data,
    )

  def releases(self, platform, release_channel, num, step_name=None):
    """Fetch releases from chromiumdash.

    Each release will be for a specific platform and release channel.

    Args:
      platform: The platform for which we are getting releases.
          i.e Android, Windows or Linux.
      release_channel: Release channel for which we are getting releases.
          i.e Beta, Stable or Dev.
      num: Number of releases we are requesting.
      step_name: Name of the step.

    Returns: Response from the fetch_releases endpoint of chromiumdash."""

    assert platform in self.VALID_PLATFORMS, (
      'Platform %r is not a valid platform in ChromiumDash' % platform
    )
    assert release_channel in self.RELEASE_CHANNELS, (
      'Channel %r is not a valid release channel in ChromiumDash'
      % release_channel
    )
    assert num >= 0, 'Cannot request a negative amount of milestones'

    default_test_data = [
      {'version': '87.0.4280.60', 'hashes': {'chromium': str(i)}}
      for i in range(num)
    ]
    return self._get_json(
      self.RELEASE_ENDPOINT,
      {'platform': platform, 'channel': release_channel, 'num': num},
      step_name,
      default_test_data,
    )

  def milestones(
    self, num, step_name=None, only_branched=False, only_active=False
  ):
    """Fetch milestones from chromiumdash.

    Args:
      num: Number of milestones to request
      step_name: Optional name for the step
      only_branched: Request only milestones with release branches

    Returns: Response containing milestones from ChromiumDash's
             fetch_milestone endpoint
    """

    default_test_data = [
      {'chromium_branch': str(4324 + i), 'milestone': 88 + i}
      for i in range(num)
    ]
    args = {'num': num}
    if only_branched:
      args.update({'only_branched': 'true'})
    if only_active:
      args.update({'only_active': 'true'})
    return self._get_json(
      self.MILESTONE_ENDPOINT, args, step_name, default_test_data
    )
