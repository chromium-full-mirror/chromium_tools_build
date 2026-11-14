# Copyright 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Buildbot recipe definition for the various Crashpad continuous builders."""

import datetime

from recipe_engine import post_process

from PB.recipe_engine import result as result_pb2
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb  # noqa: F401

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import chromium, chromium_checkout
from RECIPE_MODULES.build.chromium_rts.api import (
  DEFAULT_TARGET_CHANGE_RECALL,
  RTS_MODEL_CIPD_PREFIX,
)
from RECIPE_MODULES.depot_tools import gclient, gsutil
from RECIPE_MODULES.infra import zip as zip_module
from RECIPE_MODULES.recipe_engine import (
  buildbucket,
  cipd,
  context,
  file,
  futures,
  json,
  path,
  platform,
  runtime,
  step,
  time,
)


@dataclass
class DEPS(RecipeScriptApi):
  buildbucket: buildbucket.API
  chromium: chromium.API
  chromium_checkout: chromium_checkout.API
  cipd: cipd.API
  context: context.API
  file: file.API
  futures: futures.API
  gclient: gclient.API
  gsutil: gsutil.API
  json: json.API
  path: path.API
  platform: platform.API
  runtime: runtime.API
  step: step.API
  time: time.API
  zip: zip_module.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  buildbucket: buildbucket.TEST_API
  chromium_checkout: chromium_checkout.TEST_API
  cipd: cipd.TEST_API
  file: file.TEST_API
  gclient: gclient.TEST_API
  json: json.TEST_API
  path: path.TEST_API
  platform: platform.TEST_API
  runtime: runtime.TEST_API
  step: step.TEST_API
  time: time.TEST_API


RTS_EXEC_CIPD_PREFIX = 'chromium/rts/rts-chromium/'
PLATFORMS = [
  'linux-amd64',
  'linux-arm64',
  'mac-amd64',
  'mac-arm64',
  'windows-amd64',
  'windows-arm64',
]
REJECTION_DATA_WINDOW = datetime.timedelta(weeks=12)
TEST_DURATION_DATA_WINDOW = datetime.timedelta(weeks=1)
# Processing 10% of 1w-worth test durations takes 7h on a 32-core bot.
TEST_DURATION_DATA_PERCENTAGE = 1
MIN_SAVINGS_FLOOR = 0.20
MAX_SAVINGS_DROP = 0.03


def RunSteps(api: DEPS):
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
  checkout_dir, git_revision = checkout_dir_fut.result()
  model_dir = api.path.cleanup_dir / 'rts-chromium-model'
  api.step(
    'create-model',
    [
      exec_path,
      'create-model',
      '-checkout',
      str(checkout_dir),
      '-rejections',
      str(rejections_dir),
      '-durations',
      str(durations_dir),
      '-model-dir',
      str(model_dir),
      '-log-furthest',
      '30',
    ],
  )

  # Upload the training data for inspection if we need to diagnose the model
  archive_training_data(api)

  candidate_cfg = read_model_config(api, 'read thresholds', model_dir)

  step_result = run_integration_tests(api, exec_path, model_dir, checkout_dir)
  integration_passed = step_result.presentation.status == api.step.SUCCESS
  gate_passed = evaluate_safety_gate(api, candidate_cfg, integration_passed)

  # Still create the cipd package so we can inspect it if the build failed,
  # keep advancing latest when integration tests pass, and only promote prod
  # when the scorecard safety gate also passes.
  refs_to_update = []
  if integration_passed:
    refs_to_update.append('latest')
    if gate_passed:
      refs_to_update.append('prod')

  # Create CIPD packages.
  futures = []
  with api.step.nest('Create CIPD packages'):
    for platform, pkg_dir in exec_pkg_paths.items():
      futures.append(
        api.futures.spawn_immediate(
          create_cipd_package,
          api,
          platform,
          pkg_dir,
          model_dir,
          refs_to_update,
          git_revision,
        )
      )
  # Check success.
  for f in futures:
    f.result()

  return result_pb2.RawResult(
    status=step_result.presentation.status,
    summary_markdown=compose_build_summary(candidate_cfg),
  )


