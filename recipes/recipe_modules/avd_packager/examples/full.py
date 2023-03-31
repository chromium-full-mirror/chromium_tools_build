# Copyright 2022 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process

DEPS = [
    'avd_packager',
    'recipe_engine/json',
    'recipe_engine/properties',
]


def RunSteps(api):
  api.avd_packager.prepare()
  api.avd_packager.execute()


def GenTests(api):

  def links_include(check, step_odict, step, link_name):
    check('step result for %s contained link named %s' % (step, link_name),
          link_name in step_odict[step].links)

  def generate_properties():
    avd_packager_properties = {
        'avd_configs': [
            'tools/android/avd/proto/generic_android23.textpb',
            'tools/android/avd/proto/generic_android28.textpb',
        ],
        'gclient_config': 'chromium',
        'gclient_apply_config': ['android'],
    }
    properties = {'$build/avd_packager': avd_packager_properties}
    return api.properties(**properties)

  yield api.test(
      'basic',
      generate_properties(),
      api.post_process(
          post_process.MustRun,
          'avd create tools/android/avd/proto/generic_android23.textpb'),
      api.override_step_data(
          'avd create tools/android/avd/proto/generic_android23.textpb',
          retcode=1,
      ),
      api.post_process(
          post_process.MustRun,
          'avd create tools/android/avd/proto/generic_android28.textpb'),
      api.override_step_data(
          'avd create tools/android/avd/proto/generic_android28.textpb',
          api.json.output({
              'result': {
                  'instance_id': 'instance-id-generic-android-28',
                  'package': 'sample/avd/package/name',
              }
          })),
      api.post_process(
          links_include,
          'avd create tools/android/avd/proto/generic_android28.textpb',
          'instance-id-generic-android-28'),
      api.post_process(post_process.MustRun,
                       'cipd set-tag sample/avd/package/name'),
      api.post_process(
          post_process.MustRun,
          'avd uninstall tools/android/avd/proto/generic_android28.textpb'),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )
