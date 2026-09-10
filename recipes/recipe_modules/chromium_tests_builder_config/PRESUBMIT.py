# Copyright 2022 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations


PRESUBMIT_VERSION = '2.0.0'
USE_PYTHON3 = True


def _path(input_api, path):
  return input_api.os_path.normpath(f'{input_api.PresubmitLocalPath()}/{path}')


# TODO(gbeaty) Once all builder configs are migrated src-side, this can be
# removed
def CheckGroupings(input_api, output_api):
  projects_to_migrate = ['chromium', 'chrome']

  # First check that the groupings files are up-to-date
  script = _path(input_api, 'migration/scripts/generate_groupings.py')
  cmd = [script, '--validate'] + projects_to_migrate

  output = input_api.RunTests(
    [
      input_api.Command(
        'validate migration groupings files',
        cmd,
        kwargs={'stderr': input_api.subprocess.STDOUT},
        message=output_api.PresubmitError,
        python3=True,
      ),
    ]
  )

  if output:
    return output

  # Changes to the migration files can't actually add new builders, but they
  # might update the groupings files to add new entries. We don't want to
  # prevent that, so only perform the check to prevent new entries if trybots.py
  # or one of the files in builders is modified.
  trybots_py = _path(input_api, 'trybots.py')
  builders_dir = _path(input_api, f'builders{input_api.os_path.sep}')

  def can_add_builders(af):
    path = af.AbsoluteLocalPath()
    return path == trybots_py or path.startswith(builders_dir)

  if not input_api.AffectedFiles(file_filter=can_add_builders):
    return []

  # The groupings files are up-to-date, make sure new builders aren't being
  # unnecessarily added
  def groupings_file_filter(af):
    if af.Action() in 'AD':
      return False
    path = input_api.os_path.relpath(
      af.AbsoluteLocalPath(), input_api.PresubmitLocalPath()
    )
    return (
      input_api.os_path.dirname(path) == 'migration'
      and input_api.os_path.splitext(path)[1] == '.json'
    )

  output = []
  for af in input_api.AffectedFiles(file_filter=groupings_file_filter):
    old_groupings = input_api.json.loads(''.join(af.OldContents()))
    new_groupings = input_api.json.loads(''.join(af.NewContents()))
    bad_builders = []
    for builder_id, grouping in new_groupings.items():
      # Don't prevent adding builders if they aren't supported src-side
      if 'blockers' in grouping:
        continue
      # The builder is not new
      if builder_id in old_groupings:
        continue
      # The builder is connected to a builder that is not new
      if any(b in old_groupings for b in grouping['builders']):
        continue
      bad_builders.append(builder_id)
    if bad_builders:
      message = [
        (
          f'The following added builders in {af.LocalPath()}'
          ' should define their configs src-side:'
        )
      ]
      message.extend(f'* {b}' for b in sorted(bad_builders))
      output.append(output_api.PresubmitError('\n'.join(message)))

  return output
