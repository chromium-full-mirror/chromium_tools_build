# Copyright 2022 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import collections
import re

from google.protobuf import timestamp_pb2
from recipe_engine import recipe_api

from PB.go.chromium.org.luci.analysis.proto.v1 import common as common_v1
from PB.go.chromium.org.luci.analysis.proto.v1 import predicate as predicate_pb2
from PB.go.chromium.org.luci.analysis.proto.v1 import (
  test_verdict as test_verdict_pb2,
)

from .monorail_api import MonorailApi


def nest_step(func):
  """Wrap the class method into a nested step."""

  def wrapper(self, *args, **kwargs):
    with self.m.step.nest(func.__name__.strip('_')):
      return func(self, *args, **kwargs)

  wrapper.__name__ = func.__name__
  return wrapper


BuilderVerifyResult = collections.namedtuple(
  'BuilderVerifyResult',
  ['task_id', 'reproduced_runs', 'total_runs', 'duration', 'error'],
)
BuilderVerifyResult.__new__.__defaults__ = (None, 0, 0, 0, None)


class FlakyReproducer(recipe_api.RecipeApi):
  """Module for Chrome Flaky Reproducer Controller.

  The FlakyReproducer.run is the entrypoint that runs as a recipe controls the
  execution of the strategies and verifications.
  """

  RUNNER_PACKAGE_PATH = 'flaky_reproducer_runner'
  TEST_BINARY_ISOLATE_FILENAME = 'runner.isolate'
  TEST_BINARY_JSON_FILENAME = 'test_binary.json'
  RESULT_SUMMARY_FILENAME = 'result_summary.json'
  REPRODUCING_STEP_FILENAME = 'reproducing_step.json'
  VERIFY_RESULT_SUMMARY_FILENAME = 'result_summary_$N$.json'
  MONORAIL_LABEL = 'Flaky-Reproduced'
  PREFERRED_BUILDERS_RE = re.compile(
    r'(Linux Tests|Mac[\d\.]+ Tests|Win10 Tests x64)'
  )
  SUPPRESS_BUILDER_RE = re.compile(
    r'.*(android|ios|fieldtrial|reviver|backuprefptr|code-coverage).*',
    re.IGNORECASE,
  )
  TEST_BINARY_API_RUNNER = "test_binary_api_runner.py"
  STRATEGY_API_RUNNER = "strategy_api_runner.py"

  # Chromite includes a symlink which points to a file it expects to exist in a
  # chroot. We aren't using chromite in a chroot, so this is an invalid symlink.
  # This causes `cas archive` commands which have this directory to fail, so for
  # now we're removing this directory if we see it in any isolate we modify.
  # See https://crbug.com/1298283 for more detail.
  CHROMITE_BAD_SYMLINK_DIR = (
    'third_party',
    'chromite',
    'sdk',
    'etc',
    'bash_completion.d',
  )

  def _api_runner(self, script: str, method: str, test_data=None, **kwargs):
    """
    Run |method| from resources/|script| with given |kwargs|.

    resources/libs are designed to be shared between recipe script and the
    strategy scripts running on test swarming bots (without recipe modules).
    Due to the restriction (e.g. direct file access) of recipe modules, libs
    access would be proxy through api.step and resources/|script|.

    Args:
      script (str): Script path.
      method (str): Exported method name.
      test_data (Any): JSON-able step output for given API for testing.
      **kwargs (dict[str, str|None]): Additional kwargs will be converted into
        CLI arguments for the script CLI.

    Return:
      Return value from the script method, could be any JSON-able types.
    """
    args = []
    for key, value in kwargs.items():
      if not value:
        continue
      args.append('--' + key)
      if isinstance(value, list):
        for each_value in value:
          args.append(str(each_value))
      else:
        args.append(str(value))
    result = self.m.step(
      "api_runner",
      cmd=['python3', self.resource(script), method] + args,
      stdout=self.m.raw_io.output_text(add_output_log='on_failure'),
      step_test_data=lambda: self.m.json.test_api.output_stream(test_data),
      timeout=60,
    )
    return self.m.json.loads(result.stdout)

  def _test_binary_api(self, method, test_data=None, **kwargs):
    return self._api_runner(
      self.TEST_BINARY_API_RUNNER, method, test_data, **kwargs
    )

  def _strategy_api(self, method, test_data=None, **kwargs):
    return self._api_runner(
      self.STRATEGY_API_RUNNER, method, test_data, **kwargs
    )

  @nest_step
  def create_test_binary_from_task_request(self, task_request_path):
    test_binary = self._test_binary_api(
      'create_test_binary_from_task_request', task_request=task_request_path
    )
    test_binary_path = self.m.path.mkstemp('test_binary')
    self.m.file.write_json('dump', test_binary_path, test_binary)
    return test_binary_path

  @nest_step
  def get_test_binary_swarming_task_config(self, test_binary_path):

    class TaskConfig:
      """TestBinary test environment info used for swarming tasks"""

      def __init__(
        self, cas_input_root: str, cwd: str, dimensions: dict, env_vars: dict
      ):
        self.cas_input_root = cas_input_root
        self.cwd = cwd
        self.dimensions = dimensions
        self.env_vars = env_vars

    ret = self._test_binary_api(
      'get_test_binary_swarming_task_config',
      test_binary=test_binary_path,
      test_data={
        'cas_input_root': '12345/1',
        'cwd': '',
        'dimensions': {
          'pool': 'foo',
        },
        'env_vars': {},
      },
    )
    return TaskConfig(**ret)

  @nest_step
  def choose_strategies(self, test_binary_path, result_summary_path, test_name):
    """Chooses the strategies that be applied to the test."""
    return self._strategy_api(
      'choose_strategies',
      test_binary=test_binary_path,
      result_summary=result_summary_path,
      test_name=test_name,
      test_data=[],
    )

  @nest_step
  def choose_best_reproducing_step(self, reproducing_step_paths):
    if not reproducing_step_paths:
      return None
    reproducing_step = self._strategy_api(
      'choose_best_reproducing_step', reproducing_steps=reproducing_step_paths
    )
    if reproducing_step:
      reproducing_step_path = self.m.path.mkstemp('reproducing_step')
      self.m.file.write_json('dump', reproducing_step_path, reproducing_step)
      return reproducing_step_path

  @nest_step
  def update_test_binary_with_reproducing_step(
    self, test_binary_path, reproducing_step_path
  ):
    test_binary = self._test_binary_api(
      'update_test_binary_with_reproducing_step',
      test_binary=test_binary_path,
      reproducing_step=reproducing_step_path,
    )
    test_binary_path = self.m.path.mkstemp('test_binary')
    self.m.file.write_json('dump', test_binary_path, test_binary)
    return test_binary_path

  @nest_step
  def test_binary_as_command(self, test_binary_path, output):
    return self._test_binary_api(
      'test_binary_as_command',
      test_binary=test_binary_path,
      output=output,
      test_data=[],
    )

  @nest_step
  def count_reproduced_failures(
    self, test_name, result_summary_paths, original_result_summary_path
  ):
    return self._strategy_api(
      'count_reproduced_failures',
      test_name=test_name,
      result_summaries=result_summary_paths,
      original_result_summary=original_result_summary_path,
      test_data=0,
    )

  @nest_step
  def summarize_reproducing_steps(
    self, reproducing_step_path, all_reproducing_step_paths
  ):
    return self._strategy_api(
      'summarize_reproducing_steps',
      reproducing_step=reproducing_step_path,
      all_reproducing_steps=all_reproducing_step_paths,
      test_data=['', ''],
    )

  @nest_step
  def get_test_result_summary(self, task_id):
    """Gets TestResultSummary from the output of a swarming task result.

    Args:
      task_id (str|TaskRequestMetadata): The task_id for the swarming task.

    Returns:
      Path to result summary file
    """
    task_results = self.m.swarming.collect('swarming collect', [task_id])
    if not task_results:
      raise self.m.step.StepFailure(
        'Cannot find TaskResult for task {0}.'.format(task_id)
      )
    task_result = task_results[0]

    cas_output_path = self.m.path.mkdtemp()
    cas_output = self.m.cas.download(
      'download swarming outputs',
      task_result.cas_outputs.digest,
      self.m.raw_io.output_dir(leak_to=cas_output_path),
    )
    for output_json in ('run_histories.json', 'output.json'):
      if output_json in cas_output.raw_io.output_dir:
        return self.m.path.join(cas_output_path, output_json)

    raise self.m.step.StepFailure('Not supported task result.')

  def get_test_binary(self, task_id):
    """Gets TestBinary from the task request properties for a swarming task.

    The actual executable binary are not bound.

    Args:
      task_id (str|TaskRequestMetadata): The task_id for the swarming task.

    Returns:
      Path to test binary
    """
    task_request = self.m.swarming.show_request(
      'get_test_binary from {0}'.format(task_id), task_id
    )
    task_request_path = self.m.path.mkstemp('task_request')
    self.m.file.write_json(
      "dump task_request", task_request_path, task_request.to_jsonish()
    )
    test_binary_path = self.create_test_binary_from_task_request(
      task_request_path
    )
    return test_binary_path

  @nest_step
  def repack_test_binary(self, test_binary_path, result_summary_path):
    """Repack original test binary CAS with strategy runner."""
    tmp_dir = self.m.path.mkdtemp()
    task_config = self.get_test_binary_swarming_task_config(test_binary_path)
    self.m.cas.download(
      'download test binary', task_config.cas_input_root, tmp_dir
    )
    runner_dir = tmp_dir / self.RUNNER_PACKAGE_PATH
    self.m.file.copytree(
      'copy flaky_reproducer source', self.resource('.'), runner_dir
    )

    # Delete bad symlink that causing CAS upload failure.
    bad_symlink_dir = tmp_dir.joinpath(*self.CHROMITE_BAD_SYMLINK_DIR)
    self.m.file.rmtree('remove bad symlink directory', bad_symlink_dir)

    self.m.file.copy(
      'copy result_summary',
      result_summary_path,
      runner_dir / self.RESULT_SUMMARY_FILENAME,
    )
    self.m.file.copy(
      'copy test_binary',
      test_binary_path,
      runner_dir / self.TEST_BINARY_JSON_FILENAME,
    )

    self.m.isolate.write_isolate_file(
      runner_dir / self.TEST_BINARY_ISOLATE_FILENAME, ['../']
    )
    return self.m.isolate.isolate(
      'new test binary', runner_dir / self.TEST_BINARY_ISOLATE_FILENAME
    )

  def launch_strategy_in_swarming(
    self, strategy_name, repacked_cas_input_root, test_name, task_config
  ):
    """Launches a swarming task that runs the strategy logic."""
    command = [
      'vpython3',
      'strategy_runner.py',
      strategy_name,
      '--test-binary={0}'.format(self.TEST_BINARY_JSON_FILENAME),
      '--result-summary={0}'.format(self.RESULT_SUMMARY_FILENAME),
      '--output=${{ISOLATED_OUTDIR}}/{0}'.format(
        self.REPRODUCING_STEP_FILENAME
      ),
      test_name,
    ]

    request = (
      self.m.swarming.task_request()  # go/pyformat-break
      .with_name(
        "flaky reproducer strategy {0} for {1}".format(strategy_name, test_name)
      )  #
      .with_priority(self.c.priority)  #
      .with_tags({'flaky_reproducer': ['1'], 'strategy': [strategy_name]})  #
    )
    request_slice = (
      request[0]  # go/pyformat-break
      .with_command(command)  #
      .with_relative_cwd(self.RUNNER_PACKAGE_PATH)  #
      .with_cas_input_root(repacked_cas_input_root)  #
      .with_env_vars(**task_config.env_vars)  #
      .with_dimensions(**task_config.dimensions)  #
      .with_execution_timeout_secs(self.c.strategy_timeout)  #
      .with_io_timeout_secs(self.c.io_timeout)  #
      .with_expiration_secs(self.c.expiration)  #
    )
    request = request.with_slice(0, request_slice)

    return self.m.swarming.trigger(
      "swarming strategy {0}".format(strategy_name), [request]
    )[0]

  @nest_step
  def collect_strategy_results(self, strategy_results):
    reproducing_steps = []
    has_error = []
    step_summary = []
    for task_result in strategy_results:
      try:
        step_summary.append(
          "* [{0}]({1})".format(
            task_result.name,
            self._swarming_task_url(task_result.id),
          )
        )
        with self.m.step.nest(task_result.name):
          task_result.analyze()
          step = self.collect_strategy_result(task_result)
          if step is not None:
            reproducing_steps.append(step)
      except Exception:
        has_error.append(task_result.name)
    if has_error:
      raise self.m.step.StepFailure(
        'Error while running:\n* {0}'.format('\n* '.join(has_error))
      )

    presentation = self.m.step.active_result.presentation
    presentation.step_text = '\n'.join(step_summary)
    return reproducing_steps

  def collect_strategy_result(self, task_result):
    """Collect strategy result from swarming task output."""
    if self.REPRODUCING_STEP_FILENAME not in task_result.outputs:
      return None
    return task_result.outputs[self.REPRODUCING_STEP_FILENAME]

  def launch_verify_in_swarming(
    self, builder, test_name, test_binary_path, retries=3
  ):
    """Launches a swarming task that verify the reproducing step."""
    command = [
      "vpython3",
      "-c",
      (
        "import subprocess, sys; "
        "[subprocess.Popen("
        " [x.replace('$N$', str(i)) for x in sys.argv[1:]]"
        ").wait() for i in range({0})]"
      ).format(retries),
    ] + self.test_binary_as_command(
      test_binary_path,
      '${{ISOLATED_OUTDIR}}/{0}'.format(self.VERIFY_RESULT_SUMMARY_FILENAME),
    )
    request = (
      self.m.swarming.task_request()  # go/pyformat-break
      .with_name(
        "flaky reproducer verify on {0} for {1}".format(builder, test_name)
      )  #
      .with_priority(self.c.priority)  #
      .with_tags({'flaky_reproducer': ['1'], 'builder': [builder]})  #
    )
    task_config = self.get_test_binary_swarming_task_config(test_binary_path)
    request_slice = (
      request[0]  # go/pyformat-break
      .with_command(command)  #
      .with_relative_cwd(task_config.cwd)  #
      .with_cas_input_root(task_config.cas_input_root)  #
      .with_env_vars(**task_config.env_vars)  #
      .with_dimensions(**task_config.dimensions)  #
      .with_execution_timeout_secs(self.c.verify_timeout)  #
      .with_io_timeout_secs(self.c.io_timeout)  #
      .with_expiration_secs(self.c.expiration)  #
    )
    request = request.with_slice(0, request_slice)

    return self.m.swarming.trigger("swarming", [request])[0]

  def collect_verify_test_results(
    self, task_result, test_name, result_summary_path
  ):
    """Collect builder verify test results from swarming task output."""
    result_summary_paths = []
    for filename, filepath in task_result.outputs.items():
      if re.match(r'result_summary_\d+.json', filename):
        result_summary_paths.append(filepath)
    if result_summary_paths:
      reproduced_runs = self.count_reproduced_failures(
        test_name, result_summary_paths, result_summary_path
      )
    else:
      reproduced_runs = 0
    return BuilderVerifyResult(
      task_result.id,
      reproduced_runs,
      len(result_summary_paths),
      task_result.duration_secs,
    )

  @nest_step
  def verify_reproducing_step(
    self,
    task_id,
    test_name,
    result_summary_path,
    reproducing_step_path,
    verify_on_builders=None,
    retries=3,
  ):
    if not reproducing_step_path:
      return {}
    # Launch verify swarming tasks
    verify_builders = self.find_related_builders(
      task_id, test_name, verify_on_builders
    )
    # Verify failing builder sample
    builder = 'failing sample'
    if not verify_on_builders or builder in verify_on_builders:
      verify_builders[builder] = task_id

    return self.verify_reproducing_step_on_builders(
      verify_builders,
      test_name,
      result_summary_path,
      reproducing_step_path,
      retries,
    )

  def verify_reproducing_step_on_builders(
    self,
    verify_builders,
    test_name,
    result_summary_path,
    reproducing_step_path,
    retries=3,
  ):
    builder_results = {}
    swarming_tasks = {}  # { task_id: (builder, task_meta) }
    for builder, builder_task_id in verify_builders.items():
      with self.m.step.nest('verify on {0}'.format(builder)) as presentation:
        try:
          test_binary_path = self.get_test_binary(builder_task_id)
          test_binary_path = self.update_test_binary_with_reproducing_step(
            test_binary_path, reproducing_step_path
          )
          task = self.launch_verify_in_swarming(
            builder, test_name, test_binary_path, retries=retries
          )
          swarming_tasks[task.id] = (builder, task)
          presentation.step_text = '[{0}]({1})'.format(
            task.name, task.task_ui_link
          )
        except Exception as err:
          builder_results[builder] = BuilderVerifyResult(error=str(err))
    if not swarming_tasks:
      return builder_results
    # Collect swarming task result
    verify_results = self.m.swarming.collect(
      'collect verify results',
      list(swarming_tasks.keys()),
      output_dir=self.m.path.mkdtemp(),
    )
    for result in verify_results:
      builder, _ = swarming_tasks[result.id]
      try:
        result.analyze()
        builder_results[builder] = self.collect_verify_test_results(
          result, test_name, result_summary_path
        )
      except Exception as err:
        builder_results[builder] = builder_results.get(
          builder, BuilderVerifyResult()
        )._replace(task_id=result.id, error=str(err))
    return builder_results

  def summarize_results(self, *args, **kwargs):
    """proxy for _summarize_results to wrap and inject presentation"""
    with self.m.step.nest('summarize_results') as presentation:
      self._summarize_results(*args, **kwargs, presentation=presentation)

  def _summarize_results(
    self,
    task_id,
    test_name,
    reproducing_step_path,
    all_reproducing_step_paths,
    builder_results,
    presentation,
    monorail_issue=None,
  ):
    summary = []

    # sample failure info
    message = 'For {0}\n{1}\n'.format(
      test_name, self._milo_test_results_url(task_id, test_name)
    )
    summary.append(message)

    summary_header, message = self.summarize_reproducing_steps(
      reproducing_step_path, all_reproducing_step_paths
    )
    summary.append(message)

    # Group builder results in reproduced, not reproduced, error.
    reproduced = False
    if builder_results:
      builder_summary = []
      builder_summary.append(
        '\nThe failure could be reproduced on following builders:'
      )
      sorted_builder_results = sorted(
        (x for x in builder_results.items()),
        key=lambda kv: (
          not bool(kv[1].error),
          kv[1].reproduced_runs,
          kv[1].duration,
        ),
        reverse=True,
      )
      for builder, result in sorted_builder_results:
        if (
          not result.error
          and result.total_runs
          and result.reproduced_runs / result.total_runs > 0.6
        ):
          reproduced = True
        builder_message = ''
        if result.reproduced_runs:
          builder_message += '{0:<30s} [reproduced]({1}) {2:d}/{3:d}'.format(
            builder,
            self._swarming_task_url(result.task_id),
            result.reproduced_runs,
            result.total_runs,
          )
        else:
          builder_message += '{0:<30s} [not reproduced]({1})'.format(
            builder, self._swarming_task_url(result.task_id)
          )
        if result.error:
          # Add first line of the error message for a better presentation.
          builder_message += ', with failure: {0}'.format(
            result.error.split('\n')[0]
          )
        # Adding tailing '  ' to force line break for markdown.
        builder_summary.append(builder_message + '  ')
      summary.append('\n'.join(builder_summary))

    # Milo build UI will pick the first line as step description.
    presentation.step_summary_text = (
      summary_header + '  \n' + '\n'.join(summary)
    )
    if reproducing_step_path:
      presentation.logs['reproducing_step.json'] = self.m.file.read_raw(
        'reproducing_step.json', reproducing_step_path
      )
    if builder_results:
      presentation.logs['builder_results.json'] = self.m.json.dumps(
        builder_results, indent=2
      )

    if monorail_issue and reproduced:
      # Remove markdown links when posting to monorail, as it's not supported
      monorail_summary = re.sub(r'\[(.+?)\]\(.+?\)', r'\1', '\n'.join(summary))
      # Instead, add the link to this build for more details.
      monorail_summary += (
        '\n\nFor more detailed information: ' + self.m.buildbucket.build_url()
      )
      self.post_summary_to_monorail(monorail_issue, monorail_summary)

  @nest_step
  def check_monorail_comment_posted(self, monorail_issue):
    monorail_api = MonorailApi(self.m)
    issue_name = monorail_api.chromium_issue_name(monorail_issue)
    issue = monorail_api.get_issue(issue_name)
    for label in issue.get('labels', []):
      if label.get('label').lower() == self.MONORAIL_LABEL.lower():
        presentation = self.m.step.active_result.presentation
        presentation.step_text = (
          'Reproducing step already posted to monorail issue.'
        )
        raise self.m.step.StepWarning(presentation.step_text)

  @nest_step
  def post_summary_to_monorail(self, monorail_issue, comment_message):
    monorail_api = MonorailApi(self.m)
    issue_name = monorail_api.chromium_issue_name(monorail_issue)
    monorail_api.modify_issues(
      issue_name, comment_message, labels=[self.MONORAIL_LABEL]
    )

  @nest_step
  def query_sample_failure_from_luci_analysis(
    self, monorail_issue, test_id=None
  ):
    """Query sample failure from LUCI Analysis cluster failures."""
    rules = self.m.luci_analysis.lookup_bug('chromium/' + monorail_issue)
    if not rules:
      raise self.m.step.StepFailure('No cluster associated with bug.')
    cluster = self.m.luci_analysis.rule_name_to_cluster_name(rules[0])
    failures = self.m.luci_analysis.query_cluster_failures(cluster)
    if not failures:
      raise self.m.step.StepFailure(
        'No failure found in the LUCI Analysis cluster.'
      )

    # Count failures by test variant
    # (presubmit, frozenset(variant), test_id): [count, first_build_id]
    test_variants = {}
    for f in sorted(
      failures, key=lambda f: f.partition_time.ToDatetime(), reverse=True
    ):
      key = (
        f.HasField('presubmit_run'),
        frozenset(getattr(f.variant, 'def').items()),
        f.test_id,
      )
      if key not in test_variants:
        test_variants[key] = [
          f.count,
          f.ingested_invocation_id.strip('build-'),
        ]
      else:
        test_variants[key][0] += f.count

    # Choose best test sample
    top_presubmit_variant_count = {}
    best_score = None
    best_test_id = None
    best_build_id = None
    for (presubmit, variant_set, variant_test_id), (
      count,
      first_build_id,
    ) in sorted(test_variants.items(), key=lambda kv: kv[1][0], reverse=True):
      variant = dict(variant_set)
      # Choose from the test_id or build_id specified
      if test_id and variant_test_id != test_id:
        continue
      # Ignore reviver builders
      if 'reviver_builder' in variant:
        continue
      # The test variant should fail >50% of most failing variant.
      top_count = top_presubmit_variant_count.setdefault(presubmit, count)
      if count < top_count * 0.5:
        continue
      # Prefer CI desktop builders, suppress known not supported platforms
      builder = variant.get('builder', '')
      builder_score = 0
      if not presubmit:
        builder_score += 1
      if self.PREFERRED_BUILDERS_RE.match(builder):
        builder_score += 1
      elif self.SUPPRESS_BUILDER_RE.match(builder):
        builder_score -= 1
      if best_score is None or builder_score > best_score:
        best_score = builder_score
        best_test_id = variant_test_id
        best_build_id = first_build_id

    presentation = self.m.step.active_result.presentation
    presentation.step_text = "http://go/bbid/{0}/test-results?q={1}".format(
      best_build_id, self.m.url.quote(best_test_id)
    )
    return (best_build_id, best_test_id)

  def query_resultdb_for_task_id_and_test_name(
    self, build_id=None, task_id=None, test_id=None, test_name=None
  ):
    """Query ResultDB for task_id and test_name."""
    if not (task_id or build_id):
      raise self.m.step.StepFailure('Must specify task_id or build_id.')
    if not (test_name or test_id):
      raise self.m.step.StepFailure('Must specify test_name or test_id.')

    if task_id and test_name:
      return (task_id, test_name)

    if task_id:
      inv_id = self._generate_invocation_id_from_task_id(task_id)
    else:
      inv_id = self.generate_invocation_id_from_build_id(build_id)

    if test_id:
      test_id_regexp = re.escape(test_id)
    else:
      # test_id contains test_name but might have a different format. It's using
      # the longest word in test_name for filtering to reduce the data
      # retrieved.
      longest_word = max(re.split('\W+', test_name), key=len)
      test_id_regexp = r'.*%s.*' % longest_word

    def tag_test_name(tags):
      for tag in tags:
        if tag.key == 'test_name':
          return tag.value

    # Search for first unexpected result or return the last match.
    last_test_result = None
    res = None
    while True:
      try:
        res = self.m.resultdb.query_test_results(
          invocations=['invocations/' + inv_id],
          test_id_regexp=test_id_regexp,
          field_mask_paths=['name', 'test_id', 'tags', 'expected'],
          page_token=res and res.next_page_token,
        )
      except self.m.step.InfraFailure as err:
        if err.retcode == 123:
          raise self.m.step.StepFailure('Not support realm.')
        raise err
      for test_result in res.test_results:
        if test_id and test_result.test_id == test_id:
          last_test_result = test_result
        elif test_name and test_name == tag_test_name(test_result.tags):
          last_test_result = test_result
        if last_test_result and not last_test_result.expected:
          break
      if last_test_result and not last_test_result.expected:
        break
      if not res.next_page_token:
        break
    if not last_test_result:
      raise self.m.step.StepFailure('Cannot find TestResult.')

    return (
      self._extract_task_id_from_invocation_name(last_test_result.name),
      test_name or tag_test_name(last_test_result.tags),
    )

  def run(
    self,
    task_id=None,
    build_id=None,
    test_name=None,
    test_id=None,
    verify_on_builders=None,
    monorail_issue=None,
  ):
    """Runs the Chrome Flaky Reproducer as a recipe.

    This method is expected to run as a standalone recipe that:
      1. Takes a test and the swarming task runs it.
      2. Applies to multiple reproducing strategies.
      3. Verifies the best reproducing step on all variants for the test.
      4. Summarize the results of above.

    Args:
      task_id (str): Swarming task ID of the flaky task sample.
      build_id (int): Buildbucket build ID of the flaky build sample.
      test_name (str): The test name of the flaky test case.
      test_id (str): The test ID of the flaky test case from ResultDB.

      verify_on_builders (list of str): Verify the reproducing step on specified
        builders. Default to all CQ and sheriff builders.
      monorail_issue (str): Add a comment to the monorail_issue id if reproduced
        and the step verified.
    """
    if task_id and task_id.endswith('0'):
      # The task_id endswith '0' is the summary instead of the actual runs. And
      # the invocation we rely on is depends on TaskResult.run_id which ending
      # '1', '2' or more.
      # Also because swarming doesn't support internal retry anymore so task id
      # with '0' suffix always points to the run id with '1' now. That it's safe
      # for us to just covert the task_id to '1'.
      # Also see https://source.chromium.org/chromium/infra/infra/+/main:luci/appengine/swarming/proto/api/swarming.proto;l=844;drc=19f5f7481f1099270ed649691d5912c890f0b312
      task_id = task_id[:-1] + '1'

    if monorail_issue:
      try:
        self.check_monorail_comment_posted(monorail_issue)
      except self.m.step.StepWarning:
        # Ignore the task if comment posted to monorail.
        return

      # Try to find failing sample from LUCI Analysis cluster.
      if not (task_id or build_id) or not (test_name or test_id):
        build_id, test_id = self.query_sample_failure_from_luci_analysis(
          monorail_issue, test_id
        )

    task_id, test_name = self.query_resultdb_for_task_id_and_test_name(
      task_id=task_id, build_id=build_id, test_name=test_name, test_id=test_id
    )
    return self._run(task_id, test_name, verify_on_builders, monorail_issue)

  def _run(self, task_id, test_name, verify_on_builders, monorail_issue):
    result_summary_path = self.get_test_result_summary(task_id)
    test_binary_path = self.get_test_binary(task_id)
    repacked_cas = self.repack_test_binary(
      test_binary_path, result_summary_path
    )

    # Trigger reproducing strategies in swarming
    swarming_tasks = []
    task_config = self.get_test_binary_swarming_task_config(test_binary_path)
    for strategy in self.choose_strategies(
      test_binary_path, result_summary_path, test_name
    ):
      swarming_tasks.append(
        self.launch_strategy_in_swarming(
          strategy, repacked_cas, test_name, task_config
        )
      )
    strategy_results = self.m.swarming.collect(
      'collect strategy results',
      swarming_tasks,
      output_dir=self.m.path.mkdtemp(),
    )

    # Verify reproducing steps
    reproducing_step_paths = self.collect_strategy_results(strategy_results)
    reproducing_step_path = self.choose_best_reproducing_step(
      reproducing_step_paths
    )
    builder_results = self.verify_reproducing_step(
      task_id,
      test_name,
      result_summary_path,
      reproducing_step_path,
      verify_on_builders,
    )

    # output
    return self.summarize_results(
      task_id,
      test_name,
      reproducing_step_path,
      reproducing_step_paths,
      builder_results,
      monorail_issue=monorail_issue,
    )

  def _extract_task_id_from_invocation_name(self, inv_name):
    assert '//' in self.m.swarming.current_server
    swarming_server = self.m.swarming.current_server.split('//', 1)[1]
    m = re.match(
      r"^invocations/task-{0}-(\w+)".format(swarming_server), inv_name
    )
    if m:
      return m.group(1)

  def generate_invocation_id_from_build_id(self, build_id):
    return 'build-{0}'.format(build_id)

  def _generate_invocation_id_from_task_id(self, task_id):
    assert '//' in self.m.swarming.current_server
    swarming_server = self.m.swarming.current_server.split('//', 1)[1]
    return "task-{0}-{1}".format(swarming_server, task_id)

  def _swarming_task_url(self, task_id):
    return '{0}/task?id={1}'.format(self.m.swarming.current_server, task_id)

  def _milo_test_results_url(self, task_id, test_name):
    url = 'https://luci-milo.appspot.com/ui/inv/'
    return '{0}{1}/test-results?q={2}'.format(
      url,
      self._generate_invocation_id_from_task_id(task_id),
      self.m.url.quote(test_name),
    )

  @nest_step
  def find_related_builders(self, task_id, test_name, verify_on_builders):
    """Search for builders that run the given test."""
    # Query TestResult from invocation.
    inv_id = self._generate_invocation_id_from_task_id(task_id)
    inv_map = self.m.resultdb.query(
      [inv_id], limit=0, tr_fields=['testId', 'variant', 'tags', 'startTime']
    )
    invocation = inv_map.get(inv_id, None)
    if not invocation:
      raise self.m.step.StepFailure(
        'Cannot retrieve invocation for task {0}.'.format(task_id)
      )
    test_result = None
    for test_result in invocation.test_results:
      found = False
      for tag in test_result.tags:
        if tag.key == 'test_name' and tag.value == test_name:
          found = True
          break
      if found:
        break
    else:
      raise self.m.step.StepFailure(
        'Cannot find TestResult for test {0}.'.format(test_name)
      )

    # Query all variants with the given test.
    project, bucket = invocation.proto.realm.split(':')
    sample_builder = None
    if 'builder' in getattr(test_result.variant, 'def'):
      sample_builder = getattr(test_result.variant, 'def').get('builder')
    variant_predicate = None
    if 'test_suite' in getattr(test_result.variant, 'def'):
      variant_predicate = predicate_pb2.VariantPredicate(
        contains={
          'def': {
            'test_suite': getattr(test_result.variant, 'def').get('test_suite')
          }
        }
      )
    all_variants = []
    next_page_token = None
    while True:
      variants, next_page_token = self.m.luci_analysis.query_variants(
        test_id=test_result.test_id,
        project=project,
        sub_realm=None if self.c.verify_on_all_buckets else bucket,
        variant_predicate=variant_predicate,
        page_token=next_page_token,
      )
      for variant_info in variants:
        builder_name = getattr(variant_info.variant, 'def').get('builder')
        if verify_on_builders and builder_name not in verify_on_builders:
          continue
        if not builder_name or builder_name == sample_builder:
          continue
        all_variants.append(variant_info)
      if not next_page_token:
        break

    # Query test histories for test variants.
    time_range = common_v1.TimeRange(
      earliest=timestamp_pb2.Timestamp(
        seconds=invocation.proto.create_time.seconds
      ),
      latest=timestamp_pb2.Timestamp(
        seconds=invocation.proto.finalize_time.seconds + 24 * 60 * 60
      ),
    )
    selected_invocations = []
    for variant_info in all_variants:
      verdicts, _ = self.m.luci_analysis.query_test_history(
        test_id=test_result.test_id,
        # project=project  # This API hardcoded project as chromium.
        sub_realm=bucket,
        variant_predicate=predicate_pb2.VariantPredicate(
          hash_equals=variant_info.variant_hash
        ),
        partition_time_range=time_range,
        page_size=10,
      )
      if not verdicts:
        continue

      # Query build-bucket input for verdicts
      assert all(re.match('^build-\d+$', v.invocation_id) for v in verdicts)
      builds = self.m.buildbucket.get_multi(
        [int(v.invocation_id[len('build-') :]) for v in verdicts]
      )

      selected_verdict = None
      for v in verdicts:
        build = builds.get(int(v.invocation_id[len('build-') :]))
        cq_sheriff_build = build and (
          '$recipe_engine/cq' in build.input.properties.fields
          or 'sheriff_rotations' in build.input.properties.fields
        )
        # Skip CQ builder check if builders are manually selected.
        if verify_on_builders:
          pass
        # Select CQ or sheriff builders.
        elif self.c.verify_only_cq_sheriff_builders and not cq_sheriff_build:
          continue
        # Select first EXPECTED or FLAKY sample over UNEXPECTED or EXONERATED
        # samples.
        if v.status in (
          test_verdict_pb2.TestVerdictStatus.FLAKY,
          test_verdict_pb2.TestVerdictStatus.EXPECTED,
        ):
          selected_verdict = v
          break
        if (
          v.status
          in (
            test_verdict_pb2.TestVerdictStatus.UNEXPECTED,
            test_verdict_pb2.TestVerdictStatus.EXONERATED,
          )
          and not selected_verdict
        ):
          selected_verdict = v
      if selected_verdict:
        selected_invocations.append(selected_verdict.invocation_id)

    # Query task_id for selected verdicts.
    builders = {}
    if not selected_invocations:
      return builders
    res = None
    while True:
      res = self.m.resultdb.query_test_results(
        invocations=['invocations/%s' % inv for inv in selected_invocations],
        test_id_regexp=re.escape(test_result.test_id),
        field_mask_paths=['name', 'variant'],
        page_token=res and res.next_page_token,
      )
      for each in res.test_results:
        builder = getattr(each.variant, 'def').get('builder', None)
        if not builder or builder in builders:
          continue
        task_id = self._extract_task_id_from_invocation_name(each.name)
        if not task_id:
          continue
        builders[builder] = task_id
      if not res.next_page_token:
        break

    presentation = self.m.step.active_result.presentation
    presentation.logs['builders.json'] = self.m.json.dumps(builders, indent=2)
    return builders
