# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Flash Android devices that meet certain criteria."""

import collections

from google.protobuf import json_format

from recipe_engine import post_process
from PB.recipes.build.android import device_flasher

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.depot_tools import gsutil
from RECIPE_MODULES.recipe_engine import (
  cipd,
  json,
  properties,
  raw_io,
  step,
  swarming,
)


@dataclass
class DEPS(RecipeScriptApi):
  cipd: cipd.API
  gsutil: gsutil.API
  json: json.API
  properties: properties.API
  raw_io: raw_io.API
  step: step.API
  swarming: swarming.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  cipd: cipd.TEST_API
  json: json.TEST_API
  properties: properties.TEST_API
  raw_io: raw_io.TEST_API
  step: step.TEST_API
  swarming: swarming.TEST_API


PROPERTIES = device_flasher.InputProperties

# The gsutil uri to the place that store all the customized images.
BASE_IMAGE_URI = 'gs://chromium-android-images/images'

# Response of gsutil when non-existing objects are looked up.
GSUTIL_NO_MATCH_TXT = 'One or more URLs matched no objects'

# The lower and upper limit of the UID value
UID_LOWER_LIMIT = 10000
UID_UPPER_LIMIT = 19999


def _LookupImage(
  api: DEPS, cache: dict[tuple[str, str], bool], device_type, device_os
):
  """Check if an image exists in the bucket for a (device_type, device_os) combo

  The image url is of the pattern <base_uri>/<device_type>/<device_os>.zip

  Args:
    * cache (dict): Mapping of image_url -> result to reduce extraneous calls to
      gsutil.
    * device_type (str): The device type to lookup.
    * device_os (str): The device os to lookup.

  Returns:
    True if such an image exists otherwise False.
  """
  key = (device_type, device_os)
  result = cache.get(key, None)
  if result is None:
    image_url = '%s/%s/%s.zip' % (BASE_IMAGE_URI, device_type, device_os)
    try:
      api.gsutil.list(
        image_url,
        name='lookup image for %s, %s' % (device_type, device_os),
        stderr=api.raw_io.output_text(),
      )
      result = True
    except api.step.StepFailure as e:
      if GSUTIL_NO_MATCH_TXT in e.result.stderr:
        e.result.presentation.status = api.step.WARNING
        e.result.presentation.step_text = '%s matches no objects' % image_url
        result = False
      else:
        raise  # pragma: no cover
    cache[key] = result
  return result


def _GetSwarmingBots(api: DEPS, flash_criteria):
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
    fields=['items(dimensions,state)'],
  )
  # TODO: Add bot_id to the dimensions, if present.
  for bot in bots:
    # Skip bots that are dead, quarantined, or in maintenance
    if bot.is_dead or bot.quarantined or bot.in_maintenance:
      continue
    swarming_bots.append(bot)

  return swarming_bots


def _CreateFlashTaskRequest(api: DEPS, bot_id, pool, device_type, device_os):
  gs_image_path = '%s/%s/%s.zip' % (BASE_IMAGE_URI, device_type, device_os)
  # TODO: Swith to use the latest catapult repo, i.e.
  #  - checkout catapult
  #  - isolate dependent files
  #  - replace cipd with isolate in task_slice.
  bash_command = ' && '.join(
    [
      './cipd_gsutil/gsutil cp %s .' % gs_image_path,
      'unzip %s.zip' % device_os,
      'vpython3 cipd_devil/devil/devil/android/tools/flash_device.py '
      '-w --wait -v .',
    ]
  )
  command = ['bash', '-c', bash_command]
  task_request = (
    api.swarming.task_request()
    .with_name('Flash %s (%s) to %s' % (bot_id, device_type, device_os))
    .with_priority(1)  # Make sure the flash task is prior than test tasks
  )
  task_request = task_request.with_slice(
    0,
    task_request[0]
    .with_dimensions(pool=pool, id=bot_id)
    .with_expiration_secs(20 * 60)
    .with_cipd_ensure_file(
      api.cipd.EnsureFile()
      .add_package('infra/3pp/tools/gsutil', 'latest', subdir='cipd_gsutil')
      .add_package(
        'infra/3pp/chromium/third_party/catapult/devil/linux-amd64',
        'latest',
        subdir='cipd_devil',
      )
    )
    .with_command(command),
  )
  return task_request


