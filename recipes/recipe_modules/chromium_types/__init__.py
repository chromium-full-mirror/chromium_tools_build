# Copyright 2020 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import recipe_api

from RECIPE_MODULES.build.attr_utils import (
  FieldMapping,
  attrib,
  attrs,
  mapping,
  sequence,
)


@attrs()
class BuilderId:
  """ID type identifying a builder.

  Currently, the ID identifies a builder by the pair of group and
  buildername. This type provides a means for modifying the underlying
  ID data without having to modify code that simply needs to provide an
  ID to some other code (e.g. switching to project, bucket, builder).
  """

  group = attrib(str)
  builder = attrib(str)

  @classmethod
  def create_for_group(cls, group, builder):
    return cls(group, builder)

  def __str__(self):
    return '{}:{}'.format(self.group, self.builder)


@attrs()
class BuilderSpec(FieldMapping):
  """An immutable specification for a builder.

  This type provides the means of specifying all of the input values for a
  builder. Previously, dictionaries were used which have the following
  drawbacks:
    * Susceptible to typos for key names
    * Lack of validation at definition time
    * Poor discoverability of available fields
    * Default values for fields are specified at the point(s) of access

  This type address the first 2 points. The 3rd point is also addressed with the
  caveat that recipes/recipe modules that add their own information to the spec
  require a type containing fields with the additional information. To remain
  backwards compatible with existing code, the 4th point is not addressed yet.

  Backwards compatibility:
  Until callers have switched over to accessing the fields directly, a
  BuilderSpec acts as a mapping with an item for each field whose value is not
  None, with the name of the field as the key.
  """

  # The name of the config to use for the chromium recipe module
  chromium_config = attrib(str, default=None)
  # The names of additional configs to apply for the chromium recipe module
  chromium_apply_config = attrib(sequence[str], default=())
  # The keyword arguments used when setting the config for the chromium recipe
  # module
  chromium_config_kwargs = attrib(mapping[str, ...], default={})
  # The names of additional configs to apply for the gclient recipe module
  gclient_apply_config = attrib(sequence[str], default=())

  # A bool controlling whether have bot_update perform a clobber of any
  # pre-existing build outputs
  clobber = attrib(bool, default=False)

  @classmethod
  def create(cls, **kwargs):
    return cls(**kwargs)


class API(recipe_api.RecipeApi):
  def __init__(self, *args, **kwargs):
    raise Exception(
      'API has no methods, instead import RECIPE_MODULE.build.chromium_types'
    )  # pragma: no cover


__all__ = ['API', 'BuilderId', 'BuilderSpec']
