# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import recipe_test_api
import re
import os

METADATA_FILES = [
    "README.chromium",
    "README.angle",
    "README.pdfium",
    "README.crashpad",
    "README.skia",
    "README.swarming",
    "README.v8",
    "README.webrtc",
    "README.google",
    "README.libaom",
]


class MetadataValidatorTestApi(recipe_test_api.RecipeTestApi):

  def mock_change(self, files, revision='deadbeef', patchset=7):
    '''
      Mocks gerrit changes and file content fetching.
      files: dict {path: (status, content)}
             status: 'A', 'M', 'D'
             content: string content or None (if deleted)
      '''
    gerrit_files = {}
    steps = []

    for path, (status, content) in files.items():
      gerrit_files[path] = {'status': status}

      is_metadata = any(path.endswith(x) for x in METADATA_FILES)

      # Mock content fetch for non-deleted READMEs.
      if status == 'D' or not is_metadata:
        continue

      if ' ' in path or '.md' in path:
        continue

      content_str = content or ''
      steps.append(
          self.step_data(
              f'Fetch affected metadata files.Processing {path}.Fetch {path}',
              self.m.gitiles.make_encoded_file(content_str)))

      # Mock license fetch if referenced.
      match = re.search(r'License File:\s*(.+)', content_str, re.IGNORECASE)
      if match:
        dirname = os.path.dirname(path)
        lic_files = [p.strip() for p in match.group(1).split(',') if p.strip()]
        for lf in lic_files:
          if not os.path.isabs(lf):
            lic_path = os.path.join(dirname, lf)
          else:
            lic_path = lf
          if lic_path.startswith('/'):
            lic_path = lic_path[1:]

          if ' ' in lic_path or '.md' in lic_path:
            continue

          # Don't mock content for deleted licenses.
          if files.get(lic_path, (None, None))[0] != 'D':
            steps.append(
                self.step_data(
                    'Fetch affected metadata files.Processing {}.'
                    'Fetch {}'.format(path, lic_path),
                    self.m.gitiles.make_encoded_file('DUMMY LICENSE')))

    mock_data = {
        'current_revision': revision,
        'revisions': {
            revision: {
                '_number': patchset,
                'commit': {
                    'message': 'fake commit'
                },
                'files': gerrit_files
            }
        }
    }
    steps.append(
        self.step_data('gerrit changes', self.m.json.output([mock_data])))
    steps.append(
        self.step_data('gerrit changes (2)', self.m.json.output([mock_data])))
    return sum(steps, self.empty_test_data())