def _ProcessBot(api: DEPS, lookup_image_cache, bot, flash_criteria):
  task_request = None

  if (
    not flash_criteria.max_uid_threshold
    and not flash_criteria.min_disk_free_threshold
  ):  # pragma: no cover
    return task_request

  pool, device_type, device_os = None, None, None
  if bot.dimensions:
    pool = bot.dimensions['pool'][-1]
    device_type = bot.dimensions['device_type'][-1]
    device_os = bot.dimensions['device_os'][-1]
  if not (
    pool
    and device_type
    and device_os
    and _LookupImage(api, lookup_image_cache, device_type, device_os)
  ):
    return task_request

  # Default bot's max_uid to UID_LOWER_LIMIT, in case bot does not have this
  # prop, e.g. in fastboot mode.
  max_uid = UID_LOWER_LIMIT
  disk_free_percentage = None

  if bot.state and 'devices' in bot.state:
    # Note that one Android swarming bot will have at most *one* device.
    for _, device_state in bot.state['devices'].items():
      max_uid = device_state['max_uid']
      if 'data' in device_state['disk']:
        disk_free_mb = device_state['disk']['data']['free_mb']
        disk_size_mb = device_state['disk']['data']['size_mb']
        if disk_size_mb > 0:
          disk_free_percentage = int(disk_free_mb / disk_size_mb * 100)

  should_flash = False
  flash_reasons = []

  if (
    flash_criteria.max_uid_threshold
    and max_uid
    and max_uid >= flash_criteria.max_uid_threshold
  ):
    should_flash = True
    flash_reasons.append(
      'max_uid (%d) reaches threshold %d'
      % (max_uid, flash_criteria.max_uid_threshold)
    )

  if (
    flash_criteria.min_disk_free_threshold
    and disk_free_percentage is not None
    and disk_free_percentage < flash_criteria.min_disk_free_threshold
  ):
    should_flash = True
    flash_reasons.append(
      'disk free percentage (%d%%) is below threshold %d%%'
      % (disk_free_percentage, flash_criteria.min_disk_free_threshold)
    )

  if should_flash:
    step = api.step.empty(
      'Create flash task for %s' % bot.bot_id,
      step_text=', '.join(flash_reasons),
    )
    step.presentation.links['bot UI: %s' % bot.bot_id] = bot.bot_ui_link
    task_request = _CreateFlashTaskRequest(
      api, bot.bot_id, pool, device_type, device_os
    )

  return task_request


def _RunTasks(api: DEPS, tasks_by_host, dry_run):
  """Trigger the tasks group by group and collect the results.

  One Android host machine hosts up to 7 devices. The machine may run of disk
  space or hit IO bound if 7 devices are flashed all together. To avoid that,
  we split them into subgroups where each subgroup contains at most 3 devices
  from the same host.
  """
  task_results = []
  counter = -1
  while True:
    subgroup = []
    counter += 1
    for host_machine, tasks in tasks_by_host.items():
      subgroup.extend(tasks[:3])
      tasks_by_host[host_machine] = tasks[3:]
    if not subgroup:
      break
    if dry_run:  # pragma: no cover
      task_jsons = [t.to_jsonish() for t in subgroup]
      api.step.empty(
        'Trigger flash task group %d (dry_run)' % counter,
        log_text=api.json.dumps(task_jsons, indent=2),
      )
    else:
      tasks = api.swarming.trigger(
        'Trigger flash task group %d' % counter, subgroup
      )
      task_results.extend(
        api.swarming.collect(
          'Collect results for task group %d' % counter, tasks
        )
      )

  return task_results


