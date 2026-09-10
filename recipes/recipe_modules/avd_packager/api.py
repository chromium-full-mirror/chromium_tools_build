# Copyright 2022 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""API to create Android Virtual Device (AVD) and upload to CIPD."""

from __future__ import annotations

from recipe_engine import recipe_api


class AvdPackagerApi(recipe_api.RecipeApi):
  def __init__(self, properties, **kwargs):
    super().__init__(**kwargs)

    self._gclient_config = properties.gclient_config
    self._gclient_apply_config = properties.gclient_apply_config
    self._avd_configs = properties.avd_configs

    self._checkout_path = None
    self._chromium_src = None
    self._avd_script_path = None

  def prepare(self):
    """Sets up an avd packager run.

    This includes:
     * Setting up the given configs.
     * setting up the checkout w/ bot_update
    """
    self.m.gclient.set_config(self._gclient_config)
    for c in self._gclient_apply_config:
      self.m.gclient.apply_config(c)
    self.m.chromium_checkout.ensure_checkout()
    self._checkout_path = self.m.chromium_checkout.checkout_dir
    self._chromium_src = self._checkout_path.joinpath('src')
    self._avd_script_path = self._chromium_src.joinpath(
      'tools', 'android', 'avd', 'avd.py'
    )

  def execute(self):
    """Run the avd packager steps.

    The script //tools/android/avd/avd.py will read each avd config, create an
    avd with snapshot, and update to CIPD.
    """
    with self.m.context(cwd=self._chromium_src):
      deferred = []
      for avd_config in self._avd_configs:
        with self.m.step.nest('Process %s' % avd_config):
          metadata = self._list_avd(avd_config, deferred)
          if metadata is None:
            continue
          variants = metadata.get('avd_variants') or [None]
          for variant in variants:
            self._create_avd(avd_config, deferred, avd_variant=variant)
            self._uninstall_avd(avd_config, deferred)

      self.m.defer.collect(deferred)

  def _list_avd(self, avd_config, deferred):
    step_name = 'List AVD'
    step_cmds = [
      self._avd_script_path,
      'list',
      '-v',
      '--avd-config',
      avd_config,
      '--json-output',
      self.m.json.output(),
    ]
    deferred_result = self.m.defer(
      self.m.step,
      step_name,
      ['vpython3', '-u'] + step_cmds,
      step_test_data=lambda: self.m.json.test_api.output(
        [
          {
            'avd_proto_path': 'some/proto/foo.textpb',
            'is_available': False,
          }
        ]
      ),
    )
    deferred.append(deferred_result)

    if deferred_result.is_ok():
      list_result = deferred_result.result()
      return list_result.json.output[0]
    return None

  def _create_avd(self, avd_config, deferred, avd_variant=None):
    step_name = 'Create AVD'
    step_cmds = [
      self._avd_script_path,
      'create',
      '-v',
      '--avd-config',
      avd_config,
      '--force',
      '--snapshot',
      '--cipd-json-output',
      self.m.json.output(),
    ]
    if avd_variant is not None:
      step_name += ' with variant %r' % avd_variant
      step_cmds += ['--avd-variant', avd_variant]

    deferred_result = self.m.defer(
      self.m.step, step_name, ['vpython3', '-u'] + step_cmds
    )
    deferred.append(deferred_result)
    if deferred_result.is_ok():
      create_result = deferred_result.result()
      if create_result.json.output:
        cipd_result = create_result.json.output.get('result', {})
        if 'package' in cipd_result and 'instance_id' in cipd_result:
          self.m.cipd.add_instance_link(create_result)
          # Add buildbucket id to the CIPD instance.
          tags = {'buildbucket_id': str(self.m.buildbucket.build.id)}
          self.m.defer(
            self.m.cipd.set_tag,
            cipd_result['package'],
            cipd_result['instance_id'],
            tags,
          )

  def _uninstall_avd(self, avd_config, deferred):
    step_name = 'Uninstall AVD'
    step_cmds = [
      self._avd_script_path,
      'uninstall',
      '-v',
      '--avd-config',
      avd_config,
    ]
    deferred_result = self.m.defer(
      self.m.step, step_name, ['vpython3', '-u'] + step_cmds
    )
    deferred.append(deferred_result)
