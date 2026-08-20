# Copyright 2022 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import contextlib
import itertools
import json
from hashlib import md5
from requests.models import PreparedRequest

from RECIPE_MODULES.build import chromium_swarming
from recipe_engine.engine_types import freeze

from . import formatting

BUGANIZER_SEARCH_BUGS_TEMPLATE = 'https://issues.chromium.org/issues'

BUGANIZER_FILE_BUG_DESCRIPTION = '''\
Failing test: {name}
Failure link: {build_link}
{footer}
Suspected commit: <insert>
'''

BUGANIZER_CRASH_DESCRIPTION = '''\
Crash type: {crash_type}

Crash state:
{crash_state}

Error summary:
{stderr}

Crash analysis hash: {crash_analysis_hash}'''

BUGANIZER_FILE_BUG_TEMPLATE = f'{BUGANIZER_SEARCH_BUGS_TEMPLATE}/new'

FLAKO_LINK_TEMPLATE = 'Link to Flako run: <insert>'

FAILURE_BUG_DEFAULTS = {
    'title': 'starts failing',
    'footer': '',
}

FLAKE_BUG_DEFAULTS = {
    'title': 'starts flaking',
    'footer': FLAKO_LINK_TEMPLATE,
}

FLAGFUZZ_BUG_DEFAULTS = {
    'title': 'starts failing (flag fuzzer)',
    'footer': FLAKO_LINK_TEMPLATE,
}

MAX_BUG_LINKS = 5
REPRO_TIMEOUT_DEFAULT = 60
REPRO_TOTAL_TIMEOUT_DEFAULT = 120

# Exonerate builds with flaky crashes if all types are in this list.
EXONERATED_CRASH_TYPES = [
    # We keep this dummy to ease recipe testing.
    '_Magically_exonerated_crash_type_for_testing_',
]

# pylint: disable=abstract-method


class V8Variant:
  """Immutable class representing testing variants passed to v8."""
  def __init__(self, *variants):
    self.variants = variants

  def __str__(self):
    return ' '.join(self.variants)

  def pack(self):
    """Returns a serializable version of this object.

    This method is the counterpart to the method below.
    """
    return str(self)

  @staticmethod
  def unpack(packed):
    """Constructs a variant object from a serialized version of this class.

    This method is the counterpart to the method above.
    """
    return V8Variant(*(packed or '').split(' '))


def test_args_from_variants(*variants):
  """Merge variant specification from bot, test type and test step.

  Returns: Flags for the v8 test driver with either 1) all specific
      variants if any, or 2) flags for exhaustive testing.
  """
  specific_variants = [v for v in variants if v]
  if specific_variants:
    _variants = sorted(list(set(itertools.chain(
        *[v.variants for v in specific_variants]))))
  else:
    _variants = ['more', 'dev']
  assert _variants
  return ['--variants=' + ','.join(_variants)]


TEST_CONFIGS = freeze({
    'benchmarks': {
        'name': 'Benchmarks',
        'tests': ['benchmarks'],
    },
    'bigint': {
        'name': 'Bigint',
        'tests': ['bigint'],
    },
    'check-bytecode-baseline': {
        'tool': 'check-bytecode-baseline',
        'isolated_target': 'generate-bytecode-expectations',
    },
    'd8testing': {
        'name': 'Check - d8',
        'tests': ['d8_default'],
        'suite_mapping': [
            'debugger',
            'intl',
            'message',
            'mjsunit',
            'webkit',
        ],
    },
    'd8testing_random_gc': {
        'name': 'Check - d8',
        'tests': ['d8_default'],
        'suite_mapping': [
            'debugger',
            'intl',
            'message',
            'mjsunit',
            'webkit',
        ],
        'test_args': ['--random-gc-stress'],
    },
    'jetstream3': {
        'tool': 'run-jetstream3',
        'isolated_target': 'jetstream3',
    },
    'jsfunfuzz': {
        'tool': 'jsfunfuzz',
        'isolated_target': 'jsfunfuzz',
    },
    'gcmole_v2': {
        'tool': 'run-gcmole-v2',
        'isolated_target': 'run-gcmole',
    },
    'gcmole_v3': {
        'tool': 'run-gcmole-v3',
        'isolated_target': 'run-gcmole',
    },
    'mjsunit': {
        'name': 'Mjsunit',
        'tests': ['mjsunit'],
    },
    'mjsunit_sp_frame_access': {
        'name': 'Mjsunit - sp frame access',
        'tests': ['mjsunit'],
        'test_args': ['--extra-flags=--turbo_sp_frame_access'],
        'variants': V8Variant('default'),
    },
    'mozilla': {
        'name': 'Mozilla',
        'tests': ['mozilla'],
    },
    'numfuzz': {
        'name': 'Num Fuzz',
        'tool': 'run-num-fuzzer',
        'isolated_target': 'run-num-fuzzer',
        'idempotent': False,
        'use_random_seed': False,
        'variants': V8Variant('default'),
        'test_id_prefix': 'numfuzz//',
    },
    'optimize_for_size': {
        'name': 'OptimizeForSize',
        'tests': ['optimize_for_size'],
        'suite_mapping': [
            'cctest',
            'debugger',
            'mjsunit',
            'inspector',
            'intl',
            'webkit',
        ],
        'test_args': ['--extra-flags=--optimize-for-size'],
        'variants': V8Variant('default'),
    },
    'perf_integration': {
        'tool': 'run-perf',
        'isolated_target': 'perf_integration',
    },
    'pgo_instrumentation': {
        'tool': 'run-perf',
        'isolated_target': 'd8_pgo',
    },
    'test262': {
        'name': 'Test262',
        'tests': ['test262'],
    },
    'unittests': {
        'name': 'Unittests',
        'tests': ['unittests'],
    },
    'v8initializers': {
        'tool': 'check-static-initializers',
        'isolated_target': 'check-static-initializers',
    },
    'fuchsia-unittests': {
        'tool': 'fuchsia-unittests',
        'isolated_target': 'fuchsia-unittests',
    },
    'v8testing': {
        'name':
            'Check',
        'tests': ['bot_default'],
        'suite_mapping': [
            'cctest',
            'debugger',
            'fuzzer',
            'inspector',
            'intl',
            'message',
            'mjsunit',
            'mkgrokdump',
            'unittests',
            'wasm-spec-tests',
            'webkit',
        ],
    },
    'webkit': {
        'name': 'Webkit',
        'tests': ['webkit'],
    },
})


class BaseTest:

  def __init__(self, test_step_config, api):
    self.test_step_config = test_step_config
    self.name = test_step_config.name
    self.api = api

  @property
  def id(self):
    """Identifier for deduping identical test configs."""
    return self.test_step_config.name + self.test_step_config.step_name_suffix

  def create_task(self, test, raw_cmd, **kwargs):
    isolated_target = test.get('isolated_target')
    if not isolated_target:
      # Normally we run only one test and the isolate name is the same as the
      # test name.
      assert len(test['tests']) == 1
      isolated_target = test['tests'][0]

    cas_digest = self.api.v8_tests.isolated_tests.get(isolated_target)
    assert cas_digest
    assert '/' in cas_digest

    if raw_cmd[0].endswith('.py'):
      raw_cmd = ['vpython3', '-u'] + raw_cmd

    task = self.api.chromium_swarming.task(
        cas_input_root=cas_digest, raw_cmd=raw_cmd, **kwargs)
    if self.api.v8_tests.resultdb:
      request = task.request.with_resultdb()
      wrapperd_cmd = self.api.v8_tests.resultdb.wrap(
          self.api,
          raw_cmd,
          base_variant=self.base_variant(),
          exonerate_unexpected_pass=False,
          test_id_prefix=test.get('test_id_prefix', '//'))

      request_slice = request[0].with_command(wrapperd_cmd)
      task.request = request.with_slice(0, request_slice)

    return task

  def base_variant(self):
    vatiant_tags = {
        'bucket': self.api.buildbucket.build.builder.bucket,
        'builder': self.api.buildbucket.builder_name,
    }
    if self.test_step_config.suffix:
      vatiant_tags['test_config'] = self.test_step_config.suffix
    return vatiant_tags

  @property
  def uses_swarming(self):
    """Returns true if the test uses swarming."""
    return False

  def apply_filter(self):
    # Run all tests by default.
    return True

  def pre_run(self, test=None, **kwargs):
    """Callback preparing test runs."""

  def mid_run(self):
    """Callback for things happening after pre_run and before run."""

  def run(self, source_dir, test=None, **kwargs):
    """Callback for showing test runs.

    Each step in this callback will be shown on top-level.
    """
    raise NotImplementedError()  # pragma: no cover

  def rerun(self, source_dir, failure_dict, **kwargs):  # pragma: no cover
    raise NotImplementedError()


