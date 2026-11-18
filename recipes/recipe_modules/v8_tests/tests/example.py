# Copyright 2022 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import json
import shlex

from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb
from PB.go.chromium.org.luci.lucictx import sections as sections_pb2
from PB.recipe_engine import result as result_pb2
from recipe_engine.post_process import (
  DoesNotRun,
  DropExpectation,
  LogContains,
  LogDoesNotContain,
  StepFailure,
)

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import v8_tests
from RECIPE_MODULES.recipe_engine import (
  buildbucket,
  context,
  json as json_module,
  path,
  properties,
  runtime,
  swarming,
)


@dataclass
class DEPS(RecipeScriptApi):
  buildbucket: buildbucket.API
  json: json_module.API
  path: path.API
  properties: properties.API
  runtime: runtime.API
  swarming: swarming.API
  v8_tests: v8_tests.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  buildbucket: buildbucket.TEST_API
  context: context.TEST_API
  json: json_module.TEST_API
  properties: properties.TEST_API
  swarming: swarming.TEST_API
  v8_tests: v8_tests.TEST_API


def RunSteps(api: DEPS):
  api.v8_tests.set_config('v8')
  api.v8_tests.enable_swarming = api.properties.get('enable_swarming', True)
  api.v8_tests.set_up_swarming()
  api.v8_tests.read_cl_footer_flags()
  api.v8_tests.load_static_test_configs()
  source_dir = api.path.cache_dir / 'builder' / 'src'

  tests = api.v8_tests.extra_tests_from_properties()
  result = api.v8_tests.runtests(source_dir, tests)
  status = common_pb.FAILURE if result.is_negative else common_pb.SUCCESS
  return result_pb2.RawResult(status=status)


def _parse_repro_cmd_lines(check, steps, step_name, log_name):
  """Parses the command lines suggested for reproducing a failure like a
  shell.

  Returns: The arguments passed to the V8 test runner for the local
      reproduction and the dict of properties passed to flako.
  """
  lines = steps[step_name].logs[log_name].splitlines()

  def parse_cmd_line(header):
    return shlex.split(lines[lines.index(header) + 1])

  local_cmd = parse_cmd_line('Local flake reproduction on command line:')
  check(local_cmd[0] == 'tools/run-tests.py')

  flako_cmd = parse_cmd_line('Trigger flake bisect on command line:')
  check(flako_cmd[:3] == ['bb', 'add', 'v8/try.triggered/v8_flako'])
  flags, properties = flako_cmd[3::2], flako_cmd[4::2]
  check(len(flags) == len(properties))
  check(all(flag == '-p' for flag in flags))
  properties = dict(prop.split('=', 1) for prop in properties)
  return local_cmd[1:], {k: json.loads(v) for k, v in properties.items()}


def _check_repro_cmd_lines(check, steps, step_name, log_name, test_name):
  """Checks that the command lines suggested for reproducing a failure pass
  the test name as one literal argument when copied into a shell.
  """
  local_args, properties = _parse_repro_cmd_lines(
    check, steps, step_name, log_name
  )
  check(all(arg.startswith('--') for arg in local_args[:-1]))
  check(local_args[-1] == test_name)
  check(properties['test_name'] == test_name)
  check(properties['variant'] == 'default')


def _extra_flags(args):
  """Returns the flags that the V8 test runner passes on to a test, when
  called with the given arguments. The test runner splits the value of each
  --extra-flags option like a shell.
  """
  flags = []
  for option, value in zip(args, args[1:]):
    if option == '--extra-flags':
      flags += shlex.split(value)
  return flags


def _check_num_fuzzer_flags(check, steps, step_name, log_name, flags):
  """Checks that the command lines suggested for reproducing a failure of the
  number fuzzer make the V8 test runner pass on the fuzzer's flags unchanged.
  """
  local_args, properties = _parse_repro_cmd_lines(
    check, steps, step_name, log_name
  )
  check(_extra_flags(local_args) == flags)
  check(_extra_flags(properties['extra_args']) == flags)


