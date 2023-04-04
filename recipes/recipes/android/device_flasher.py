# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Flash Android devices that meet certain criteria."""

import collections
import functools
import re
import textwrap

from google.protobuf import json_format

from recipe_engine import post_process
from PB.recipes.build.android import device_flasher

DEPS = [
    'depot_tools/gsutil',
    'recipe_engine/cipd',
    'recipe_engine/json',
    'recipe_engine/properties',
    'recipe_engine/raw_io',
    'recipe_engine/step',
    'recipe_engine/swarming',
]

PROPERTIES = device_flasher.InputProperties

# The gsutil uri to the place that store all the customized images.
BASE_IMAGE_URI = 'gs://chromium-android-images/images'

# Response of gsutil when non-existing objects are looked up.
GSUTIL_NO_MATCH_TXT = 'One or more URLs matched no objects'

# The lower and upper limit of the UID value
UID_LOWER_LIMIT = 10000
UID_UPPER_LIMIT = 19999


@functools.lru_cache()
def _LookupImage(api, device_type, device_os):
  """Check if an image exists in the bucket for a (device_type, device_os) combo

  The image url is of the pattern <base_uri>/<device_type>/<device_os>.zip
  The decorator functools.lru_cache is used to cache the results and reduce the
  calls to the gsutil API.

  Args:
    * device_type (str): The device type to lookup.
    * device_os (str): The device os to lookup.

  Returns:
    True if such an image exists otherwise False.
  """
  image_url = '%s/%s/%s.zip' % (BASE_IMAGE_URI, device_type, device_os)
  try:
    api.gsutil.list(
        image_url,
        name='lookup image for %s, %s' % (device_type, device_os),
        #raise_on_failure=False,
        stderr=api.raw_io.output_text())
    return True
  except api.step.StepFailure as e:
    if GSUTIL_NO_MATCH_TXT in e.result.stderr:
      e.result.presentation.status = api.step.WARNING
      e.result.presentation.step_text = '%s matches no objects' % image_url
      return False
    raise  # pragma: no cover


def _GetSwarmingBots(api, flash_criteria):
  """Get all bots that match the given criteria:

  Criteria include:
   * Dimension os=Android
   * Dimensions pool, device_type, device_os, and bot_id from flash_criteria.
   * Alive, i.e. not dead,not quarantined, and not in maintenance.
  """
  swarming_bots = []
  dimensions = {
      'os': 'Android',
      'device_type': flash_criteria.device_type,
      'device_os': flash_criteria.device_os,
      'pool': flash_criteria.pool,
  }
  bots = api.swarming.list_bots(
      'List Android bots',
      dimensions=dimensions,
      fields=['items(dimensions,state)'])
  # TODO: Add bot_id to the dimensions, if present.
  for bot in bots:
    # Skip bots that are dead, quarantined, or in maintenance
    # TODO: Add bot.in_maintenance after https://crrev.com/c/4383489 lands.
    if bot.is_dead or bot.quarantined:
      continue
    swarming_bots.append(bot)

  return swarming_bots


def _ProcessBot(api, bot, flash_criteria):
  task_request = None

  if flash_criteria.max_uid_threshold:
    device_type, device_os = None, None
    if bot.dimensions:
      device_type = bot.dimensions.get('device_type', [None])[-1]
      device_os = bot.dimensions.get('device_os', [None])[-1]
    if device_type and device_os and _LookupImage(api, device_type, device_os):
      # TODO
      # - Get the max_uid from the bot.state
      # - If the max_uid reaches the threshold, create a task request which will
      #   flash the device in the same way as
      #   http://go/cci-trooper/procedures/Android_Device_Issues.md#reflash
      pass

  return task_request


def _RunTasks(api, tasks_by_host, task_num_per_host, dry_run):
  task_results = []

  # TODO
  # - Split the tasks into chucks so that one host will run at most
  #   <task_num_per_host> tasks at a time.
  # - Trigger and collect tasks chuck by chunk.
  # - Return all the task results

  return task_results


def RunSteps(api, properties):
  for index, flash_criteria in enumerate(properties.flash_criteria):
    with api.step.nest('Process flash criteria %d' % index) as parent_prep:
      parent_prep.step_text = json_format.MessageToJson(
          flash_criteria, indent=2)

      tasks_by_host = collections.defaultdict(list)
      swarming_bots = _GetSwarmingBots(api, flash_criteria)
      for bot in swarming_bots:
        _ProcessBot(api, bot, flash_criteria)
        # TODO: group the task by the host_machine

      _RunTasks(api, tasks_by_host, 3, properties.dry_run)

      # TODO: Display the successfull and failed tasks


def GenTests(api):

  yield api.test(
      'basic',
      api.properties(flash_criteria=[{
          'pool': 'chromium.tests',
          'device_type': 'walleye|sailfish',
          'device_os': 'PQ3A.190801.002',
          'max_uid_threshold': 18000,
      }]),
      api.override_step_data(
          'Process flash criteria 0.List Android bots',
          api.json.output([
              api.swarming.generate_bot_json(
                  'flash--device1',
                  dimensions={
                      'pool': ['chromium.tests'],
                      'device_type': ['walleye'],
                      'device_os': ['P', 'PQ3A.190801.002'],
                  },
                  state={'devices': {
                      'device_serial': {
                          'max_uid': 19000,
                      }
                  }},
              ),
              api.swarming.generate_bot_json(
                  'no-image--device1',
                  dimensions={
                      'pool': ['chromium.tests'],
                      'device_type': ['sailfish'],
                      'device_os': ['P', 'PQ3A.190801.002'],
                  },
                  state={'devices': {
                      'device_serial': {
                          'max_uid': 17000,
                      }
                  }},
              ),
              api.swarming.generate_bot_json(
                  'dead--device3',
                  is_dead=True,
              ),
              api.swarming.generate_bot_json(
                  'quarantined--device3',
                  quarantined=True,
              ),
              # TODO: Add a bot in maintenance once
              # https://crrev.com/c/4383489 lands.
          ])),
      api.override_step_data(
          'Process flash criteria 0.'
          'gsutil lookup image for sailfish, PQ3A.190801.002',
          stderr=api.raw_io.output_text(GSUTIL_NO_MATCH_TXT),
          retcode=1,
      ),
      api.post_process(post_process.DropExpectation),
      status="SUCCESS",
  )

  yield api.test(
      'lookup-image-caching',
      api.properties(flash_criteria=[{
          'pool': 'chromium.tests',
          'device_type': 'walleye',
          'device_os': 'PQ3A.190801.002',
          'max_uid_threshold': 18000,
      }]),
      api.override_step_data(
          'Process flash criteria 0.List Android bots',
          api.json.output([
              api.swarming.generate_bot_json(
                  'flash--device1',
                  dimensions={
                      'pool': ['chromium.tests'],
                      'device_type': ['walleye'],
                      'device_os': ['P', 'PQ3A.190801.002'],
                  },
              ),
              api.swarming.generate_bot_json(
                  'flash--device2',
                  dimensions={
                      'pool': ['chromium.tests'],
                      'device_type': ['walleye'],
                      'device_os': ['P', 'PQ3A.190801.002'],
                  },
              ),
          ])),
      api.post_process(post_process.MustRun,
                       'Process flash criteria 0.'
                       'gsutil lookup image for walleye, PQ3A.190801.002'),
      # Check if "lru_cache" works as expected
      api.post_process(post_process.DoesNotRun,
                       'Process flash criteria 0.'
                       'gsutil lookup image for walleye, PQ3A.190801.002 (2)'),
      api.post_process(post_process.DropExpectation),
      status='SUCCESS',
  )
