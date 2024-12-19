# Copyright 2020 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process

DEPS = [
    'chromium',
    'chromium_checkout',
    'depot_tools/gclient',
    'depot_tools/gerrit',
    'depot_tools/tryserver',
    'recipe_engine/buildbucket',
    'recipe_engine/cipd',
    'recipe_engine/platform',
    'recipe_engine/json',
    'recipe_engine/path',
    'recipe_engine/tricium',
]


def RunSteps(api):
  """This recipe runs quick legacy analyzers for the chromium repo.

  The purpose of this recipe is to wrap analyzers that were previously
  triggered directly by Tricium for the chromium/src repo, in order to
  transition to recipe-based analyzers only.
  """
  # All the analyzers run here must run on 64-bit Linux.
  assert api.platform.is_linux and api.platform.bits == 64
  api.buildbucket.hide_current_build_in_gerrit()
  commit_message = api.gerrit.get_change_description(
      'https://%s' % api.tryserver.gerrit_change.host,
      api.tryserver.gerrit_change.change, api.tryserver.gerrit_change.patchset)
  with api.chromium.chromium_layout():
    api.gclient.set_config('chromium')
    api.chromium.set_config('chromium')
    # We want line numbers for the file as it is in the CL, not as it is
    # rebased on origin/main. gerrit_no_rebase_patch_ref prevents rebasing
    # on origin/main which may result in incorrect line numbers.
    api.chromium_checkout.ensure_checkout(gerrit_no_rebase_patch_ref=True)
    input_dir = api.chromium_checkout.source_dir
    affected_files = [
        f for f in api.chromium_checkout.get_files_affected_by_patch()
        if not _should_skip(f) and api.path.exists(input_dir / f)
    ]
    # TODO(qyearsley): Add Pylint analyzer after debugging.
    analyzers = [
        api.tricium.analyzers.SPELLCHECKER,
    ]
    api.tricium.run_legacy(analyzers, input_dir, affected_files, commit_message)


def _should_skip(path):
  """Checks if a path should be skipped, e.g. because it's third_party.

  This is meant as quick emulation of hte gitattributes-based skipping
  behavior previously provided by GitFileIolator.
  TODO(qyearsley): Implement gitattributes reading, or an alternative.
  https://source.chromium.org/chromium/chromium/src/+/main:.gitattributes
  """
  return (('third_party/blink/' in path and '-expected.' in path) or
          ('third_party/' in path and 'third_party/blink/' not in path))


def GenTests(api):

  def test_data(affected_files):
    existing_files = [
        api.path.cache_dir.joinpath('builder', 'src', x) for x in affected_files
    ]
    return sum([
        api.chromium.try_build(
            builder_group='tryserver.chromium.linux',
            builder='tricium-simple',
            patch_set=1),
        api.platform('linux', 64),
        api.override_step_data(
            'gerrit changes',
            api.json.output([{
                'revisions': {
                    'aaaa': {
                        '_number': 1,
                        'commit': {
                            'message': 'my commit msg',
                        }
                    }
                }
            }])),
        api.path.exists(*existing_files)
    ], api.empty_test_data())

  yield api.test(
      'one_file',
      test_data(['README.md']),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'with_third_party',
      test_data([
          'third_party/foo/x.cc',
          'third_party/blink/web_tests/x.html',
          'third_party/README.md',
          'third_party/blink/web_tests/x-expected.png',
      ]),
      api.post_process(post_process.DropExpectation),
  )
