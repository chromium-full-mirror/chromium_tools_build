# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Checks GN args specified in starlark versus mb config file.

Once migration of all GN args to starlark is complete, this recipe will be
irrelevant.
"""

from collections.abc import Iterable
import difflib
import re

from recipe_engine import post_process
from recipe_engine import recipe_test_api
from recipe_engine.config_types import Path
from recipe_engine.engine_types import StepPresentation

from RECIPE_MODULES.build import proto_validation
from RECIPE_MODULES.build.attr_utils import attrib, attrs
from RECIPE_MODULES.build.chromium import BuilderId

from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb
from PB.recipe_engine import result as result_pb
from PB.recipes.build.chromium import (gn_args_verifier as gn_args_verifier_pb)

PROPERTIES = gn_args_verifier_pb.InputProperties

DEPS = [
    'chromium',
    'gn',
    'depot_tools/bot_update',
    'depot_tools/gclient',
    'depot_tools/tryserver',
    'recipe_engine/buildbucket',
    'recipe_engine/context',
    'recipe_engine/file',
    'recipe_engine/json',
    'recipe_engine/path',
    'recipe_engine/properties',
    'recipe_engine/raw_io',
    'recipe_engine/step',
]


def _gn_args_file_re(builder_config_directory: str) -> re.Pattern:
  """Get a regular expression that will match a GN args file path.

  Returns:
    A pattern object that will match repo-root-relative paths that refer
    to GN args files. The pattern captures the repo-root-relative
    path to the directory that contains the gn-args.json of a single builder.
  """
  return re.compile(
      f'^{re.escape(builder_config_directory)}/([^/]+/[^/]+/gn-args\.json)')


def RunSteps(api, properties):
  api.tryserver.require_is_tryserver()

  errors = VALIDATORS.validate(properties)
  if errors:
    return _result(
        status=common_pb.INFRA_FAILURE,
        elements=errors,
        header='The following errors were found with the input properties:')

  api.gclient.set_config(properties.gclient_config)
  api.chromium.set_config('chromium')
  checkout_root = api.path.cache_dir / 'builder'

  with api.context(cwd=checkout_root):
    update_result = api.bot_update.ensure_checkout(patch=True)
  repo_path = update_result.source_root.path
  # Absolute path of the per-builder config directory root.
  builder_config_root = repo_path / properties.builder_config_directory

  # TODO(crbug.com/1471251): Add support for multiple mb config files.
  mb_config_path = checkout_root / properties.mb_config_paths[0]

  with api.step.nest('process data from patch'):
    gn_args_file_path_by_builder_by_builder_group = api.file.read_json(
        'read gn_args_locations.json',
        builder_config_root / 'gn_args_locations.json')
    # Mapping from gn-args.json file path to the builder group of the builder.
    builder_group_by_gn_args_file_path = {}
    for builder_group, gn_args_file_path_by_builder in (
        gn_args_file_path_by_builder_by_builder_group.items()):
      for file_path in gn_args_file_path_by_builder.values():
        builder_group_by_gn_args_file_path[file_path] = builder_group

    with api.context(cwd=repo_path):
      affected_files = set(api.tryserver.get_files_affected_by_patch(''))
    with api.step.nest('parse gn-args.json files'):
      # Mapping from per-builder gn-args.json to its content.
      gn_args_json_by_file_path = {}
      gn_args_file_re = _gn_args_file_re(properties.builder_config_directory)
      for f in affected_files:
        if match := gn_args_file_re.match(f):
          file_path = match.group(1)
          # Add a GN args file to the mapping for verification if it is not
          # being removed by the patch.
          if file_path in builder_group_by_gn_args_file_path:
            gn_args_json_by_file_path[file_path] = api.file.read_json(
                f'read {file_path}', repo_path / f)

    # Find GN args with patch applied.
    gn_args_by_builder_id = {}
    file_path_by_builder_id = {}
    for file_path, gn_args_json in gn_args_json_by_file_path.items():
      builder_id = BuilderId.create_for_group(
          builder_group_by_gn_args_file_path[file_path],
          file_path.split('/')[1])
      file_path_by_builder_id[builder_id] = file_path
      mb_step_name = f'mb lookup - {str(builder_id)}'
      if 'phases' in gn_args_json:
        phased_gn_args = {}
        for phase in gn_args_json['phases']:
          phased_gn_args[phase] = api.chromium.mb_lookup(
              builder_id,
              name=f'{mb_step_name}, phase: {phase}',
              mb_config_path=mb_config_path,
              phase=phase,
              raise_on_failure=True)
        gn_args_by_builder_id[builder_id] = phased_gn_args
      else:
        gn_args_by_builder_id[builder_id] = api.chromium.mb_lookup(
            builder_id,
            name=mb_step_name,
            mb_config_path=mb_config_path,
            raise_on_failure=True)

  with api.context(cwd=checkout_root):
    api.bot_update.deapply_patch(update_result)

  # Verify each builder.
  failures = []
  for builder_id, post_patch_args in gn_args_by_builder_id.items():
    with api.step.nest(
        f'verify {file_path_by_builder_id[builder_id]}') as presentation:
      if api.path.exists(builder_config_root /
                         file_path_by_builder_id[builder_id]):
        presentation.step_text = '\nbuilder already migrated, skip.'
        continue

      success = True
      if isinstance(post_patch_args, dict):
        for phase, phase_args in post_patch_args.items():
          with api.step.nest(f'phase: {phase}') as phase_presentation:
            try:
              pre_patch_args = api.chromium.mb_lookup(
                  builder_id,
                  mb_config_path=mb_config_path,
                  phase=phase,
                  raise_on_failure=True)
            except api.step.StepFailure:
              phase_presentation.status = api.step.SUCCESS
              phase_presentation.step_text = (
                  '\ncould not load GN args from mb config, skip.')
              continue
            if not _verify_gn_args(api, pre_patch_args, phase_args,
                                   phase_presentation):
              success = False
      else:
        try:
          pre_patch_args = api.chromium.mb_lookup(
              builder_id,
              mb_config_path=mb_config_path,
              raise_on_failure=True)
        except api.step.StepFailure:
          presentation.status = api.step.SUCCESS
          presentation.step_text = (
              '\ncould not load GN args from mb config, skip.')
          continue

        success = _verify_gn_args(api, pre_patch_args, post_patch_args,
                                  presentation)
      if not success:
        failures.append(str(builder_id))

  if failures:
    return _result(
        status=common_pb.FAILURE,
        elements=list(failures),
        header='verification failed for the following builders:')


def _verify_gn_args(api, pre_patch_args: str, post_patch_args: str,
                    presentation: StepPresentation) -> bool:
  pre_patch_args_lines = pre_patch_args.splitlines()
  post_patch_args_lines = post_patch_args.splitlines()
  diff = list(
      difflib.unified_diff(
          pre_patch_args_lines,
          post_patch_args_lines,
          fromfile='mb config GN args',
          tofile='Starlark GN args',
          n=max(len(pre_patch_args_lines), len(post_patch_args_lines)),
      ))

  if diff:
    presentation.logs['diff'] = diff
    presentation.status = api.step.FAILURE
    presentation.step_text = '\nGN args mismatch; see "diff" log for details'
    return False

  presentation.step_text = '\nGN args match between starlark and mb config'
  return True


VALIDATORS = proto_validation.Registry()


@VALIDATORS.register(gn_args_verifier_pb.InputProperties)
def _validate_properties(message, ctx):
  ctx.validate_field(message, 'gclient_config')
  ctx.validate_field(message, 'builder_config_directory')
  ctx.validate_field(message, 'mb_config_paths')
  if len(message.mb_config_paths) != 1:
    ctx.error('Only a single mb config path is supported at the moment.')


def _result(
    status: common_pb.Status,
    header: str,
    elements: Iterable[str],
) -> result_pb.RawResult:
  summary = [header, '']
  summary.extend('* {}'.format(e) for e in elements)
  return result_pb.RawResult(status=status, summary_markdown='\n'.join(summary))


def GenTests(api):

  builder_config_dir = 'builder-config-dir'
  mb_config_path = 'src/tools/mb/mb_config.pyl'

  def common_test_data(api) -> recipe_test_api.StepTestData:
    """Set up common test data for the build"""
    data = api.buildbucket.try_build()
    data += api.properties(
        gn_args_verifier_pb.InputProperties(
            gclient_config='chromium',
            builder_config_directory=builder_config_dir,
            mb_config_paths=[mb_config_path]))
    return data

  @attrs()
  class BuilderDataEntry:
    """ An object that contsins test data for an individual builder."""

    # Builder group name
    group = attrib(str, default='')

    # Bucket name
    bucket = attrib(str, default='')

    # Builder name
    builder = attrib(str, default='')

    # Whether the builder is being removed in the patch.
    builder_removed = attrib(bool, default=False)

    # Content of gn-args.json generated by Starlark definition.
    sl_gn_args = attrib(dict, default={})

    # GN args defined in mb config file, but stored in the same format as
    # the sl_gn_args dict.
    mb_gn_args = attrib(dict, default={})

    # Whether the builder has already been migrated to Starlark GN args
    migrated = attrib(bool, default=False)

    # Whether the mb_lookup step will fail while patch applied
    mb_lookup_failure_with_patch = attrib(bool, default=False)

    # Whether the mb_lookup step will fail without patch
    mb_lookup_failure = attrib(bool, default=False)

    # The phase for which the mb_lookup step will fail without patch
    mb_lookup_phase_failure = attrib(str, default='')

  def gn_args_test_data(
      api, builder_entries: Iterable[BuilderDataEntry]
  ) -> recipe_test_api.StepTestData:
    """Set up test data for GN args verification steps

    Args:
      * builder_entries - A list of BuilderDataEntry objects.
    """

    def dict_to_gn_args_str(args: dict) -> str:
      """Convert a dict of GN args into an args.gn format string."""
      lines = []
      for arg, val in args.items():
        str_val = str(val)
        if isinstance(val, str):
          str_val = f'\"{str_val}\"'
        elif isinstance(val, bool):
          str_val = str_val.lower()
        lines.append(f'{arg} = {str_val}')
      return '\n'.join(lines)

    test_steps = api.empty_test_data()
    locations_json = {}
    gn_args_json_paths = []
    for builder_data in builder_entries:
      group = builder_data.group
      bucket = builder_data.bucket
      builder = builder_data.builder
      removed = builder_data.builder_removed
      file_path = f'{bucket}/{builder}/gn-args.json'
      path_in_repo = f'{builder_config_dir}/{file_path}'
      gn_args_json_paths.append(path_in_repo)
      if removed:
        continue

      locations_json.setdefault(group, {})[builder] = file_path

      # Setup Starlark GN args test data
      sl_gn_args = builder_data.sl_gn_args
      test_steps += api.step_data(
          'process data from patch.'
          f'parse gn-args.json files.read {file_path}',
          api.file.read_json(sl_gn_args))
      if 'phases' in sl_gn_args:
        for phase, phase_args in sl_gn_args['phases'].items():
          step_name = ('process data from patch.mb lookup - '
                       f'{group}:{builder}, phase: {phase}')
          if builder_data.mb_lookup_failure_with_patch:
            test_steps += api.step_data(step_name, retcode=1, status='FAILURE')
            break

          test_steps += api.step_data(
              step_name,
              stdout=api.raw_io.output_text(
                  dict_to_gn_args_str(phase_args['gn_args'])))
      else:
        step_name = f'process data from patch.mb lookup - {group}:{builder}'
        if builder_data.mb_lookup_failure_with_patch:
          test_steps += api.step_data(step_name, retcode=1, status='FAILURE')
        else:
          test_steps += api.step_data(
              step_name,
              stdout=api.raw_io.output_text(
                  dict_to_gn_args_str(sl_gn_args['gn_args'])))

      if builder_data.mb_lookup_failure_with_patch:
        break

      # Setup mb config GN args test data
      if builder_data.migrated:
        # Builder already migrated
        test_steps += api.path.exists(
            api.path.cache_dir.joinpath('builder',
                                        'src').joinpath(path_in_repo))
        test_steps += api.post_process(
            post_process.StepTextEquals,
            f'verify {bucket}/{builder}/gn-args.json',
            '<br/>builder already migrated, skip.')
      else:
        mb_gn_args = builder_data.mb_gn_args
        if 'phases' in mb_gn_args:
          for phase, phase_args in mb_gn_args['phases'].items():
            step_name = (f'verify {bucket}/{builder}/gn-args.json.'
                         f'phase: {phase}.lookup GN args')
            if phase == builder_data.mb_lookup_phase_failure:
              test_steps += api.step_data(
                  step_name, retcode=1, status='FAILURE')
            else:
              test_steps += api.step_data(
                  step_name,
                  stdout=api.raw_io.output_text(
                      dict_to_gn_args_str(phase_args['gn_args'])))
        else:
          step_name = (f'verify {bucket}/{builder}/gn-args.json.lookup GN args')
          if builder_data.mb_lookup_failure:
            test_steps += api.step_data(step_name, retcode=1, status='FAILURE')
          else:
            test_steps += api.step_data(
                step_name,
                stdout=api.raw_io.output_text(
                    dict_to_gn_args_str(mb_gn_args['gn_args'])))

    test_steps += api.tryserver.get_files_affected_by_patch(
        [*gn_args_json_paths],
        step_name=('process data from patch.git diff to analyze patch'))
    test_steps += api.step_data(
        'process data from patch.'
        'read gn_args_locations.json', api.file.read_json(locations_json))
    return test_steps

  yield api.test(
      'basic',
      common_test_data(api),
      gn_args_test_data(api, [
          BuilderDataEntry(
              group='group1',
              bucket='bucket1',
              builder='builder1',
              sl_gn_args={
                  'gn_args': {
                      'str_arg': 'test1',
                      'int_arg': 1,
                      'bool_arg': False
                  }
              },
              mb_gn_args={
                  'gn_args': {
                      'str_arg': 'test1',
                      'int_arg': 1,
                      'bool_arg': False
                  }
              },
          ),
          BuilderDataEntry(
              group='group2',
              bucket='bucket2',
              builder='builder2',
              sl_gn_args={
                  'phases': {
                      'phase1': {
                          'gn_args': {
                              'is_phase1': True,
                          }
                      },
                      'phase2': {
                          'gn_args': {
                              'is_phase2': True,
                          }
                      }
                  }
              },
              mb_gn_args={
                  'phases': {
                      'phase1': {
                          'gn_args': {
                              'is_phase1': True,
                          }
                      },
                      'phase2': {
                          'gn_args': {
                              'is_phase2': True,
                          }
                      }
                  }
              },
          ),
      ]),
      api.post_process(post_process.DropExpectation),
      status='SUCCESS',
  )

  yield api.test(
      'gn_args_mismatch',
      common_test_data(api),
      gn_args_test_data(api, [
          BuilderDataEntry(
              group='group1',
              bucket='bucket1',
              builder='builder1',
              sl_gn_args={
                  'gn_args': {
                      'str_arg': 'test1',
                      'int_arg': 1,
                      'bool_arg': False
                  }
              },
              mb_gn_args={
                  'gn_args': {
                      'str_arg': 'test1',
                      'int_arg': 2,
                      'bool_arg': False
                  }
              },
          ),
          BuilderDataEntry(
              group='group2',
              bucket='bucket2',
              builder='builder2',
              sl_gn_args={
                  'phases': {
                      'phase1': {
                          'gn_args': {
                              'is_phase1': True,
                          }
                      },
                      'phase2': {
                          'gn_args': {
                              'is_phase2': True,
                          }
                      }
                  }
              },
              mb_gn_args={
                  'phases': {
                      'phase1': {
                          'gn_args': {
                              'is_phase1': False,
                          }
                      },
                      'phase2': {
                          'gn_args': {
                              'int_arg': 2,
                          }
                      }
                  }
              },
          ),
      ]),
      api.post_process(post_process.StepFailure,
                       'verify bucket1/builder1/gn-args.json'),
      api.post_process(post_process.StepFailure,
                       'verify bucket2/builder2/gn-args.json'),
      api.post_process(post_process.DropExpectation),
      status='FAILURE',
  )

  yield api.test(
      'already_migrated_builder',
      common_test_data(api),
      gn_args_test_data(api, [
          BuilderDataEntry(
              group='group1',
              bucket='bucket1',
              builder='builder1',
              sl_gn_args={
                  'gn_args': {
                      'str_arg': 'test1',
                      'int_arg': 1,
                      'bool_arg': False
                  }
              },
              migrated=True,
          )
      ]),
      api.post_process(post_process.DropExpectation),
      status='SUCCESS',
  )

  yield api.test(
      'mb_lookup_failure_without_patch',
      common_test_data(api),
      gn_args_test_data(api, [
          BuilderDataEntry(
              group='group1',
              bucket='bucket1',
              builder='builder1',
              sl_gn_args={
                  'gn_args': {
                      'str_arg': 'test1',
                      'int_arg': 1,
                      'bool_arg': False
                  }
              },
              mb_lookup_failure=True,
          ),
          BuilderDataEntry(
              group='group2',
              bucket='bucket2',
              builder='builder2',
              sl_gn_args={
                  'phases': {
                      'phase1': {
                          'gn_args': {
                              'is_phase1': True,
                          }
                      },
                      'phase2': {
                          'gn_args': {
                              'is_phase2': True,
                          }
                      }
                  }
              },
              mb_gn_args={
                  'phases': {
                      'phase1': {
                          'gn_args': {
                              'is_phase1': True,
                          }
                      },
                      'phase2': {}
                  }
              },
              mb_lookup_phase_failure='phase2'),
      ]),
      api.post_process(post_process.StepSuccess,
                       'verify bucket1/builder1/gn-args.json'),
      api.post_process(post_process.StepTextEquals,
                       'verify bucket1/builder1/gn-args.json',
                       '<br/>could not load GN args from mb config, skip.'),
      api.post_process(post_process.StepSuccess,
                       'verify bucket2/builder2/gn-args.json.phase: phase2'),
      api.post_process(post_process.StepTextEquals,
                       'verify bucket2/builder2/gn-args.json.phase: phase2',
                       '<br/>could not load GN args from mb config, skip.'),
      api.post_process(post_process.DropExpectation),
      status='SUCCESS',
  )

  yield api.test(
      'mb_lookup_failure_with_patch_non_phased',
      common_test_data(api),
      gn_args_test_data(api, [
          BuilderDataEntry(
              group='group1',
              bucket='bucket1',
              builder='builder1',
              mb_lookup_failure_with_patch=True),
      ]),
      api.post_process(post_process.DropExpectation),
      status='FAILURE',
  )

  yield api.test(
      'mb_lookup_failure_with_patch_phased',
      common_test_data(api),
      gn_args_test_data(api, [
          BuilderDataEntry(
              group='group2',
              bucket='bucket2',
              builder='builder2',
              mb_lookup_failure_with_patch=True,
              sl_gn_args={
                  'phases': {
                      'phase1': {
                          'gn_args': {
                              'is_phase1': True,
                          }
                      },
                      'phase2': {
                          'gn_args': {
                              'is_phase2': True,
                          }
                      }
                  }
              },
              mb_gn_args={
                  'phases': {
                      'phase1': {
                          'gn_args': {
                              'is_phase1': True,
                          }
                      },
                      'phase2': {
                          'gn_args': {
                              'is_phase2': True,
                          }
                      }
                  }
              },
          ),
      ]),
      api.post_process(post_process.DropExpectation),
      status='FAILURE',
  )

  yield api.test(
      'add_and_remove_builders',
      common_test_data(api),
      gn_args_test_data(api, [
          BuilderDataEntry(
              group='group1',
              bucket='bucket1',
              builder='builder1',
              builder_removed=True,
          )
      ]),
      api.post_process(post_process.DropExpectation),
      status='SUCCESS',
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
      'gclient-config-not-set',
      api.buildbucket.try_build(),
      invalid_properties('gclient_config is not set'),
  )

  yield api.test(
      'builder-config-dir-not-set',
      api.buildbucket.try_build(),
      invalid_properties('builder_config_directory is not set'),
  )

  yield api.test(
      'mb-config-paths-not-set',
      api.buildbucket.try_build(),
      invalid_properties('mb_config_paths is not set'),
  )
