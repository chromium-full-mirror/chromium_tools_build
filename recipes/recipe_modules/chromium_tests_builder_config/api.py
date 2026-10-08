# Copyright 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import recipe_api

from . import builders, proto, trybots, BuilderConfig, TEST


class ChromiumTestsBuilderConfigApi(recipe_api.RecipeApi):
  def __init__(self, properties, **kwargs):
    super().__init__(**kwargs)
    self._properties = properties
    self._builder_db = builders.BUILDERS
    self._try_db = trybots.TRYBOTS
    if self._test_data.enabled:
      databases = self._test_data.get('databases')
      if databases is not None:
        self._builder_db, self._try_db = databases

  @property
  def builder_db(self):
    return self._builder_db

  @property
  def try_db(self):
    return self._try_db

  def _get_builder_config_from_properties(
    self, default_retry_failed_shards=True
  ):
    if not self._properties.HasField('builder_config'):
      return None

    errors = proto.VALIDATORS.validate(
      self._properties, '$build/chromium_tests_builder_config'
    )
    if errors:
      result = self.m.step(
        'invalid chromium_tests_builder_config properties', []
      )
      result.presentation.step_text = '\n'.join(
        [''] + ['* {}'.format(e) for e in errors]
      )
      # TODO(gbeaty) Should this be failure if the properties file is affected?
      result.presentation.status = self.m.step.EXCEPTION
      self.m.step.raise_on_failure(result)

    return proto.convert_builder_config(
      self._properties.builder_config, default_retry_failed_shards
    )

  # TODO(gbeaty) Remove the builder ID argument when it is possible. The builder
  # ID argument is only used for Findit and the compilator. Once those use cases
  # are switched to always use the module properties, the builder ID argument
  # will not be necessary. Until then, if the module properties are set, the
  # builder ID will be ignored.
  def lookup_builder(
    self,
    builder_id=None,
    builder_db=None,
    try_db=None,
    use_try_db=None,
    builder_config_class=None,
  ):
    """Lookup a builder, getting the matching bot config.

    Args:
      * builder_id - The BuilderId identifying the builder to get a
        BuilderConfig for. By default, self.m.chromium.get_builder_id()
        will be used.
      * builder_db - The BuilderDatabase to look for BuilderSpecs in. By
        default, self.builder_db will be used.
      * try_db - The TryDatabase to look for TrySpecs in. By Default,
        self.trybots will be used.
      * use_try_db - Whether or not to look for TrySpecs in try_db. If
        try_db is used, and it contains a TrySpec for the builder
        identified by builder_id, the BuilderConfig will wrap the
        BuilderSpecs for the builders identified by the mirrors of the
        TrySpec. Otherwise, the BuilderConfig will wrap the BuilderSpec
        for the builder identified by builder_id. By default, the try_db
        will be used if self.m.tryserver.is_tryserver is True.
      * builder_config_class - The class of the builder config to
        create. If not provided, BuilderConfig will be used. The class
        must have a lookup method that takes the builder_id, builder_db
        and try_db as positional arguments and keyword arguments
        use_try_db and step_api. use_try_db acts as indicated above.
        step_api is the API object for the step recipe module and
        should be used for creating an infra failing step if creation of
        the builder config fails.

    Returns:
      A 2-tuple:
        * The BuilderId of the builder the BuilderConfig is for.
        * The BuilderConfig for the builder.
    """
    builder_id = builder_id or self.m.chromium.get_builder_id()

    # retry_failed_shards is default enabled on tryjobs and disabled on CI.
    default_retry_failed_shards = self.m.tryserver.is_tryserver
    builder_config = self._get_builder_config_from_properties(
      default_retry_failed_shards
    )

    if builder_config is None:
      if builder_db is None:
        assert try_db is None
        builder_db = self.builder_db
        try_db = self.try_db

      if use_try_db is None:
        use_try_db = self.m.tryserver.is_tryserver

      builder_config_class = builder_config_class or BuilderConfig
      builder_config = builder_config_class.lookup(
        builder_id,
        builder_db,
        try_db,
        use_try_db=use_try_db,
        step_api=self.m.step,
        default_retry_failed_shards=default_retry_failed_shards,
      )

    bucket = self.m.buildbucket.build.builder.bucket
    if bucket == 'official' or bucket.startswith('official.'):
      assert builder_config.execution_mode != TEST, (
        'Test-only builders are not allowed in official buckets '
      )

    return builder_id, builder_config