def archive_training_data(api: DEPS):
  zip_out = api.path.cleanup_dir.joinpath(
    'rts-chromium-model-data', 'rts-chromium-model-data.zip'
  )
  api.zip.directory(
    'zip model dir', api.path.cleanup_dir / 'rts-chromium-model-data', zip_out
  )
  api.gsutil.upload(
    zip_out,
    'chrome-rts',
    api.path.join('rts-chromium-model-data', api.buildbucket.build.number),
    raise_on_failure=False,
    use_retry_wrapper=False,
    name='archive model data',
  )


def create_cipd_package(
  api: DEPS,
  platform,
  exec_pkg_dir,
  model_dir,
  refs,
  git_revision,
):
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
      package_name=RTS_MODEL_CIPD_PREFIX + platform,
      package_root=pkg_dir,
    )
    pkg.add_dir(pkg_dir)

    pin = api.cipd.create_from_pkg(
      pkg,
      tags={
        'build': 'https://ci.chromium.org/b/%d' % api.buildbucket.build.id,
        'git_revision': git_revision,
      },
    )

    # Skip the ref update if it is an experimental build.
    if not api.runtime.is_experimental and refs:
      api.cipd.set_ref(pin.package, version=pin.instance_id, refs=refs)


def install_rts_executables(api: DEPS):
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


def pick_executable_version(api: DEPS) -> str:
  """Returns the CIPD version of rts-executable CIPD packages to use.
  Picks the newest git_revision tag shared by the latest package across all
  platforms, waiting and retrying if no common tag exists yet.
  """
  max_attempts = 10
  sleep_seconds = 60

  for attempt in range(max_attempts):
    common_revisions = None
    ordered_revisions = []
    for platform_name in PLATFORMS:
      description = api.cipd.describe(
        RTS_EXEC_CIPD_PREFIX + platform_name,
        'latest',
        test_data_tags=[
          'git_revision:c1ee0e03d15281730ebedf1f7151474f7a523001'
        ],
      )
      platform_revisions = [
        tag.tag
        for tag in description.tags
        if tag.tag.startswith('git_revision:')
      ]
      if not platform_revisions:
        raise api.step.StepFailure(
          f'git_revision tag not found in {platform_name} latest'
        )
      if common_revisions is None:
        ordered_revisions = platform_revisions
        common_revisions = set(platform_revisions)
      else:
        common_revisions &= set(platform_revisions)

    for revision in ordered_revisions:
      if revision in common_revisions:
        return revision

    if attempt < max_attempts - 1:
      api.time.sleep(sleep_seconds)

  raise api.step.StepFailure(
    'Timed out waiting for a common git_revision tag to appear on all platforms'
  )


def checkout_chromium(api: DEPS):
  """Checks out chromium/src and returns its path and git revision."""
  api.gclient.set_config('chromium_empty')
  update_result = api.chromium_checkout.ensure_checkout()
  return (
    api.chromium_checkout.source_dir,
    update_result.properties['got_revision'],
  )


def _fetch_model_data(
  api: DEPS, exec_path, rejection_date_range, duration_date_range
):
  """Fetches the data for model creation.

  Returns:
    A tuple (rejections_dir, durations_dir) with path to the directories with
    rejections and durations respectively.
  """
  data_dir = api.path.cleanup_dir / 'rts-chromium-model-data'
  rejections_dir = data_dir / 'rejections'
  durations_dir = data_dir / 'durations'

  futures = api.futures.wait(
    [
      api.futures.spawn_immediate(
        api.step,
        'fetch rejections (fast)',
        [
          str(exec_path),
          'fetch-rejections-fast',
          '-out',
          str(rejections_dir),
        ]
        + _date_range_flags(rejection_date_range),
      ),
      api.futures.spawn_immediate(
        api.step,
        'fetch durations',
        [
          str(exec_path),
          'fetch-durations',
          '-frac',
          '%.3f' % (TEST_DURATION_DATA_PERCENTAGE / 100.0),
          '-out',
          str(durations_dir),
        ]
        + _date_range_flags(duration_date_range),
      ),
    ]
  )

  # Check future's exception.
  for f in futures:
    f.result()

  return rejections_dir, durations_dir


def _date_range_flags(date_range):
  from_date, to_date = date_range
  return [
    '-from',
    from_date.strftime('%Y-%m-%d'),
    '-to',
    to_date.strftime('%Y-%m-%d'),
  ]


