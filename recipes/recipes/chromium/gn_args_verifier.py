# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Checks GN args specified in starlark versus mb config file.

Once migration of all GN args to starlark is complete, this recipe will be
irrelevant.
"""

from collections.abc import Iterable
import contextlib
import difflib
import re
import typing

from recipe_engine import config_types
from recipe_engine import post_process
from recipe_engine import recipe_test_api
from recipe_engine.engine_types import StepPresentation

from RECIPE_MODULES.build import proto_validation
from RECIPE_MODULES.build.attr_utils import attrib, attrs
from RECIPE_MODULES.build.chromium_types import BuilderId

from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb
from PB.recipe_engine import result as result_pb
from PB.recipes.build.chromium import (gn_args_verifier as gn_args_verifier_pb)

PROPERTIES = gn_args_verifier_pb.InputProperties

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import chromium, gn
from RECIPE_MODULES.depot_tools import bot_update, gclient, tryserver
from RECIPE_MODULES.recipe_engine import (
    buildbucket,
    context,
    file,
    json,
    path,
    properties,
    raw_io,
    step,
)


@dataclass
class DEPS(RecipeScriptApi):
  bot_update: bot_update.API
  buildbucket: buildbucket.API
  chromium: chromium.API
  context: context.API
  file: file.API
  gclient: gclient.API
  gn: gn.API
  json: json.API
  path: path.API
  properties: properties.API
  raw_io: raw_io.API
  step: step.API
  tryserver: tryserver.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  buildbucket: buildbucket.TEST_API
  chromium: chromium.TEST_API
  file: file.TEST_API
  path: path.TEST_API
  properties: properties.TEST_API
  raw_io: raw_io.TEST_API
  step: step.TEST_API
  tryserver: tryserver.TEST_API


def _gn_args_file_re(builder_config_directory: str) -> re.Pattern:
  """Get a regular expression that will match a GN args file path.

  Returns:
    A pattern object that will match repo-root-relative paths that refer
    to GN args files. The pattern captures the repo-root-relative
    path to the directory that contains the gn-args.json of a single builder.
  """
  return re.compile(
      f'^{re.escape(builder_config_directory)}/([^/]+/[^/]+/gn-args\.json)')


def RunSteps(api: DEPS, properties):
  api.tryserver.require_is_tryserver()

  errors = VALIDATORS.validate(properties)
  if errors:
    return _result(
        status=common_pb.INFRA_FAILURE,
        elements=errors,
        header='The following errors were found with the input properties:')

  api.gclient.set_config(properties.gclient_config)
  api.chromium.set_config('chromium')

  with api.context(cwd=api.path.cache_dir / 'builder'):
    update_result = api.bot_update.ensure_checkout(patch=True)
  checkout_dir = update_result.checkout_dir
  source_dir = update_result.source_root.path
  patch_dir = update_result.patch_root.path

  # Absolute path of the per-builder config directory root.
  builder_config_root = patch_dir / properties.builder_config_directory

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

    with api.context(cwd=patch_dir):
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
                f'read {file_path}', patch_dir / f)

    failures = []

    gn_args_by_phase_by_builder_id: dict[BuilderId, _GnArgsByPhase] = {}
    file_path_by_builder_id = {}
    for file_path, gn_args_json in gn_args_json_by_file_path.items():
      builder_id = BuilderId.create_for_group(
          builder_group_by_gn_args_file_path[file_path],
          file_path.split('/')[1])
      file_path_by_builder_id[builder_id] = file_path

      gn_args_by_phase = _get_post_patch_gn_args(
          api,
          checkout_dir,
          source_dir,
          file_path,
          builder_id,
          gn_args_json,
          properties.mb_config_paths,
      )
      if gn_args_by_phase is not None:
        gn_args_by_phase_by_builder_id[builder_id] = gn_args_by_phase
      else:
        failures.append(str(builder_id))

  if failures:
    return _result(
        status=common_pb.FAILURE,
        elements=list(failures),
        header=('could not get GN args with patch applied'
                ' for the following builders:'))

  with api.context(cwd=checkout_dir):
    api.bot_update.deapply_patch(update_result)

  # Verify each builder.
  failures = []

  for builder_id, gn_args_by_phase in gn_args_by_phase_by_builder_id.items():
    file_path = file_path_by_builder_id[builder_id]
    step_name = f'verify {file_path}'

    if api.path.exists(builder_config_root / file_path):
      api.step.empty(
          name=step_name, step_text='builder already migrated, skip.')
      continue

    success = _verify_gn_args(
        api,
        checkout_dir,
        source_dir,
        step_name,
        builder_id,
        gn_args_by_phase,
        properties.mb_config_paths,
    )

    if not success:
      failures.append(str(builder_id))

  if failures:
    return _result(
        status=common_pb.FAILURE,
        elements=list(failures),
        header='verification failed for the following builders:')


class _GnArgsJson(typing.TypedDict, total=False):
  phases: dict[str, typing.Any]


# For non-phased builders, there will only be a single entry  with None as the
# key
_GnArgsByPhase = dict[str | None, str]


def _get_post_patch_gn_args(
    api: DEPS,
    checkout_dir: config_types.Path,
    source_dir: config_types.Path,
    step_name: str,
    builder_id: BuilderId,
    gn_args_json: _GnArgsJson,
    mb_config_paths: list[str],
) -> _GnArgsByPhase | None:
  """Get a builder's GN args with the patch applied.

  Args:
    api: The recipe API object.
    checkout_dir: The directory where the checkout was performed. The source
      should already be checked out, with the patch from the CL applied.
    step_name: The name of the step to use for getting the GN args.
    source_dir: The path to the top level repo.
    builder_id: The ID of the builder to get GN args for.
    gn_args_json: The json decoded contents of the builder's gn_args.json file.
    mb_config_paths: The paths to the MB config files to try to look up the
      builder's GN args in. The paths are relative to checkout_dir. It is an
      error if the builder isn't present in any of the config files.

  Returns:
    The GN args for each phase of the builder or None if the GN args couldn't be
    looked up for the builder (the builder isn't present in any of the config
    files or some other error occurs running mb lookup).
  """
  with api.step.nest(step_name) as presentation:
    phases = gn_args_json.get('phases') or [None]

    for mb_config_path in mb_config_paths:
      with api.step.nest(f'check in {mb_config_path}'):
        gn_args_by_phase = {}

        for phase in phases:
          suffix = '' if phase is None else f', phase: {phase}'
          try:
            gn_args = _mb_lookup(
                api,
                source_dir,
                f'mb lookup - {builder_id}{suffix}',
                builder_id,
                checkout_dir / mb_config_path,
                phase,
            )
          except api.step.StepFailure:
            return None

          # If the builder isn't present in the config file, we don't need to
          # check each phase
          if gn_args is None:
            presentation.step_text = f'{builder_id} is not present'
            break

          gn_args_by_phase[phase] = gn_args

        else:
          return gn_args_by_phase

    presentation.status = api.step.FAILURE
    presentation.step_text = 'not present in any of the config files'
    return None


def _verify_gn_args(
    api: DEPS,
    checkout_dir: config_types.Path,
    source_dir: config_types.Path,
    step_name: str,
    builder_id: BuilderId,
    post_patch_gn_args_by_phase: _GnArgsByPhase,
    mb_config_paths: list[str],
) -> bool:
  """Get a builder's GN args with the patch applied.

  Args:
    api: The recipe API object.
    checkout_dir: The directory where the checkout was performed. The source
      should already be checked out, without the patch from the CL applied.
    step_name: The name of the step to use for the verification.
    source_dir: The path to the top level repo.
    builder_id: The ID of the builder to verify GN args for.
    post_patch_gn_args_by_phase: The GN args for the builder with the patch
      applied for each phase.
    mb_config_paths: The paths to the MB config files to try to look up the
      builder's GN args in. The paths are relative to checkout_dir. It is an
      error if the builder isn't present in any of the config files.

  Returns:
    True if the verification succeeds (the GN args for each phase are equal or
    the builder doesn't exist in any of the config files). False if the
    verifications fails (there is a difference in GN args) or some error occurs
    when getting the GN args for the builder.
  """
  with api.step.nest(step_name) as presentation:
    for mb_config_path in mb_config_paths:
      with api.step.nest(f'check in {mb_config_path}') as file_presentation:

        success = True

        for phase, post_patch_args in post_patch_gn_args_by_phase.items():
          ctx = (
              contextlib.nullcontext(presentation)
              if phase is None else api.step.nest(f'phase: {phase}'))
          with ctx as phase_presentation:
            try:
              pre_patch_args = _mb_lookup(
                  api,
                  source_dir,
                  'mb lookup',
                  builder_id,
                  checkout_dir / mb_config_path,
                  phase,
              )
            except api.step.StepFailure:
              return False

            # If the builder isn't present in the config file, we don't need to
            # check each phase
            if pre_patch_args is None:
              file_presentation.step_text = f'{builder_id} is not present'
              break

            if not _compare_gn_args(api, pre_patch_args, post_patch_args,
                                    phase_presentation):
              success = False

        else:
          return success

    presentation.step_text = "new builder: wasn't present in any config file"
    return True


def _compare_gn_args(api: DEPS, pre_patch_args: str, post_patch_args: str,
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
    for line in diff[4:]:
      if not _check_diff_line(line):
        presentation.logs['diff'] = diff
        presentation.status = api.step.FAILURE
        presentation.step_text = 'GN args mismatch; see "diff" log for details'
        return False

  presentation.step_text = 'GN args match between starlark and mb config'
  return True


def _check_diff_line(line: str) -> bool:
  """ Checks if a gn_arg unified diff line is an acceptable change.

  Because mb_config.pyl did not require configs to explicitly list target_cpu
  and target_os and starlark requires those args to be explicit, a properly
  migrated list may not match exactly. This function checks lines of the
  unified diff of the gn args, and will verify that a line represents an exact
  match of gn args or that the only difference is the addition of target_os
  or target_cpu when those args did not exist in the mb_config.pyl file.

  Args:
    line: the unified diff line to check for gn_args mismatches.

  Returns:
    True if a line represents an exact match of args or if the line represents
      the addition of target_cpu or target_os when those args were not present
      in the original mb_config file.
    False in all other cases, including when target_cpu and target_os existed in
      the mb_config file and have been changed in the starlark file.
  """
  # Because starlark requires target_cpu and target_os, adding those args when
  # they did not exist before is fine.
  if line.startswith(('+target_cpu', '+target_os')):
    return True

  # Lines that do not start with + or - represent a match so those are fine.
  if not line.startswith(('+', '-')):
    return True

  # Otherwise a non permitted mismatch.
  return False


_UNKNOWN_BUILDER_RETCODE = 2


def _mb_lookup(
    api: DEPS,
    source_dir: config_types.Path,
    step_name: str,
    builder_id: BuilderId,
    mb_config_path: config_types.Path,
    phase: str | None,
) -> str | None:
  """Lookup the GN args for a builder.

  Args:
    api: The recipe API object.
    step_name: The name of the step to use for the lookup.
    builder_id: The ID of the builder to get GN args for.
    mb_config_paths: The path to the MB config file to lookup the builder's GN
      args in.
    phase: The phase of the GN args to look up.

  Returns:
    The GN args as returned by "mb lookup" if the builder is present in
    the config file. None if the builder is not present in the config
    file.

  Raises:
    StepFailure if some error occurs executing the lookup besides the builder
    not being present in the config file.
  """
  result = api.chromium.run_mb_cmd(
      step_name,
      'lookup',
      source_dir,
      builder_id,
      mb_config_path=mb_config_path,
      phase=phase,
      additional_args=['--quiet'],
      ok_ret=(0, _UNKNOWN_BUILDER_RETCODE),
      stdout=api.raw_io.output_text(add_output_log='on_failure'),
  )
  if result.retcode == _UNKNOWN_BUILDER_RETCODE:
    return None
  gn_args = result.presentation.step_text = f'\n{result.stdout}'
  return gn_args


VALIDATORS = proto_validation.Registry()


@VALIDATORS.register(gn_args_verifier_pb.InputProperties)
def _validate_properties(message, ctx):
  ctx.validate_field(message, 'gclient_config')
  ctx.validate_field(message, 'builder_config_directory')
  ctx.validate_repeated_field(message, 'mb_config_paths')


def _result(
    status: common_pb.Status,
    header: str,
    elements: Iterable[str],
) -> result_pb.RawResult:
  summary = [header, '']
  summary.extend('* {}'.format(e) for e in elements)
  return result_pb.RawResult(status=status, summary_markdown='\n'.join(summary))


def GenTests(api: TEST_DEPS):

  builder_config_dir = 'builder-config-dir'

  @attrs()
  class BuilderDataEntry:
    """ An object that contains test data for an individual builder."""

    # Builder group name
    group = attrib(str)

    # Bucket name
    bucket = attrib(str)

    # Builder name
    builder = attrib(str)

    # The mb config file that the builder should be present in, as a path
    # relative to the directory where the checkout would be performed
    mb_config_path = attrib(str, default=None)

    # Content of gn-args.json generated by Starlark definition.
    sl_gn_args = attrib(dict, default={'gn_args': {}})

    # GN args defined in mb config file, but stored in the same format as
    # the sl_gn_args dict.
    mb_gn_args = attrib(dict, default={'gn_args': {}})

    # Whether the builder is being removed in the patch.
    builder_removed = attrib(bool, default=False)

    # Whether the builder is being added in the patch
    builder_added = attrib(bool, default=False)

    # Whether the builder has already been migrated to Starlark GN args
    already_migrated = attrib(bool, default=False)

    # Whether the mb_lookup step will fail with patch applied
    mb_lookup_error_with_patch = attrib(bool, default=False)

    # Whether the mb_lookup step will fail without patch applied
    mb_lookup_error_without_patch = attrib(bool, default=False)

  def gn_args_test_data(
      builder_entries: Iterable[BuilderDataEntry],
      *,
      mb_config_paths: list[str] | None = None,
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

    if mb_config_paths is None:
      mb_config_paths = list({
          e.mb_config_path: None
          for e in builder_entries
          if e.mb_config_path is not None
      })

    data = api.properties(
        gn_args_verifier_pb.InputProperties(
            gclient_config='chromium',
            builder_config_directory=builder_config_dir,
            mb_config_paths=mb_config_paths))

    locations_json = {}
    gn_args_json_paths = []
    for builder_data in builder_entries:
      group = builder_data.group
      bucket = builder_data.bucket
      builder = builder_data.builder
      file_path = f'{bucket}/{builder}/gn-args.json'
      path_in_repo = f'{builder_config_dir}/{file_path}'
      gn_args_json_paths.append(path_in_repo)
      if builder_data.builder_removed:
        continue

      locations_json.setdefault(group, {})[builder] = file_path

      # Setup Starlark GN args test data
      sl_gn_args = builder_data.sl_gn_args

      data += api.step_data(
          'process data from patch.'
          f'parse gn-args.json files.read {file_path}',
          api.file.read_json(sl_gn_args))

      if 'phases' in sl_gn_args:
        sl_gn_args_and_suffixes = [
            (value['gn_args'], f', phase: {phase}')
            for phase, value in sl_gn_args['phases'].items()
        ]
      else:
        sl_gn_args_and_suffixes = [(sl_gn_args['gn_args'], '')]

      for mb_config_path in mb_config_paths:
        builder_in_config_file = mb_config_path == builder_data.mb_config_path
        for gn_args, step_name in sl_gn_args_and_suffixes:
          step_name = (
              f'process data from patch.{bucket}/{builder}/gn-args.json'
              f'.check in {mb_config_path}'
              f'.mb lookup - {group}:{builder}{step_name}')
          # We'll only check a single phase for a builder that's not in the
          # config file
          if not builder_in_config_file:
            data += api.step_data(step_name, retcode=_UNKNOWN_BUILDER_RETCODE)
            break
          if builder_data.mb_lookup_error_with_patch:
            data += api.step_data(step_name, retcode=1)
            break
          data += api.step_data(
              step_name,
              stdout=api.raw_io.output_text(dict_to_gn_args_str(gn_args)))
        # Once the config file containing the builder is found, we don't check
        # remaining files
        if builder_in_config_file:
          break

      # Verification doesn't happen if there was an error when looking up GN
      # args or if the builder was never found in any config file
      if (builder_data.mb_lookup_error_with_patch or
          builder_data.mb_config_path not in mb_config_paths):
        continue

      # Setup mb config GN args test data
      if builder_data.already_migrated:
        data += api.path.exists(api.path.cache_dir / 'builder/src' /
                                path_in_repo)
        data += api.post_process(post_process.StepTextEquals,
                                 f'verify {bucket}/{builder}/gn-args.json',
                                 'builder already migrated, skip.')
        continue

      mb_gn_args = builder_data.mb_gn_args
      if 'phases' in mb_gn_args:
        mb_gn_args_and_phase_steps = [
            (value['gn_args'], f'.phase: {phase}')
            for phase, value in mb_gn_args['phases'].items()
        ]
      else:
        mb_gn_args_and_phase_steps = [(mb_gn_args['gn_args'], '')]

      for mb_config_path in mb_config_paths:
        builder_in_config_file = (
            mb_config_path == builder_data.mb_config_path and
            not builder_data.builder_added)
        for gn_args, phase_step in mb_gn_args_and_phase_steps:
          step_name = (f'verify {bucket}/{builder}/gn-args.json'
                       f'.check in {mb_config_path}'
                       f'{phase_step}.mb lookup')
          # We'll only check a single phase for a builder that's not in the
          # config file
          if not builder_in_config_file:
            data += api.step_data(step_name, retcode=_UNKNOWN_BUILDER_RETCODE)
            break
          if builder_data.mb_lookup_error_without_patch:
            data += api.step_data(step_name, retcode=1)
            break
          data += api.step_data(
              step_name,
              stdout=api.raw_io.output_text(dict_to_gn_args_str(gn_args)))
        # Once the config file containing the builder is found, we don't check
        # remaining files
        if builder_in_config_file:
          break

    data += api.tryserver.get_files_affected_by_patch(
        [*gn_args_json_paths],
        step_name=('process data from patch.git diff to analyze patch'))
    data += api.step_data(
        'process data from patch.'
        'read gn_args_locations.json', api.file.read_json(locations_json))
    return data

  yield api.test(
      'basic',
      api.buildbucket.try_build(),
      gn_args_test_data([
          BuilderDataEntry(
              group='group1',
              bucket='bucket1',
              builder='builder1',
              mb_config_path='src/tools/mb/mb_config.pyl',
              sl_gn_args={
                  'gn_args': {
                      'str_arg': 'test1',
                      'int_arg': 1,
                      'bool_arg': False,
                      # Adding target_cpu and/or target_os when that arg did not
                      # exist before should be considered a match since starlark
                      # requires those to be explicitly set.
                      'target_cpu': 'CPU',
                      'target_os': 'OS'
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
              mb_config_path='src/internal/tools/mb/mb_config.pyl',
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
      api.buildbucket.try_build(),
      gn_args_test_data([
          BuilderDataEntry(
              group='group1',
              bucket='bucket1',
              builder='builder1',
              mb_config_path='src/tools/mb/mb_config.pyl',
              sl_gn_args={
                  'gn_args': {
                      'str_arg': 'test1',
                      'int_arg': 1,
                      'bool_arg': False,
                      # Overwriting an existing target_cpu or target_os should
                      # be considered a gn args mismatch.
                      'target_cpu': 'cpu2',
                      'target_os': 'os2'
                  }
              },
              mb_gn_args={
                  'gn_args': {
                      'str_arg': 'test1',
                      'int_arg': 2,
                      'bool_arg': False,
                      'target_cpu': 'cpu1',
                      'target_os': 'os1'
                  }
              },
          ),
          BuilderDataEntry(
              group='group2',
              bucket='bucket2',
              builder='builder2',
              mb_config_path='src/internal/tools/mb/mb_config.pyl',
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
      api.buildbucket.try_build(),
      gn_args_test_data([
          BuilderDataEntry(
              group='group1',
              bucket='bucket1',
              builder='builder1',
              mb_config_path='src/tools/mb/mb_config.pyl',
              sl_gn_args={
                  'gn_args': {
                      'str_arg': 'test1',
                      'int_arg': 1,
                      'bool_arg': False
                  }
              },
              already_migrated=True,
          )
      ]),
      api.post_process(post_process.DropExpectation),
      status='SUCCESS',
  )

  yield api.test(
      'builder-not-in-any-config-files',
      api.buildbucket.try_build(),
      gn_args_test_data(
          [
              BuilderDataEntry(
                  group='group1', bucket='bucket1', builder='builder1'),
          ],
          mb_config_paths=['src/tools/mb/mb_config.pyl'],
      ),
      api.post_check(post_process.DoesNotRun, 'bot_update (without patch)'),
      api.post_process(post_process.DropExpectation),
      status='FAILURE',
  )

  yield api.test(
      'mb-lookup-error-with-patch',
      api.buildbucket.try_build(),
      gn_args_test_data([
          BuilderDataEntry(
              group='group1',
              bucket='bucket1',
              builder='builder1',
              mb_config_path='src/tools/mb/mb_config.pyl',
              mb_lookup_error_with_patch=True),
      ]),
      api.post_check(post_process.DoesNotRun, 'bot_update (without patch)'),
      api.post_process(post_process.DropExpectation),
      status='FAILURE',
  )

  yield api.test(
      'mb-lookup-error-without-patch',
      api.buildbucket.try_build(),
      gn_args_test_data([
          BuilderDataEntry(
              group='group1',
              bucket='bucket1',
              builder='builder1',
              mb_config_path='src/tools/mb/mb_config.pyl',
              sl_gn_args={'gn_args': {}},
              mb_lookup_error_without_patch=True),
      ]),
      api.post_process(post_process.DropExpectation),
      status='FAILURE',
  )

  yield api.test(
      'added-builder',
      api.buildbucket.try_build(),
      gn_args_test_data([
          BuilderDataEntry(
              group='group1',
              bucket='bucket1',
              builder='builder1',
              mb_config_path='src/tools/mb/mb_config.pyl',
              builder_added=True,
          )
      ]),
      api.post_check(post_process.StepTextEquals,
                     'verify bucket1/builder1/gn-args.json',
                     "new builder: wasn't present in any config file"),
      api.post_process(post_process.DropExpectation),
      status='SUCCESS',
  )

  yield api.test(
      'removed-builder',
      api.buildbucket.try_build(),
      gn_args_test_data(
          [
              BuilderDataEntry(
                  group='group1',
                  bucket='bucket1',
                  builder='builder1',
                  builder_removed=True,
              )
          ],
          mb_config_paths=['src/tools/mb/mb_config.pyl'],
      ),
      api.post_process(post_process.DropExpectation),
      status='SUCCESS',
  )

  # Must appear last since it drops expectations
  def invalid_properties(*errors):
    test_data = api.expect_status('INFRA_FAILURE')
    test_data += api.post_check(
        post_process.SummaryMarkdownRE,
        '^The following errors were found with the input properties')
    for error in errors:
      test_data += api.post_check(post_process.SummaryMarkdownRE, error)
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
      invalid_properties('mb_config_paths is empty'),
  )
