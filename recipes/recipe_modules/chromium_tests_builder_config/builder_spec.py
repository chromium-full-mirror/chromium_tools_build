# Copyright 2020 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import attr
import itertools

from RECIPE_MODULES.build.attr_utils import (attrib, attrs, enum, mapping,
                                             sequence)

COMPILE_AND_TEST = 'compile/test'
TEST = 'test'


@attrs()
class BuilderSpec:
  """An immutable specification for a bot.

  This type provides a means of specifying all of the input values for
  the bot with the following benefits:
    * All available fields and their default values are declared and
      documented in one place
    * Typos are caught at the time a spec is instantiated
    * Validation is performed to ensure that values are of approriate
      type and that combinations make sense

  If another recipe/recipe module needs to add its own information, it
  should define a subclass of this type and declare fields for its
  additional information.
  """

  @classmethod
  def create(cls, **kwargs):
    """Create a BuilderSpec.

    Arguments:
      kwargs - Values to initialize the BuilderSpec.

    Returns:
      A new BuilderSpec instance with fields set to the values passed in kwargs
      and all other fields set to their defaults.
    """

    def get_filtered_attrs(*attributes):
      return [a for a in attributes if a in kwargs]

    execution_mode = kwargs.get('execution_mode', COMPILE_AND_TEST)
    if execution_mode != COMPILE_AND_TEST:
      invalid_attrs = get_filtered_attrs('compile_targets')
      assert not invalid_attrs, (
          "The following fields are ignored unless 'execution_mode' is {!r}: {}"
          .format(COMPILE_AND_TEST, invalid_attrs))

    if not kwargs.get('cf_archive_build'):
      invalid_attrs = get_filtered_attrs('cf_gs_bucket', 'cf_archive_name',
                                         'cf_gs_acl',
                                         'cf_archive_subdir_suffix')
      assert not invalid_attrs, (
          'The following fields are ignored unless '
          "'cf_archive_build' is set to True: {}".format(invalid_attrs))

    if not kwargs.get('bisect_archive_build'):
      invalid_attrs = get_filtered_attrs('bisect_gs_bucket', 'bisect_gs_extra')
      assert not invalid_attrs, (
          'The following fields are ignored unless '
          "'bisect_archive_build' is set to True: {}".format(invalid_attrs))

    return cls(**kwargs)

  def __attrs_post_init__(self):
    if self.execution_mode == TEST:
      assert self.parent_buildername is not None, (
          'Test-only builder must specify a parent builder')
    elif self.execution_mode == COMPILE_AND_TEST:
      assert self.parent_buildername is None, (
          'Non-test-only builder must not specify a parent builder')
      assert self.parent_builder_group is None, (
          'Non-test-only builder must not specify parent builder group')

    if self.cf_archive_build:
      assert self.cf_gs_bucket, (
          "'cf_gs_bucket' must be provided when 'cf_archive_build' is True")

    if self.bisect_archive_build:
      assert self.bisect_gs_bucket, ("'bisect_gs_bucket' must be provided when "
                                     "'bisect_archive_build' is True")

  # The execution mode of the builder
  # COMPILE_AND_TEST - Compile targets and optionally run tests and/or trigger
  #     a tester
  # TEST - Run tests, requires a parent builder, some ancestor must have
  #     COMPILE_AND_TEST execution mode
  execution_mode = attrib(
      enum([COMPILE_AND_TEST, TEST]), default=COMPILE_AND_TEST)

  # An optional group of the bot's parent builder - if parent_buildername is
  # provided and parent_builder_group is not, the parent's builder_group is the
  # same as the bot associated with this spec
  parent_builder_group = attrib(str, default=None)
  # An optional buildername of the bot's parent builder
  parent_buildername = attrib(str, default=None)

  # The name of the config to use for the chromium recipe module
  chromium_config = attrib(str, default=None)
  # The names of additional configs to apply for the chromium recipe module
  chromium_apply_config = attrib(sequence[str], default=())
  # The keyword arguments used when setting the config for the chromium recipe
  # module
  chromium_config_kwargs = attrib(mapping[str, ...], default={})
  # The name of the config to use for the gclient recipe module
  gclient_config = attrib(str, default=None)
  # The names of additional configs to apply for the gclient recipe module
  gclient_apply_config = attrib(sequence[str], default=())
  # The name of the config to use for the android recipe module
  android_config = attrib(str, default=None)
  # The names of additional configs to apply for the android recipe module
  android_apply_config = attrib(sequence[str], default=())

  # A path relative to the checkout to a file containing the Chrome version
  # information for Android
  android_version = attrib(str, default=None)

  # A bool controlling whether have bot_update perform a clobber of any
  # pre-existing build outputs
  clobber = attrib(bool, default=False)

  # Name of a Google Storage bucket to use when using the legacy package
  # transfer where build outputs are uploaded to Google Storage and then
  # downloaded by the tester
  # This must be set for builders with the BUILDER bot type that trigger testers
  # that will run non-isolated tests
  build_gs_bucket = attrib(str, default=None)

  # A bool controlling whether swarming tests should be run serially
  # If not True, requests for test tasks are issued to swarming in parallel
  # Running tests in serial can be useful if you have limited hardware capacity
  serialize_tests = attrib(bool, default=False)

  # A bool controlling whether an isolate is uploaded to the perf dashboard
  perf_isolate_upload = attrib(bool, default=False)

  # A bool controlling if a build should expose details about triggering tests.
  # If set, the 'trigger_properties' output property will be present on the
  # build.  It will contain the properties normally set when triggering
  # subsequent builds, which includes the isolate digests, the digest of a file
  # containing the command lines for each isolate to execute, and the cwd of
  # the checkout.
  # This will only do something if the build actually produces isolates. This
  # also only works on CI builders.
  #
  # This is normally not necessary. Builders only need to archive command
  # lines if another build will need to use them. The chromium recipe
  # automatically does this if your build triggers another build using the
  # chromium recipe. Only set this value if something other than a triggered
  # chromium builder needs to use the isolates created during a build execution.
  # TODO(martiniss): Rename this to expose_execution_properties.
  expose_trigger_properties = attrib(bool, default=False)

  # A bool controlling whether to archive the build outputs for Clusterfuzz
  cf_archive_build = attrib(bool, default=False)
  # The bucket to archive the build to
  # Must be provided when cf_archive_build is True
  # Cannot be provided when cf_archive_build is not True
  cf_gs_bucket = attrib(str, default=None)
  # The ACL to apply to the archived build
  # Cannot be provided when cf_archive_build is not True
  cf_gs_acl = attrib(str, default=None)
  # The prefix to apply to the archived build
  # Cannot be provided when cf_archive_build is not True
  # TODO(gbeaty) Rename this to cf_archive_prefix
  cf_archive_name = attrib(str, default=None)
  # Suffix to apply to the subdirectory within the bucket the archived is
  # uploaded to
  # Cannot be provided when cf_archive_build is not True
  cf_archive_subdir_suffix = attrib(str, default='')

  # A bool indicating whether the build should be archived for bisection
  bisect_archive_build = attrib(bool, default=False)
  # The bucket to archive the build to
  # Must be provided when bisect_archive_build is True
  # Cannot be provided when bisect_archive_build is not True
  bisect_gs_bucket = attrib(str, default=None)
  # Additional URL components to add to the Google Storage URL for bisection
  # archiving
  # Cannot be provided when bisect_archive_build is not True
  bisect_gs_extra = attrib(str, default=None)

  # The platform of the builder (e.g. 'linux'), used when running simulation
  # tests
  # Normally the platform is provided by the platform recipe module, but during
  # simulation tests we need to tell the platform recipe module what platform
  # the builder would be run on
  # For acceptable values, see
  # https://source.chromium.org/chromium/infra/infra/+/main:recipes-py/recipe_modules/platform/test_api.py?q=symbol:name
  simulation_platform = attrib(str, default=None)

  # The Google Storage bucket used by lacros on skylab pipeline.
  skylab_gs_bucket = attrib(str, default=None)

  # Additional URL components to add to the Google Storage URL for lacros on
  # skylab pipeline.
  skylab_gs_extra = attrib(str, default=None)

  def evolve(self, **kwargs):
    """Create a new BuilderSpec with updated values.

    Arguments:
      kwargs - Updated values for fields.

    Returns:
      A new BuilderSpec where the fields specified in kwargs have been
      overridden and all other fields are kept the same.
    """
    return attr.evolve(self, **kwargs)

  def extend(self, **kwargs):
    """Create a new BuilderSpec extending the values of sequence fields.

    Arguments:
      kwargs - Additional values for fields. The value of each keyword must be
        an iterable object.

    Returns:
      A new BuilderSpec where the fields specified in kwargs have been updated
      with the concatenation of the existing value for the field (or an empty
      iterable if the existing value is None) with the value provided in kwargs.
    """
    for k, v in kwargs.items():
      current = getattr(self, k)
      kwargs[k] = itertools.chain(current, v)
    return self.evolve(**kwargs)
