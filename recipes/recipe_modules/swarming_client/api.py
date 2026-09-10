# Copyright 2014 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import contextlib

from recipe_engine import recipe_api


class SwarmingClientApi(recipe_api.RecipeApi):
  """Code that both isolate and swarming recipe modules depend on.

  Both swarming and isolate scripts live in a single repository called
  'swarming client'. This module include common functionality like finding
  existing swarming client checkout, fetching a new one, getting version of
  a swarming script, etc.
  """

  def __init__(self, **kwargs):
    super().__init__(**kwargs)
    self._client_path = None
    self._script_version = {}

  def checkout(self, revision=None):
    """Returns a step to checkout swarming client into a separate directory.

    Ordinarily swarming client is checked out via Chromium DEPS into
    src/tools/swarming_client. This step configures recipe module to use
    a separate checkout.

    Fail-fast behavior is used because if machines silently fell back
    to checking out the entire workspace, that would cause dramatic increases
    in cycle time if a misconfiguration were made and it were no longer possible
    for the bot to check out swarming_client separately.
    """
    # If the following line throws an exception, it either means the
    # bot is misconfigured, or, if you're testing locally, that you
    # need to pass in some recent legal revision for this property.
    if revision is None:
      revision = '6b5e452e39fc4c629c40726b0421d495e40b3620'
    self._client_path = self.m.path.start_dir / 'swarming.client'
    self.m.git.checkout(
      url='https://chromium.googlesource.com/infra/luci/client-py.git',
      ref=revision,
      dir_path=self._client_path,
      step_suffix='swarming_client',
    )

  @property
  def path(self):
    """Returns path to a swarming client checkout."""
    if not self._client_path:
      self.checkout()
    return self._client_path