def read_model_config(api: DEPS, step_name: str, model_dir):
  return api.file.read_json(
    step_name,
    model_dir.joinpath('git-file-graph', 'config.json'),
    test_data={
      'thresholds': [
        {
          'changeRecall': '0.95',
          'savings': '0.6',
          'maxDistance': '40',
        },
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


def get_savings_for_recall(
  api: DEPS, cfg: dict, target_recall: float
) -> tuple[float, float]:
  """Returns (change_recall, savings) for the lowest recall >= target_recall."""
  matching = [
    th
    for th in cfg.get('thresholds', [])
    if float(th.get('changeRecall', 0)) >= target_recall
  ]
  if not matching:
    raise api.step.StepFailure(
      f'No threshold found for target change recall {target_recall}'
    )
  chosen = min(matching, key=lambda th: float(th.get('changeRecall', 0)))
  return float(chosen.get('changeRecall', 0)), float(chosen.get('savings', 0))


def _fetch_prod_savings(api: DEPS) -> float:
  """Fetches the current prod model and returns its target-recall savings."""
  prod_dir = api.path.cleanup_dir / 'rts-prod-model'
  ensure_file = api.cipd.EnsureFile()
  ensure_file.add_package(RTS_MODEL_CIPD_PREFIX + 'linux-amd64', 'prod')
  try:
    api.cipd.ensure(prod_dir, ensure_file, name='fetch current prod model')
  except api.cipd.Error as e:
    if 'no such ref' in str(e):
      api.step.active_result.presentation.status = api.step.SUCCESS
      api.step.active_result.presentation.step_text = (
        'prod ref not found; skipping relative comparison'
      )
    else:
      api.step.active_result.presentation.step_text = (
        f'failed to fetch prod model: {e}'
      )
    raise
  prod_cfg = read_model_config(api, 'read prod thresholds', prod_dir)
  _, prod_savings = get_savings_for_recall(
    api, prod_cfg, DEFAULT_TARGET_CHANGE_RECALL
  )
  return prod_savings


def evaluate_safety_gate(
  api: DEPS,
  candidate_cfg: dict,
  integration_passed: bool,
) -> bool:
  """Verifies candidate savings meet the global floor and prod tolerance."""
  with api.step.nest('Evaluate model safety gate') as gate_step:
    props = gate_step.properties
    scorecard = {
      'target_change_recall': DEFAULT_TARGET_CHANGE_RECALL,
      'promoted_to_prod': False,
    }
    gate_passed = False
    try:
      try:
        candidate_recall, candidate_savings = get_savings_for_recall(
          api, candidate_cfg, DEFAULT_TARGET_CHANGE_RECALL
        )
      except api.step.StepFailure as e:
        gate_step.step_text = str(e)
        return False

      scorecard['change_recall'] = round(candidate_recall, 4)
      scorecard['savings'] = round(candidate_savings, 4)
      scorecard['thresholds'] = [
        {
          'change_recall': round(float(th.get('changeRecall', 0)), 4),
          'savings': round(float(th.get('savings', 0)), 4),
        }
        for th in candidate_cfg.get('thresholds', [])
        if float(th.get('changeRecall', 0)) >= DEFAULT_TARGET_CHANGE_RECALL
      ]

      reasons = []
      if candidate_savings < MIN_SAVINGS_FLOOR:
        reasons.append(
          f'candidate savings {candidate_savings:.4f} is below '
          f'minimum floor {MIN_SAVINGS_FLOOR:.4f}'
        )

      try:
        prod_savings = _fetch_prod_savings(api)
      except (api.step.StepFailure, api.cipd.Error) as e:
        if not (isinstance(e, api.cipd.Error) and 'no such ref' in str(e)):
          gate_step.step_text = f'failed to evaluate prod baseline: {e}'
          return False
        passed_text = (
          f'bootstrapping prod ref (savings: {candidate_savings:.4f})'
        )
      else:
        scorecard['prod_savings'] = round(prod_savings, 4)
        if round(prod_savings - candidate_savings, 4) > MAX_SAVINGS_DROP:
          reasons.append(
            f'candidate savings {candidate_savings:.4f} dropped more than '
            f'{MAX_SAVINGS_DROP:.4f} from prod ({prod_savings:.4f})'
          )
        passed_text = (
          f'passed (candidate: {candidate_savings:.4f}, '
          f'prod: {prod_savings:.4f})'
        )

      if reasons:
        gate_step.step_text = '; '.join(reasons)
        return False

      gate_passed = True
      scorecard['promoted_to_prod'] = (
        integration_passed and not api.runtime.is_experimental
      )
      gate_step.step_text = passed_text
      return True
    finally:
      props['rts_model_scorecard'] = scorecard
      if not gate_passed:
        gate_step.set_worse_status(api.step.FAILURE)


def compose_build_summary(cfg: dict):
  # Format of the file is GitBasedStrategyConfig in
  # https://source.chromium.org/chromium/infra/infra/+/main:go/src/infra/rts/cmd/rts-chromium/rts-chromium.proto

  # Note: this is essentially HTML, not plaintext, so whitespace is collapsed.
  lines = ['ChangeRecall | Savings | Max distance']
  lines += [
    '%.4f | %.2f | %.2f'
    % (
      float(th.get('changeRecall', '0')),
      float(th.get('savings', '0')),
      float(th.get('maxDistance', '0')),
    )
    for th in cfg['thresholds']  # note: thresholds are already sorted
    if float(th.get('changeRecall', '0')) >= 0.9
  ]
  return '<br>'.join(lines)


def run_integration_tests(api: DEPS, exec_path, model_dir, checkout_dir):
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


def GenTests(api: TEST_DEPS):
  linux_amd64 = (
    api.platform.name('linux')
    + api.platform.arch('intel')
    + api.platform.bits(64)
  )

  yield api.test('basic') + linux_amd64

  yield (
    api.test('integration_tests_failed', status='FAILURE')
    + linux_amd64
    + api.step_data('integration_tests', retcode=1)
    + api.post_process(
      post_process.StepCommandContains,
      'Create CIPD packages.Upload CIPD package - linux-amd64'
      '.create chromium/rts/model/linux-amd64',
      [
        '-tag',
        'git_revision:f27fede2220bcd326aee3e86ddfd4ebd0fe58cb9',
      ],
    )
    + api.post_process(
      post_process.DoesNotRunRE,
      r'.*cipd set-ref.*',
    )
    + api.post_process(post_process.DropExpectation)
  )

  default_thresholds = [
    {'change_recall': 0.95, 'savings': 0.6},
    {'change_recall': 0.97, 'savings': 0.5},
    {'change_recall': 0.98, 'savings': 0.4},
    {'change_recall': 0.99, 'savings': 0.3},
    {'change_recall': 1.0, 'savings': 0.2},
  ]

  yield (
    api.test('experimental_build')
    + linux_amd64
    + api.runtime(is_experimental=True)
    + api.post_process(
      post_process.PropertyEquals,
      'rts_model_scorecard',
      {
        'change_recall': 0.95,
        'prod_savings': 0.6,
        'promoted_to_prod': False,
        'savings': 0.6,
        'target_change_recall': 0.95,
        'thresholds': default_thresholds,
      },
    )
    + api.post_process(
      post_process.MustRun,
      'Create CIPD packages.Upload CIPD package - linux-amd64'
      '.create chromium/rts/model/linux-amd64',
    )
    + api.post_process(
      post_process.DoesNotRunRE,
      r'.*cipd set-ref.*',
    )
    + api.post_process(post_process.DropExpectation)
  )

  yield (
    api.test('prod_ref_not_found_bootstrap')
    + linux_amd64
    + api.step_data(
      'Evaluate model safety gate.fetch current prod model',
      api.cipd.example_error('no such ref'),
    )
    + api.post_process(
      post_process.PropertyEquals,
      'rts_model_scorecard',
      {
        'change_recall': 0.95,
        'promoted_to_prod': True,
        'savings': 0.6,
        'target_change_recall': 0.95,
        'thresholds': default_thresholds,
      },
    )
    + api.post_process(
      post_process.StepCommandContains,
      'Create CIPD packages.Upload CIPD package - linux-amd64'
      '.cipd set-ref chromium/rts/model/linux-amd64',
      ['-ref', 'latest', '-ref', 'prod'],
    )
    + api.post_process(post_process.DropExpectation)
  )

  yield (
    api.test('bootstrap_below_min_savings_floor')
    + linux_amd64
    + api.step_data(
      'read thresholds',
      api.file.read_json(
        {
          'thresholds': [
            {
              'changeRecall': '0.95',
              'savings': '0.15',
              'maxDistance': '21',
            },
          ]
        }
      ),
    )
    + api.step_data(
      'Evaluate model safety gate.fetch current prod model',
      api.cipd.example_error('no such ref'),
    )
    + api.post_process(
      post_process.PropertyEquals,
      'rts_model_scorecard',
      {
        'change_recall': 0.95,
        'promoted_to_prod': False,
        'savings': 0.15,
        'target_change_recall': 0.95,
        'thresholds': [{'change_recall': 0.95, 'savings': 0.15}],
      },
    )
    + api.post_process(
      post_process.StepCommandContains,
      'Create CIPD packages.Upload CIPD package - linux-amd64'
      '.cipd set-ref chromium/rts/model/linux-amd64',
      ['-ref', 'latest'],
    )
    + api.post_process(
      post_process.StepCommandDoesNotContain,
      'Create CIPD packages.Upload CIPD package - linux-amd64'
      '.cipd set-ref chromium/rts/model/linux-amd64',
      ['-ref', 'prod'],
    )
    + api.post_process(post_process.DropExpectation)
  )

  yield (
    api.test('below_min_savings_floor')
    + linux_amd64
    + api.step_data(
      'read thresholds',
      api.file.read_json(
        {
          'thresholds': [
            {
              'changeRecall': '0.95',
              'savings': '0.15',
              'maxDistance': '21',
            },
          ]
        }
      ),
    )
    + api.post_process(
      post_process.PropertyEquals,
      'rts_model_scorecard',
      {
        'change_recall': 0.95,
        'prod_savings': 0.6,
        'promoted_to_prod': False,
        'savings': 0.15,
        'target_change_recall': 0.95,
        'thresholds': [{'change_recall': 0.95, 'savings': 0.15}],
      },
    )
    + api.post_process(
      post_process.StepCommandContains,
      'Create CIPD packages.Upload CIPD package - linux-amd64'
      '.cipd set-ref chromium/rts/model/linux-amd64',
      ['-ref', 'latest'],
    )
    + api.post_process(
      post_process.StepCommandDoesNotContain,
      'Create CIPD packages.Upload CIPD package - linux-amd64'
      '.cipd set-ref chromium/rts/model/linux-amd64',
      ['-ref', 'prod'],
    )
    + api.post_process(post_process.DropExpectation)
  )

  yield (
    api.test('exact_max_savings_drop_passes')
    + linux_amd64
    + api.step_data(
      'read thresholds',
      api.file.read_json(
        {
          'thresholds': [
            {
              'changeRecall': '0.95',
              'savings': '0.57',
              'maxDistance': '40',
            },
          ]
        }
      ),
    )
    + api.post_process(
      post_process.PropertyEquals,
      'rts_model_scorecard',
      {
        'change_recall': 0.95,
        'prod_savings': 0.6,
        'promoted_to_prod': True,
        'savings': 0.57,
        'target_change_recall': 0.95,
        'thresholds': [{'change_recall': 0.95, 'savings': 0.57}],
      },
    )
    + api.post_process(
      post_process.StepCommandContains,
      'Create CIPD packages.Upload CIPD package - linux-amd64'
      '.cipd set-ref chromium/rts/model/linux-amd64',
      ['-ref', 'latest', '-ref', 'prod'],
    )
    + api.post_process(post_process.DropExpectation)
  )

  yield (
    api.test('savings_regression_from_prod')
    + linux_amd64
    + api.step_data(
      'read thresholds',
      api.file.read_json(
        {
          'thresholds': [
            {
              'changeRecall': '0.95',
              'savings': '0.50',
              'maxDistance': '40',
            },
          ]
        }
      ),
    )
    + api.post_process(
      post_process.PropertyEquals,
      'rts_model_scorecard',
      {
        'change_recall': 0.95,
        'prod_savings': 0.6,
        'promoted_to_prod': False,
        'savings': 0.5,
        'target_change_recall': 0.95,
        'thresholds': [{'change_recall': 0.95, 'savings': 0.5}],
      },
    )
    + api.post_process(
      post_process.StepCommandContains,
      'Create CIPD packages.Upload CIPD package - linux-amd64'
      '.cipd set-ref chromium/rts/model/linux-amd64',
      ['-ref', 'latest'],
    )
    + api.post_process(
      post_process.StepCommandDoesNotContain,
      'Create CIPD packages.Upload CIPD package - linux-amd64'
      '.cipd set-ref chromium/rts/model/linux-amd64',
      ['-ref', 'prod'],
    )
    + api.post_process(post_process.DropExpectation)
  )

  yield (
    api.test('missing_recall_threshold')
    + linux_amd64
    + api.step_data('read thresholds', api.file.read_json({'thresholds': []}))
    + api.post_process(
      post_process.PropertyEquals,
      'rts_model_scorecard',
      {
        'promoted_to_prod': False,
        'target_change_recall': 0.95,
      },
    )
    + api.post_process(
      post_process.StepCommandContains,
      'Create CIPD packages.Upload CIPD package - linux-amd64'
      '.cipd set-ref chromium/rts/model/linux-amd64',
      ['-ref', 'latest'],
    )
    + api.post_process(
      post_process.StepCommandDoesNotContain,
      'Create CIPD packages.Upload CIPD package - linux-amd64'
      '.cipd set-ref chromium/rts/model/linux-amd64',
      ['-ref', 'prod'],
    )
    + api.post_process(post_process.DropExpectation)
  )

  yield (
    api.test('prod_fetch_infra_error')
    + linux_amd64
    + api.step_data(
      'Evaluate model safety gate.fetch current prod model',
      api.cipd.example_error('transient rpc error'),
    )
    + api.post_process(
      post_process.PropertyEquals,
      'rts_model_scorecard',
      {
        'change_recall': 0.95,
        'promoted_to_prod': False,
        'savings': 0.6,
        'target_change_recall': 0.95,
        'thresholds': default_thresholds,
      },
    )
    + api.post_process(
      post_process.StepCommandContains,
      'Create CIPD packages.Upload CIPD package - linux-amd64'
      '.cipd set-ref chromium/rts/model/linux-amd64',
      ['-ref', 'latest'],
    )
    + api.post_process(
      post_process.StepCommandDoesNotContain,
      'Create CIPD packages.Upload CIPD package - linux-amd64'
      '.cipd set-ref chromium/rts/model/linux-amd64',
      ['-ref', 'prod'],
    )
    + api.post_process(post_process.DropExpectation)
  )

  yield (
    api.test('missing_git_revision', status='FAILURE')
    + linux_amd64
    + api.step_data(
      'cipd describe chromium/rts/rts-chromium/linux-amd64',
      api.cipd.example_describe(
        'chromium/rts/rts-chromium/linux-amd64',
        test_data_tags=['some_other:tag'],
      ),
    )
    + api.post_process(post_process.DropExpectation)
  )

  yield (
    api.test('lagging_platform_recovery')
    + linux_amd64
    + api.step_data(
      'cipd describe chromium/rts/rts-chromium/linux-arm64',
      api.cipd.example_describe(
        'chromium/rts/rts-chromium/linux-arm64',
        test_data_tags=['git_revision:old_unshared_rev'],
      ),
    )
    + api.step_data(
      'cipd describe chromium/rts/rts-chromium/linux-amd64 (2)',
      api.cipd.example_describe(
        'chromium/rts/rts-chromium/linux-amd64',
        test_data_tags=[
          'git_revision:new_unshared_rev',
          'git_revision:c1ee0e03d15281730ebedf1f7151474f7a523001',
        ],
      ),
    )
    + api.post_process(post_process.StepSuccess, 'install RTS executables')
    + api.post_process(post_process.DropExpectation)
  )

  timeout_test = api.test('timeout', status='FAILURE') + linux_amd64
  for i in range(10):
    suffix = f' ({i + 1})' if i > 0 else ''
    timeout_test += api.step_data(
      f'cipd describe chromium/rts/rts-chromium/linux-arm64{suffix}',
      api.cipd.example_describe(
        'chromium/rts/rts-chromium/linux-arm64',
        test_data_tags=['git_revision:different_rev'],
      ),
    )
  yield timeout_test + api.post_process(post_process.DropExpectation)