def RunSteps(api: DEPS, properties):
  has_failure = False
  lookup_image_cache = {}
  for index, flash_criteria in enumerate(properties.flash_criteria):
    with api.step.nest('Process flash criteria %d' % index) as parent_prep:
      parent_prep.step_summary_text = '\n\n```\n%s\n```' % (
        json_format.MessageToJson(
          flash_criteria, indent=2, preserving_proto_field_name=True
        )
      )

      tasks_by_host = collections.defaultdict(list)
      swarming_bots = _GetSwarmingBots(api, flash_criteria)
      for bot in swarming_bots:
        task_request = _ProcessBot(api, lookup_image_cache, bot, flash_criteria)
        if task_request:
          # bot_id for Android follows the format "<host_machine>--device{1,7}"
          # We group the task by the host_machine.
          # TODO: Switch to get host_machine from "authenticated_as"
          host_machine = bot.bot_id.split('--device')[0]
          tasks_by_host[host_machine].append(task_request)

      task_results = _RunTasks(api, tasks_by_host, flash_criteria.dry_run)
      failed_results = [r for r in task_results if not r.success]
      if failed_results:
        has_failure = True
        step = api.step.empty(
          'Failed tasks', status='FAILURE', raise_on_failure=False
        )
        for result in failed_results:
          step.presentation.links[result.name] = '%s/task?id=%s' % (
            api.swarming.current_server,
            result.id,
          )

  if has_failure:
    api.step.empty(
      'Some flash tasks failed. See above steps for details', status='FAILURE'
    )


