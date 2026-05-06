# Copyright 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Buildbot recipe definition for the various Crashpad continuous builders.
"""

import datetime

from recipe_engine import post_process

from PB.recipe_engine import result as result_pb2
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb

DEPS = [
    'chromium',  # to import gclient configs
    'chromium_checkout',
    'depot_tools/gclient',
    'depot_tools/gsutil',
    'infra/zip',
    'recipe_engine/buildbucket',
    'recipe_engine/cipd',
    'recipe_engine/context',
    'recipe_engine/file',
    'recipe_engine/futures',
    'recipe_engine/json',
    'recipe_engine/path',
    'recipe_engine/platform',
    'recipe_engine/runtime',
    'recipe_engine/step',
    'recipe_engine/time',
]

MODEL_CIPD_PREFIX = 'chromium/rts/model/'
RTS_EXEC_CIPD_PREFIX = 'chromium/rts/rts-chromium/'
PLATFORMS = [
    'linux-amd64',
    'mac-amd64',
    'windows-amd64',
]
REJECTION_DATA_WINDOW = datetime.timedelta(weeks=12)
TEST_DURATION_DATA_WINDOW = datetime.timedelta(weeks=1)
# Processing 10% of 1w-worth test durations takes 7h on a 32-core bot.
TEST_DURATION_DATA_PERCENTAGE = 1


def RunSteps(api):
  # Start bot_update in background.
  checkout_dir_fut = api.futures.spawn_immediate(checkout_chromium, api)

  # Install rts-chromium executables.
  exec_pkg_paths = install_rts_executables(api)

  assert api.platform.is_linux
  assert api.platform.arch == 'intel'
  assert api.platform.bits == 64
  exec_path = exec_pkg_paths['linux-amd64'].joinpath('rts-chromium')

  # Fetch the dataset.
  # Ignore today because we might fetch incomplete data.
  yesterday = api.time.utcnow().date() - datetime.timedelta(days=1)
  rejections_dir, durations_dir = _fetch_model_data(
      api,
      exec_path,
      rejection_date_range=(
          yesterday - REJECTION_DATA_WINDOW,
          yesterday,
      ),
      duration_date_range=(
          yesterday - TEST_DURATION_DATA_WINDOW,
          yesterday,
      ),
  )

  # Create the model.
  checkout_dir = checkout_dir_fut.result()
  model_dir = api.path.cleanup_dir / 'rts-chromium-model'
  api.step(
      'create-model',
      [
        exec_path, 'create-model', \
        '-checkout', str(checkout_dir),
        '-rejections', str(rejections_dir), \
        '-durations', str(durations_dir), \
        '-model-dir', str(model_dir),
        '-log-furthest', '30',
      ],
  )

  # Upload the training data for inspection if we need to diagnose the model
  archive_training_data(api)

  step_result = run_integration_tests(api, exec_path, model_dir, checkout_dir)
  # Still create the cipd package so we can inspect it if the build failed
  # but don't move the ref to latest so broken models don't end up in builds
  should_update_ref = step_result.presentation.status == api.m.step.SUCCESS

  # TODO(crbug.com/1172372): ensure the new model is not significantly worse
  # than the current one.

  # Create CIPD packages.
  futures = []
  with api.step.nest('Create CIPD packages'):
    for platform, pkg_dir in exec_pkg_paths.items():
      futures.append(
          api.futures.spawn_immediate(create_cipd_package, api, platform,
                                      pkg_dir, model_dir, should_update_ref))
  # Check success.
  for f in futures:
    f.result()

  return result_pb2.RawResult(
      status=step_result.presentation.status,
      summary_markdown=compose_build_summary(api, model_dir),
  )


def archive_training_data(api):
  zip_out = api.path.cleanup_dir.joinpath('rts-chromium-model-data',
                                          'rts-chromium-model-data.zip')
  api.zip.directory('zip model dir',
                    api.path.cleanup_dir / 'rts-chromium-model-data', zip_out)
  api.gsutil.upload(
      zip_out,
      'chrome-rts',
      api.path.join('rts-chromium-model-data', api.buildbucket.build.number),
      raise_on_failure=False,
      use_retry_wrapper=False,
      name='archive model data')


def create_cipd_package(api, platform, exec_pkg_dir, model_dir, update_ref):
  with api.step.nest('Upload CIPD package - %s' % platform):
    # Copy the model files, such that create_cipd_package can be called for
    # different platforms concurrently.
    # Note that we add a platform-specific executable below.
    pkg_dir = api.path.cleanup_dir.joinpath('model-pkg-dir', platform)
    api.file.copytree('Copy the model files', model_dir, pkg_dir)

    # Add the executable to the model.
    exe_ext = '.exe' if platform.startswith('windows-') else ''
    exe_base_name = 'rts-chromium' + exe_ext
    api.file.copy(
        'Include the executable',
        exec_pkg_dir / exe_base_name,
        pkg_dir / exe_base_name,
    )

    # Upload to CIPD.
    pkg = api.cipd.PackageDefinition(
        package_name=MODEL_CIPD_PREFIX + platform,
        package_root=pkg_dir,
    )
    pkg.add_dir(pkg_dir)

    pin = api.cipd.create_from_pkg(
        pkg,
        tags={
            'build': 'https://ci.chromium.org/b/%d' % api.buildbucket.build.id,
        },
    )
    # Skip the ref update if it is an experimental build.
    if not api.runtime.is_experimental and update_ref:  # pragma: no branch
      api.cipd.set_ref(pin.package, version=pin.instance_id, refs=['latest'])


def install_rts_executables(api):
  """Installs rts-chromium executables for all platforms.

  Returns:
    Mapping {platform: package_path}, where
    - platform is a CIPD platform, e.g. "linux-amd64".
    - package_path is a api.path.Path object.
  """
  ver = pick_executable_version(api)
  install_dir = api.path.cleanup_dir / 'rts-chromium'

  ret = {}
  ensure_file = api.cipd.EnsureFile()
  for plat in PLATFORMS:
    ensure_file.add_package(
        name=RTS_EXEC_CIPD_PREFIX + plat,
        version=ver,
        subdir=plat,
    )
    ret[plat] = install_dir / plat

  api.cipd.ensure(install_dir, ensure_file, name='install RTS executables')
  return ret


def pick_executable_version(api) -> str:
  """Returns the CIPD version of rts-executable CIPD packages to use.
  Waits until the latest Windows revision is available on all platforms.
  """
  # Find the git_revision of the latest windows-amd64 package.
  # Using the git revision guarantees that packages for all platforms are built
  # from the same source code.
  # Use Windows because it seems to be slower to update than Mac now,
  # so its latest version is more likely to exist on all platforms.
  descr = api.cipd.describe(
      RTS_EXEC_CIPD_PREFIX + 'windows-amd64',
      'latest',
      test_data_tags=['git_revision:c1ee0e03d15281730ebedf1f7151474f7a523001'],
  )

  # Pick any git_revision.
  # It doesn't matter which one if they produced the same instance hash.
  ver = None
  for t in descr.tags:
    if t.tag.startswith('git_revision:'):
      ver = t.tag
      break

  if not ver:
    raise api.step.StepFailure(
        'git_revision tag not found in windows-amd64 latest')

  max_attempts = 10
  sleep_seconds = 60

  for attempt in range(max_attempts):
    # Check if this specific version exists on all other platforms.
    all_present = True
    for plat in PLATFORMS:
      if plat == 'windows-amd64':
        continue
      try:
        api.cipd.describe(
            RTS_EXEC_CIPD_PREFIX + plat,
            ver,
            test_data_tags=[
                'git_revision:c1ee0e03d15281730ebedf1f7151474f7a523001'
            ],
        )
      except api.step.StepFailure:
        all_present = False
        break

    if all_present:
      return ver

    if attempt < max_attempts - 1:
      api.time.sleep(sleep_seconds)

  raise api.step.StepFailure(
      'Timed out waiting for version %s to appear on all platforms' % ver)


def checkout_chromium(api):
  """Checks out chromium/src and returns its path."""
  api.gclient.set_config('chromium_empty')
  api.chromium_checkout.ensure_checkout()
  return api.chromium_checkout.source_dir


def _fetch_model_data(api, exec_path, rejection_date_range,
                      duration_date_range):
  """Fetches the data for model creation.

  Returns:
    A tuple (rejections_dir, durations_dir) with path to the directories with
    rejections and durations respectively.
  """
  data_dir = api.path.cleanup_dir / 'rts-chromium-model-data'
  rejections_dir = data_dir / 'rejections'
  durations_dir = data_dir / 'durations'

  futures = api.futures.wait([
    api.futures.spawn_immediate(
        api.step,
        'fetch rejections (fast)',
        [
          str(exec_path), 'fetch-rejections-fast', \
          '-out', str(rejections_dir),
        ] + _date_range_flags(rejection_date_range),
    ),
    api.futures.spawn_immediate(
        api.step,
        'fetch durations',
        [
          str(exec_path), 'fetch-durations', \
          '-frac', '%.3f' % (TEST_DURATION_DATA_PERCENTAGE/100.0), \
          '-out', str(durations_dir),
        ] + _date_range_flags(duration_date_range),
    ),
  ])

  # Check future's exception.
  for f in futures:
    f.result()

  return rejections_dir, durations_dir


def _date_range_flags(date_range):
  from_date, to_date = date_range
  return [
    '-from', from_date.strftime('%Y-%m-%d'), \
    '-to', to_date.strftime('%Y-%m-%d'),
  ]


def compose_build_summary(api, model_dir):
  cfg = api.file.read_json(
      'read thresholds',
      model_dir.joinpath('git-file-graph', 'config.json'),
      test_data={
          'thresholds': [
              {
                  'changeRecall': '0.97',
                  'savings': '0.5',
                  'maxDistance': '50',
              },
              {
                  'changeRecall': '0.98',
                  'savings': '0.4',
                  'maxDistance': '60',
              },
              {
                  'changeRecall': '0.99',
                  'savings': '0.3',
                  'maxDistance': '70',
              },
              {
                  'changeRecall': '1.00',
                  'savings': '0.2',
                  'maxDistance': '80',
              },
          ]
      },
  )

  # Format of the file is GitBasedStrategyConfig in
  # https://source.chromium.org/chromium/infra/infra/+/main:go/src/infra/rts/cmd/rts-chromium/rts-chromium.proto

  # Note: this is essentially HTML, not plaintext, so whitespace is collapsed.
  lines = ['ChangeRecall | Savings | Max distance']
  lines += [
      '%.4f | %.2f | %.2f' %
      (
        float(th.get('changeRecall', '0')), \
        float(th.get('savings', '0')), \
        float(th.get('maxDistance', '0'))
      )
      for th in cfg['thresholds']  # note: thresholds are already sorted
      if float(th.get('changeRecall', '0')) >= 0.9
  ]
  return '<br>'.join(lines)


def run_integration_tests(api, exec_path, model_dir, checkout_dir):
  return api.step(
      'integration_tests',
      [
          'python3',
          api.resource('integration_tests.py'),
          '--rts-exec',
          exec_path,
          '--model-dir',
          model_dir,
          '--chromium-checkout',
          checkout_dir,
      ],
      raise_on_failure=False,
  )


def GenTests(api):
  linux_amd64 = (
      api.platform.name('linux') + \
      api.platform.arch('intel') +
      api.platform.bits(64))

  yield api.test('basic') + linux_amd64

  yield api.test(
      'missing_git_revision', status='FAILURE') + linux_amd64 + api.step_data(
          'cipd describe chromium/rts/rts-chromium/windows-amd64',
          api.json.output({
              'result': {
                  'pin': {
                      'instance_id': 'resolved-instance_id-of-latest----------',
                      'package': 'chromium/rts/rts-chromium/windows-amd64'
                  },
                  'refs': [{
                      'instance_id':
                          'resolved-instance_id-of-latest----------',
                      'modified_by':
                          'user:44-blablbla@developer.gserviceaccount.com',
                      'modified_ts':
                          1446574210,
                      'ref':
                          'latest'
                  }],
                  'registered_by':
                      'user:44-blablbla@developer.gserviceaccount.com',
                  'registered_ts':
                      1446574210,
                  'tags': [{
                      'registered_by':
                          'user:44-blablbla@developer.gserviceaccount.com',
                      'registered_ts':
                          1446574210,
                      'tag':
                          'some_other_tag'
                  }]
              }
          }),
      ) + api.post_process(post_process.DropExpectation)

  timeout_test = api.test('timeout', status='FAILURE') + linux_amd64
  for i in range(10):
    suffix = f' ({i+1})' if i > 0 else ''
    timeout_test += api.step_data(
        f'cipd describe chromium/rts/rts-chromium/linux-amd64{suffix}',
        retcode=1)
  yield timeout_test + api.post_process(post_process.DropExpectation)
