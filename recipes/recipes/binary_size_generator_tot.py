# Copyright 2020 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import re

from recipe_engine import post_process

from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import (
  binary_size,
  builder_group,
  chromium,
  chromium_android,
  chromium_checkout,
  chromium_tests,
)
from RECIPE_MODULES.build.binary_size import constants
from RECIPE_MODULES.depot_tools import gclient, gsutil
from RECIPE_MODULES.infra import zip as zip_module
from RECIPE_MODULES.recipe_engine import (
  buildbucket,
  context,
  file,
  path,
  properties,
  raw_io,
  step,
  time,
)


@dataclass
class DEPS(RecipeScriptApi):
  binary_size: binary_size.API
  buildbucket: buildbucket.API
  builder_group: builder_group.API
  chromium: chromium.API
  chromium_android: chromium_android.API
  chromium_checkout: chromium_checkout.API
  chromium_tests: chromium_tests.API
  context: context.API
  file: file.API
  gclient: gclient.API
  gsutil: gsutil.API
  path: path.API
  properties: properties.API
  raw_io: raw_io.API
  step: step.API
  time: time.API
  zip: zip_module.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  builder_group: builder_group.TEST_API
  raw_io: raw_io.TEST_API


GS_DIRECTORY = 'android-binary-size/commit_size_analysis/'

# Number of the most recent analysis zips to keep in GS_DIRECTORY. Trybots read
# LATEST at the start of their build and download the zip that it points to
# after their with-patch build. Keeping the previously uploaded zip supports
# trybots that take less time than one run of this recipe.
_NUM_ZIPS_TO_KEEP = 2

# Matches zip files named '{timestamp}_{revision}.zip'.
_ZIP_URL_RE = re.compile(r'/(\d+)_[0-9a-f]+\.zip$')


def RunSteps(api: DEPS):
  """Zips up and uploads analysis files for android-binary-size trybot to use.

  This recipe will be run continuously on chromium ToT to keep the latest zip
  upload as recent as possible.
  """
  with api.chromium.chromium_layout():
    api.gclient.set_config('chromium')
    api.gclient.apply_config('android')
    api.gclient.apply_config('checkout_pgo_profiles')
    api.chromium.set_config('chromium')
    api.chromium.apply_config('mb')
    api.chromium_android.set_config('base_config')

    update_result = api.chromium_checkout.ensure_checkout()
    got_revision = update_result.properties['got_revision']

    source_dir = update_result.source_root.path
    build_dir = api.chromium.default_build_dir(source_dir)

    api.chromium.runhooks(source_dir, build_dir, name='runhooks')

    raw_result = api.chromium_tests.run_mb_and_compile(
      source_dir,
      build_dir,
      api.chromium.get_builder_id(),
      api.binary_size.compile_targets,
      None,
      name_suffix='',
    )

    if raw_result.status != common_pb.SUCCESS:
      return raw_result

    staging_dir = api.path.mkdtemp('binary-size-generator-tot')
    api.binary_size.android_size_analysis(source_dir, build_dir, staging_dir)
    api.binary_size.arm64_size_config_json = 'config/Chrome64_size_config.json'
    api.binary_size.android_size_analysis_arm64(
      source_dir, build_dir, staging_dir
    )

    zip_path = staging_dir / 'analysis_files.zip'
    api.zip.directory(
      'Zipping generated files',
      staging_dir,
      zip_path,
    )

    # Timestamp is needed so that clients of these zip files quickly know how
    # recent the file is
    timestamp = str(int(api.time.time()))
    file_name = '{}_{}.zip'.format(timestamp, got_revision)

    gs_dest = GS_DIRECTORY + file_name
    api.gsutil.upload(
      source=zip_path,
      bucket=api.binary_size.results_bucket,
      dest=gs_dest,
      name='Uploading zip file',
      unauthenticated_url=True,
    )

    local_latest_file = api.path.mkstemp()
    # Write the name of the most recent zip file for android-binary-size builds
    # for quick fetch, also to encode timestamp and revision.
    api.file.write_text('write local latest file', local_latest_file, gs_dest)

    latest_dest = GS_DIRECTORY + 'LATEST'
    api.gsutil.upload(
      source=local_latest_file,
      bucket=api.binary_size.results_bucket,
      dest=latest_dest,
      name='Uploading LATEST file',
      unauthenticated_url=True,
    )

    _DeleteObsoleteZips(api)