def GenTests(api: TEST_DEPS):

  walleye_dimensions = {
    'pool': ['chromium.tests'],
    'device_type': ['walleye'],
    'device_os': ['P', 'PQ3A.190801.002'],
  }
  sailfish_dimensions = {
    'pool': ['chromium.tests'],
    'device_type': ['sailfish'],
    'device_os': ['P', 'PQ3A.190801.002'],
  }
  flash_state = {'devices': {'device_serial': {'max_uid': 19000, 'disk': {}}}}
  no_flash_state = {
    'devices': {
      'device_serial': {
        'max_uid': 17000,
        'disk': {'data': {'free_mb': 900, 'size_mb': 1000}},
      }
    }
  }

  yield api.test(
    'basic',
    api.properties(
      flash_criteria=[
        {
          'pool': 'chromium.tests',
          'device_type': 'walleye',
          'device_os': 'PQ3A.190801.002',
          'max_uid_threshold': 18000,
        }
      ]
    ),
    api.override_step_data(
      'Process flash criteria 0.List Android bots',
      api.json.output(
        [
          api.swarming.generate_bot_json(
            'flash--device1',
            dimensions=walleye_dimensions,
            state=flash_state,
          ),
          api.swarming.generate_bot_json(
            'no-flash--device1',
            dimensions=walleye_dimensions,
            state=no_flash_state,
          ),
          api.swarming.generate_bot_json(
            'dead--device3',
            is_dead=True,
            dimensions=walleye_dimensions,
            state=flash_state,
          ),
          api.swarming.generate_bot_json(
            'quarantined--device3',
            quarantined=True,
            dimensions=walleye_dimensions,
            state=flash_state,
          ),
          api.swarming.generate_bot_json(
            'maintenance--device4',
            maintenance_msg='I am in maintenance.',
            dimensions=walleye_dimensions,
            state=flash_state,
          ),
        ]
      ),
    ),
    api.post_process(
      post_process.MustRun,
      'Process flash criteria 0.Create flash task for flash--device1',
    ),
    api.post_process(
      post_process.DoesNotRun,
      'Process flash criteria 0.Create flash task for no-flash--device1',
    ),
    api.post_process(
      post_process.DoesNotRun,
      'Process flash criteria 0.Create flash task for dead--device1',
    ),
    api.post_process(
      post_process.DoesNotRun,
      'Process flash criteria 0.Create flash task for quarantined--device1',
    ),
    api.post_process(
      post_process.DoesNotRun,
      'Process flash criteria 0.Create flash task for maintenance--device1',
    ),
    api.post_process(post_process.DropExpectation),
    status="SUCCESS",
  )

  yield api.test(
    'lookup-image',
    api.properties(
      flash_criteria=[
        {
          'pool': 'chromium.tests',
          'device_type': 'walleye|sailfish',
          'device_os': 'PQ3A.190801.002',
          'max_uid_threshold': 18000,
        }
      ]
    ),
    api.override_step_data(
      'Process flash criteria 0.List Android bots',
      api.json.output(
        [
          api.swarming.generate_bot_json(
            'flash--device1',
            dimensions=walleye_dimensions,
          ),
          api.swarming.generate_bot_json(
            'flash--device2',
            dimensions=walleye_dimensions,
          ),
          api.swarming.generate_bot_json(
            'no-image--device1',
            dimensions=sailfish_dimensions,
          ),
        ]
      ),
    ),
    api.post_process(
      post_process.MustRun,
      'Process flash criteria 0.'
      'gsutil lookup image for walleye, PQ3A.190801.002',
    ),
    # Check if "lru_cache" works as expected
    api.post_process(
      post_process.DoesNotRun,
      'Process flash criteria 0.'
      'gsutil lookup image for walleye, PQ3A.190801.002 (2)',
    ),
    # Check when no image exists
    api.override_step_data(
      'Process flash criteria 0.'
      'gsutil lookup image for sailfish, PQ3A.190801.002',
      stderr=api.raw_io.output_text(GSUTIL_NO_MATCH_TXT),
      retcode=1,
    ),
    api.post_process(
      post_process.StepWarning,
      'Process flash criteria 0.'
      'gsutil lookup image for sailfish, PQ3A.190801.002',
    ),
    api.post_process(
      post_process.StepTextContains,
      'Process flash criteria 0.'
      'gsutil lookup image for sailfish, PQ3A.190801.002',
      ['matches no object'],
    ),
    api.post_process(post_process.DropExpectation),
    status='SUCCESS',
  )

  yield api.test(
    'run-tasks',
    api.properties(
      flash_criteria=[
        {
          'pool': 'chromium.tests',
          'device_type': 'walleye',
          'device_os': 'PQ3A.190801.002',
          'max_uid_threshold': 18000,
        }
      ]
    ),
    api.override_step_data(
      'Process flash criteria 0.List Android bots',
      api.json.output(
        [
          api.swarming.generate_bot_json(
            'flash--device%d' % index,
            dimensions=walleye_dimensions,
            state=flash_state,
          )
          for index in range(4)
        ]
      ),
    ),
    api.post_process(
      post_process.LogContains,
      'Process flash criteria 0.Trigger flash task group 0',
      'json.input',
      ['Flash flash--device%d' % index for index in range(3)],
    ),
    api.post_process(
      post_process.MustRun,
      'Process flash criteria 0.Collect results for task group 0',
    ),
    api.post_process(
      post_process.LogContains,
      'Process flash criteria 0.Trigger flash task group 1',
      'json.input',
      ['Flash flash--device3'],
    ),
    api.override_step_data(
      'Process flash criteria 0.Collect results for task group 1',
      api.swarming.collect(
        [
          api.swarming.task_result(
            id=1, name='Flash flash--device4', failure=True
          )
        ]
      ),
    ),
    api.post_process(
      post_process.MustRun,
      'Some flash tasks failed. See above steps for details',
    ),
    api.post_process(post_process.DropExpectation),
    status='FAILURE',
  )

  disk_flash_state = {
    'devices': {
      'device_serial': {
        'max_uid': 17000,
        'disk': {'data': {'free_mb': 400, 'size_mb': 10000}},
      }
    }
  }
  yield api.test(
    'disk-free-threshold',
    api.properties(
      flash_criteria=[
        {
          'pool': 'chromium.tests',
          'device_type': 'walleye',
          'device_os': 'PQ3A.190801.002',
          'min_disk_free_threshold': 5,
        }
      ]
    ),
    api.override_step_data(
      'Process flash criteria 0.List Android bots',
      api.json.output(
        [
          api.swarming.generate_bot_json(
            'flash--device1',
            dimensions=walleye_dimensions,
            state=disk_flash_state,
          ),
        ]
      ),
    ),
    api.post_process(
      post_process.MustRun,
      'Process flash criteria 0.Create flash task for flash--device1',
    ),
    api.post_process(
      post_process.StepTextEquals,
      'Process flash criteria 0.Create flash task for flash--device1',
      'disk free percentage (4%) is below threshold 5%',
    ),
    api.post_process(post_process.DropExpectation),
    status='SUCCESS',
  )
