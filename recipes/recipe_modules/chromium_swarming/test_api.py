# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import recipe_test_api


class SwarmingTestApi(recipe_test_api.RecipeTestApi):
  def canned_summary_output_raw(
    self,
    *,
    shard_indices=None,
    shards=1,
    task_ids=None,
    invocations=None,
    failure=False,
    internal_failure=False,
    bot_dimensions_sets=None,
  ):
    """Get the summary json object.

    Args:
      shard_indices: The index values of the different shards to create.
        The summary will contain a shard entry for each element. If not
        provided, then range(shards) will be used.
      shards: The number of shards to include in the summary. Only used
        if shard_indices is not provided.
      task_ids: A sequence of task IDs, one for each shard. If not
        provided, then each shard will have its shard index as the task
        ID.
      invocations: A sequence of invocation names, one for each shard.
        If not provided, then each shard will have an invocation of the
        form invocations/<task-ID>. If the invocation name for a shard
        is None then the summary entry for the shard will not have an
        invocation.
      bot_dimensions: A list of list of dicts corresponding to the dimensions
        of the bots that ran each shard.
    """
    if shard_indices is None:
      shard_indices = range(shards)
    if task_ids is None:
      task_ids = [str(i) for i in shard_indices]
    assert len(task_ids) == len(shard_indices)
    if invocations is None:
      invocations = [f'invocations/{i}' for i in shard_indices]
    assert len(invocations) == len(shard_indices)
    if bot_dimensions_sets is None:
      bot_dimensions_sets = [
        [{'key': 'os', 'value': ['Linux']}] for i in shard_indices
      ]
    assert len(bot_dimensions_sets) == len(shard_indices)

    cas_hash = (
      '24b2420bc49d8b8fdc1d011a163708927532b37dc9f91d7d8d6877e3a86559ca'
    )

    def shard_entry(task_id, invocation, bot_dimensions):
      entry = {
        'bot_id': 'vm30',
        'bot_dimensions': bot_dimensions,
        'completed_ts': '2014-09-25T01:43:11.123',
        'created_ts': '2014-09-25T01:41:00.123',
        'duration': 31.5,
        'exit_code': 1 if failure else 0,
        'failure': failure,
        'task_id': task_id,
        'internal_failure': internal_failure,
        'modified_ts': '2014-09-25 01:42:00',
        # TODO(gbeaty) Support setting name and output since these constant
        # values are confusing
        'name': 'heartbeat-canary-2014-09-25_01:41:55-os=Windows',
        'output': 'Heart beat succeeded on win32.\nFoo',
        'cas_output_root': {
          'cas_instance': 'projects/example-project/instances/default_instance',
          'digest': {
            'hash': cas_hash,
            'size_bytes': 73,
          },
        },
        'started_ts': '2014-09-25T01:42:11.123',
        'state': 'COMPLETED',
      }
      if invocation is not None:
        entry['resultdb_info'] = {
          'invocation': invocation,
        }
      return entry

    return {
      'shards': [
        shard_entry(task_id, invocation, bot_dimensions)
        for task_id, invocation, bot_dimensions in zip(
          task_ids, invocations, bot_dimensions_sets
        )
      ],
    }

  def wait_for_finished_task_set(
    self, states, suffix=None, nest_step_name=None
  ):
    res = None
    for i, (tasks, attempts) in enumerate(states):
      if nest_step_name:
        name = '%s%s.wait for tasks%s' % (
          nest_step_name,
          '' if not i else ' (%d)' % (i + 1),
          suffix or '',
        )
      else:
        name = 'wait for tasks%s%s' % (
          suffix or '',
          '' if not i else ' (%d)' % (i + 1),
        )
      data = self.step_data(
        name, self.m.json.output(data={'sets': tasks, 'attempts': attempts})
      )
      if not res:
        res = data
      else:
        res += data

    return res

  def summary(self, dispatched_task_step_test_data, raw_summary, retcode=None):
    """Returns step test data for a swarming collect step.

    Args:
      dispatched_task_step_test_data: A StepTestData which wraps
          PlaceholderTestDatas for the dispatched task and/or merge script.
      data: A python dictionary that holds the swarming summary.
      retcode: The retcode for the collect step.

    Returns:
      A StepTestData that wraps multiple PlaceholderTestDatas.
    """
    # Generate step test data for the swarming step.
    step_test_data = recipe_test_api.StepTestData()

    # Add the test data for the dispatched step.
    if dispatched_task_step_test_data:
      step_test_data += dispatched_task_step_test_data

    key = ('chromium_swarming', 'summary', None)
    placeholder = recipe_test_api.PlaceholderTestData(
      self.m.json.dumps(raw_summary)
    )
    assert key not in step_test_data.placeholder_data
    step_test_data.placeholder_data[key] = placeholder

    # Explicitly set the retcode
    step_test_data.retcode = retcode

    # The 'exit_code' of the swarming shards is currently populated by the
    # 'failure' parameter. As a future improvement, we could automatically set
    # the 'exit_code' parameter of the swarming shards based on the exit code of
    # the placeholder for the dispatched tasks.
    return step_test_data

  # Swarming is used to dispatch tasks remotely. This means that unless there is
  # an internal swarming error, the results should include both:
  #  1) The swarming output itself.
  #  2) The output from the dispatched task.
  # The retcode of (2) should become the exit_code in the swarming output.
  # The swarming task itself should almost always have a retcode of 0, unless
  # the test is trying to test swarming failures. output from swarming itself,
  def canned_summary_output(
    self,
    dispatched_task_step_test_data,
    *,
    shards=1,
    shard_indices=None,
    task_ids=None,
    invocations=None,
    failure=False,
    internal_failure=False,
    retcode=0,
  ):
    """Returns step test data for a swarming collect step.

    Swarming is used to dispatch tasks remotely. Those tasks typically have
    their own placeholders. This function returns a single StepTestData that
    wraps multiple placeholders -- the placeholder for the swarming summary, and
    the placeholder(s) for the dispatched task and/or merge script.

    Args:
      dispatched_task_step_test_data: A StepTestData which wraps
          PlaceholderTestDatas for the dispatched task and/or merge script.
      shards: The number of shards that the task was divided into.
      shard_indices: The indices of the shards that were dispatched.
      failure: Whether the swarming task failed.
      internal_failure: Whether swarming itself encountered an error.
      retcode: The retcode for the collect step.

    Returns:
      A StepTestData that wraps multiple PlaceholderTestDatas.
    """
    assert dispatched_task_step_test_data or retcode or internal_failure, (
      'There must be a placeholder for the dispatched task unless there is a '
      'swarming error'
    )
    raw_summary = self.canned_summary_output_raw(
      shards=shards,
      shard_indices=shard_indices,
      task_ids=task_ids,
      invocations=invocations,
      failure=failure,
      internal_failure=internal_failure,
    )
    return self.summary(dispatched_task_step_test_data, raw_summary, retcode)
