# Copyright 2020 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process

from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb

DEPS = [
    'binary_size',
    'builder_group',
    'chromium',
    'chromium_android',
    'chromium_checkout',
    'chromium_tests',
    'depot_tools/gclient',
    'depot_tools/gsutil',
    'infra/zip',
    'recipe_engine/buildbucket',
    'recipe_engine/context',
    'recipe_engine/file',
    'recipe_engine/path',
    'recipe_engine/properties',
    'recipe_engine/step',
    'recipe_engine/time',
]

GS_DIRECTORY = 'android-binary-size/commit_size_analysis/'


def RunSteps(api):
  """Zips up and uploads analysis files for android-binary-size trybot to use.

  This recipe will be run continuously on chromium ToT to keep the latest zip
  upload as recent as possible.
  """
  with api.chromium.chromium_layout():
    api.gclient.set_config('chromium')
    api.gclient.apply_config('android')
    api.chromium.set_config('chromium')
    api.chromium.apply_config('mb')
    api.chromium_android.set_config('base_config')

    result = api.chromium_checkout.ensure_checkout()
    got_revision = result.presentation.properties['got_revision']

    api.chromium.runhooks(name='runhooks')

    api.chromium.ensure_goma()

    raw_result = api.chromium_tests.run_mb_and_compile(
        api.chromium.get_builder_id(),
        api.binary_size.compile_targets,
        None,
        name_suffix='',
    )

    if raw_result.status != common_pb.SUCCESS:
      return raw_result

    staging_dir = api.path.mkdtemp('binary-size-generator-tot')
    api.step(
        name='Generate commit size analysis files',
        cmd=api.binary_size.get_android_size_analysis_command(staging_dir))

    zip_path = staging_dir.join('analysis_files.zip')
    api.zip.directory(
        'Zipping generated files',
        staging_dir,
        zip_path,
    )

    # Timestamp is needed so that clients of these zip files quickly know how
    # recent the file is
    timestamp = str(int(api.m.time.time()))
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


def GenTests(api):
  yield api.test(
      'basic',
      api.builder_group.for_current('chromium.android'),
      api.post_check(
          lambda check, steps: check('gsutil Uploading zip file' in steps)),
      api.post_check(
          lambda check, steps: check('gsutil Uploading LATEST file' in steps)),
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
          lambda check, steps: check('gsutil Uploading zip file' not in steps)),
      api.post_check(lambda check, steps: check('gsutil Uploading LATEST file'
                                                not in steps)),
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
          lambda check, steps: check('gsutil Uploading zip file' not in steps)),
      api.post_check(lambda check, steps: check('gsutil Uploading LATEST file'
                                                not in steps)),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )
