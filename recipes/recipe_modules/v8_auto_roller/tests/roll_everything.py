# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine.post_process import (DoesNotRun, DoesNotRunRE,
                                        DropExpectation, MustRun,
                                        SummaryMarkdown)

DEPS = [
    'recipe_engine/buildbucket',
    'recipe_engine/cipd',
    'recipe_engine/context',
    'recipe_engine/file',
    'recipe_engine/json',
    'recipe_engine/path',
    'recipe_engine/platform',
    'recipe_engine/raw_io',
    'recipe_engine/step',
    'v8',
    'v8_auto_roller',
]

RETSAM = 'retsam'[::-1]


def RunSteps(api):
  autoroller_config = {
      'target_config': {
          'solution_name': 'v8',
          'project_name': 'v8/v8',
          'account': 'v8@example.com',
          'log_template': 'Rolling v8/%s: %s/+log/%s..%s',
          'cipd_log_template': 'Rolling v8/%s: %s..%s',
      },
      'subject': 'Update V8 deps',
      'reviewers': [
          'anybody@chromium.org',
          'ciciobello@chromium.org',
      ],
      'deps_key_mapping': {
          'buildtools-mapped': 'buildtools',
      },
      'dependency_version_sources': {
          'mock-skip-chromium-roll': 'tip_of_tree',
      },
      'show_commit_log': True,
      'roll_chromium_pin': True,
      'roll_test262': True,
      'scripted_rolls': ['puppeteer-core', 'browser-protocol'],
      'bugs': 'none',
  }

  api.v8_auto_roller.setup(autoroller_config)

  api.v8_auto_roller.regular_roll(autoroller_config)
  api.v8_auto_roller.cft_pin_roll(autoroller_config)
  api.v8_auto_roller.scripted_rolls(autoroller_config)

  return api.v8_auto_roller.report_result()


def GenTests(api):
  v8_deps_info = """v8: https://chromium.googlesource.com/v8/v8.git
v8/buildtools-mapped: https://chromium.googlesource.com/chromium/buildtools.git@5fd66957f08bb752dca714a591c84587c9d70762
src/tools/luci-go:infra/tools/luci/isolate/${platform}: https://chrome-infra-packages.appspot.com/infra/tools/luci/isolate/${platform}@git_revision:8b15ba47cbaf07a56f93326e39f0c8e5069c19e9
src/ninja:infra/3pp/tools/ninja/${platform}: https://chrome-infra-packages.appspot.com/infra/3pp/tools/ninja/${platform}@version:2@1.8.2.chromium.3
src/mock-skip-chromium-roll: mock/skip-chromium-roll.git@1
v8/mock-tot-rolled: https://chromium.googlesource.com/tot-rolled.git@d3f34f8dfaecc23202a6ef66957e83462d6c826d
v8/mock-tot-retsam-rolled: https://chromium.googlesource.com/tot-retsam-rolled.git@d3f34f8dfaecc23202a6ef66957e83462d6c826d
v8/tools/clang: https://chromium.googlesource.com/chromium/src/tools/clang@d3f34f8dfaecc23202a6ef66957e83462d6c826d
v8/tools/clang-reviewed: https://example.com/tools/clang-reviewed.git@d3f34f8dfaecc23202a6ef66957e83462d6c826d
v8/mock-set-dep-failing: mock/set-dep-failing.git@1
v8/mock-package-without-latest-ref:mock/package-without-latest-ref: https://chrome-infra-packages.appspot.com/mock/package-without-latest-ref@7fd66957f08bb752dca714a591c84587c9d70764
v8/mock-package-latest:mock/package-latest: https://chrome-infra-packages.appspot.com/mock/package-latest@6fd66957f08bb752dca714a591c84587c9d70763"""

  cr_deps_info = """src: https://chromium.googlesource.com/chromium/src.git
src/buildtools: https://chromium.googlesource.com/chromium/buildtools.git@5fd66957f08bb752dca714a591c84587c9d70762
src/tools/luci-go:infra/tools/luci/isolate/${platform}: https://chrome-infra-packages.appspot.com/infra/tools/luci/isolate/${platform}@git_revision:3d8f881462b1a93c7525499381fafc8a08691be7
src/ninja:infra/3pp/tools/ninja/${platform}: https://chrome-infra-packages.appspot.com/infra/3pp/tools/ninja/${platform}@version:2@1.8.2.chromium.4
src/mock-set-dep-failing: mock/set-dep-failing.git@2
src/mock-skip-chromium-roll: mock/skip-chromium-roll.git@2"""

  git_cl_info = """remote:
remote:   https://chromium-review.googlesource.com/c/chromium/tools/build/+/3840339 [v8] Remove deprecated roll recipe [WIP]
remote:"""

  def base_template(testname, additional_v8_deps, additional_cr_deps):
    return [
        testname,
        api.buildbucket.ci_build(
            project='v8',
            git_repo='https://chromium.googlesource.com/v8/v8',
            builder=testname,
        ),
        api.override_step_data(
            'Find updated deps.gclient get v8 deps',
            api.raw_io.stream_output_text(
                v8_deps_info + additional_v8_deps, stream='stdout'),
        ),
        api.override_step_data(
            'Find updated deps.gclient get src deps',
            api.raw_io.stream_output_text(
                cr_deps_info + additional_cr_deps, stream='stdout'),
        ),
    ]

  def template(
      testname,
      additional_v8_deps='',
      additional_cr_deps='',
      git_diff='some difference',
  ):
    result = base_template(testname, additional_v8_deps, additional_cr_deps) + [
        # CIPDs test api has a latest ref by default for each package. We over-
        # ride this behaviour for our mock package `without-latest-ref`.
        api.override_step_data(
            'Find updated deps.cipd instances mock/package-without-latest-ref',
            api.cipd._resultify({
                'instances': [{
                    'pin': {
                        'package':
                            'mock/package-without-latest-ref',
                        'instance_id':
                            api.cipd.make_resolved_version('no-latest'),
                    },
                    'registered_by': 'user:doe@developer.gserviceaccount.com',
                    'registered_ts': 1987654321,
                    'refs': None,
                }]
            })),
        api.override_step_data(
            'Update trusted deps.git status',
            api.raw_io.stream_output_text(git_diff, stream='stdout'),
        ),
        api.override_step_data(
            'Update reviewed deps.git status',
            api.raw_io.stream_output_text(git_diff, stream='stdout'),
        ),
        api.override_step_data(
            'Update trusted deps.gclient setdep mock-set-dep-failing',
            retcode=1),
        api.override_step_data(
            'Find updated deps.look up mock-tot-rolled (main)',
            api.raw_io.stream_output_text(
                'deadbeef\trefs/heads/main', stream='stdout'),
        ),
        api.override_step_data(
            'Find updated deps.look up mock-skip-chromium-roll (main)',
            api.raw_io.stream_output_text(
                '3\trefs/heads/main', stream='stdout'),
        ),
        api.override_step_data(
            'Find updated deps.look up mock-tot-retsam-rolled (main)',
            api.raw_io.stream_output_text('', stream='stdout'),
        ),
        api.override_step_data(
            f'Find updated deps.look up mock-tot-retsam-rolled ({RETSAM})',
            api.raw_io.stream_output_text(
                f'deadbeef\trefs/heads/{RETSAM}', stream='stdout'),
        ),
        api.override_step_data(
            'Find updated deps.look up tools_clang (main)',
            api.raw_io.stream_output_text(
                'deadbeef\trefs/heads/main', stream='stdout'),
        ),
        api.override_step_data(
            'Find updated deps.look up tools_clang-reviewed (main)',
            api.raw_io.stream_output_text(
                'deadbeef\trefs/heads/main', stream='stdout'),
        ),
        api.override_step_data(
            'Update chromium pin deps.gclient get chrome deps',
            api.raw_io.stream_output_text('123.0.4500.7', stream='stdout'),
        ),
    ]

    if git_diff:
      result += [
          api.override_step_data(
              'Update reviewed deps.git cl',
              api.raw_io.stream_output_text(git_cl_info, stream='stdout'),
          ),
          api.override_step_data(
              'Update trusted deps.git cl',
              api.raw_io.stream_output_text(git_cl_info, stream='stdout'),
          ),
      ]

    return result

  # Happy path
  yield api.test(
      *template('default') + [
          api.override_step_data(
              'Scripted rolls.Update Puppeteer Core deps.git status',
              api.raw_io.stream_output_text(
                  'diff generated by script', stream='stdout'),
          ),
          api.override_step_data(
              'Scripted rolls.Update Puppeteer Core deps.git cl',
              api.raw_io.stream_output_text(git_cl_info, stream='stdout'),
          ),
          api.post_process(
              SummaryMarkdown,
              'updated 4 trusted dep(s), 6 reviewed dep(s), Puppeteer Core'),
          api.v8.filter_exclude('Setup.ensure builder cache dir'),
      ],)

  # No chrome pin roll
  yield api.test(*template('missing key') + [
      api.override_step_data(
          'Update chromium pin deps.gclient get chrome deps',
          api.raw_io.stream_output_text(
              'Could not find any variable called chrome.', stream='stderr')),
      api.post_process(MustRun,
                       "Update chromium pin deps.Failed get dep chrome"),
      api.post_process(DropExpectation),
  ])

  # Stale rolls: If active roll CLs exists in gerrit, we abandon those first
  yield api.test(
      *(template('no-stale-roll') + [
          api.post_process(DoesNotRunRE, 'Update .* deps\.gerrit abandon'),
          api.post_process(DropExpectation),
      ]),
      status='SUCCESS')

  yield api.test(
      *(template('stale-roll') + [
          api.override_step_data(
              'Update trusted deps.gerrit changes',
              api.json.output([
                  {
                      '_number': '123',
                      'subject': 'Update V8 deps (trusted)'
                  },
              ])),
          api.override_step_data(
              'Update reviewed deps.gerrit changes',
              api.json.output([
                  {
                      '_number': '123',
                      'subject': 'Update V8 deps (reviewed)'
                  },
              ])),
          api.post_process(MustRun, 'Update trusted deps.gerrit abandon'),
          api.post_process(MustRun, 'Update trusted deps.Previous roll failed'),
          api.post_process(MustRun, 'Update reviewed deps.gerrit abandon'),
          api.post_process(MustRun,
                           'Update reviewed deps.Previous roll failed'),
          api.post_process(DropExpectation),
      ]),
      status='SUCCESS')

  # No version difference: There is no new dependency version, and we do not try
  # to update any dep (via `gclient setdep`).
  yield api.test(
      'no-version-difference',
      api.buildbucket.ci_build(
          project='v8',
          git_repo='https://chromium.googlesource.com/v8/v8',
          builder='no-version-difference',
      ),
      api.override_step_data(
          'Find updated deps.gclient get src deps',
          api.raw_io.stream_output_text(
              'src: https://chromium.googlesource.com/chromium/src.git\n'
              'src/tools: https://example.com/chromium/tools.git@42',
              stream='stdout',
          ),
      ),
      api.override_step_data(
          'Find updated deps.gclient get v8 deps',
          api.raw_io.stream_output_text(
              'v8: https://chromium.googlesource.com/chromium/v8.git\n'
              'v8/tools: https://example.com/chromium/tools.git@42',
              stream='stdout',
          ),
      ),
      api.override_step_data(
          'Update chromium pin deps.gclient get chrome deps',
          api.raw_io.stream_output_text('123', stream='stdout'),
      ),
      api.post_process(DoesNotRunRE, r'^Update \w* deps\.gclient setdep .*'),
      api.post_process(
          DoesNotRun,
          'Update chromium pin deps.gclient set chromium_linux deps'),
      api.post_process(DropExpectation),
      status='SUCCESS',
  )

  # No update succeeded: If there is no dependency update, we don't create CLs
  yield api.test(
      *template('no-depependency-update', git_diff='') + [
          api.post_process(DoesNotRunRE, r'^Update \w* deps\.git cl$'),
          api.post_process(DropExpectation),
      ],
      status='SUCCESS')

  # Malformed DEPS file: Raise an exception
  yield api.test(*base_template(
      'malformed-deps-file',
      additional_v8_deps='\nsrc/mock-malformed:  https://example.com/',
      additional_cr_deps='',
  ) + [
      api.expect_exception('Exception'),
      api.post_process(DropExpectation),
  ])

  # Changed locations: Fail if a dependency changes its repository
  yield api.test(
      *base_template(
          'changed-location',
          additional_v8_deps='\nv8/mock-changed-location: foo/changed-location@1',
          additional_cr_deps='\nsrc/mock-changed-location: bar/changed-location@2',
      ) + [
          api.post_process(DropExpectation),
      ],
      status='FAILURE')
