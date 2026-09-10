# Copyright 2020 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import base64
import datetime
import logging
import re
from recipe_engine import recipe_api, util

from PB.recipe_modules.build.symupload import properties as symupload_properties


class SymuploadApi(recipe_api.RecipeApi):
  """Chromium specific module for symuploads."""

  def __init__(self, properties, **kwargs):
    super().__init__(**kwargs)
    self._properties = properties

  @property
  def platform(self):
    return self.m.chromium.c.HOST_PLATFORM

  def _get_msdia_paths(self):
    """Returns a list of paths to search for msdia140.dll.

    The caller should add these to the PATH so symupload can find the DLL.

    Newer Win 10 bots have msdia140.dll available in the system, and it might in
    fact be causing problems due to some incompatibility with the win_toolchain
    version of DIA SDK (see also crbug/1085581). So the caller should add these
    to the END of PATH, so that the system default location will be searched
    first.
    """
    if not self.platform.startswith('win'):
      # Only needed on Windows.
      return []

    # Find the win_toolchain version of DIA SDK, which should include
    # msdia140.dll.
    #
    # NOTE: symupload.exe should always be compiled as 64-bit, even for 32-bit
    # Chrome builds, since the 32-bit symupload can't handle the huge
    # chrome.dll, so always use the 64-bit DLL.
    try:
      toolchain_data = self.m.file.read_json(
        'find_win_toolchain',
        self.m.chromium_checkout.source_dir.joinpath(
          'build', 'win_toolchain.json'
        ),
      )
      dia_dir = self.m.path.join(
        toolchain_data['path'], 'DIA SDK', 'bin', 'amd64'
      )
      x64_runtime_dir = toolchain_data['runtime_dirs'][0]
      return [dia_dir, x64_runtime_dir]
    # TODO(gbeaty) We really shouldn't catch Exception as it can hide
    # programming errors that should be fixed
    except Exception:
      # Ignore any errors.
      return []

  def symupload(self, symupload_binary, artifact, server_url):
    """Uploads the given symbols file with the V1 protocol.

    Note that V1 is deprecated and will be removed once all consumers migrate
    to V2.

    Args:
      artifact: Name of the artifact to upload. Will be found relative to the
        out directory, so must have already been compiled.
      server_url: URL of the symbol server to upload to.
    """
    cmd = [symupload_binary]
    if self.platform.startswith('win'):
      cmd.extend(['--timeout', '0', artifact, server_url])
    else:
      cmd.extend([artifact, server_url])

    self.m.step('symupload %s' % artifact, cmd, infra_step=True)

  def symupload_v2(
    self,
    artifacts,
    artifact_type,
    encrypted_key_path,
    kms_key_path,
    server_url,
    symupload_binary,
    name=None,
  ):
    """Invokes symupload.py for V2 protocol.

    Args:
      artifacts: (list) of artifacts to upload
      encrypted_key_path: Path object to the encrypted key
      kms_key_path: (str) path to the symupload kms key. Please refer to the
        definition in this .proto for details.
      server_url: (str) url to perform symupload on
      symupload_binary: Path object to the symupload binary
      name: (str) name of the step
    """
    with self.m.step.nest('Prepare API key') as key_presentation:
      output_api_key = self.m.path.cleanup_dir / 'symupload-api-key.txt'
      self.m.cloudkms.decrypt(kms_key_path, encrypted_key_path, output_api_key)

      api_key = self.m.file.read_raw(
        'read decrypted api key', output_api_key, test_data='test_key'
      )
      key_presentation.logs['api_key sanity check'] = str(len(api_key))

    cmd = [
      'python3',
      self.resource('symupload_script.py'),
      '--artifacts',
      ','.join(artifacts),
      '--api-key-file',
      output_api_key,
      '--binary-path',
      symupload_binary,
      '--platform',
      self.platform,
      '--server-urls',
      # the recipe currently only supports one url at the
      # moment, so this should be reworked to pass it a
      # comma-delimited list of urls when supported.
      server_url,
    ]

    if artifact_type:
      cmd.extend(['--artifact_type', artifact_type])

    return self.m.step(name or 'symupload_v2', cmd, infra_step=True)

  def _replace_placeholders(self, custom_vars, input_str):
    for placeholder, key in re.findall('({%(.*?)%})', input_str):
      if key in custom_vars:
        input_str = input_str.replace(placeholder, custom_vars[key])
      else:
        self.m.step.empty(
          'Unresolved placeholder',
          status=self.m.step.INFRA_FAILURE,
          step_text=placeholder + ' can not be resolved',
        )

    return input_str

  @property
  def symupload_binary(self):
    binary_name = 'symupload'
    if self.platform.startswith('win'):
      binary_name = 'symupload.exe'

    return binary_name

  def __call__(
    self,
    build_dir,
    config_file_path=None,
    experimental=False,
    symupload_binary=None,
    custom_vars=None,
  ):
    """
    Args:
      build_dir: The absolute path to the build output directory, e.g.
                 [cache]/builder/src/out/Release.
      config_file_path: (str) Absolute path to a config file to utilize
                        instead of the recipe_module property. The format of
                        the file must be the same as the structure of
                        the symupload proto definition. See properties.proto
                        for moree details.
      experimental: (bool) flag for experimental, meaning it will skip
                    symuploads.
      custom_vars: Dict of custom string substitution.
                   E.g. custom_vars={'url':'https://foo.com'}, then
                   url='{%url%}' will be replaced to 'https://foo.com'.
    """
    if not (
      self._properties.symupload_datas
      or self._properties.source_side_spec_path
      or config_file_path
    ):
      return

    if self._properties.source_side_spec_path:
      config_file_path = self.m.chromium_checkout.checkout_dir.joinpath(
        *self._properties.source_side_spec_path
      )

    if config_file_path is not None:
      if not self.m.path.exists(config_file_path):
        self.m.step.empty(
          'Could not find specified symupload config',
          status=self.m.step.INFRA_FAILURE,
          step_text=f'{config_file_path} symupload spec does not exist.',
        )

      # read the file content and load it as a symupload_datas object
      # for the remainder of the workflow to run.
      config = self.m.file.read_json(
        'read config from file',
        config_file_path,
        test_data=[
          {
            'artifacts': ['some_artifact.txt'],
            'url': 'https://some.url.com',
            'base64_api_key': base64.b64encode(b'encrypted_api_key').decode(
              'ascii'
            ),
            'kms_key_path': 'some/key/path',
          }
        ],
      )
      self._properties = symupload_properties.InputProperties(
        symupload_datas=config
      )

    with self.m.step.nest('symupload') as presentation:
      # Check binary before moving on
      if symupload_binary is None:
        symupload_binary = self.m.path.join(build_dir, self.symupload_binary)
      if not self.m.path.exists(symupload_binary):
        raise self.m.step.InfraFailure(
          'The symupload binary cannot be found '
          'at %s. Please ensure targets symupload '
          'are being built such that the binaries '
          'are generated.' % str(symupload_binary)
        )

      with self.m.context(env_suffixes={'PATH': self._get_msdia_paths()}):
        for symupload_data in self._properties.symupload_datas:
          # Gather all artifacts that are needed to upload via file glob.
          uploads = []
          for filename in symupload_data.file_globs:
            for f in self.m.file.glob_paths(
              'expand file globs',
              build_dir,
              filename,
              test_data=('glob1.txt', 'glob2.txt'),
            ):
              assert build_dir.base == f.base
              assert build_dir in f.parents
              uploads.append(self.m.path.abspath(f))

          if symupload_data.artifact:
            uploads.append(self.m.path.join(build_dir, symupload_data.artifact))

          for artifact in symupload_data.artifacts:
            uploads.append(self.m.path.join(build_dir, artifact))

          if experimental:
            presentation.step_text = 'Experimental mode. Skipping symupload.'
            presentation.status = self.m.step.SUCCESS
            return uploads

          # Use the symupload resource to mask the API key for v2 protocol.
          # Because the symupload script accepts all urls to upload for a set of
          # artifacts, we'll invoke it here for each symupload data config.
          if symupload_data.base64_api_key:
            if not symupload_data.kms_key_path:
              raise self.m.step.InfraFailure(
                'api_key exists but kms_key_path is missing'
              )

            custom_vars = custom_vars or {}
            url = self._replace_placeholders(custom_vars, symupload_data.url)
            kms_key_path = self._replace_placeholders(
              custom_vars, symupload_data.kms_key_path
            )
            base64_api_key = custom_vars.get(
              'base64_api_key', symupload_data.base64_api_key
            )

            # Write out decoded api key
            input_api_key = self.m.path.cleanup_dir.joinpath(
              'symupload-api-key.encrypted'
            )
            api_key_data = base64.b64decode(base64_api_key)
            self.m.file.write_raw(
              'write encrypted api key', input_api_key, api_key_data
            )

            # (crbug.com/1295894) Add retry to symupload_v2 step to mitigate
            # flaky http request errors.
            @self.m.time.exponential_retry(
              retries=2, delay=datetime.timedelta(seconds=30)
            )
            def _retry_symupload():
              logging.disable('ERROR')
              self.symupload_v2(
                artifacts=uploads,
                artifact_type=symupload_data.artifact_type,
                encrypted_key_path=input_api_key,
                kms_key_path=kms_key_path,
                server_url=url,
                symupload_binary=symupload_binary,
              )
              presentation.status = self.m.step.SUCCESS

            _retry_symupload()
          else:
            # We'll continue to invoke the binary directly for V1.
            # TODO: Note that V1 is deprecated and will be removed once all
            # consumers migrate to V2.
            for artifact in uploads:
              self.symupload(
                symupload_binary,
                artifact,  # artifact
                symupload_data.url,
              )
      return uploads