def GenTests(api: TEST_DEPS):
  # Minimal v8-side test spec for simulating most recipe features.
  test_spec = json.dumps(
    {
      "tests": [
        {"name": "v8testing"},
        {"name": "test262", "test_args": ["--extra-flags=--flag"]},
      ],
    },
    indent=2,
  )
  parent_test_spec = api.v8_tests.example_parent_test_spec_properties(
    'v8_foobar_rel_ng_triggered', test_spec
  )
  buider_spec = parent_test_spec.get('parent_test_spec', {})
  swarm_hashes = api.v8_tests._make_dummy_swarm_hashes(
    test[0] for test in buider_spec.get('tests', [])
  )

  yield api.test(
    'basic',
    api.buildbucket.try_build(project='v8'),
    api.properties(swarm_hashes=swarm_hashes, **parent_test_spec),
    api.post_process(
      api.swarming.check_triggered_request,
      'trigger tests.[trigger] Check on Ubuntu-16.04',
      lambda check, req: check(
        ('pool', 'chromium.tests') in req[0].dimensions.items()
      ),
      lambda check, req: check(
        ('os', 'Ubuntu-16.04') in req[0].dimensions.items()
      ),
      lambda check, req: check('project:v8' in req.tags),
    ),
    api.post_process(
      LogContains,
      'trigger tests.[trigger] Test262 on Ubuntu-16.04',
      'json.input',
      ['"--extra-flags=--flag",'],
    ),
    api.post_process(DropExpectation),
  )

  # Builders without ResultDB integration (e.g. on the dev infrastructure)
  # must neither wrap the test command with rdb nor include invocations.
  yield api.test(
    'resultdb_disabled',
    api.buildbucket.try_build(project='v8'),
    api.context.luci_context(resultdb=sections_pb2.ResultDB()),
    api.properties(swarm_hashes=swarm_hashes, **parent_test_spec),
    api.post_process(
      api.swarming.check_triggered_request,
      'trigger tests.[trigger] Check on Ubuntu-16.04',
      lambda check, req: check(not req.resultdb.enable),
      lambda check, req: check('rdb' not in req[0].command),
    ),
    api.post_process(
      DoesNotRun, 'trigger tests.resultdb.update_included_invocations'
    ),
    api.post_process(DropExpectation),
  )

  yield api.test(
    'cl_with_resultdb_footer',
    api.buildbucket.try_build(),
    api.properties(swarm_hashes=swarm_hashes, **parent_test_spec),
    api.step_data(
      'parse description', api.json.output({'V8-Recipe-Flags': ['resultdb']})
    ),
    api.post_process(
      LogContains,
      'parse description',
      'json.output',
      [
        '"V8-Recipe-Flags":',
        '"resultdb"',
      ],
    ),
    api.post_process(DropExpectation),
  )

  yield api.test(
    'local_test_failure',
    api.buildbucket.try_build(),
    api.properties(enable_swarming=False, **parent_test_spec),
    api.step_data('Check', retcode=1),
    api.post_process(StepFailure, 'Check'),
    api.post_process(DropExpectation),
  )

  yield api.test(
    'local_other_failure',
    api.buildbucket.try_build(),
    api.properties(enable_swarming=False, **parent_test_spec),
    api.step_data('Check', retcode=2),
    api.post_process(StepFailure, 'Check'),
    api.post_process(DropExpectation),
    status='FAILURE',
  )

  yield api.test(
    'exonerated_flake',
    api.buildbucket.try_build(),
    api.properties(swarm_hashes=swarm_hashes, **parent_test_spec),
    api.step_data(
      'Check',
      api.v8_tests.flakes(
        2, crash_type='_Magically_exonerated_crash_type_for_testing_'
      ),
    ),
    api.post_process(DropExpectation),
    status='SUCCESS',
  )

  # Test results are read from the untrusted output of swarming tasks. The
  # command lines suggested for reproducing a failure must not let such data
  # break out into the shell.
  malicious_name = 'suite-name/dir/test\'; echo pwned | sh #'
  yield api.test(
    'repro_cmd_lines_are_quoted',
    api.buildbucket.ci_build(project='v8'),
    api.properties(swarm_hashes=swarm_hashes, **parent_test_spec),
    api.step_data('Check', api.v8_tests.one_failure(name=malicious_name)),
    api.post_process(
      _check_repro_cmd_lines,
      'Check',
      'test\'; echo pwned | sh #',
      malicious_name,
    ),
    api.post_process(DropExpectation),
    status='FAILURE',
  )

  # The suggested command lines must make the V8 test runner pass on the
  # flags used by the number fuzzer unchanged. This covers all forms of flags
  # that are passed on: with and without a value, with underscores and with
  # a negative number.
  num_fuzz_flags = [
    '--stress-gc=4',
    '--no-enable-sse4_2',
    '--cache=code',
    '--fuzzer-random-seed=-2147483648',
  ]
  yield api.test(
    'num_fuzzer_flags_in_repro_cmd_lines',
    api.buildbucket.ci_build(project='v8'),
    api.properties(swarm_hashes=swarm_hashes, **parent_test_spec),
    api.step_data(
      'Check',
      api.v8_tests.one_flake(num_fuzz=True, num_fuzz_flags=num_fuzz_flags),
    ),
    api.post_process(
      _check_num_fuzzer_flags,
      'Check (flakes)',
      'test-name-0',
      num_fuzz_flags,
    ),
    api.post_process(DropExpectation),
    status='FAILURE',
  )

  # The flags used by the number fuzzer are read from the untrusted output of
  # swarming tasks. Command lines are only suggested if all of them have the
  # form --name or --name=value without special characters, and none of them
  # lets code execute system commands. Everything else could e.g. make d8
  # execute code passed on the command line or a script, or access a path.
  for i, unsafe_flags in enumerate(
    [
      # Code passed on the command line.
      ['--flag1', '-e', ' var __pv_0;'],
      # Flags that are unsafe despite their form, also in other spellings.
      ['--enable-os-system', '--flag1'],
      ['--enable_os_system=true'],
      ['--e'],
      # Not the exact form of a flag.
      [' --e'],
      ['--flag1\n'],
      ['-flag1'],
      ['--flag1='],
      ['--flag1=a b'],
      ['--flag1=a\'b'],
      # Scripts, which d8 executes.
      ['--flag1', 'script.js'],
      ['/tmp/script.js'],
      # Values that are paths.
      ['--snapshot-blob=/tmp/blob'],
      ['--lcov=..\\lcov'],
    ]
  ):
    yield api.test(
      f'no_repro_cmd_lines_for_unsafe_num_fuzzer_flags_{i}',
      api.buildbucket.ci_build(project='v8'),
      api.properties(swarm_hashes=swarm_hashes, **parent_test_spec),
      api.step_data(
        'Check',
        api.v8_tests.one_flake(num_fuzz=True, num_fuzz_flags=unsafe_flags),
      ),
      api.post_process(
        LogContains,
        'Check (flakes)',
        'test-name-0',
        ['not limited to plain --name or --name=value flags'],
      ),
      api.post_process(
        LogDoesNotContain,
        'Check (flakes)',
        'test-name-0',
        ['bb add', 'tools/run-tests.py', '--extra-flags'],
      ),
      api.post_process(DropExpectation),
      status='FAILURE',
    )

  intl_test_spec = json.dumps(
    {
      "tests": [
        {"name": "intl"},
      ],
    },
    indent=2,
  )
  intl_parent_test_spec = api.v8_tests.example_parent_test_spec_properties(
    'v8_foobar_rel_ng_triggered', intl_test_spec
  )
  intl_swarm_hashes = api.v8_tests._make_dummy_swarm_hashes(['intl'])

  yield api.test(
    'intl',
    api.buildbucket.try_build(),
    api.properties(swarm_hashes=intl_swarm_hashes, **intl_parent_test_spec),
    api.post_process(
      LogContains,
      'trigger tests.[trigger] Intl on Ubuntu-16.04',
      'json.input',
      ['"intl"'],
    ),
    api.post_process(DropExpectation),
  )
