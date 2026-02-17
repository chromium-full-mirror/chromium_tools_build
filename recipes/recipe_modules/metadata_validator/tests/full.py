# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine.post_process import StatusFailure, StatusSuccess, MustRun, DoesNotRun, DropExpectation

DEPS = [
    'metadata_validator',
    'depot_tools/gerrit',
    'depot_tools/gitiles',
    'depot_tools/tryserver',
    'recipe_engine/buildbucket',
    'recipe_engine/json',
    'recipe_engine/properties',
    'recipe_engine/raw_io',
]


def RunSteps(api):
  return api.metadata_validator.validate()


def GenTests(api):
  SKIP_FOOTER = 'Metadata-Validate-Bypass'

  def mock_validator(issues_by_path=None):
    '''Mocks the validator step response.'''
    if issues_by_path is None:
      issues_by_path = {}

    return api.step_data(
        'Validate.Run validator',
        api.json.output({
            'files': issues_by_path,
            'summary': {
                'invalid_files': len(issues_by_path)
            }
        }))

  yield api.test(
      'basic_success',
      api.buildbucket.try_build(
          project='chromium',
          builder='linux-metadata-validator',
      ),
      api.tryserver.get_footers(footers={}, step_name='parse description'),
      api.metadata_validator.mock_change({
          'third_party/foo/README.chromium':
              ('M', 'Name: Foo\nLicense File: LICENSE\n')
      }),
      mock_validator(),
      api.post_process(StatusSuccess),
      api.post_process(DoesNotRun, 'Validation bypassed'),
      api.post_process(MustRun, 'Validate.Run validator'),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'readme_deleted',
      api.buildbucket.try_build(
          project='chromium',
          builder='linux-metadata-validator',
      ),
      api.metadata_validator.mock_change({
          'third_party/exists/README.chromium': ('M', 'Name: Exists\n'),
          'third_party/deleted/README.chromium': ('D', None)
      }),
      mock_validator(),
      api.post_process(StatusSuccess),
      api.post_process(DoesNotRun, 'Validation bypassed'),
      api.post_process(MustRun, 'Validate.Run validator'),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'warning_with_unrelated_failure',
      api.buildbucket.try_build(
          project='chromium',
          builder='linux-metadata-validator',
      ),
      api.metadata_validator.mock_change(
          {'third_party/foo/README.chromium': ('M', 'Name: Foo\n')}),
      mock_validator(
          issues_by_path={
              'third_party/foo/README.chromium': [{
                  'severity': 'WARNING',
                  'message': 'License file not found.',
                  'fatal': False
              }],
              'third_party/foo/README.chromium.old':
                  [{  # This should be ignored as it doesn't match basename
                      'severity': 'ERROR',
                      'message': "Required field 'Name' is missing.",
                      'fatal': True
                  }]
          }),
      api.post_process(StatusSuccess),
      api.post_process(DoesNotRun, 'Validation bypassed'),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'validation_failure',
      api.buildbucket.try_build(
          project='chromium',
          builder='linux-metadata-validator',
      ),
      api.metadata_validator.mock_change(
          {'third_party/bar/README.chromium': ('M', 'Invalid Content')}),
      mock_validator(
          issues_by_path={
              'third_party/bar/README.chromium': [{
                  'severity': 'ERROR',
                  'message': "Required field 'Name' is missing.",
                  'fatal': True
              }]
          }),
      api.expect_status('FAILURE'),
      api.post_process(StatusFailure),
      api.post_process(DoesNotRun, 'Validation bypassed'),
      api.post_process(MustRun, 'Validate.Run validator'),
      api.post_process(MustRun, 'Validation Issues'),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'bypass_validation',
      api.buildbucket.try_build(
          project='chromium',
          builder='linux-metadata-validator',
      ),
      api.tryserver.get_footers(
          footers={SKIP_FOOTER: ['test']}, step_name='parse description'),
      api.post_process(StatusSuccess),
      api.post_process(MustRun, 'Validation bypassed'),
      api.post_process(DoesNotRun, 'Validate.Run validator'),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'no_readme',
      api.buildbucket.try_build(
          project='chromium',
          builder='linux-metadata-validator',
      ),
      api.metadata_validator.mock_change({'foo/bar.txt':
          ('M', 'some content')}),
      api.post_process(StatusSuccess),
      api.post_process(DoesNotRun, 'Validation bypassed'),
      api.post_process(MustRun,
                       'Fetch affected metadata files.no readme changes'),
      api.post_process(DoesNotRun, 'Validate.Run validator'),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'warnings_only',
      api.buildbucket.try_build(
          project='chromium',
          builder='linux-metadata-validator',
      ),
      api.metadata_validator.mock_change(
          {'third_party/baz/README.chromium': ('M', 'Name: Baz\n')}),
      mock_validator(
          issues_by_path={
              'third_party/baz/README.chromium': [{
                  'severity': 'WARNING',
                  'message': 'License file not found.',
                  'fatal': False
              }]
          }),
      api.post_process(StatusSuccess),
      api.post_process(DoesNotRun, 'Validation bypassed'),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'ignore_unrelated_files',
      api.buildbucket.try_build(
          project='chromium',
          builder='linux-metadata-validator',
      ),
      api.metadata_validator.mock_change(
          {'third_party/foo/README.chromium': ('M', 'Name: Foo\n')}),
      mock_validator(
          issues_by_path={
              'third_party/foo/README.chromium.old': [{
                  'severity': 'ERROR',
                  'message': 'Old file error',
                  'fatal': True
              }]
          }),
      api.post_process(StatusSuccess),
      api.post_process(DoesNotRun, 'Validation bypassed'),
      api.post_process(DropExpectation),
  )
  yield api.test(
      'errors_and_warnings',
      api.buildbucket.try_build(
          project='chromium',
          builder='linux-metadata-validator',
      ),
      api.metadata_validator.mock_change(
          {'third_party/mixed/README.chromium': ('M', 'Mixed\n')}),
      mock_validator(
          issues_by_path={
              'third_party/mixed/README.chromium': [{
                  'severity': 'ERROR',
                  'message': 'Critical issue.',
                  'fatal': True
              }, {
                  'severity': 'WARNING',
                  'message': 'Minor issue.',
                  'fatal': False
              }]
          }),
      api.expect_status('FAILURE'),
      api.post_process(StatusFailure),
      api.post_process(DoesNotRun, 'Validation bypassed'),
      api.post_process(MustRun, 'Validation Issues'),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'multiple_readmes_with_issues',
      api.buildbucket.try_build(
          project='chromium',
          builder='linux-metadata-validator',
      ),
      api.metadata_validator.mock_change({
          'third_party/bar/README.angle': ('M', 'Bar\n'),
          'third_party/foo/README.pdfium': ('M', 'Foo\n')
      }),
      # Mock validators for both files.
      # Note: The order depends on sorted() in the recipe.
      # bar comes before foo.
      mock_validator(
          issues_by_path={
              'third_party/bar/README.angle': [{
                  'severity': 'ERROR',
                  'message': "Required field 'Name' is missing.",
                  'fatal': True
              }],
              'third_party/foo/README.pdfium': [{
                  'severity': 'ERROR',
                  'message': "Required field 'Description' is missing.",
                  'fatal': True
              }]
          }),
      api.expect_status('FAILURE'),
      api.post_process(StatusFailure),
      api.post_process(DoesNotRun, 'Validation bypassed'),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'license_deleted_but_referenced',
      api.buildbucket.try_build(
          project='chromium',
          builder='linux-metadata-validator',
      ),
      api.metadata_validator.mock_change({
          'third_party/foo/README.chromium':
              ('M', 'Name: Foo\nLicense File: LICENSE\n'),
          'third_party/foo/LICENSE': ('D', None)
      }),
      mock_validator(
          issues_by_path={
              'third_party/foo/README.chromium': [{
                  'severity': 'WARNING',
                  'message': 'License file not found.',
                  'fatal': False
              }]
          }),
      api.post_process(StatusSuccess),
      api.post_process(DoesNotRun, 'Validation bypassed'),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'internal_project_reciprocal_license_check',
      api.buildbucket.try_build(project='chromium-internal'),
      api.metadata_validator.mock_change({
          'third_party/foo/README.chromium':
              ('M', 'Name: Foo\nLicense File: LICENSE\n')
      }),
      mock_validator(),
      api.post_process(
          MustRun, 'Validate.Enforcing reciprocal license check for host'
          ' chrome-internal-review.googlesource.com'),
      api.post_process(StatusSuccess),
      api.post_process(DoesNotRun, 'Validation bypassed'),
      api.post_process(DropExpectation),
  )

  def check_truncated_summary(check, steps):
    check('... (truncated)' in steps['Validation Issues'].step_text)

  yield api.test(
      'truncated_summary',
      api.buildbucket.try_build(project='chromium'),
      api.metadata_validator.mock_change(
          {'third_party/foo/README.chromium': ('M', 'Name: Foo\n')}),
      mock_validator(
          issues_by_path={
              'third_party/foo/README.chromium': [{
                  'severity': 'ERROR',
                  'message': 'E' * 4005,
                  'fatal': True
              }]
          }),
      api.expect_status('FAILURE'),
      api.post_process(StatusFailure),
      api.post_process(DoesNotRun, 'Validation bypassed'),
      api.post_process(check_truncated_summary),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'basic_skip_checks',
      api.buildbucket.try_build(project='chromium'),
      api.metadata_validator.mock_change({
          'third_party/foo bar/README.chromium': ('M', 'Name: Foo Bar\n'),
          'third_party/foo/README.chromium':
              ('M', 'Name: Foo\nLicense File: LICENSE.md\n')
      }),
      # Mock .gitmodules to prevent crash when
      # fallback logic runs for LICENSE.md.
      api.step_data(
          'Fetch affected metadata files'
          '.Processing third_party/foo/README.chromium'
          '.Fetch .gitmodules',
          api.gitiles.make_encoded_file('')  # Empty file
      ),
      api.post_process(
          MustRun, 'Fetch affected metadata files'
          '.Processing third_party/foo bar/README.chromium'
          '.Not fetching third_party/foo bar/README.chromium because it'
          ' contains spaces, see b/483862822'),
      api.post_process(
          MustRun, 'Fetch affected metadata files'
          '.Processing third_party/foo/README.chromium'
          '.Not fetching third_party/foo/LICENSE.md because it contains .md,'
          ' see b/484171134'),
      api.post_process(
          DoesNotRun, 'Fetch affected metadata files'
          '.Processing third_party/foo bar/README.chromium'
          '.Fetch third_party/foo bar/README.chromium'),
      api.post_process(
          DoesNotRun, 'Fetch affected metadata files'
          '.Processing third_party/foo/README.chromium'
          '.Fetch third_party/foo/LICENSE.md'),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'submodule_license',
      api.buildbucket.try_build(project='chromium'),
      api.metadata_validator.mock_change({
          'third_party/sub/README.chromium':
              ('M', 'Name: Sub\nLicense File: LICENSE\n')
      }),
      # Override the license fetch to simulate 404 (not found in main repo)
      # to trigger submodule logic. We use api.json.output({'value': None})
      # to simulate what gitiles module expects for a missing file
      # (or similar 404 case where value is null)
      api.step_data(
          'Fetch affected metadata files'
          '.Processing third_party/sub/README.chromium'
          '.Fetch third_party/sub/LICENSE', api.json.output({'value': None})),

      # Mock .gitmodules fetch
      api.step_data(
          'Fetch affected metadata files'
          '.Processing third_party/sub/README.chromium'
          '.Fetch .gitmodules',
          api.gitiles.make_encoded_file(
              '[submodule "third_party/sub"]\n'
              '\tpath = third_party/sub\n'
              '\turl = https://example.com/sub.git\n')),
      # Mock git config --get-regexp
      api.step_data(
          'Fetch affected metadata files'
          '.Processing third_party/sub/README.chromium'
          '.list submodules',
          api.raw_io.stream_output_text(
              'submodule.third_party/sub.path third_party/sub\n')),
      # Mock git config --get
      api.step_data(
          'Fetch affected metadata files'
          '.Processing third_party/sub/README.chromium'
          '.git config',
          api.raw_io.stream_output_text('https://example.com/sub.git\n')),
      # Mock gitlink fetch (submodule SHA1)
      api.step_data(
          'Fetch affected metadata files'
          '.Processing third_party/sub/README.chromium'
          '.Fetch third_party/sub',
          api.gitiles.make_encoded_file('deadbeef' * 5)  # 40 chars hex
      ),
      # Mock license fetch from submodule
      api.step_data(
          'Fetch affected metadata files'
          '.Processing third_party/sub/README.chromium'
          '.Fetch license from submodule third_party/sub : LICENSE',
          api.gitiles.make_encoded_file('Submodule License Content')),
      mock_validator(),
      api.post_process(StatusSuccess),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'multiple_warnings',
      api.buildbucket.try_build(
          project='chromium',
          builder='linux-metadata-validator',
      ),
      api.metadata_validator.mock_change(
          {'third_party/baz/README.chromium': ('M', 'Name: Baz\n')}),
      mock_validator(
          issues_by_path={
              'third_party/baz/README.chromium': [{
                  'severity': 'WARNING',
                  'message': 'Warning 1.',
                  'fatal': False
              }, {
                  'severity': 'WARNING',
                  'message': 'Warning 2.',
                  'fatal': False
              }]
          }),
      api.post_process(StatusSuccess),
      api.post_process(DoesNotRun, 'Validation bypassed'),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'submodule_filtering_and_sha1_failure',
      api.buildbucket.try_build(project='chromium'),
      api.metadata_validator.mock_change({
          'third_party/sub/README.chromium':
              ('M', 'Name: Sub\nLicense File: LICENSE\n')
      }),
      # Simulate 404 for main repo license to trigger submodule logic
      api.step_data(
          'Fetch affected metadata files'
          '.Processing third_party/sub/README.chromium'
          '.Fetch third_party/sub/LICENSE', api.json.output({'value': None})),
      api.step_data(
          'Fetch affected metadata files'
          '.Processing third_party/sub/README.chromium'
          '.Fetch .gitmodules',
          api.gitiles.make_encoded_file('[submodule "third_party/sub"]\n'
                                        '\tpath = third_party/sub\n'
                                        '\turl = ...\n')),
      # Mock multiple submodules to cover filtering logic (line 258)
      api.step_data(
          'Fetch affected metadata files'
          '.Processing third_party/sub/README.chromium'
          '.list submodules',
          api.raw_io.stream_output_text(
              'submodule.third_party/sub.path third_party/sub\n'
              'submodule.third_party/other.path third_party/other\n'
              '\n'  # Empty line coverage (line 252)
          )),
      api.step_data(
          'Fetch affected metadata files'
          '.Processing third_party/sub/README.chromium'
          '.git config',
          api.raw_io.stream_output_text('https://example.com/sub.git\n')),
      # Mock invalid/missing SHA1 to cover line 308-309
      api.step_data(
          'Fetch affected metadata files'
          '.Processing third_party/sub/README.chromium'
          '.Fetch third_party/sub',
          api.gitiles.make_encoded_file('not_a_sha1')),
      api.post_process(
          MustRun, 'Fetch affected metadata files'
          '.Processing third_party/sub/README.chromium'
          '.Failed to fetch SHA1 for submodule third_party/sub'),
      api.post_process(StatusSuccess),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'submodule_skip_checks',
      api.buildbucket.try_build(project='chromium'),
      api.metadata_validator.mock_change({
          'third_party/sub/README.chromium':
              ('M', 'Name: Sub\nLicense File: LICENSE.md, SPACE LICENSE\n')
      }),
      api.step_data(
          'Fetch affected metadata files'
          '.Processing third_party/sub/README.chromium'
          '.Fetch .gitmodules',
          api.gitiles.make_encoded_file(
              '[submodule "third_party/sub"]\n\tpath = third_party/sub\n')),
      api.step_data(
          'Fetch affected metadata files'
          '.Processing third_party/sub/README.chromium'
          '.Fetch .gitmodules (2)',
          api.gitiles.make_encoded_file(
              '[submodule "third_party/sub"]\n\tpath = third_party/sub\n')),
      api.step_data(
          'Fetch affected metadata files'
          '.Processing third_party/sub/README.chromium'
          '.list submodules',
          api.raw_io.stream_output_text(
              'submodule.third_party/sub.path third_party/sub\n')),
      api.step_data(
          'Fetch affected metadata files'
          '.Processing third_party/sub/README.chromium'
          '.list submodules (2)',
          api.raw_io.stream_output_text(
              'submodule.third_party/sub.path third_party/sub\n')),
      api.step_data(
          'Fetch affected metadata files'
          '.Processing third_party/sub/README.chromium'
          '.git config',
          api.raw_io.stream_output_text('https://example.com/sub.git\n')),
      api.step_data(
          'Fetch affected metadata files'
          '.Processing third_party/sub/README.chromium'
          '.git config (2)',
          api.raw_io.stream_output_text('https://example.com/sub.git\n')),
      api.step_data(
          'Fetch affected metadata files'
          '.Processing third_party/sub/README.chromium'
          '.Fetch third_party/sub',
          api.gitiles.make_encoded_file('deadbeef' * 5)),
      api.step_data(
          'Fetch affected metadata files'
          '.Processing third_party/sub/README.chromium'
          '.Fetch third_party/sub (2)',
          api.gitiles.make_encoded_file('deadbeef' * 5)),
      api.post_process(
          MustRun, 'Fetch affected metadata files'
          '.Processing third_party/sub/README.chromium'
          '.Not fetching third_party/sub : LICENSE.md because it contains .md,'
          ' see b/484171134'),
      api.post_process(
          MustRun, 'Fetch affected metadata files'
          '.Processing third_party/sub/README.chromium'
          '.Not fetching third_party/sub : SPACE LICENSE because it contains'
          ' spaces, see b/483862822'),
      api.post_process(StatusSuccess),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'absolute_license_path',
      api.buildbucket.try_build(project='chromium'),
      api.metadata_validator.mock_change({
          'third_party/foo/README.chromium':
              ('M', 'Name: Foo\nLicense File: /LICENSE\n')
      }),
      # This covers api.py line 395 and test_api.py line 64.
      api.post_process(
          MustRun, 'Fetch affected metadata files'
          '.Processing third_party/foo/README.chromium'
          '.Fetch LICENSE'),
      api.post_process(StatusSuccess),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'validator_fatal_md_error',
      api.buildbucket.try_build(project='chromium'),
      api.metadata_validator.mock_change(
          {'third_party/foo/README.chromium': ('M', 'Name: Foo\n')}),
      mock_validator(
          issues_by_path={
              'third_party/foo/README.chromium': [{
                  'severity': 'ERROR',
                  'message': 'Some fatal error referencing .md file',
                  'fatal': True
              }]
          }),
      # This covers api.py line 476 (continue if fatal and .md in msg)
      # Since we ignore the error, the step should be SUCCESS.
      api.post_process(StatusSuccess),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'no_submodules_listed',
      api.buildbucket.try_build(project='chromium'),
      api.metadata_validator.mock_change({
          'third_party/sub/README.chromium':
              ('M', 'Name: Sub\nLicense File: LICENSE\n')
      }),
      api.step_data(
          'Fetch affected metadata files'
          '.Processing third_party/sub/README.chromium'
          '.Fetch third_party/sub/LICENSE', api.json.output({'value': None})),
      api.step_data(
          'Fetch affected metadata files'
          '.Processing third_party/sub/README.chromium'
          '.Fetch .gitmodules',
          api.gitiles.make_encoded_file(
              '[submodule "third_party/sub"]\n\tpath = third_party/sub\n')),
      # This mocks empty output for git config --get-regexp,
      # triggering api.py line 248.
      api.step_data(
          'Fetch affected metadata files'
          '.Processing third_party/sub/README.chromium'
          '.list submodules', api.raw_io.stream_output_text('')),
      api.post_process(
          MustRun, 'Fetch affected metadata files'
          '.Processing third_party/sub/README.chromium'
          '.No submodule root found for license path'),
      api.post_process(StatusSuccess),
      api.post_process(DropExpectation),
  )
