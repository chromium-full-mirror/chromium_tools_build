# Copyright 2019 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import socket

from recipe_engine import recipe_api


class TSMonApi(recipe_api.RecipeApi):
  def __init__(self, **kwargs):
    super().__init__(**kwargs)
    self._send_ts_mon_pkg_path = None

  def _ensure_send_ts_mon_values(self):
    if self._send_ts_mon_pkg_path:
      return

    self._send_ts_mon_pkg_path = self.m.path.start_dir.joinpath(
      'send_ts_mon_values'
    )
    self.m.cipd.ensure(
      self._send_ts_mon_pkg_path,
      self.m.cipd.EnsureFile().add_package(
        'infra/send_ts_mon_values/all', 'latest'
      ),
    )

  def send_value(
    self,
    name,
    metric_type,
    value,
    fields=None,
    service_name='luci',
    job_name='recipe',
    step_name='upload ts_mon metrics',
  ):
    """Sends a value to the ts_mon monitoring service.

    Based on the ts_mon monitoring pipeline and script for sending data to it:
    https://cs.chromium.org/chromium/infra/infra/tools/send_ts_mon_values/.
    Internal users can read more about ts_mon at go/brown-bag-timeseries-basics.
    External users can read the following doc to understand various metric
    types:
    https://cs.chromium.org/chromium/infra/packages/infra_libs/infra_libs/ts_mon/common/metrics.py?l=90-95&rcl=6b6087ba98d50b15e2ece42da503b0b3596005b9.

    Note that the candinality of all possible combinations of different values
    passed to fields should be less than 5000, i.e. do not use unique
    identifiers or timestamps and instead use values from a known small set.

    Additionally, the reported values for each unique combination of field
    values get downsampled by ts_mon according to the following rules:
     - no downsampling for 12 hrs
     - keep at most 1 data point for each min for the next 1 week
     - keep at most 1 data point for 5 mins interval for the next 12 weeks
     - Keep at most 1 data point for 30 mins interval for the next 52 weeks

    Therefore, please keep in mind that for metrics reported with high frequency
    (which can also be caused by having them reported at low frequency, but from
    large number of bots), the graphs may change as a result of downsampling and
    thus users may see different values in the past than what was reported
    originally and what was used to generate alerts.

    Arguments:
      name: Name of the metric, which is automatically prefixed with
          /chrome/infra, i.e. /foo/bar will become /chrome/infra/foo/bar.
      metric_type: Type of the metric: 'gauge', 'float', 'string', 'bool',
          'counter' or 'cumulative'. See documentation linked above to
          understand which type of metric you need.
      value: The value to be reported.
      fields: Dictionary with fields to be associated with the value.
      service_name: Name of the service being monitored.
      job_name: Name of this job instance of the task.
      step_name: Name of the step sending information to ts_mon.
    """
    self.send_values_batch(
      name, metric_type, [(value, fields)], service_name, job_name, step_name
    )

  def send_values_batch(
    self,
    name,
    metric_type,
    value_fields,
    service_name='luci',
    job_name='recipe',
    step_name='upload ts_mon metrics',
  ):
    """Sends multiple values to the ts_mon monitoring service in batch mode.

    This method allows to send multiple values to the same metric in a batch.
    See doc for the send_value method above for various caveats.

    Arguments:
      name: Name of the metric, which is automatically prefixed with
          /chrome/infra, i.e. /foo/bar will become /chrome/infra/foo/bar.
      metric_type: Type of the metric: 'gauge', 'float', 'string', 'bool',
          'counter' or 'cumulative'. See documentation for send_value method to
          understand which type of metric you need.
      value_fields: List of tuples (value, fields), where each tuple represents
          a separate value to be sent with its own set of fields.
      service_name: Name of the service being monitored.
      job_name: Name of this job instance of the task.
      step_name: Name of the step sending information to ts_mon.
    """
    assert metric_type in [
      'gauge',
      'float',
      'string',
      'bool',
      'counter',
      'cumulative',
    ]
    self._ensure_send_ts_mon_values()

    metric_data = []
    for value, fields in value_fields:
      value_data = {'name': name, 'value': value}
      value_data.update(fields or {})
      metric_data.append(value_data)

    serialized_data = '\n'.join(self.m.json.dumps(d) for d in metric_data)
    with self.m.context(cwd=self._send_ts_mon_pkg_path):
      result = self.m.step(
        step_name,
        [
          'vpython3',
          '-vpython-spec',
          self._send_ts_mon_pkg_path.joinpath(
            'infra', 'tools', 'send_ts_mon_values', 'standalone.vpython3'
          ),
          '-m',
          'infra.tools.send_ts_mon_values',
          '--ts-mon-target-type',
          'task',
          '--ts-mon-task-service-name',
          service_name,
          '--ts-mon-task-job-name',
          job_name,
          '--%s-file' % metric_type,
          self.m.raw_io.input(serialized_data),
        ],
        infra_step=True,
      )
    result.presentation.logs['metric_data'] = self.m.json.dumps(
      metric_data, indent=2, separators=(',', ': ')
    ).splitlines()