class V8Test(BaseTest):
  def __init__(self, *args, **kwargs):
    super().__init__(*args, **kwargs)
    self.applied_test_filter = ''

  def apply_filter(self):
    test_config = self.api.v8_tests.test_configs[self.name]
    self.applied_test_filter = self.api.v8_tests._applied_test_filter(
        test_config)
    if self.api.v8_tests.test_filter and not self.applied_test_filter:
      self.api.step(test_config['name'] + ' - skipped', cmd=None)
      return False
    return True

  def run(self, source_dir, test=None, **kwargs):
    test = test or self.api.v8_tests.test_configs[self.name]

    full_args, env = self.api.v8_tests._setup_test_runner(
        test, self.applied_test_filter, self.test_step_config)
    full_args += [
      '--json-test-results',
      self.api.json.output(add_json_log=False),
    ]
    script = source_dir.joinpath('tools', 'run-tests.py')
    with self.api.context(cwd=source_dir, env=env):
      try:
        self.api.step(
          test['name'] + self.test_step_config.step_name_suffix,
          ['vpython3', '-u', script] + full_args,
          step_test_data=self.api.v8_tests.test_api.output_json,
          **kwargs
        )
      except self.api.step.StepFailure as e:
        if e.retcode != 1:
          # Return code == 1: Normal test failures that are also listed in the
          # json output and handled in post_run.
          raise
    return self.post_run(test)

  def post_run(self, test):
    # The active step was either a local test run or the swarming collect step.
    step_result = self.api.step.active_result
    json_output = step_result.json.output

    # Log used test filters.
    if self.applied_test_filter:
      step_result.presentation.logs['test filter'] = self.applied_test_filter

    assert isinstance(json_output, dict)
    self.api.v8_tests.update_slowest(json_output, step_result.presentation)
    self.api.v8_tests.update_max_rss(json_output, step_result.presentation)
    self.api.v8_tests.update_max_vms(json_output, step_result.presentation)
    failure_factory = Failure.factory_func(self)
    failure_log, failures, flake_log, flakes = (
        self.api.v8_tests._get_failure_logs(json_output, failure_factory))
    self.api.v8_tests._update_failure_presentation(
        failure_log, failures, step_result.presentation)
    self._add_bug_links(failures, step_result.presentation)

    if failure_log and failures:
      # Mark the test step as failure only if there were real failures (i.e.
      # non-flakes) present.
      step_result.presentation.status = self.api.step.FAILURE

    infra_failures = []
    if 'UNRELIABLE_RESULTS' in json_output.get('tags', []):
      infra_failures.append('One ore more shards did not complete.')
      step_result.presentation.status = self.api.step.EXCEPTION

    if flake_log and flakes:
      # Emit a separate step to show flakes from the previous step
      # to not close the tree.
      step_result = self.api.step(
          test['name'] + self.test_step_config.step_name_suffix + ' (flakes)',
          cmd=None)
      # TODO(sergiyb): Use WARNING result type after crbug.com/854099 is fixed.
      step_result.presentation.status = self.api.step.FAILURE
      self.api.v8_tests._update_failure_presentation(
            flake_log, flakes, step_result.presentation)
      self._add_bug_links(flakes, step_result.presentation)

    return TestResults(failures, flakes, infra_failures,
                       json_output['test_total'])

  def _add_bug_links(self, failures, presentation):
    """Adds links to search/file bugs for up to MAX_BUG_LINKS tests."""
    for failure in failures[:MAX_BUG_LINKS]:
      link = PreparedRequest()
      ui_label = self.api.v8_tests.ui_test_label(failure.name)
      link_params = failure.get_buganizer_params(
          self.api.buildbucket.build_url())

      search_query = ' -status:Fixed -status:Verified'
      bug_description = BUGANIZER_FILE_BUG_DESCRIPTION
      if link_params['crash_analysis_hash']:
        search_query = '"{crash_analysis_hash}"' + search_query
        bug_description += BUGANIZER_CRASH_DESCRIPTION
      else:
        search_query = '"{name}"' + search_query

      bug_search_link_params = {
          'q': search_query.format(**link_params),
      }

      bug_file_link_params = {
          'priority': 'P1',
          'component': 1456824,  # Chromium > Blink > JavaScript
          'type': 'Bug',
          'title': f'{link_params["name"]} {link_params["title"]}',
          'description': bug_description.format(**link_params),
      }

      link.prepare_url(BUGANIZER_SEARCH_BUGS_TEMPLATE, bug_search_link_params)
      presentation.links['%s (bugs)' % ui_label] = link.url
      link.prepare_url(BUGANIZER_FILE_BUG_TEMPLATE, bug_file_link_params)
      presentation.links['%s (new)' % ui_label] = link.url
    if len(failures) > MAX_BUG_LINKS:
      presentation.step_text += (
          'too many failures, only showing some links below<br/>')

  def _setup_rerun_config(self, failure_dict):
    """Return: A test config that reproduces a specific failure."""
    # Make sure bisection is only activated on builders that give enough
    # information to retry.
    assert failure_dict.get('variant')
    assert failure_dict.get('random_seed')

    orig_config = self.api.v8_tests.test_configs[self.name]

    # If not specified, the isolated target is the same as the first test of
    # the original list. We need to set it explicitly now, as the tests
    # parameter changes on rerun, but the isolated target is still the same.
    isolated_target = orig_config.get(
        'isolated_target', orig_config['tests'][0])

    test_args = list(orig_config.get('test_args', [])) + [
      '--random-seed', str(failure_dict['random_seed']),
    ]

    rerun_config = {
      'name': 'Retry',
      'isolated_target': isolated_target,
      'tests': [failure_dict['name']],
      'test_args': test_args,
      'variants': V8Variant(failure_dict['variant'])
    }

    # Switch off test filters on rerun.
    self.applied_test_filter = None
    return rerun_config

  def rerun(self, source_dir, failure_dict, **kwargs):
    return self.run(
        source_dir, test=self._setup_rerun_config(failure_dict), **kwargs)


def _trigger_swarming_task(api, task, test_step_config):
  """Triggers a task on swarming setting custom dimensions and task attributes.

  Args:
    api: Recipe modules api.
    task: Task object from swarming recipe module.
    test_step_config: Configuration object used to configure this task. Contains
        e.g. dimension and task-attribute overrides.
  """
  task_slice = task.request[0]
  task_dimensions = task_slice.dimensions

  # Override with per-test dimensions.
  task_dimensions.update(test_step_config.swarming_dimensions or {})

  # Override cpu defaults for Android as such devices don't have this
  # dimension.
  if task_dimensions['os'] == 'Android':
    task_dimensions['cpu'] = None

  task_slice = task_slice.with_dimensions(**task_dimensions)

  override_swarming_attrs(task, task_slice,
                          test_step_config.swarming_task_attrs)

  api.chromium_swarming.trigger_task(task)

  # Remove 'invocations/' because it is added again in include_invocations.
  api.resultdb.include_invocations(
      [i[len('invocations/'):] for i in task.get_invocation_names()])


