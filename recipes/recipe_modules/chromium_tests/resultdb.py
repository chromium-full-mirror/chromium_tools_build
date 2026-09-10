# Copyright 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
from __future__ import annotations

import attr

from recipe_engine.config_types import Path
from recipe_engine.util import Placeholder
from RECIPE_MODULES.build.attr_utils import (
  attrib,
  attrs,
  enum,
  mapping,
  sequence,
)


@attrs()
class ResultDB:
  """Configuration for ResultDB-integration for a test.

  Attributes:
    * enable - Whether or not ResultDB-integration is enabled. Defaults to True.
      Disabling ResultDB for a test should be used **very** sparingly, as the
      Chromium recipe stack depends on ResultDB-integration to get granular test
      results.
    * has_native_resultdb_integration - If True, indicates the test will upload
      its results to ResultDB via ResultSink and that `result_adapter` is not
      needed.
    * result_format - The format of the test results.
    * test_id_as_test_location - Whether the test ID will be used as the
      test location. It only makes sense to set this for blink_web_tests
      and webgl_conformance_tests.
    * test_location_base - File base to prepend to the test location
      file name, if the file name is a relative path. It must start with
      "//".
    * base_tags - Tags to attach to all test results by default. Each element
      is (key, value), and a key may be repeated.
    * base_variant - Dict of Variant key-value pairs to attach to all test
      results by default.
    * test_id_prefix - Prefix to prepend to test IDs of all test results.
    * module_name - Module name to upload results to for
      ResultSink's ReportTestResults RPC. Do not set in conjunction with
      test_id_prefix.
    * module_scheme - A specific scheme to use for results such as gtest or
      junit with ResultSink's ReportTestResults RPC. Do not set in conjunction
      with test_id_prefix.
    * coerce_negative_duration - If True, negative duration values will
      be coerced to 0. If false, tests results with negative duration values
      will be rejected with an error.
    * result_file - path to result file for result_adapter to read test results
      from. It is meaningful only when result_format is not None.
    * artifact_directory - path to artifact directory where result_adapter
      finds and uploads artifacts from.
    * location_tags_file - path to the location tags file for ResultSink to read
      and attach location based tags to each test result.
    * exonerate_unexpected_pass - flag to control if ResultSink should
      automatically exonerate unexpected passes.
    * result_adapter_path - path to result_adapter binary.
    * include - If True, a new invocation will be created for the test and
      included in the parent invocation.
    * sources - JSON-serialized luci.resultdb.v1.Sources object that
      contains information about the code sources tested by the invocation.
    * inv_extended_properties_dir - Path to a directory that contains files
      for the invocation's extended_properties in JSON format.
      Only files directly under this dir with the extension ".jsonpb" will be
      read. The filename after removing ".jsonpb" and the file content will be
      added as a key-value pair to the invocation's extended_properties map.
  """

  enable = attrib(bool, default=True)
  has_native_resultdb_integration = attrib(bool, default=False)
  result_format = attrib(
    enum(['gtest', 'json', 'single', 'tast', 'gtest_json', 'native']),
    default=None,
  )
  test_id_as_test_location = attrib(bool, default=False)
  test_location_base = attrib(str, default=None)
  base_tags = attrib(sequence[tuple], default=None)
  base_variant = attrib(mapping[str, str], default=None)
  coerce_negative_duration = attrib(bool, default=True)
  test_id_prefix = attrib(str, default='')
  module_name = attrib(str, default='')
  module_scheme = attrib(str, default='')
  result_file = attrib(str, default='${ISOLATED_OUTDIR}/output.json')
  artifact_directory = attrib(
    (str, Placeholder, Path), default='${ISOLATED_OUTDIR}'
  )
  location_tags_file = attrib(str, default=None)
  exonerate_unexpected_pass = attrib(bool, default=True)
  include = attrib(bool, default=False)
  baseline_id = attrib(str, default='')
  sources = attrib(str, default='')
  # result_adapter binary is available in chromium checkout or
  # the swarming bot.
  #
  # local tests can use it from chromium checkout with the following path
  # : $CHECKOUT/src/tools/resultdb/result_adapter
  #
  # However, swarmed tasks can't use it, because there is no guarantee that
  # the isolate would include result_adapter always. Instead, swarmed tasks use
  # result_adapter deployed via the pool config. That is, result_adapter
  # w/o preceding path.
  result_adapter_path = attrib(str, default='result_adapter')
  inv_extended_properties_dir = attrib(str, default='')

  @classmethod
  def create(cls, **kwargs):
    """Create a ResultDB instance.

    Args:
      * kwargs - Keyword arguments to initialize the attributes of the
        created object.

    Returns:
      A `ResultDB` instance with attributes initialized with the matching
      keyword arguments.
    """
    # Unconditionally construct an instance with the keywords to have the
    # arguments validated.
    return cls(**kwargs)

  @staticmethod
  def generate_baseline_id(bucket: str, builder_name: str) -> str:
    """Generates baseline identifiers in the format {bucket}:{builder_name}

    For example, try:linux-rel. Baseline identifiers are sets of test variants,
    used as part of ResultDB's QueryNewTestVariants RPC call to determine
    if there are new tests introduced in the patchset.

    Args:
      bucket: Buildbucket bucket.
      builder_name: Buildbucket builder name.

    Returns:
      string
    """
    return f'{bucket}:{builder_name}'

  def wrap(
    self,
    api,
    cmd,
    step_name=None,
    base_variant=None,
    base_tags=None,
    require_build_inv=True,
    **kwargs,
  ):
    """Wraps the cmd with ResultSink and result_adapter, if conditions are met.

    This function enables resultdb for a given command by wrapping it with
    ResultSink and result_adapter, based on the config values set in ResultDB
    instance. Find the config descriptions for more info.

    If config values are passed as params, they override the values set in
    the ResultDB object.

    Args:
      * api - Recipe API object.
      * cmd - List with the test command and arguments to wrap with ResultSink
        and result_adapter.
      * step_name - Step name to add as a tag to each test result.
      * base_variant - Dict of variants to add to base_variant.
        If there are duplicate keys, the new variant value wins.
      * base_tags - List of tags to add to base_tags.
      * require_build_inv - flag to control if the build is required to have
        an invocation.
      * kwargs - Overrides for the rest of ResultDB attrs.
    """
    assert isinstance(cmd, (tuple, list)), "%s: %s" % (step_name, cmd)
    assert isinstance(step_name, (type(None), str)), "%s: %s" % (step_name, cmd)
    configs = attr.evolve(self, **kwargs)
    if not configs.enable:
      # If RDB is disabled, not only are we going to not wrap it in `resultdb`,
      # but we're going to wrap it in `reset_luci_context.py`, which will remove
      # the result_sink info from the current LUCI_CONTEXT. This can happen if
      # the test runs locally and so would inherit the build's LUCI_CONTEXT,
      # which very likely does have result_sink info attached.
      return [
        'vpython3',
        api.chromium_tests.resource('reset_luci_context.py'),
      ] + cmd

    in_rdb_exp = (
      'chromium_tests.resultdb_module'
      in api.buildbucket.build.input.experiments
    )
    # wrap it with result_adapter
    if not configs.has_native_resultdb_integration and configs.result_format:
      exe = configs.result_adapter_path + (
        '.exe' if api.platform.is_win else ''
      )
      result_adapter = [
        exe,
        configs.result_format,
        '-result-file',
        configs.result_file,
      ]
      if (
        in_rdb_exp and configs.module_scheme and configs.result_format == 'json'
      ):
        result_adapter += [
          '-scheme',
          configs.module_scheme,
        ]

      if configs.artifact_directory:
        result_adapter += ['-artifact-directory', configs.artifact_directory]

      if configs.result_format == 'json' and configs.test_id_as_test_location:
        result_adapter += ['-test-location']

      cmd = result_adapter + ['--'] + list(cmd)

    var = {}
    var.update(self.base_variant or {})
    var.update(base_variant or {})

    tags = set(base_tags or [])
    tags.update(self.base_tags or [])
    if step_name:
      tags.add(('step_name', step_name))

    if not configs.baseline_id and api.buildbucket:
      baseline_id = self.generate_baseline_id(
        api.buildbucket.build.builder.bucket, api.buildbucket.builder_name
      )
      configs = attr.evolve(configs, baseline_id=baseline_id)

    test_id_prefix = configs.test_id_prefix
    previous_test_id_prefix = None
    module_name = None
    module_scheme = None

    if in_rdb_exp and configs.module_scheme and configs.module_name:
      previous_test_id_prefix = configs.test_id_prefix
      test_id_prefix = None
      module_name = configs.module_name
      module_scheme = configs.module_scheme

    # wrap it with rdb-stream
    return api.resultdb.wrap(
      cmd,
      base_tags=list(tags),
      base_variant=var,
      coerce_negative_duration=configs.coerce_negative_duration,
      test_id_prefix=test_id_prefix,
      previous_test_id_prefix=previous_test_id_prefix,
      module_name=module_name,
      module_scheme=module_scheme,
      test_location_base=configs.test_location_base,
      location_tags_file=configs.location_tags_file,
      require_build_inv=require_build_inv,
      exonerate_unexpected_pass=configs.exonerate_unexpected_pass,
      include=configs.include,
      inherit_sources=True,
      baseline_id=configs.baseline_id,
      inv_extended_properties_dir=configs.inv_extended_properties_dir,
    )

  def unwrap(self, api, cmd: list[str]) -> list[str]:
    """Reverses the wrap command"""

    cmd = api.resultdb.unwrap(cmd)
    if (
      cmd[0] != self.result_adapter_path
      and cmd[0] != self.result_adapter_path + '.exe'
    ):
      return cmd
    return cmd[cmd.index('--') + 1 :]
