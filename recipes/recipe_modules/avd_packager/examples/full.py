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

  def generate_properties(avd_configs):
    avd_packager_properties = {
        'avd_configs': avd_configs,
        'gclient_config': 'chromium',
        'gclient_apply_config': ['android'],
    }
    properties = {'$build/avd_packager': avd_packager_properties}
    return api.properties(**properties)

  yield api.test(
      'basic',
      generate_properties([
          'some/proto/foo.textpb',
      ]),
      api.post_process(post_process.MustRun,
                       'Process some/proto/foo.textpb.List AVD'),
      api.post_process(post_process.MustRun,
                       'Process some/proto/foo.textpb.Create AVD'),
      api.override_step_data(
          'Process some/proto/foo.textpb.Create AVD',
          api.json.output({
              'result': {
                  'instance_id': 'instance-id-foo',
                  'package': 'sample/avd/package/name',
              }
          }),
      ),
      api.post_process(links_include,
                       'Process some/proto/foo.textpb.Create AVD',
                       'instance-id-foo'),
      api.post_process(
          post_process.MustRun, 'Process some/proto/foo.textpb.'
          'cipd set-tag sample/avd/package/name'),
      api.post_process(post_process.MustRun,
                       'Process some/proto/foo.textpb.Uninstall AVD'),
      api.expect_status('SUCCESS'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'avd_list',
      generate_properties([
          'some/proto/foo.textpb',
          'some/proto/bar.textpb',
      ]),
      api.post_process(post_process.MustRun,
                       'Process some/proto/foo.textpb.List AVD'),
      api.override_step_data(
          'Process some/proto/foo.textpb.List AVD',
          retcode=1,
      ),
      api.post_process(post_process.MustRun, 'Process some/proto/bar.textpb'),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'avd_create',
      generate_properties([
          'some/proto/foo.textpb',
          'some/proto/bar.textpb',
      ]),
      api.post_process(post_process.MustRun,
                       'Process some/proto/foo.textpb.Create AVD'),
      api.override_step_data(
          'Process some/proto/foo.textpb.Create AVD',
          retcode=1,
      ),
      api.post_process(post_process.MustRun,
                       'Process some/proto/bar.textpb.Create AVD'),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'avd_uninstall',
      generate_properties([
          'some/proto/foo.textpb',
          'some/proto/bar.textpb',
      ]),
      api.post_process(post_process.MustRun,
                       'Process some/proto/foo.textpb.Uninstall AVD'),
      api.override_step_data(
          'Process some/proto/foo.textpb.Uninstall AVD',
          retcode=1,
      ),
      api.post_process(post_process.MustRun,
                       'Process some/proto/bar.textpb.Uninstall AVD'),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'avd_variants',
      generate_properties([
          'some/proto/foo.textpb',
      ]),
      api.post_process(post_process.MustRun,
                       'Process some/proto/foo.textpb.List AVD'),
      api.override_step_data(
          'Process some/proto/foo.textpb.List AVD',
          api.json.output([{
              'avd_variants': ['landscape', 'portrait'],
          }]),
      ),
      api.post_process(
          post_process.MustRun, 'Process some/proto/foo.textpb.'
          "Create AVD with variant 'landscape'"),
      api.post_process(
          post_process.MustRun, 'Process some/proto/foo.textpb.'
          "Create AVD with variant 'portrait'"),
      api.expect_status('SUCCESS'),
      api.post_process(post_process.DropExpectation),
  )