def override_swarming_attrs(task, task_slice, attrs):
  if attrs.get('hard_timeout'):
    task_slice = task_slice.with_execution_timeout_secs(
        int(attrs['hard_timeout']))
  if attrs.get('expiration'):
    task_slice = task_slice.with_expiration_secs(int(attrs['expiration']))
  task.request = task.request.with_slice(0, task_slice)
  if attrs.get('priority'):
    task.request = task.request.with_priority(int(attrs['priority']))
  if attrs.get('service_account'):
    task.request = task.request.with_service_account(attrs['service_account'])


class V8SwarmingTest(V8Test):
  def __init__(self, *args, **kwargs):
    super().__init__(*args, **kwargs)
    self.task = None
    self.test = None

  @property
  def uses_swarming(self):
    """Returns true if the test uses swarming."""
    return True

  def _v8_collect_step(self, task, **kwargs):
    """Produces a step that collects and processes a result of a v8 task."""
    # Placeholders for the merged json output and warnings during collection.
    json_output = self.api.json.output(add_json_log=False)
    warnings_json = self.api.json.output(name='warnings', add_json_log=False)

    # Shim script's own arguments.
    args = [
        '--temp-root-dir',
        self.api.path.tmp_base_dir,
        '--merged-test-output',
        json_output,
        '--warnings-json',
        warnings_json,
    ]

    # Arguments for actual 'collect' command.
    args.append('--')
    args.extend(
        self.api.chromium_swarming.get_collect_cmd_args(
            task.collect_cmd_input()))

    with self.api.swarming.on_path():
      with self.api.context(infra_steps=True):
        script = self.api.v8_tests.resource('collect_v8_task.py')
        return self.api.step(
            self.test['name'] + self.test_step_config.step_name_suffix,
            ['python3', '-u', script] + args,
            step_test_data=kwargs.pop('step_test_data', None),
            **kwargs)

  def _post_process_warnings(self):
    step_result = self.api.step.active_result
    warnings = step_result.json.outputs['warnings']
    for warning in warnings or []:
      step_result.presentation.logs[warning[0]] = warning[1].splitlines()

  def pre_run(self, test=None, **kwargs):
    # Set up arguments for test runner.
    self.test = test or self.api.v8_tests.test_configs[self.name]
    extra_args, _ = self.api.v8_tests._setup_test_runner(
        self.test, self.applied_test_filter, self.test_step_config)

    # Let json results be stored in swarming's output folder. The collect
    # step will copy the folder's contents back to the client.
    extra_args += [
        '--swarming',
        '--json-test-results',
        '${ISOLATED_OUTDIR}/output.json',
    ]

    # Initialize number of shards, either per test or per builder.
    shards = 1
    if self.api.v8_tests.c.testing.may_shard:
      shards = self.test_step_config.shards

    command = ['tools/%s.py' % self.test.get('tool', 'run-tests')]
    idempotent = self.test.get('idempotent')

    # Initialize swarming task with custom data-collection step for v8
    # test-runner output.
    self.task = self.create_task(
        self.test,
        name=self.test['name'] + self.test_step_config.step_name_suffix,
        idempotent=idempotent,
        shards=shards,
        raw_cmd=command + extra_args,
        **kwargs
    )
    self.task.collect_step = self._v8_collect_step

    _trigger_swarming_task(self.api, self.task, self.test_step_config)

  def run(self, source_dir, test=None, **kwargs):
    assert self.task
    result = TestResults.empty()
    try:
      # Collect swarming results. Use the same test simulation data for the
      # swarming collect step like for local testing.
      self.api.chromium_swarming.collect_task(
        self.task,
        step_test_data=self.api.v8_tests.test_api.output_json,
      )
    except self.api.step.InfraFailure as e:
      result += TestResults.infra_failure(e)

    self._post_process_warnings()

    return result + self.post_run(self.test)

  def rerun(self, source_dir, failure_dict, **kwargs):
    self.pre_run(test=self._setup_rerun_config(failure_dict), **kwargs)
    return self.run(source_dir, **kwargs)


class V8GenericSwarmingTest(BaseTest):
  # FIXME: BaseTest.rerun is an abstract method which isn't implemented in this
  # class.  Should it be abstract?
  def __init__(self, test_step_config, api, title=None, command=None):
    super().__init__(test_step_config, api)
    self._command = command or []
    self._title = (
        title or
        self.api.v8_tests.test_configs[self.name].get('name', 'Generic test'))
    self.test = None
    self.task = None

  @property
  def title(self):
    return self._title + self.test_step_config.step_name_suffix

  @property
  def command(self):
    """Command to pass to the swarming task."""
    return self._command

  @property
  def task_output_dir(self):
    return None  # pragma: no cover

  @property
  def uses_swarming(self):
    """Returns true if the test uses swarming."""
    return True

  def pre_run(self, test=None, **kwargs):
    self.test = test or self.api.v8_tests.test_configs[self.name]
    self.task = self.create_task(
        self.test,
        name=self.title,
        task_output_dir=self.task_output_dir,
        raw_cmd=self.command or [],
        **kwargs
    )

    _trigger_swarming_task(self.api, self.task, self.test_step_config)

  def run(self, source_dir, test=None, **kwargs):
    assert self.task
    step_result, _ = self.api.chromium_swarming.collect_task(self.task)
    self.api.step.raise_on_failure(step_result)
    return TestResults.not_empty()


class V82PhaseGenericSwarmingTest(BaseTest):
  """Framework for dependent tasks on swarming, executed in two phases.

  - Trigger/collect tasks of phase 1.
  - Perform local calculations (e.g. reduce results).
  - Trigger/collect tasks of phase 2.
  """

  def __init__(self, *args, **kwargs):
    super().__init__(*args, **kwargs)
    # State stored between callbacks.
    self.test = None
    self.task = None
    self.output_dir = None

  @property
  def uses_swarming(self):
    return True

  @property
  def title(self):
    """Step name prefix used across all phases."""
    raise NotImplementedError()  # pragma: no cover

  @property
  def command_phase1(self):
    """Command passed to the swarming tasks of phase 1."""
    raise NotImplementedError()  # pragma: no cover

  @property
  def command_phase2(self):
    """Command passed to the swarming tasks of phase 2."""
    raise NotImplementedError()  # pragma: no cover

  @property
  def shards(self):
    """Number of shards used in both phase 1 and 2."""
    raise NotImplementedError()  # pragma: no cover

  @property
  def idempotent(self):
    """Wheather tasks are idempotent in both phase 1 and 2."""
    raise NotImplementedError()  # pragma: no cover

  def prepare_local(self, workspace):
    """Callback executed while waiting for tasks of phase 1."""
    raise NotImplementedError()  # pragma: no cover

  def run_local(self, workspace):
    """Callback executed after collecting tasks of phase 1.

    Results are collected to self.output_dir.
    """
    raise NotImplementedError()  # pragma: no cover

  def pre_run(self, test=None, **kwargs):
    """Trigger tasks of phase 1."""
    self.test = test or self.api.v8_tests.test_configs[self.name]
    self.output_dir = self.api.path.mkdtemp('swarming_output')

    self.task = self.create_task(
        self.test,
        name=f'{self.title} - prepare',
        idempotent=self.idempotent,
        shards=self.shards,
        task_output_dir=self.output_dir,
        raw_cmd=self.command_phase1,
        **kwargs
    )

    _trigger_swarming_task(self.api, self.task, self.test_step_config)

  def mid_run(self):
    """Transition between phase 1 and 2.

    - Prepare local workspace in a temporary directory while waiting.
    - Collect tasks of phase 1.
    - Perform any local calculations via callback.
    - Trigger tasks of phase 2.

    Note, if multiple 2-phase tests are run on the same builder, the parts
    "prepare workspace" and "collect phase 1" are executed sequencially for
    each 2-phase test. If workspace preparation becomes a bottleneck, we
    could split this method into two, doing all workspace preparations in
    sequence first, before waiting for the first phase-1 task.
    """
    with self.api.step.nest(f'{self.title} - local',):
      workspace = self.api.path.mkdtemp('workspace')

      with self.api.context(cwd=workspace):
        self.prepare_local(workspace)

        assert self.task
        step_result, _ = self.api.chromium_swarming.collect_task(self.task)
        self.api.step.raise_on_failure(step_result)

        self.run_local(workspace)

      self.task = self.create_task(
          self.test,
          name=self.title,
          idempotent=self.idempotent,
          shards=self.shards,
          raw_cmd=self.command_phase2,
      )

      _trigger_swarming_task(self.api, self.task, self.test_step_config)

  def run(self, source_dir, test=None, **kwargs):
    """Collect tasks of phase 2."""
    assert self.task
    step_result, _ = self.api.chromium_swarming.collect_task(self.task)
    self.api.step.raise_on_failure(step_result)
    return TestResults.not_empty()


class V8CompositeSwarmingTest(BaseTest):
  # FIXME: BaseTest.rerun is an abstract method which isn't implemented in this
  # class.  Should it be abstract?
  def __init__(self, *args, **kwargs):
    super().__init__(*args, **kwargs)
    self.composites = []

  @property
  def composite_tests(self):
    """Returns: An iterable of V8GenericSwarmingTest instances."""
    raise NotImplementedError()  # pragma: no cover

  @property
  def uses_swarming(self):
    """Returns true if the test uses swarming."""
    return True

  def pre_run(self, test=None, **kwargs):
    self.composites = list(self.composite_tests)
    for c in self.composites:
      c.pre_run(test, **kwargs)

  def run(self, source_dir, test=None, **kwargs):
    for c in self.composites:
      c.run(test, **kwargs)
    return TestResults.not_empty()


class V8CheckInitializers(V8GenericSwarmingTest):
  @property
  def title(self):
    return 'Static-Initializers'

  @property
  def command(self):
    return [
      'tools/check-static-initializers.sh',
      self.api.v8_tests.relative_path_to_d8,
    ]

class V8FuchsiaUnittests(V8GenericSwarmingTest):
  @property
  def title(self):
    return 'Unittests'

  @property
  def command(self):
    return [
      self.api.path.join(
          'out', 'build', 'bin', 'run_v8_unittests'),
    ]

class V8CheckBytecodeBaseline(V8GenericSwarmingTest):
  @property
  def title(self):
    return 'Bytecode-Baseline'

  @property
  def command(self):
    return [
      self.api.path.join(
          'out', 'build', 'generate-bytecode-expectations'),
      '--check-baseline',
    ]


class V8Fuzzer(V8GenericSwarmingTest):
  def __init__(self, test_step_config, api, title='Generic test',
               command=None):
    self.output_dir = api.path.mkdtemp('swarming_output')
    self.archive = 'fuzz-results-%s.tar.bz2' % (
        api.properties['parent_got_revision'])
    super().__init__(
        test_step_config,
        api,
        title='Fuzz',
        command=[
            'tools/jsfunfuzz/fuzz-harness.sh',
            api.v8_tests.relative_path_to_d8,
            '${ISOLATED_OUTDIR}/%s' % self.archive,
        ],
    )

  @property
  def task_output_dir(self):
    return self.output_dir

  def run(self, source_dir, test=None, **kwargs):
    try:
      super().run(test, **kwargs)
    except self.api.step.StepFailure as e:
      self.api.gsutil.upload(
          self.output_dir.joinpath(self.task.get_task_shard_output_dirs()[0],
                                   self.archive),
          'chromium-v8',
          self.api.path.join('fuzzer-archives', self.archive),
      )
      raise e
    return TestResults.not_empty()


class V8GCMoleV2(V8GenericSwarmingTest):
  @property
  def title(self):
    return f'GCMole{self.test_step_config.step_name_suffix}'

  @property
  def command(self):
    return [
        'tools/gcmole/run-gcmole.py',
        str(self.test_step_config.variants),
    ] + self.test_step_config.test_args


class V8GCMoleV3(V82PhaseGenericSwarmingTest):
  @property
  def title(self):
    return f'GCMole{self.test_step_config.step_name_suffix}'

  @property
  def command_phase1(self):
    return [
      'tools/gcmole/run-gcmole.py',
      'collect',
      str(self.test_step_config.variants),
      '--output', '${ISOLATED_OUTDIR}/callgraph.bin',
    ] + self.test_step_config.test_args

  @property
  def command_phase2(self):
    return [
      'tools/gcmole/run-gcmole.py',
      'check',
      str(self.test_step_config.variants),
    ] + self.test_step_config.test_args

  @property
  def shards(self):
    return self.test_step_config.shards

  @property
  def idempotent(self):
    return True

  def prepare_local(self, workspace):
    """Get the isolated gcmole archive to prepare running locally.

    This is performed while waiting for other tasks, so its runtime cost is
    irrelevant, since it's much smaller.
    """
    target = self.test.get('isolated_target')
    cas_digest = self.api.v8_tests.isolated_tests.get(target)
    self.api.cas.download('download', cas_digest, workspace)

  def run_local(self, workspace):
    """Locally merge partial gcmole callgraphs returned from phase-1 tasks and
    upload results to CAS.
    """
    command = [
        'python3',
        '-u',
        workspace.joinpath('tools', 'gcmole', 'run-gcmole.py'),
        'merge',
        str(self.test_step_config.variants),
    ] + self.test_step_config.test_args
    for taskdir in self.task.get_task_shard_output_dirs():
      command += ['--input', self.output_dir.joinpath(taskdir, 'callgraph.bin')]

    self.api.step('Merge callgraphs', command)

    with self.api.context(cwd=workspace):
      isolate_file = self.api.path.join(workspace,
                                        'merged_gcmole_files.isolate')
      self.api.isolate.write_isolate_file(isolate_file, ['.'])
      digest = self.api.isolate.isolate('Archive workspace', isolate_file)
      target = self.test.get('isolated_target')
      self.api.v8_tests.isolated_tests[target] = digest


class V8RunPerf(V8CompositeSwarmingTest):
  @property
  def composite_tests(self):
    return [
      V8GenericSwarmingTest(
          self.test_step_config, self.api,
          title='JSTests%d' % i,
          command=[
            'tools/run_perf.py',
            'test/js-perf-test/JSTests%d.json' % i,
            '--arch', 'x64',
            '--buildbot',
            # Low run-count for more throughput. Run fastest shard (1) twice
            # to cover code adding up multiple results.
            '--run-count=%d' % (2 if i == 1 else 1),
          ],
      ) for i in range(1, 6)
    ]


class V8RunJetStream3(V8CompositeSwarmingTest):

  @property
  def composite_tests(self):
    return [
        V8GenericSwarmingTest(
            self.test_step_config,
            self.api,
            title='JetStream3',
            command=[
                'tools/run_perf.py',
                'test/benchmarks/JetStream3.json',
                '--arch',
                'x64',
                '--buildbot',
            ],
        )
    ]


TOOL_TO_TEST = freeze({
  'run-tests': V8Test,
})


TOOL_TO_TEST_SWARMING = freeze({
    'check-bytecode-baseline': V8CheckBytecodeBaseline,
    'check-static-initializers': V8CheckInitializers,
    'jsfunfuzz': V8Fuzzer,
    'run-gcmole-v2': V8GCMoleV2,
    'run-gcmole-v3': V8GCMoleV3,
    'run-num-fuzzer': V8SwarmingTest,
    'run-jetstream3': V8RunJetStream3,
    'run-perf': V8RunPerf,
    'run-tests': V8SwarmingTest,
    'fuchsia-unittests': V8FuchsiaUnittests,
})


