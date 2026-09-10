# Copyright 2022 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from collections.abc import Mapping, Sequence
import re

from recipe_engine import recipe_test_api

from RECIPE_MODULES.build.attr_utils import attrib, attrs


class ChromiumTestsBuilderConfigVerifierApi(recipe_test_api.RecipeTestApi):
  @attrs()
  class Contents:
    """An object identifying the contents of a file.

    The patched and at_head attributes respectively identify the file's
    contents with the patched applied and at the head of the repo. A
    None value indicates that the file does not exist in that revision.
    """

    patched = attrib(str, default=None)
    at_head = attrib(str, default=None)

  def test_case(
    self,
    *,
    properties_files_directory: str | None = None,
    properties_files: Mapping[str, Contents] | None = None,
    affected_files: Sequence[str] | None = None,
  ) -> recipe_test_api.StepTestData:
    """Set necessary step test data for calling verify_builder_configs.

    The test case will need to separately set the buildbucket build,
    which must be a try build.

    Args:
      * properties_files_directory - The repo-root-relative path to the
        directory containing the properties files. The relative path to
        the properties files from the properties files directory must be
        <bucket>/<builder>/properties.json.
      * properties_files - The properties files that should be found
        when globbing for properties files. The value for each file
        determines the existence and contents of the file when patched
        and at head. The file must exist either patched or at head.
      * affected_files - The repo-root-relative path to files that are
        affected by the CL. Any properties files that do not contain the
        same contents when patched and at head will automatically be
        added as an affected file.
    """
    assert properties_files is None or properties_files_directory is not None

    affected_files = affected_files or []
    affected_files = set(affected_files)
    files_at_head = set()

    t = self.empty_test_data()

    if properties_files:
      properties_file_re = re.compile(
        f'{properties_files_directory}/[^/]+/[^/]+/properties.json'
      )
      for f, contents in properties_files.items():
        assert properties_file_re.fullmatch(f), (
          f'{f} does not match properties file glob'
        )
        assert contents.patched is not None or contents.at_head is not None, (
          f'contents for {f} cannot be None both patched and at head'
        )
        if contents.patched == contents.at_head:
          continue
        affected_files.add(f)
        if contents.patched is not None:
          t += self.override_step_data(
            f'verify {f}.read file at CL',
            self.m.file.read_text(contents.patched),
          )
        if contents.at_head is not None:
          relative_f = f[len(properties_files_directory) + 1 :]
          files_at_head.add(relative_f)
          t += self.override_step_data(
            f'verify {f}.read file at HEAD',
            self.m.raw_io.stream_output_text(contents.at_head),
          )

      t += self.override_step_data(
        'determine affected properties files.find builder properties files',
        self.m.file.glob_paths(sorted(properties_files.keys())),
      )

    t += self.m.tryserver.get_files_affected_by_patch(
      sorted(affected_files),
      step_name=(
        'determine affected properties files.git diff to analyze patch'
      ),
    )

    t += self.override_step_data(
      'determine affected properties files.git ls-tree',
      self.m.raw_io.stream_output_text('\n'.join(sorted(files_at_head) + [''])),
    )

    return t
