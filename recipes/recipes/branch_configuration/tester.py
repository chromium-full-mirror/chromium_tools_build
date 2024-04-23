# Copyright 20201 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Tests a change for branch-conditional LUCI services starlark.

The starlark config for the chromium and chrome LUCI projects contains
branch-conditional logic to enable regenerating the configuration files
for release branches by only changing a JSON settings file.
Unfortunately, unless extreme care is taken, it's possible to make a
change on trunk that has no problems but that causes an error when
executed with the settings modified for a branch.

This recipe provides the means to ensure that the configuration can be
generated with the settings modified, so that unexpected errors
shouldn't occur when generating the branch config on branch day. It
provides no guarantees about the effect of the generated configuration
files, only that they can be generated.

To run this, the repo against which the recipe is run must have a script
that can be used to change what platforms are enabled for the
configuration and scripts that can be used to verify that the
configuration "works". It is not necessarily feasible to ensure that the
configuration has the desired effects, but at a minimum the starlark
scripts can be executed to verify that the configuration can be
generated for the different platforms with the change under test. See
tester.proto for details on the scripts.
"""

from recipe_engine import post_process

from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb
from PB.recipe_engine import result as result_pb
from PB.recipes.build.branch_configuration import tester as tester_pb

PROPERTIES = tester_pb.InputProperties

DEPS = [
    'chromium_bootstrap',
    'depot_tools/bot_update',
    'depot_tools/gclient',
    'depot_tools/git',
    'depot_tools/tryserver',
    'recipe_engine/buildbucket',
    'recipe_engine/context',
    'recipe_engine/defer',
    'recipe_engine/path',
    'recipe_engine/properties',
    'recipe_engine/step',
]


def _validate_properties(properties):
  errors = []

  def validate_repeated_field(field_name, elements):
    element_map = {}
    for i, element in enumerate(elements):
      if not element:
        errors.append('{}[{}] is empty'.format(field_name, i))
      else:
        element_map.setdefault(element, []).append(i)

    for element, indices in element_map.items():
      if len(indices) > 1:
        errors.append("multiple occurrences of '{}' in {}: {!r}".format(
            element, field_name, indices))

  if not properties.branch_script:
    errors.append('branch_script is empty')

  if not properties.branch_configs:
    errors.append('branch_configs is empty')
  else:
    config_map = {}
    for i, config in enumerate(properties.branch_configs):
      if not config.name:
        errors.append('branch_configs[{}].name is empty'.format(i))
      else:
        config_map.setdefault(config.name, []).append(i)

      if not config.platforms:
        errors.append(f'branch_configs[{i}].platforms is empty')
      else:
        validate_repeated_field(f'branch_configs[{i}].platforms',
                                config.platforms)

    for config, indices in config_map.items():
      if len(indices) > 1:
        errors.append(
            "multiple configs named '{}' in branch_configs: {!r}".format(
                config, indices))

  if not properties.starlark_entry_points:
    errors.append('starlark_entry_points is empty')
  else:
    validate_repeated_field('starlark_entry_points',
                            properties.starlark_entry_points)

  return errors


def _result(status, header, elements, footer=None):
  summary = [header, '']
  summary.extend('* {}'.format(e) for e in elements)
  if footer:
    summary.extend(['', footer])
  return result_pb.RawResult(status=status, summary_markdown='\n'.join(summary))


def RunSteps(api, properties):
  errors = _validate_properties(properties)
  if errors:
    return _result(
        status=common_pb.INFRA_FAILURE,
        elements=errors,
        header='The following errors were found with the input properties:')

  gclient_config = api.gclient.make_config()
  s = gclient_config.solutions.add()
  s.url = api.tryserver.gerrit_change_repo_url
  s.name = s.url.rsplit('/', 1)[-1]
  gclient_config.got_revision_mapping[s.name] = 'got_revision'
  gclient_config.repo_path_map[s.url] = (s.name, 'HEAD')

  with api.chromium_bootstrap.update_gclient_config(gclient_config) as callback:
    with api.context(cwd=api.path.cache_dir / 'builder'):
      update_result = api.bot_update.ensure_checkout(
          patch=True, gclient_config=gclient_config)
    callback(update_result.json.output['manifest'])

  repo_path = api.path.cache_dir.joinpath('builder',
                                          update_result.json.output['root'])

  bad_branch_configs = []
  with api.context(cwd=repo_path):
    branch_script = repo_path / properties.branch_script
    for branch_config in properties.branch_configs:
      with api.step.nest(branch_config.name):
        sheriff_rotation_args = []
        if branch_config.sheriff_rotation:
          sheriff_rotation_args = [
              '--sheriff-rotation',
              branch_config.sheriff_rotation,
          ]
        for p in branch_config.platforms:
          api.step(f'enable {p}', [
              branch_script,
              'enable-platform',
              p,
              '--description',
              'testing',
              *sheriff_rotation_args,
          ])

        with api.step.nest('verify'):
          try:
            with api.defer.context() as defer:
              for entry_point in properties.starlark_entry_points:
                for subcommand in ('generate', 'validate'):
                  cmd = ['lucicfg', subcommand, entry_point]
                  defer(api.step, ' '.join(cmd), cmd)
          except* api.step.StepFailure:
            bad_branch_configs.append(branch_config.name)

        with api.step.nest('restore'):
          api.git('restore', '.', infra_step=True)
          api.git('clean', '-f', infra_step=True)

  if bad_branch_configs:
    return _result(
        status=common_pb.FAILURE,
        elements=bad_branch_configs,
        header='The following branch configs failed verification:',
        footer='See steps for more information')


def GenTests(api):
  yield api.test(
      'basic',
      api.buildbucket.try_build(),
      api.properties(
          tester_pb.InputProperties(
              branch_script='branch-script',
              branch_configs=[
                  tester_pb.BranchConfig(
                      name='branch-config1',
                      platforms=['platform1'],
                  ),
                  tester_pb.BranchConfig(
                      name='branch-config2',
                      platforms=['platform2'],
                  ),
                  tester_pb.BranchConfig(
                      name='branch-config3',
                      platforms=['platform1', 'platform2'],
                      sheriff_rotation='sheriff-rotation',
                  ),
              ],
              starlark_entry_points=[
                  'entry-point1.star',
                  'entry-point2.star',
              ],
          )),
      api.post_check(
          post_process.MustRun,
          'branch-config1.enable platform1',
          'branch-config1.verify.lucicfg generate entry-point1.star',
          'branch-config1.verify.lucicfg validate entry-point1.star',
          'branch-config1.verify.lucicfg generate entry-point2.star',
          'branch-config1.verify.lucicfg validate entry-point2.star',
          'branch-config2.enable platform2',
          'branch-config2.verify.lucicfg generate entry-point1.star',
          'branch-config2.verify.lucicfg validate entry-point1.star',
          'branch-config2.verify.lucicfg generate entry-point2.star',
          'branch-config2.verify.lucicfg validate entry-point2.star',
          'branch-config3.enable platform1',
          'branch-config3.enable platform2',
          'branch-config3.verify.lucicfg generate entry-point1.star',
          'branch-config3.verify.lucicfg validate entry-point1.star',
          'branch-config3.verify.lucicfg generate entry-point2.star',
          'branch-config3.verify.lucicfg validate entry-point2.star',
      ),
      api.post_check(post_process.StepCommandContains,
                     'branch-config1.enable platform1',
                     ['platform1', '--description', 'testing']),
      api.post_check(post_process.StepCommandContains,
                     'branch-config2.enable platform2',
                     ['platform2', '--description', 'testing']),
      api.post_check(
          post_process.StepCommandContains,
          'branch-config3.enable platform1',
          [
              'platform1',
              '--description',
              'testing',
              '--sheriff-rotation',
              'sheriff-rotation',
          ],
      ),
      api.post_check(
          post_process.StepCommandContains,
          'branch-config3.enable platform2',
          [
              'platform2',
              '--description',
              'testing',
              '--sheriff-rotation',
              'sheriff-rotation',
          ],
      ),
  )

  yield api.test(
      'failed enable platform step',
      api.buildbucket.try_build(),
      api.properties(
          tester_pb.InputProperties(
              branch_script='branch-script',
              branch_configs=[
                  tester_pb.BranchConfig(
                      name='bad-branch-config',
                      platforms=['platform1'],
                  ),
              ],
              starlark_entry_points=[
                  'entry-point1.star',
                  'entry-point2.star',
              ],
          )),
      api.step_data('bad-branch-config.enable platform1', retcode=1),
      api.post_check(post_process.StepFailure, 'bad-branch-config'),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'failed verification step',
      api.buildbucket.try_build(),
      api.properties(
          tester_pb.InputProperties(
              branch_script='branch-script',
              branch_configs=[
                  tester_pb.BranchConfig(
                      name='branch-config1',
                      platforms=['platform1'],
                  ),
                  tester_pb.BranchConfig(
                      name='branch-config2',
                      platforms=['platform2'],
                  ),
                  tester_pb.BranchConfig(
                      name='branch-config3',
                      platforms=['platform3'],
                  ),
              ],
              starlark_entry_points=[
                  'entry-point1.star',
                  'entry-point2.star',
              ],
          )),
      api.step_data(
          'branch-config1.verify.lucicfg generate entry-point1.star',
          retcode=1),
      api.step_data(
          'branch-config3.verify.lucicfg generate entry-point2.star',
          retcode=1),
      api.post_check(
          post_process.StepFailure,
          'branch-config1.verify.lucicfg generate entry-point1.star'),
      api.post_check(post_process.StepFailure, 'branch-config1'),
      api.post_check(
          post_process.StepFailure,
          'branch-config3.verify.lucicfg generate entry-point2.star'),
      api.post_check(post_process.StepFailure, 'branch-config3'),
      api.post_check(post_process.StepSuccess, 'branch-config2'),
      api.expect_status('FAILURE'),
      api.post_check(post_process.ResultReasonRE,
                     '^The following branch configs failed verification'),
      api.post_check(post_process.ResultReasonRE, r'\bbranch-config1\b'),
      api.post_check(post_process.ResultReasonRE, r'\bbranch-config3\b'),
      api.post_process(post_process.DropExpectation),
  )

  # Must appear last since it drops expectations
  def invalid_properties(*errors):
    test_data = api.expect_status('INFRA_FAILURE')
    test_data += api.post_check(
        post_process.ResultReasonRE,
        '^The following errors were found with the input properties')
    for error in errors:
      test_data += api.post_check(post_process.ResultReasonRE, error)
    test_data += api.post_process(post_process.DropExpectation)
    return test_data

  yield api.test(
      'empty input properties',
      api.buildbucket.try_build(),
      api.properties(tester_pb.InputProperties()),
      invalid_properties(
          'branch_script is empty',
          'branch_configs is empty',
          'starlark_entry_points is empty',
      ),
  )

  yield api.test(
      'invalid input properties',
      api.buildbucket.try_build(),
      api.properties(
          tester_pb.InputProperties(
              branch_script='branch-script',
              branch_configs=[
                  tester_pb.BranchConfig(),
                  tester_pb.BranchConfig(name='branch-config'),
                  tester_pb.BranchConfig(name='branch-config'),
              ],
              starlark_entry_points=[
                  '',
                  'entry-point.star',
                  'entry-point.star',
              ],
          )),
      invalid_properties(
          r'\bbranch_configs\[0\].name is empty\b',
          r'\bbranch_configs\[0\].platforms is empty\b',
          (r"\bmultiple configs named 'branch-config' "
           r'in branch_configs: \[1, 2\]'),
          r'\bstarlark_entry_points\[0\] is empty\b',
          (r"\bmultiple occurrences of 'entry-point.star' "
           r'in starlark_entry_points: \[1, 2\]'),
      ),
  )
