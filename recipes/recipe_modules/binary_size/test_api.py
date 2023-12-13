# Copyright 2020 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import recipe_test_api
from PB.recipe_modules.build.binary_size import properties as properties_pb

from . import constants


class BinarySizeTestApi(recipe_test_api.RecipeTestApi):

  def properties(self, **kwargs):
    for key in kwargs:
      assert hasattr(properties_pb.InputProperties, key)
    return self.m.properties(**{'$build/binary_size': kwargs})

  def build(self,
            commit_message='message',
            android_size_footer=False,
            fuchsia_size_footer=False,
            extra_footers=None,
            recent_upload_cp=constants.TEST_RECENT_UPLOAD_CP,
            patch_parent_cp=constants.TEST_PATCH_PARENT_CP,
            override_commit_log=False,
            **kwargs):
    kwargs['revision'] = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
    revision_info = {
        '_number': 1,
        'commit': {
            'author': {
                'email': 'foo@bar.com',
            },
            'message': commit_message,
            'parents': [{
                'commit': 'deadveggie12345'
            }],
            'subject': commit_message.splitlines()[0],
        },
    }
    footer_json = extra_footers or {}
    if android_size_footer:
      footer_json['Binary-Size'] = ['Totally worth it.']
    if fuchsia_size_footer:
      footer_json['Fuchsia-Binary-Size'] = ['Totally worth it for Fuchsia.']
    commit_log_overrides = []
    if override_commit_log:
      recent_upload_commit_log = {
          'message':
              'Upload message\n\nCr-Commit-Position: refs/heads/main@{{#{}}}\n'
              .format(recent_upload_cp)
      }
      recent_upload_footer = {
          'Cr-Commit-Position': [
              'refs/heads/main@{{#{}}}'.format(recent_upload_cp)
          ]
      }
      commit_log_overrides = [
          self.override_step_data('Commit log for uploaded revision',
                                  self.m.json.output(recent_upload_commit_log)),
          self.override_step_data('parse description (2)',
                                  self.m.json.output(recent_upload_footer)),
      ]

      if patch_parent_cp:
        patch_parent_commit_log = {
            'message': 'Parent message\n\nCr-Commit-Position: '
                       'refs/heads/main@{{#{}}}\n'.format(patch_parent_cp)
        }
        patch_parent_footer = {
            'Cr-Commit-Position': [
                'refs/heads/main@{{#{}}}'.format(patch_parent_cp)
            ]
        }
      else:
        patch_parent_commit_log = {'message': 'Parent message\n\n'}
        patch_parent_footer = {}

      commit_log_overrides.extend([
          self.override_step_data('Commit log for patch\'s parent revision',
                                  self.m.json.output(patch_parent_commit_log)),
          self.override_step_data('parse description (3)',
                                  self.m.json.output(patch_parent_footer)),
      ])
    return sum([
        self.m.chromium.try_build(
            builder_group='tryserver.chromium.android',
            builder=constants.TEST_BUILDER,
            build_number=constants.TEST_BUILDNUMBER,
            patch_set=1,
            **kwargs),
        self.m.platform('linux', 64),
        self.override_step_data(
            'gerrit changes',
            self.m.json.output([{
                'revisions': {
                    kwargs['revision']: revision_info
                }
            }])),
        self.override_step_data('parse description',
                                self.m.json.output(footer_json)),
        self.m.time.seed(constants.TEST_TIME),
    ] + commit_log_overrides, self.empty_test_data())

  def on_significant_binary_package_restructure(self):
    # Simulates manual clearing of the LATEST file.
    return self.override_step_data('gsutil cat LATEST',
                                   self.m.raw_io.stream_output(''))
