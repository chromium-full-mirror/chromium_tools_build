# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Classes for representing the result of the "checkout" steps in builds.

Some builds will fetch full git checkouts of their respective projects.
Others may fetch only the minimum set of files needed for their builds from
RBE-CAS. The classes here provide an abstraction around the step result of
both methods.
"""

from __future__ import annotations

import abc
from typing import Any, Mapping

from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb
from recipe_engine.config_types import Path
from RECIPE_MODULES.depot_tools import bot_update


class SourceRootInterface(abc.ABC):
  """Abstract base class representing a source root."""

  @property
  @abc.abstractmethod
  def path(self) -> Path:
    """Path to the source root."""

  @property
  @abc.abstractmethod
  def name(self) -> str:
    """Name of the source root directory."""


class CheckoutResult(abc.ABC):
  """Abstract base class representing the result of a checkout."""

  @property
  @abc.abstractmethod
  def checkout_dir(self) -> Path:
    """Path to the directory where the checkout was performed."""

  @property
  @abc.abstractmethod
  def source_root(self) -> SourceRootInterface:
    """Object representing the source root."""

  @property
  @abc.abstractmethod
  def patch_root(self) -> SourceRootInterface | None:
    """Object representing the patch root, or None if no patch."""

  @property
  @abc.abstractmethod
  def properties(self) -> dict:
    """Dict of properties exported by the checkout."""

  @property
  @abc.abstractmethod
  def manifest(self) -> dict:
    """Dict representing the manifest of checked out revisions."""

  @property
  @abc.abstractmethod
  def fixed_revisions(self) -> dict:
    """Dict of fixed revisions used in the checkout."""

  @property
  @abc.abstractmethod
  def out_commit(self) -> common_pb.GitilesCommit | None:
    """Gitiles output commit derived from got_revision."""


class BotUpdateSourceRootAdapter(SourceRootInterface):
  """Adapts bot_update source root to SourceRootInterface."""

  def __init__(self, real_source_root):
    self._real = real_source_root

  @property
  def path(self) -> Path:
    return self._real.path

  @property
  def name(self) -> str:
    return self._real.name


class BotUpdateResultAdapter(CheckoutResult):
  """Adapts bot_update.Result to the CheckoutResult interface.

  This adapter can be removed if depot_tools' recipe code is updated to have
  a parent class for `bot_update.Result`. The primary need for the adapter is
  so that Chromium recipe code can return a `CasCheckoutResult` if using the
  test trigger CAS codepath instead of getting a full checkout.
  """

  def __init__(self, real_result: bot_update.Result):
    self._real = real_result
    self._source_root = BotUpdateSourceRootAdapter(real_result.source_root)
    self._patch_root = (
        BotUpdateSourceRootAdapter(real_result.patch_root)
        if real_result.patch_root else None)

  @property
  def checkout_dir(self) -> Path:
    return self._real.checkout_dir

  @property
  def source_root(self) -> SourceRootInterface:
    return self._source_root

  @property
  def patch_root(self) -> SourceRootInterface | None:
    return self._patch_root

  @property
  def properties(self) -> dict:
    return self._real.properties

  @property
  def manifest(self) -> dict:
    return self._real.manifest

  @property
  def fixed_revisions(self) -> dict:
    return self._real.fixed_revisions

  @property
  def out_commit(self) -> common_pb.GitilesCommit | None:
    return self._real.out_commit


class CasSourceRoot(SourceRootInterface):
  """Source root representation for a test trigger CAS 'checkout'."""

  def __init__(self, path: Path, name: str):
    self._path = path
    self._name = name

  @property
  def path(self) -> Path:
    return self._path

  @property
  def name(self) -> str:
    return self._name


class CasCheckoutResult(CheckoutResult):
  """Checkout result for test trigger CAS builds."""

  def __init__(
      self,
      checkout_dir: Path,
      source_dir: Path,
      raw_properties: Mapping[str, Any],
      source_root_name: str,
  ):
    self._checkout_dir = checkout_dir
    self._source_root = CasSourceRoot(source_dir, source_root_name)
    self._properties = self._map_properties(raw_properties)

  def _map_properties(self, raw_properties: Mapping[str,
                                                    Any]) -> dict[str, Any]:
    # Map parent properties to standard got_ properties since they are normally
    # set when getting a full checkout.
    properties = {}
    for k, v in raw_properties.items():
      if k.startswith('parent_got_'):
        properties[k[7:]] = v

    if 'got_revision' not in properties and 'revision' in raw_properties:
      properties['got_revision'] = raw_properties['revision']
    return properties

  @property
  def checkout_dir(self) -> Path:
    return self._checkout_dir

  @property
  def source_root(self) -> SourceRootInterface:
    return self._source_root

  @property
  def patch_root(self) -> SourceRootInterface | None:
    return None

  @property
  def properties(self) -> dict:
    return self._properties

  @property
  def manifest(self) -> dict:
    return {}

  @property
  def fixed_revisions(self) -> dict:
    return {}

  @property
  def out_commit(self) -> common_pb.GitilesCommit | None:
    return None