class Failure:
  """Represents a test run leading to a failure (possibly re-run several times).
  """
  def __init__(self, test, results):
    """
    Args:
      test: Test (type V8Test) that led to this failure.
      results: List of failure dicts with one item per run of the test. The
          first item is the original failure, the other items are re-runs for
          flake checking. Each failure dict consists of the data as returned by
          the V8-side test runner.
    """
    assert results
    assert test
    self.test = test
    self.results = results
    # A failure is flaky if not all results are the same (e.g. all 'FAIL').
    self.is_flaky = not all(
        x['result'] == results[0]['result'] for x in results)

  def get_buganizer_params(self, build_link):
    if self.framework_name == 'num_fuzzer':
      link_params = FLAGFUZZ_BUG_DEFAULTS
    elif self.is_flaky:
      link_params = FLAKE_BUG_DEFAULTS
    else:
      link_params = FAILURE_BUG_DEFAULTS
    analysis_failure_values = ['NULL', 'Unknown', '', None]
    if self.crash_state in analysis_failure_values:
      crash_analysis_hash = None
    else:
      crash_analysis_hash = md5((self.crash_type + self.crash_state +
                                 '1').encode('utf-8')).hexdigest()
    return dict(
        link_params,
        name=self.name,
        build_link=build_link,
        crash_type=self.crash_type,
        crash_state=self.crash_state,
        stderr='\n'.join(self.stderr.splitlines()[:20])[:2000],
        crash_analysis_hash=crash_analysis_hash)

  @property
  def failure_dict(self):
    return self.results[0]

  @property
  def framework_name(self):
    return self.failure_dict.get('framework_name')

  @property
  def duration(self):
    return self.failure_dict['duration']

  @property
  def name(self):
    return self.failure_dict['name']

  @property
  def stderr(self):
    return self.failure_dict.get('stderr')

  @property
  def crash_type(self):
    return self.failure_dict.get('crash_type')

  @property
  def crash_state(self):
    return self.failure_dict.get('crash_state')

  @property
  def is_exonerated(self):
    return self.crash_type in EXONERATED_CRASH_TYPES

  @property
  def shard_info(self):
    index = self.failure_dict.get('shard_id', 0)
    count = self.failure_dict.get('shard_count', 1)
    if count > 1:
      return f'{index}:{count}'
    return ''

  @property
  def test_step_config(self):
    return self.test.test_step_config

  @property
  def api(self):
    return self.test.api

  def _format_swarming_dimensions(self, dims):
    return ['%s:%s' % (k, v) for k, v in dims.items()]

  def _flako_properties(self):
    test_config = self.api.v8_tests.test_configs[self.test.name]

    # In order to use the test runner to also repro cases from the number
    # fuzzer, we have to ignore the arguments passed to the fuzzer and instead
    # pass the flags the fuzzer used for that particular test. Also variants
    # are not used on the fuzzer, which is the same as using 'default'.
    extra_args = [f'--framework={self.framework_name or "default"}']
    if self.framework_name == 'num_fuzzer':
      for flag in self.results[0]['variant_flags']:
        extra_args += ['--extra-flags', flag]
      variant = 'default'
    else:  # Standard test runner.
      # TODO(machenbach): The api should hide the details how to get the args.
      extra_args += list(test_config.get('test_args', []))
      extra_args += list(self.api.v8_tests.c.testing.test_args)
      extra_args += list(self.test_step_config.test_args)
      variant = self.failure_dict['variant']

    properties = {
        # This assumes the builder's group is the same as the tester.
        'bisect_builder_group':
            self.api.builder_group.for_current,
        # Use builds from parent builder to bisect if any.
        'bisect_buildername':
            self.api.properties.get('parent_buildername')
            or self.api.buildbucket.builder_name,
        # Start bisecting backwards at the revision that was tested.
        'revision':
            self.api.buildbucket.gitiles_commit.id,
        # Use the same dimensions as the swarming task that ran this test.
        'swarming_dimensions':
            self._format_swarming_dimensions(
                self.test.task.request[0].dimensions),
        # The isolated name is either specified in the test configurations or
        # corresponds to the name of the test suite.
        'isolated_name':
            test_config.get('isolated_target') or test_config['tests'][0],
        # Full qualified test name that failed (e.g. mjsunit/foo/bar).
        'test_name':
            self.name,
        # Add timeout default for convenience.
        'timeout_sec':
            REPRO_TIMEOUT_DEFAULT,
        # Add total timeout default for convenience.
        'total_timeout_sec':
            REPRO_TOTAL_TIMEOUT_DEFAULT,
        # The variant the failing test ran in.
        'variant':
            variant,
        # Extra arguments passed to the V8 test runner.
        'extra_args':
            extra_args,
    }

    return properties

  def _local_repro_cmd_line(self):
    """Returns the command line for reproducing the flake locally."""
    test_properties = self._flako_properties()
    base_cmd = [
        'tools/run-tests.py', '--outdir=SET_OUTDIR_HERE',
        '--variants=%s' % test_properties['variant'],
        '--random-seed-stress-count=1000000',
        '--total-timeout-sec=%d' % test_properties['total_timeout_sec'],
        '--exit-after-n-failures=1'
    ]

    base_cmd += test_properties['extra_args']
    base_cmd.append(test_properties['test_name'])

    return ' '.join(base_cmd)

  def _flako_cmd_line(self):
    """Returns the command line for bisecting this failure with flako."""
    return 'bb add v8/try.triggered/v8_flako %s' % ' '.join(
        '-p \'%s=%s\'' % (k, json.dumps(v, sort_keys=True))
        for k, v in self._flako_properties().items())

  def log_lines(self):
    """Return a list of lines for logging all runs of this failure."""
    lines = []

    # Add common description for multiple runs.
    flaky_suffix = ' (flaky in a repeated run)' if self.is_flaky else ''
    lines.append('Test: %s%s' % (self.name, flaky_suffix))
    lines.append('Flags: %s' % ' '.join(self.failure_dict['flags']))
    lines.append('Command: %s' % self.failure_dict['command'])
    lines.append('Variant: %s' % self.failure_dict['variant'])

    if self.shard_info:
      lines.append(f'Shard: {self.shard_info}')

    lines.append('')
    lines.append('GN arguments:')
    if self.api.v8_tests.gn_args is None:
      lines.append(
          'Not available. Please look up the builder\'s configuration.')
    else:
      lines.extend(self.api.v8_tests.gn_args)
    lines.append('')

    # Print the command line for flake bisect.
    if (isinstance(self.test, V8SwarmingTest) and
        not self.api.tryserver.is_tryserver):
      lines.append('Trigger flake bisect on command line:')
      lines.append(self._flako_cmd_line())
      lines.append('')

      lines.append('Local flake reproduction on command line:')
      lines.append(self._local_repro_cmd_line())
      lines.append('')

    # Add results for each run of a command.
    for result in sorted(self.results, key=lambda r: int(r['run'])):
      lines.append('Run #%d' % int(result['run']))
      hex_value = '0x%02X' % (result['exit_code'] & 0xffffffff)
      lines.append('Exit code: %s [%s]' % (result['exit_code'], hex_value))
      lines.append('Result: %s' % result['result'])
      if result.get('expected'):
        lines.append('Expected outcomes: %s' % ", ".join(result['expected']))
      lines.append('Duration: %s' % formatting.duration(result))
      lines.append('')
      if result.get('crash_type'):
        lines.append('Crash Type:')
        lines.extend(result['crash_type'].splitlines())
        lines.append('')
      if result.get('crash_state'):
        lines.append('Crash State:')
        lines.extend(result['crash_state'].splitlines())
        lines.append('')
      if result['stdout']:
        lines.append('Stdout:')
        lines.extend(result['stdout'].splitlines())
        lines.append('')
      if result['stderr']:
        lines.append('Stderr:')
        lines.extend(result['stderr'].splitlines())
        lines.append('')
    return lines

  @staticmethod
  def factory_func(test):
    def create(results):
      return Failure(test, results)
    return create


