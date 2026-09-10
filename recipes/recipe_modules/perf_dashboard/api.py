# Copyright 2014 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from urllib.parse import urlencode

from recipe_engine import recipe_api

CHROMEPERF_URL = 'https://chromeperf.appspot.com'
CHROMEPERF_STAGING_URL = 'https://chromeperf-stage.uc.r.appspot.com/'
PINPOINT_URL = 'https://pinpoint-dot-chromeperf.appspot.com'
PINPOINT_STAGING_URL = 'https://pinpoint-dot-chromeperf-stage.uc.r.appspot.com/'


def _get_base_url(is_staging):
  return CHROMEPERF_STAGING_URL if is_staging else CHROMEPERF_URL


def _get_pinpoint_base_url(is_staging):
  return PINPOINT_STAGING_URL if is_staging else PINPOINT_URL


class PerfDashboardApi(recipe_api.RecipeApi):
  """Provides steps to take a list of perf points and post them to the
  Chrome Perf Dashboard.  Can also use the test url for testing purposes."""

  def get_skeleton_point(self, test, revision, value, bot=None):
    # TODO(https://crbug.com/1113290) Update the dashboard to refer to groups
    # instead of masters
    # TODO: masterid is really mastername
    assert test != ''
    assert revision != ''
    assert value != ''
    return {
      'master': self.m.builder_group.for_current,
      'bot': bot or self.m.buildbucket.builder_name,
      'test': test,
      'revision': revision,
      'value': value,
      'masterid': self.m.builder_group.for_current,
      'buildername': self.m.buildbucket.builder_name,
      'buildnumber': self.m.buildbucket.build.number,
    }

  def add_dashboard_link(self, presentation, test, revision, bot=None):
    """Adds a results-dashboard link to the step presentation.

    Must be called from a follow-up function of the step, to which the link
    should be added. For a working link, the parameters test, revision and bot
    must match to the parameters used to upload points to the dashboard.

    Args:
      presentation: A step presentation object. Can be obtained by
                    step_result.presentation from a followup_fn of a step.
      test: Slash-separated test path.
      revision: The build revision, e.g. got_revision from the update step.
      bot: The bot name used for the data on the perf dashboard.
    """
    assert presentation
    assert test
    assert revision
    # TODO(https://crbug.com/1113290) Update the dashboard to refer to groups
    # instead of masters
    params = urlencode(
      [
        ('masters', self.m.builder_group.for_current),
        ('bots', bot or self.m.buildbucket.builder_name),
        ('tests', test),
        ('rev', revision),
      ]
    )
    base_url = _get_base_url(self.m.properties.get('staging', False))
    presentation.links['Results Dashboard'] = '%s/report?%s' % (
      base_url,
      params,
    )

  def set_default_config(self):
    # TODO: Remove.
    pass

  def add_point(self, data, halt_on_failure=False, name=None, **kwargs):
    base_url = _get_base_url(self.m.properties.get('staging', False))
    return self.post(
      name or 'perf dashboard post',
      '%s/add_point' % base_url,
      {'data': self.m.json.dumps(data)},
      halt_on_failure,
      **kwargs,
    )

  def upload_isolate(
    self,
    builder_name,
    change,
    isolate_server,
    isolate_map,
    halt_on_failure=False,
    **kwargs,
  ):
    data = {
      'builder_name': builder_name,
      'change': self.m.json.dumps(change),
      'isolate_server': isolate_server,
      'isolate_map': self.m.json.dumps(isolate_map),
    }

    is_staging = self.m.properties.get('staging', False)

    # Update data store on staging to allow build reuse on linux-builder-perf.
    if not is_staging and builder_name == 'linux-builder-perf':
      self.post(
        'pinpoint isolate upload (staging)',
        '%s/api/isolate' % PINPOINT_STAGING_URL,
        data,
        False,
        **kwargs,
      )

    pinpoint_base_url = _get_pinpoint_base_url(is_staging)

    return self.post(
      'pinpoint isolate upload',
      '%s/api/isolate' % pinpoint_base_url,
      data,
      halt_on_failure,
      **kwargs,
    )

  def get_change_info(self, commits):
    change = {
      'commits': commits,
    }

    deps_revision_overrides = self.m.properties.get('deps_revision_overrides')
    if deps_revision_overrides:
      change['commits'] += list(
        {'repository': repository, 'git_hash': git_hash}
        for repository, git_hash in deps_revision_overrides.items()
      )

    if self.m.tryserver.is_tryserver:
      change['patch'] = {
        'server': 'https://' + self.m.tryserver.gerrit_change.host,
        'change': self.m.tryserver.gerrit_change.change,
        'revision': self.m.tryserver.gerrit_change.patchset,
      }

    return change

  def post(
    self, name, url, data, halt_on_failure, step_test_data=None, **kwargs
  ):
    """Send a POST request to a URL with a payload.

    Args:
      name: The name of the step.
      url: The URL to post to.
      data: A dict of parameters to send in the body of the request.
      halt_on_failure: If True, the step turns purple on failure. Otherwise, it
          turns orange.
      step_test_data: Opional recipe simulation data. Defaults to a successful
          request.
    """
    token = self.m.service_account.default().get_access_token()
    cmd = [
      'vpython3',
      self.resource('post_json.py'),
      url,
      '-i',
      self.m.json.input(data),
      '-o',
      self.m.json.output(),
      '-t',
      self.m.raw_io.input_text(token),
    ]
    step_test_data = step_test_data or (
      lambda: self.m.json.test_api.output({'status_code': 200})
    )
    step_result = self.m.step(
      name=name, cmd=cmd, step_test_data=step_test_data, **kwargs
    )

    response = step_result.json.output
    if not response or response['status_code'] != 200:  # pragma: no cover
      error = response['status_code'] if response else 'None'
      reason = 'Failed to post to Perf Dashboard. Error response: %s' % error
      if halt_on_failure:
        self.halt(step_result, reason)
      else:
        self.warning(step_result, reason)

    return step_result

  def halt(self, step_result, reason):  # pragma: no cover
    step_result.presentation.step_text = reason
    step_result.presentation.status = self.m.step.EXCEPTION
    raise self.m.step.InfraFailure(reason)

  def warning(self, step_result, reason):  # pragma: no cover
    step_result.presentation.step_text = reason
    step_result.presentation.status = self.m.step.WARNING
