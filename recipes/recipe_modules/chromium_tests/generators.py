# Copyright 2018 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from collections.abc import Callable, Container, Iterable, Mapping
import string
import textwrap

from . import steps

import attr

from recipe_engine import engine_types
from recipe_engine.config_types import Path

from RECIPE_MODULES.build import chromium_swarming
from RECIPE_MODULES.build.attr_utils import attrib, attrs, mapping, sequence

TargetsSpec = Mapping[str, object]
_RawTestSpec = Mapping[str, object]


@attrs()
class PrecommitDetails:
  footers = attrib(mapping[str, sequence[str]], default={})


class Generator:

  def __init__(
      self,
      chromium_tests_api,
      got_revisions: Mapping[str, str],
      checkout_path: Path,
      remote_tests_only: bool = False,
      precommit_details: PrecommitDetails | None = None,
      scripts_compile_targets_fn: Callable[[], Iterable[str]] | None = None,
      force_experimental_tests: bool = False,
  ):
    """
    Args:
      chromium_tests_api: The chromium_tests api object.
      got_revisions: The got_revision properties returned from
        bot_update.
      checkout_path: Path to checked out repo that contains test specs.
        For chromium builders this is usually cache/builder/src, but for
        other builders, like angle, this is cache/builder/angle.
      remote_tests_only: Whether to only yield remote tests.
      precommit_details: Details used for the pre-commit-specific
        behavior when generating tests. If None, then the generation
        will use the non-pre-commit behavior.
      scripts_compile_targets_fn: A function that can be called to get
        the compile targets required for script tests.
      force_experimental_tests: If True, treats any experimental test as if it
        was a normal non-experimental test. Should be used sparingly.
    """
    self._chromium_tests_api = chromium_tests_api
    self._got_revisions = got_revisions
    self._checkout_path = checkout_path
    self._remote_tests_only = remote_tests_only
    self._precommit_details = precommit_details
    self._scripts_compile_targets_fn = (
        scripts_compile_targets_fn or (lambda: []))
    self._force_experimental_tests = force_experimental_tests

  def generate(
      self,
      builder_group: str,
      builder: str,
      targets_spec: TargetsSpec,
      test_names_to_skip: Iterable[str] = (),
  ) -> Iterable[steps.AbstractTestSpec]:

    def generate_inner():
      for key, handler in (
          ('gtest_tests', self._generate_gtest_test_spec),
          ('isolated_scripts', self._generate_isolated_script_test_spec),
          ('scripts', self._generate_script_test_spec),
          ('skylab_tests', self._generate_skylab_test_spec),
      ):
        for raw_test_spec in targets_spec.get(key, []):
          test_spec = handler(raw_test_spec)
          if test_spec is not None:
            yield raw_test_spec, test_spec

    test_names_to_skip = set(test_names_to_skip)
    for raw_test_spec, test_spec in generate_inner():
      test_spec = attr.evolve(
          test_spec,
          waterfall_builder_group=builder_group,
          waterfall_buildername=builder)
      if description := raw_test_spec.get('description'):
        test_spec = attr.evolve(test_spec, description=description)
      test_spec = self._handle_resultdb(raw_test_spec, test_spec)
      test_spec = self._handle_experimental(raw_test_spec, test_spec)
      test_spec = self._handle_ci_only(raw_test_spec, test_spec)
      test_spec = self._handle_skip_test(test_spec, test_names_to_skip)
      yield test_spec

  def _handle_resultdb(
      self,
      raw_test_spec: _RawTestSpec,
      test_spec: steps.AbstractTestSpec,
  ) -> steps.ResultDB:
    kwargs = dict(raw_test_spec.get('resultdb', {}))
    if 'result_format' not in kwargs:
      result_format = None
      # HACK: If an isolated script test doesn't explicitly enable resultdb in its
      # test spec, assume it doesn't have native integration with result-sink and
      # consequently needs the result_adapter added using the 'json' format.
      # TODO(crbug.com/1135718): Explicitly mark all such tests as using the json
      # result format in the testing specs.
      if isinstance(test_spec, steps.SwarmingIsolatedScriptTestSpec):
        if not kwargs:
          result_format = 'json'
      elif isinstance(test_spec,
                      (steps.SwarmingGTestTestSpec, steps.LocalGTestTestSpec)):
        result_format = 'gtest'
      if result_format:
        kwargs.setdefault('result_format', result_format)
    kwargs.setdefault('test_id_prefix', raw_test_spec.get('test_id_prefix'))
    kwargs.setdefault('module_name', raw_test_spec.get('module_name', None))
    kwargs.setdefault('module_scheme', raw_test_spec.get('module_scheme', None))
    base_variant = self._chromium_tests_api.base_variant_getter(test_spec)
    kwargs.setdefault('base_variant', {}).update(base_variant)
    return attr.evolve(test_spec, resultdb=steps.ResultDB.create(**kwargs))

  def _handle_experimental(
      self,
      raw_test_spec: _RawTestSpec,
      test_spec: steps.AbstractTestSpec,
  ) -> steps.AbstractTestSpec:
    experiment_percentage = raw_test_spec.get('experiment_percentage')
    if experiment_percentage is None or self._force_experimental_tests:
      return test_spec
    return steps.ExperimentalTestSpec.create(test_spec,
                                             int(experiment_percentage))

  def _handle_ci_only(
      self,
      raw_test_spec: _RawTestSpec,
      test_spec: steps.AbstractTestSpec,
  ) -> steps.AbstractTestSpec:
    """Handle the ci_only attribute of test specs.

    Args:
      * raw_spec - The source-side spec dictionary describing the test.
      * test_spec - The TestSpec instance for the test.

    Returns:
      The test_spec object that should be used for the test. If the test
      is not ci-only, `test_spec` will be returned unchanged. Otherwise,
      if a CI builder is running, a spec will be returned that includes a
      message indicating that the test will not be run on try builders.
      Otherwise, if the Include-Ci-Only-Test gerrit footer is present and
      evaluates to 'true' when converted to lowercase, a spec will be
      returned that includes a message indicating that the test is being
      run due to the footer. Otherwise, a spec will be returned that
      indicates the test is disabled.
    """
    if not raw_test_spec.get('ci_only'):
      return test_spec
    return steps.CiOnlyTestSpec.create(test_spec)

  def _handle_skip_test(
      self,
      test_spec: steps.AbstractTestSpec,
      test_names_to_skip: Container[str],
  ) -> steps.AbstractTestSpec:
    """Handle tests that are being skipped because of previous successs

    Args:
      * test_spec - The TestSpec instance for the test.
      * test_names_to_skip - Names of tests that should be disabled

    Returns:
      The test_spec object that should be used for the test. If the test's name
      is present in the test_names_to_skip list a SuccessReuseTestSpec
      object is used to prevent that test from running while still providing
      steps.
    """
    if test_spec.get_test(self._chromium_tests_api).name in test_names_to_skip:
      return steps.SuccessReuseTestSpec.create(test_spec)
    return test_spec

  def _get_args_for_test(self, raw_test_spec: _RawTestSpec):
    """Gets the argument list for a dynamically generated test, as
    provided by the JSON files in src/testing/buildbot/ in the Chromium
    workspace. This function provides the following build properties in
    the form of variable substitutions in the tests' argument lists:

        buildbucket_project
        buildbucket_build_id
        builder_group
        buildername
        buildnumber
        got_cr_revision
        got_revision
        got_src_revision
        patch_issue
        patch_set
        use_permissive_angle_pixel_comparison
        xcode_build_version

    so, for example, a test can declare the argument:

        --test-machine-name=\"${buildername}\"

    and ${buildername} will be replaced with the associated build
    property. In this example, it will also be double-quoted, to handle
    the case where the machine name contains contains spaces.

    This function also supports trybot-only and waterfall-only
    arguments, so that a test can pass a different argument lists on the
    continuous builders compared to tryjobs. This is useful when the
    waterfall bots generate some reference data that is tested against
    during tryjobs.

    This function also supports more general conditional arguments,
    where one of the substitution variables and a target value is
    specified. If the value of that variable is equal to the target
    value, then the associated arguments are added.
    """

    args = list(raw_test_spec.get('args', []))
    if self._precommit_details:
      args.extend(raw_test_spec.get('precommit_args', []))
    else:
      args.extend(raw_test_spec.get('non_precommit_args', []))

    # Perform substitution of known variables.
    build = self._chromium_tests_api.m.buildbucket.build
    cl = (build.input.gerrit_changes or [None])[0]
    if self._precommit_details:
      footer_values = [
          val.lower() for val in self._precommit_details.footers.get(
              'Use-Permissive-Angle-Pixel-Comparison', [])
      ]
      use_permissive_angle_pixel_comparison = 'true' in footer_values
    else:
      use_permissive_angle_pixel_comparison = False
    substitutions = {
        'buildbucket_project':
            build.builder.project,
        'buildbucket_build_id':
            build.id,
        'builder_group':
            self._chromium_tests_api.m.builder_group.for_current,
        'buildername':
            build.builder.builder,
        'buildnumber':
            build.number,
        # This is only set on Chromium when using ANGLE as a component. We
        # use the parent revision when available.
        'got_angle_revision':
            (self._got_revisions.get('parent_got_angle_revision') or
             self._got_revisions.get('got_angle_revision')),
        # This is only ever set on builders where the primary repo is not
        # Chromium, such as V8 or WebRTC.
        'got_cr_revision':
            self._got_revisions.get('got_cr_revision'),
        'got_revision': (self._got_revisions.get('got_revision') or
                         self._got_revisions.get('got_src_revision')),
        # Similar to got_cr_revision, but for use in repos where the primary
        # repo is not Chromium and got_cr_revision is not defined.
        'got_src_revision':
            self._got_revisions.get('got_src_revision'),
        'patch_issue':
            cl.change if cl else None,
        'patch_set':
            cl.patchset if cl else None,
        'use_permissive_angle_pixel_comparison':
            '1' if use_permissive_angle_pixel_comparison else '0',
        'xcode_build_version':
            self._chromium_tests_api.m.chromium.xcode_build_version
    }

    for conditional in raw_test_spec.get('conditional_args', []):

      def get_variable():
        if 'variable' not in conditional:
          error_message = "Conditional has no 'variable' key"
        else:
          variable = conditional['variable']
          if variable in substitutions:
            return substitutions[variable]
          error_message = "Unknown variable '{}'".format(
              conditional['variable'])
        self._chromium_tests_api.m.step.empty(
            'Invalid conditional',
            status=self._chromium_tests_api.m.step.INFRA_FAILURE,
            step_text='Test spec has invalid conditional: {}\n{}'.format(
                error_message,
                self._chromium_tests_api.m.json.dumps(
                    engine_types.thaw(raw_test_spec), indent=2),
            ))

      variable = get_variable()
      value = conditional.get('value', '')
      if (variable == value) == (not conditional.get('invert', False)):
        args.extend(conditional.get('args', []))

    return [string.Template(arg).safe_substitute(substitutions) for arg in args]

  @staticmethod
  def _normalize_optional_dimensions(optional_dimensions):
    if not optional_dimensions:
      return optional_dimensions

    normalized = {}
    for expiration, dimensions_sequence in optional_dimensions.items():
      if isinstance(dimensions_sequence, Mapping):
        dimensions = dimensions_sequence
      else:
        # TODO(https://crbug.com/1148971): Convert source side specs to use single
        # dicts rather than lists of dicts so this can be removed
        dimensions = {}
        for d in dimensions_sequence:
          dimensions.update(d)
      normalized[int(expiration)] = dimensions
    return normalized

  def _generator_common(
      self,
      raw_test_spec: _RawTestSpec,
      swarming_delegate: Callable[..., steps.TestSpec],
      local_delegate: Callable[..., steps.TestSpec],
  ) -> steps.TestSpec | None:
    """Common logic for generating tests from JSON specs.

    Args:
      raw_test_spec: The configuration of the test(s) that should be
        generated.
      swarming_delegate: Function to call to create a swarming test.
        Must take the raw test spec as a positional argument and then
        keyword arguments to be passed to the spec create call.
      local_delegate: Function to call to create a local test. Must take
        the raw test spec as a positional argument and then keyword
        arguments to be passed to the spec create call.
    """

    kwargs = {}

    target_name = raw_test_spec.get('test')
    name = raw_test_spec.get('name', target_name)

    kwargs['target_name'] = target_name
    # TODO(crbug.com/1074033): Remove full_test_target.
    kwargs['full_test_target'] = raw_test_spec.get('test_target')
    kwargs['test_id_prefix'] = raw_test_spec.get('test_id_prefix')
    kwargs['retry_only_failed_tests'] = raw_test_spec.get(
        'retry_only_failed_tests', True)
    kwargs['check_flakiness_for_new_tests'] = raw_test_spec.get(
        'check_flakiness_for_new_tests', True)
    kwargs['name'] = name

    swarming_spec = raw_test_spec.get('swarming', None)
    if swarming_spec is None:
      if self._remote_tests_only:
        return None
      return local_delegate(raw_test_spec, **kwargs)

    kwargs['server'] = swarming_spec.get('server')
    kwargs['dimensions'] = swarming_spec.get('dimensions', {})
    kwargs['optional_dimensions'] = self._normalize_optional_dimensions(
        swarming_spec.get('optional_dimensions'))
    kwargs['expiration'] = swarming_spec.get('expiration')
    kwargs['containment_type'] = swarming_spec.get('containment_type')
    kwargs['hard_timeout'] = swarming_spec.get('hard_timeout')
    kwargs['io_timeout'] = swarming_spec.get('io_timeout')
    kwargs['shards'] = swarming_spec.get('shards', 1)
    kwargs['extra_suffix'] = swarming_spec.get('extra_suffix')
    # If idempotent wasn't explicitly set, let chromium_swarming/api.py apply
    # its default_idempotent val.
    if 'idempotent' in swarming_spec:
      kwargs['idempotent'] = swarming_spec['idempotent']

    named_caches = swarming_spec.get('named_caches')
    if named_caches:
      kwargs['named_caches'] = {nc['name']: nc['path'] for nc in named_caches}

    packages = swarming_spec.get('cipd_packages')
    if packages:
      kwargs['cipd_packages'] = [
          chromium_swarming.CipdPackage.create(
              name=p['cipd_package'],
              version=p['revision'],
              root=p['location'],
          ) for p in packages
      ]

    service_account = swarming_spec.get('service_account')
    if service_account:
      kwargs['service_account'] = service_account

    realm = swarming_spec.get('realm')
    if realm:
      kwargs['realm'] = realm

    merge = dict(raw_test_spec.get('merge', {}))
    if merge:
      merge_script = merge.get('script')
      if merge_script:
        if merge_script.startswith('//'):
          merge['script'] = self._checkout_path.joinpath(
              merge_script[2:].replace('/',
                                       self._chromium_tests_api.m.path.sep))
        else:
          self._chromium_tests_api.m.step.empty(
              'test spec format error',
              status=self._chromium_tests_api.m.step.FAILURE,
              log_name='details',
              log_text=textwrap.wrap(
                  textwrap.dedent("""\
                      The test target "%s" contains a custom merge_script "%s"
                      that doesn't match the expected format. Custom
                      merge_script entries should be a path relative to the
                      top-level chromium src directory and should start with
                      "//".
                      """ % (name, merge_script))))
      kwargs['merge'] = chromium_swarming.MergeScript.create(**merge)

    trigger_script = dict(raw_test_spec.get('trigger_script', {}))
    if trigger_script:
      trigger_script_path = trigger_script.get('script')
      if trigger_script_path:
        if trigger_script_path.startswith('//'):
          trigger_script['script'] = self._checkout_path.joinpath(
              trigger_script_path[2:].replace(
                  '/', self._chromium_tests_api.m.path.sep))
        else:
          self._chromium_tests_api.m.step.empty(
              'test spec format error',
              status=self._chromium_tests_api.m.step.FAILURE,
              log_name='details',
              log_text=textwrap.wrap(
                  textwrap.dedent("""\
                  The test target "%s" contains a custom trigger_script "%s"
                  that doesn't match the expected format. Custom trigger_script
                  entries should be a path relative to the top-level chromium
                  src directory and should start with "//".
                  """ % (name, trigger_script_path))))
      kwargs['trigger_script'] = chromium_swarming.TriggerScript.create(
          **trigger_script)

    return swarming_delegate(raw_test_spec, **kwargs)

  def _generate_gtest_test_spec(
      self,
      raw_test_spec: _RawTestSpec,
  ) -> steps.TestSpec | None:
    if raw_test_spec.get('use_isolated_scripts_api'):
      return self._generate_isolated_script_test_spec(raw_test_spec)

    def gtest_delegate_common(raw_test_spec):
      common_gtest_kwargs = {}
      args = self._get_args_for_test(raw_test_spec)
      shard_index = raw_test_spec.get('shard_index', 0)
      total_shards = raw_test_spec.get('total_shards', 1)
      if shard_index != 0 or total_shards != 1:
        args.extend([
            f'--test-launcher-shard-index={shard_index}',
            f'--test-launcher-total-shards={total_shards}',
        ])
      common_gtest_kwargs['args'] = args
      return common_gtest_kwargs

    def gtest_swarming_delegate(raw_test_spec, **kwargs):
      kwargs.update(gtest_delegate_common(raw_test_spec))
      kwargs['isolate_profile_data'] = raw_test_spec.get('isolate_profile_data')
      return steps.SwarmingGTestTestSpec.create(**kwargs)

    def gtest_local_delegate(raw_test_spec, **kwargs):
      kwargs.update(gtest_delegate_common(raw_test_spec))
      kwargs['use_xvfb'] = raw_test_spec.get('use_xvfb', True)
      return steps.LocalGTestTestSpec.create(**kwargs)

    return self._generator_common(raw_test_spec, gtest_swarming_delegate,
                                  gtest_local_delegate)

  def _generate_script_test_spec(
      self,
      raw_test_spec: _RawTestSpec,
  ) -> steps.TestSpec | None:
    if self._remote_tests_only:
      return None

    all_compile_targets = self._scripts_compile_targets_fn()

    name = raw_test_spec['name']
    script = raw_test_spec['script']

    substitutions = {'name': name}

    script_args = raw_test_spec.get('args', [])
    if self._precommit_details:
      script_args.extend(raw_test_spec.get('precommit_args', []))
    else:
      script_args.extend(raw_test_spec.get('non_precommit_args', []))

    return steps.ScriptTestSpec.create(
        name,
        script=script,
        compile_targets=[
            string.Template(s).safe_substitute(substitutions)
            for s in all_compile_targets.get(script, [])
        ],
        script_args=script_args,
    )

  def _generate_isolated_script_test_spec(
      self,
      raw_test_spec: _RawTestSpec,
  ) -> steps.TestSpec | None:

    def isolated_script_delegate_common(raw_test_spec, name=None, **kwargs):
      del kwargs

      common_kwargs = {}

      # The variable substitution and precommit/non-precommit arguments
      # could be supported for the other test types too, but that wasn't
      # desired at the time of this writing.
      common_kwargs['args'] = self._get_args_for_test(raw_test_spec)
      # This features is only needed for the cases in which the *_run compile
      # target is needed to generate isolate files that contains dynamically libs.
      # TODO(nednguyen, kbr): Remove this once all the GYP builds are converted
      # to GN.
      common_kwargs['isolate_profile_data'] = raw_test_spec.get(
          'isolate_profile_data')

      # TODO(tansell): Remove this once custom handling of results is no longer
      # needed.
      results_handler_name = raw_test_spec.get('results_handler', 'default')
      if results_handler_name not in steps.ALLOWED_RESULT_HANDLER_NAMES:
        self._chromium_tests_api.m.step.empty(
            'isolated_scripts spec format error',
            status=self._chromium_tests_api.m.step.FAILURE,
            log_name='details',
            log_text=textwrap.wrap(
                textwrap.dedent("""\
                    The isolated_scripts target "%s" contains a custom
                    results_handler "%s" but that result handler was not found.
                    """ % (name, results_handler_name))))
      common_kwargs['results_handler_name'] = results_handler_name

      return common_kwargs

    def isolated_script_swarming_delegate(raw_test_spec, **kwargs):
      kwargs.update(isolated_script_delegate_common(raw_test_spec, **kwargs))
      return steps.SwarmingIsolatedScriptTestSpec.create(**kwargs)

    def isolated_script_local_delegate(raw_test_spec, **kwargs):
      kwargs.update(isolated_script_delegate_common(raw_test_spec, **kwargs))
      return steps.LocalIsolatedScriptTestSpec.create(**kwargs)

    return self._generator_common(raw_test_spec,
                                  isolated_script_swarming_delegate,
                                  isolated_script_local_delegate)

  def _generate_skylab_test_spec(
      self,
      raw_test_spec: _RawTestSpec,
  ) -> steps.TestSpec | None:
    kwargs_to_forward = set(
        k for k in attr.fields_dict(steps.SkylabTestSpec)
        if not k in ['test_args', 'resultdb'])
    common_skylab_kwargs = {
        k: v for k, v in raw_test_spec.items() if k in kwargs_to_forward
    }
    common_skylab_kwargs['test_args'] = self._get_args_for_test(raw_test_spec)
    common_skylab_kwargs['target_name'] = raw_test_spec.get('test')
    has_tag_criteria = any(key in common_skylab_kwargs for key in [
        'cros_test_tags',
        'cros_test_tags_exclude',
        'cros_test_names',
        'cros_test_names_exclude',
        'cros_test_names_from_file',
        'cros_test_names_exclude_from_file',
    ])
    assert common_skylab_kwargs.get('autotest_name') or has_tag_criteria, (
        f'{raw_test_spec} must have either autotest_name or tag-based criteria'
        'Autotest name candidates can be: chromium, chromium_Telemetry, '
        'chromium_Graphics, and etc, or legacy tast.chrome-from-gcs if '
        'tag-based criteria does not fit the need.')
    # Default test exeuction timeout to half of the total timeout to allow for
    # DUT provisioning and other overhead.
    if not common_skylab_kwargs.get('max_run_sec') and common_skylab_kwargs.get(
        'timeout_sec', 0) > 0:
      common_skylab_kwargs['max_run_sec'] = int(
          common_skylab_kwargs.get('timeout_sec') / 2)

    return steps.SkylabTestSpec.create(
        raw_test_spec.get('name'), **common_skylab_kwargs)