class TestResults:

  def __init__(self, failures, flakes, infra_failures, num_tests):
    self.failures = failures
    self.flakes = flakes
    self.infra_failures = infra_failures
    self.num_tests = num_tests

  @staticmethod
  def empty():
    return TestResults([], [], [], 0)

  @staticmethod
  def not_empty():
    return TestResults([], [], [], 1)

  @staticmethod
  def infra_failure(exception):
    return TestResults([], [], [exception], 1)

  @property
  def is_negative(self):
    flakes_not_exonerated = not all(
        flake.is_exonerated for flake in self.flakes)
    return bool(self.failures or flakes_not_exonerated or self.infra_failures)

  @property
  def has_failures(self):
    return bool(self.failures or self.infra_failures)

  def __add__(self, other):
    return TestResults(
        self.failures + other.failures,
        self.flakes + other.flakes,
        self.infra_failures + other.infra_failures,
        self.num_tests + other.num_tests,
    )


class TestGroup:

  def __init__(self, api, tests):
    self.api = api
    self.tests = tests
    self.failed_tests = []
    self.test_results = TestResults.empty()

  def pre_run(self):
    """Executes the |pre_run| method of each test."""
    for test in self.tests:
      with self.run_checked(test):
        test.pre_run()

  def mid_run(self):
    """Executes the |mid_run| method of each test."""
    for test in self.tests:
      with self.run_checked(test):
        test.mid_run()

  def run(self, source_dir):
    """Executes the |run| method of each test."""
    for test in self.tests:
      with self.run_checked(test):
        self.test_results += test.run(source_dir)

  @contextlib.contextmanager
  def run_checked(self, test):
    try:
      yield
    except self.api.step.InfraFailure:  # pragma: no cover
      raise
    except self.api.step.StepFailure:  # pragma: no cover
      self.failed_tests.append(test.name)

  def raise_on_failure(self):
    if self.failed_tests:
      raise self.api.step.StepFailure(
          '%d tests failed: %r' % (len(self.failed_tests), self.failed_tests))

  def raise_on_empty(self):
    if self.test_results.num_tests == 0:
      raise self.api.step.StepFailure('No tests were run')


def create_test(test_step_config, api):
  if api.v8_tests.enable_swarming:
    tools_mapping = TOOL_TO_TEST_SWARMING
  else:
    tools_mapping = TOOL_TO_TEST

  # The tool the test is going to use. Default: V8 test runner (run-tests).
  tool = api.v8_tests.test_configs[test_step_config.name].get(
      'tool', 'run-tests')
  test_cls = tools_mapping[tool]
  return test_cls(test_step_config, api)
