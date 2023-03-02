# Copyright 2022 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from PB.recipes.build.gofindit.chromium.single_revision import InputProperties
from RECIPE_MODULES.build import chromium
from RECIPE_MODULES.build import chromium_tests_builder_config as ctbc
from recipe_engine.post_process import (DoesNotRun, DropExpectation, MustRun,
                                        StepCommandContains, StatusFailure,
                                        StatusSuccess)
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb
from PB.recipe_engine import result as result_pb

DEPS = [
    'chromium',
    'chromium_swarming',
    'chromium_tests',
    'chromium_tests_builder_config',
    'findit',
    'gofindit',
    'goma',
    'recipe_engine/step',
    'recipe_engine/properties',
]

PROPERTIES = InputProperties


def RunSteps(api, properties):
  try:
    # If any error happens, send INFRA_FAILURE back to LUCI Bisection
    compile_status = common_pb.INFRA_FAILURE

    # Configure the builder
    builder_id, builder_config = _configure_builder(api,
                                                    properties.target_builder,
                                                    properties.should_clobber)

    # Check out the code.
    bot_update_step, build_config = api.chromium_tests.prepare_checkout(
        builder_config, set_output_commit=False)
    api.chromium_swarming.configure_swarming('chromium', precommit=False)

    # Since these builders run on different platforms, and require different Goma
    # settings depending on the platform, set the Goma ATS flag based on the OS.
    api.goma.configure_enable_ats()

    compile_targets = tuple(properties.compile_targets)
    if not compile_targets:
      # If compile_targets is not there, compile all targets
      compile_targets = build_config.compile_targets

    compile_result, _ = api.chromium_tests.compile_specific_targets(
        builder_id,
        builder_config,
        bot_update_step,
        build_config,
        compile_targets,
        override_execution_mode=ctbc.COMPILE_AND_TEST,
        tests=[],
    )
    compile_status = compile_result.status
    markdown = "LUCI Bisection's compile result from the input commit was {0}.".format(
        api.gofindit.rerun_result(compile_status))
    return result_pb.RawResult(
        status=compile_result.status, summary_markdown=(markdown))
  finally:
    api.gofindit.send_result_to_luci_bisection("send_result_to_luci_bisection",
                                               properties.analysis_id,
                                               compile_status,
                                               properties.bisection_host)


def _configure_builder(api, target_builder, should_clobber):
  target_builder_id = chromium.BuilderId.create_for_group(
      target_builder.group, target_builder.builder)
  # TODO: replace this with the polymorphic API when it is ready (go/test-reviver-builders-dd)
  builder_config = api.findit.get_builder_config(target_builder_id)
  api.chromium_tests.configure_build(builder_config)

  # If there is a problem with goma, rather than default to compiling locally
  # only, fail. This is important because findit relies on fast compile for
  # timely production of actionable changes, and local compilation alone is
  # unlikely to help findit find a culprit in time for automatic revert.
  # Better to fail the analysis and let the sheriffs try to find a culprit
  # manually.
  api.chromium.apply_config('goma_failfast')
  if should_clobber:
    api.chromium.c.clobber_before_runhooks = True

  return builder_config.builder_ids[0], builder_config


def GenTests(api):

  def setup(api,
            target_builder_group='fake-group',
            target_builder='fake-builder',
            should_clobber=False):
    """Create test properties and other data for tests."""
    _default_spec = 'fake-group', {'fake-builder': {}}
    _default_builders = ctbc.BuilderDatabase.create({
        'fake-group': {
            'fake-builder':
                ctbc.BuilderSpec.create(
                    chromium_config='chromium',
                    chromium_apply_config=['mb'],
                    gclient_config='chromium',
                    chromium_config_kwargs={
                        'BUILD_CONFIG': 'Release',
                        'TARGET_BITS': 64,
                    },
                    simulation_platform='linux',
                ),
        },
    })

    props_proto = InputProperties()
    props_proto.target_builder.group = target_builder_group
    props_proto.target_builder.builder = target_builder
    props_proto.should_clobber = should_clobber

    t = sum([
        api.chromium.ci_build(
            builder_group=target_builder_group,
            builder=target_builder,
        ),
        api.properties(props_proto),
        api.chromium_tests.read_targets_spec(*_default_spec),
        api.chromium_tests_builder_config.databases(_default_builders),
    ], api.empty_test_data())
    return t

  yield api.test(
      'compile',
      setup(api),
      api.post_process(MustRun, 'bot_update'),
      api.post_process(MustRun, 'compile'),
      api.post_process(MustRun, 'send_result_to_luci_bisection'),
      api.post_process(StatusSuccess),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'compile with clobber',
      setup(api, should_clobber=True),
      api.post_process(MustRun, 'bot_update'),
      api.post_process(MustRun, 'clobber'),
      api.post_process(MustRun, 'compile'),
      api.post_process(MustRun, 'send_result_to_luci_bisection'),
      api.post_process(StatusSuccess),
      api.post_process(DropExpectation),
  )
