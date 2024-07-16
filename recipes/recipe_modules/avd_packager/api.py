# Copyright 2022 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""API to create Android Virtual Device (AVD) and upload to CIPD."""

from recipe_engine import recipe_api


class AvdPackagerApi(recipe_api.RecipeApi):

  def __init__(self, properties, **kwargs):
    super().__init__(**kwargs)

    self._gclient_config = properties.gclient_config
    self._gclient_apply_config = properties.gclient_apply_config
    self._avd_configs = properties.avd_configs

    self._checkout_path = None

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

  def execute(self):
    """Run the avd packager steps.

    The script //tools/android/avd/avd.py will read each avd config, create an
    avd with snapshot, and update to CIPD.
    """
    chromium_src = self._checkout_path / 'src'
    avd_script_path = chromium_src / 'tools' / 'android' / 'avd' / 'avd.py'

    with self.m.context(cwd=chromium_src):
      with self.m.defer.context() as defer:
        for avd_config in self._avd_configs:
          with self.m.step.nest('Process %s' % avd_config):
            avd_config_path = chromium_src / avd_config

            # Call "create" to create and upload AVD.
            create_commands = [
                avd_script_path, 'create', '-v', '--avd-config',
                avd_config_path, '--snapshot', '--cipd-json-output',
                self.m.json.output()
            ]
            create_result = defer(self.m.step, 'avd create %s' % avd_config,
                                  ['vpython3', '-u'] + create_commands)
            if create_result.is_ok():
              create_result = create_result.result()
              if create_result.json.output:
                cipd_result = create_result.json.output.get('result', {})
                if 'package' in cipd_result and 'instance_id' in cipd_result:
                  self.m.cipd.add_instance_link(create_result)
                  # Add buildbucket id to the CIPD instance.
                  tags = {'buildbucket_id': str(self.m.buildbucket.build.id)}
                  defer(self.m.cipd.set_tag, cipd_result['package'],
                        cipd_result['instance_id'], tags)

            # Call "uninstall" to free disk space.
            uninstall_commands = [
                avd_script_path, 'uninstall', '-v', '--avd-config',
                avd_config_path
            ]
            defer(self.m.step, 'avd uninstall %s' % avd_config,
                  ['vpython3', '-u'] + uninstall_commands)
