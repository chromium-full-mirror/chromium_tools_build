# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Rolls Test262 commits into V8.

This recipe runs the import script; it is expected to be run as a recurring job
at a short interval. It creates CLs for V8 with an updated revision for the
Test262 dependency, staging tests updates if any, features updates if any and
updates to the test262 status file for removed tests and tests with unexpected
behaviour after the DEPS update.
"""

import re

DEPS = [
    'chromium',
    'depot_tools/depot_tools',
    'depot_tools/gclient',
    'infra/cloudkms',
    'recipe_engine/context',
    'recipe_engine/path',
    'recipe_engine/raw_io',
    'recipe_engine/step',
    'v8',
]


CREDS_NAME = 'test262-import-export'
KMS_CRYPTO_KEY = (
    'projects/v8-infra/locations/global/keyRings/%s/cryptoKeys/default' %
    CREDS_NAME)
V8_TEST262_ROLLS_META_BUG = 'v8:7834'


def RunSteps(api):
  configure(api)

  api.v8.checkout()

  creds = api.path['cleanup'].join(CREDS_NAME + '.json')
  api.cloudkms.decrypt(
      KMS_CRYPTO_KEY,
      api.repo_resource('recipes', 'recipes', 'assets', CREDS_NAME),
      creds,
  )

  checkout_root = api.path['cache'].join('builder')
  chromium_path = checkout_root.join('src')
  blink_tools_path = chromium_path.join('third_party', 'blink', 'tools')

  v8_path = checkout_root.join('v8')

  script = v8_path.join('test', 'test262', 'tools', 'import.py')

  with api.context(cwd=v8_path), api.depot_tools.on_path():
    api.v8.git_output('branch', '-D', 'test262_import', ok_ret='any')
    api.v8.git_output('new-branch', 'test262_import')
    last_test262_revision = api.gclient(
      'get test262 revsion',
      ['getdep', '-r', 'test/test262/data'],
      stdout=api.raw_io.output_text(),
    ).stdout.strip()

    run_import_script(api, creds, blink_tools_path, script,
        step_name='Import Test262 changes into V8.',
        extra_args=['--phase=PREBUILD'])

    # TODO(liviurau): #1 Maybe compile, run tests and collect failures in the
    # recipe. Alternatively, implement a resolution strategy in roll_watcher.

    output_lines = run_import_script(api, creds, blink_tools_path, script,
        step_name='Update Test262 status file.',
        extra_args=[
          '--phase=POSTBUILD',
          '--v8-test262-last-revision',
          last_test262_revision,
          # TODO(liviurau): #2 Pass the failures collected at #1 to the script.
          # '--test262-failure-file', failure_file,
        ]
    )
    assert output_lines, 'Step should have outputted at least one line.'
    import_range = output_lines[-1]

    upload_import_cl(api, import_range)


def configure(api):
  api.gclient.set_config('chromium')
  api.gclient.apply_config('v8_bare')
  # TODO: Remove this in finalized version. This is just for testing.
  #api.gclient.c.revisions['v8'] = "49cd7d838c98245268b12d2c75538faa3e402ac0"


def run_import_script(api, creds, blink_tools_path, script, step_name, extra_args):
  args = [
      '--credentials-json',
      creds,
      '--blink-tools-path',
      blink_tools_path,
  ] + extra_args
  return api.v8.vpython(step_name, script, args,
      stdout=api.raw_io.output_text()).stdout.splitlines()


def upload_import_cl(api, import_range):
  with api.step.nest('Upload import CL') as parent_step:
    api.v8.git_output('commit', '-am', '[test262] Roll test262',
                      '-m', import_range,
                      '-m', 'no-export: true',)

    api.v8.git_output('cl', 'upload', '-f', '--bypass-hooks', '--send-mail',
                      '-b', V8_TEST262_ROLLS_META_BUG, '-d')
    issue = api.v8.git_output('cl', 'issue', name='cl_issue')
    issue_url = re.search('\((.*)\)', issue).group(1)
    parent_step.presentation.links[issue_url] = issue_url


def GenTests(api):
  yield (
        api.test('test262-import') +
        api.override_step_data(
           'Update Test262 status file.',
           api.raw_io.stream_output_text('range 1..3'),
        ) +
        api.override_step_data(
           'gclient get test262 revsion',
           api.raw_io.stream_output_text('345'),
        ) +
        api.override_step_data(
                'Upload import CL.cl_issue',
                api.raw_io.stream_output_text(
                        '(https://review.source.com/1)',
                        stream='stdout'
                )
        )
  )