def _DeleteObsoleteZips(api: DEPS):
  """Deletes all but the _NUM_ZIPS_TO_KEEP most recent zips in GS_DIRECTORY."""
  list_result = api.gsutil.list(
    'gs://{}/{}*.zip'.format(api.binary_size.results_bucket, GS_DIRECTORY),
    name='Listing zip files',
    stdout=api.raw_io.output_text(),
    step_test_data=lambda: api.raw_io.test_api.stream_output_text(''),
  )
  timestamped_urls = []
  for line in list_result.stdout.splitlines():
    url = line.strip()
    m = _ZIP_URL_RE.search(url)
    if m:
      timestamped_urls.append((int(m.group(1)), url))
  timestamped_urls.sort(reverse=True)
  obsolete_urls = [url for _, url in timestamped_urls[_NUM_ZIPS_TO_KEEP:]]
  list_result.presentation.step_text = '{} obsolete of {} zip files'.format(
    len(obsolete_urls), len(timestamped_urls)
  )
  if not obsolete_urls:
    return

  # Read the URLs from stdin (-I) since there may be too many for the command
  # line. The retry wrapper is not used since retries would not see stdin.
  # Failures are not fatal since the next build will retry the deletion.
  rm_result = api.gsutil(
    ['rm', '-I'],
    name='Deleting obsolete zip files',
    use_retry_wrapper=False,
    multithreaded=True,
    stdin=api.raw_io.input_text('\n'.join(obsolete_urls) + '\n'),
    raise_on_failure=False,
  )
  if rm_result.retcode != 0:
    rm_result.presentation.status = api.step.WARNING


def GenTests(api: TEST_DEPS):
  yield api.test(
    'basic',
    api.builder_group.for_current('chromium.android'),
    api.post_check(
      lambda check, steps: check('gsutil Uploading zip file' in steps)
    ),
    api.post_check(
      lambda check, steps: check('gsutil Uploading LATEST file' in steps)
    ),
    api.post_check(
      lambda check, steps: check(
        'gsutil Deleting obsolete zip files' not in steps
      )
    ),
    api.post_process(post_process.DropExpectation),
  )

  def _zip_url(timestamp):
    return 'gs://{}/{}{}_551be50f2e3dae7dd1b31522fce7a91374c0efab.zip'.format(
      constants.RESULTS_GS_BUCKET, GS_DIRECTORY, timestamp
    )

  yield api.test(
    'delete_obsolete_zips',
    api.builder_group.for_current('chromium.android'),
    api.step_data(
      'gsutil Listing zip files',
      stdout=api.raw_io.output_text(
        '\n'.join(
          [
            # Lexicographic order differs from timestamp order.
            _zip_url(999999999),
            _zip_url(1000000001),
            _zip_url(1000000003),
            _zip_url(1000000002),
            'gs://{}/{}unexpected_name.zip'.format(
              constants.RESULTS_GS_BUCKET, GS_DIRECTORY
            ),
          ]
        )
      ),
    ),
    api.post_check(
      post_process.StepCommandContains,
      'gsutil Deleting obsolete zip files',
      ['-m', '--', 'rm', '-I'],
    ),
    api.post_check(
      lambda check, steps: check(
        steps['gsutil Deleting obsolete zip files'].stdin
        == '\n'.join([_zip_url(1000000001), _zip_url(999999999)]) + '\n'
      )
    ),
    api.post_check(
      post_process.StepSuccess, 'gsutil Deleting obsolete zip files'
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'delete_obsolete_zips_failed',
    api.builder_group.for_current('chromium.android'),
    api.step_data(
      'gsutil Listing zip files',
      stdout=api.raw_io.output_text(
        '\n'.join(_zip_url(t) for t in range(1000000001, 1000000004))
      ),
    ),
    api.step_data('gsutil Deleting obsolete zip files', retcode=1),
    api.post_check(
      post_process.StepWarning, 'gsutil Deleting obsolete zip files'
    ),
    api.expect_status('SUCCESS'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'failed_generate_analysis_files',
    api.builder_group.for_current('chromium.android'),
    api.override_step_data(
      'Generate commit size analysis files',
      retcode=1,
    ),
    api.post_process(
      post_process.StepFailure,
      'Generate commit size analysis files',
    ),
    api.post_check(
      lambda check, steps: check('gsutil Uploading zip file' not in steps)
    ),
    api.post_check(
      lambda check, steps: check('gsutil Uploading LATEST file' not in steps)
    ),
    api.expect_status('FAILURE'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'compile failed',
    api.builder_group.for_current('chromium.android'),
    api.override_step_data(
      'compile',
      retcode=1,
    ),
    api.post_check(
      lambda check, steps: check('gsutil Uploading zip file' not in steps)
    ),
    api.post_check(
      lambda check, steps: check('gsutil Uploading LATEST file' not in steps)
    ),
    api.expect_status('FAILURE'),
    api.post_process(post_process.DropExpectation),
  )
