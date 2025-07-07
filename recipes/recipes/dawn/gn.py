# Copyright 2018 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Buildbot recipe to build and test Dawn standalone using GN.
"""

DEPS = [
    'depot_tools/bot_update',
    'depot_tools/depot_tools',
    'depot_tools/gclient',
    'depot_tools/gsutil',
    'depot_tools/osx_sdk',
    'recipe_engine/buildbucket',
    'recipe_engine/context',
    'recipe_engine/file',
    'recipe_engine/json',
    'recipe_engine/nodejs',
    'recipe_engine/path',
    'recipe_engine/platform',
    'recipe_engine/properties',
    'recipe_engine/step',
    'recipe_engine/time',
    'siso',
]

import hashlib
from contextlib import contextmanager
from recipe_engine.recipe_api import Property

PROPERTIES = {
    'target_cpu': Property(default=None, kind=str),
    'debug': Property(default=False, kind=bool),
    'clang': Property(default=None, kind=bool),
    'gen_fuzz_corpus': Property(default=False, kind=bool)
}

DAWN_REPO = "https://dawn.googlesource.com/dawn"


def _checkout_steps(api, build_dawn_node):
  solution_path = api.path.cache_dir / 'builder'
  api.file.ensure_directory('init cache if not exists', solution_path)

  with api.context(cwd=solution_path):
    # Checkout dawn and its dependencies (specified in DEPS) using gclient.
    api.gclient.set_config('dawn')
    api.gclient.c.got_revision_mapping['dawn'] = 'got_revision'
    # Standalone developer dawn builds want the dawn checkout in the same
    # directory the .gclient file is in.  Bots want it in a directory called
    # 'dawn'.  To make both cases work, the dawn DEPS file pulls deps and runs
    # hooks relative to the variable "root" which is set to . by default and
    # then to 'dawn' on bots here:
    api.gclient.c.solutions[0].custom_vars = {
        'dawn_root': 'dawn',
        'dawn_node': 'True' if build_dawn_node else 'False',
    }
    if api.siso.enabled:
      api.siso.enable_download_remoteexec_cfg_hook()
    update_result = api.bot_update.ensure_checkout()
    api.gclient.runhooks()
  return update_result

# Make a GN build for Dawn using **kwargs as the GN args.
# Yields a function `build` which compiles the targets specified by *args and
# returns a tuple of the file paths where those targets' executables should be located.
@contextmanager
def _gn_build(source_dir, flavor, api, **kwargs):
  use_remoteexec = kwargs['is_clang'] is True or kwargs['is_clang'] is None
  gn_args = []
  for key, value in kwargs.items():
    if value is None:
      continue
    if isinstance(value, str):
      value = api.json.dumps(value)
    elif isinstance(value, bool):
      value = 'true' if value else 'false'
    gn_args.append('%s=%s' % (key, value))

  gn_args.extend([
      'tint_build_spv_reader=true',
      'tint_build_spv_writer=true',
      'tint_build_wgsl_reader=true',
      'tint_build_wgsl_writer=true',
      'tint_build_msl_writer=true',
      'tint_build_hlsl_writer=true',
      'use_siso=true',
  ])

  if use_remoteexec:
    gn_args.append('use_remoteexec=true')
    gn_args.append('use_reclient=false')

  # We run the end2end tests with SwiftShader, but the D3D12 backend,
  # though it would run zero tests, crashes on Windows 7.
  # Disable it for now.
  if kwargs['dawn_use_swiftshader']:
    gn_args.append('dawn_enable_d3d12=false')

  # Create a unique outdir name.
  out_dir = hashlib.sha1(api.json.dumps(
      kwargs, sort_keys=True).encode('utf8')).hexdigest()

  gn_cmd = api.depot_tools.gn_py_path
  with api.context(cwd=source_dir):
    api.step('gn gen', [
        'python3',
        gn_cmd,
        '--root=' + str(source_dir),
        'gen',
        '//out/' + out_dir,
        '--args=' + ' '.join(gn_args),
    ])

  build_path = source_dir.joinpath('out', out_dir)
  base_ninja_cmd = ['ninja', '-C', build_path]

  def build(*targets):
    ninja_cmd = base_ninja_cmd.copy()
    ninja_cmd.extend(targets)
    desc = 'compile {} with siso'.format(flavor)
    api.siso.run_ninja(source_dir, ninja_cmd, name=desc)

    return tuple(build_path / t for t in targets)

  yield build


def _generate_fuzz_corpus(api, source_dir, **kwargs):
  # SwiftShader builds must be a component build
  kwargs.update({
      'is_component_build': True,
      'dawn_use_swiftshader': True,
  })
  with _gn_build(source_dir, 'dawn tests', api, **kwargs) as build:
    (dawn_unittests,
     dawn_end2end_tests_swiftshader) = build('dawn_unittests',
                                             'dawn_end2end_tests')
  # Collect the traces in temporary directories.
  testcase_dir = api.path.tmp_base_dir / 'testcases'
  hashed_testcase_dir = api.path.tmp_base_dir / 'hashed_testcases'

  api.file.ensure_directory('mkdir {}'.format(testcase_dir), testcase_dir)
  api.file.ensure_directory('mkdir {}'.format(hashed_testcase_dir),
                            hashed_testcase_dir)

  api.step('Trace the dawn_unittests', [
      dawn_unittests, '--use-wire', '--wire-trace-dir={}'.format(testcase_dir)
  ])

  api.step('Trace the dawn_end2end_tests with SwiftShader', [
      dawn_end2end_tests_swiftshader, '--adapter-vendor-id=0x1AE0',
      '--use-wire', '--wire-trace-dir={}'.format(testcase_dir)
  ])

  testcases = api.file.listdir('listdir {}'.format(testcase_dir), testcase_dir)

  # Hash the traces so we have a unique name per trace.
  cmd = ([
      'python3',
      '-u',
      api.resource('hash_testcases.py'),
      hashed_testcase_dir,
  ] + testcases)
  api.step('Hash testcases', cmd)

  # Upload test cases to the fuzzer corpus directories
  for fuzzer_name in [
      'dawn_wire_server_and_frontend_fuzzer',
      'dawn_wire_server_and_vulkan_backend_fuzzer',
      'dawn_wire_server_and_d3d12_backend_fuzzer'
  ]:
    api.gsutil.upload(
        hashed_testcase_dir.joinpath('*'),
        'clusterfuzz-corpus',
        'libfuzzer/{}'.format(fuzzer_name),
        args=['-r', '-n'],  # recursive, no clobber
        parallel_upload=True,
        multithreaded=True,
        name='Upload to the {} seed corpus'.format(fuzzer_name))


def _run_dawn_node_test(api, source_dir, dawn_node_exe,
                        dawn_node_uses_swiftshader):
  with api.step.nest('Run basic CTS test with dawn.node') as _, \
    api.nodejs('16.13.0') as _:
    # Install node deps in webgpu-cts directory
    cts_dir = source_dir.joinpath('third_party', 'webgpu-cts')
    with api.context(cwd=cts_dir):
      api.step('Install CTS dependencies',
               ['npm.cmd' if api.platform.is_win else 'npm', 'install'])
    # Run test using run-cts
    go_path = source_dir.joinpath('tools', 'golang', 'bin')
    with api.context(cwd=source_dir) as _, \
        api.context(env_prefixes={'PATH': [go_path]}) as _:
      case = 'webgpu:api,operation,adapter,requestDevice:default:*'
      # Run shell scripts with bash on Windows
      shell_wrapper = ('bash', '--') if api.platform.is_win else ()
      cmd = ['tools/run', 'run-cts', '-bin', api.path.dirname(dawn_node_exe)]
      if dawn_node_uses_swiftshader:
        cmd.extend(['-backend', 'vulkan', '-adapter', 'SwiftShader'])
      else:
        cmd.extend(
            ['-backend', 'd3d12', '-adapter', 'Microsoft Basic Render Driver'])
      cmd.append(case)
      api.step(case, cmd, wrapper=shell_wrapper)

def RunSteps(api, target_cpu, debug, clang, gen_fuzz_corpus):
  # dawn.node not currently supported on x86
  build_dawn_node = target_cpu != 'x86'
  # On Windows, we use D3D12 WARP for dawn_node, otherwise we use SwiftShader
  dawn_node_uses_swiftshader = not api.platform.is_win

  update_result = _checkout_steps(api, build_dawn_node)
  source_dir = update_result.source_root.path
  if gen_fuzz_corpus:
    _generate_fuzz_corpus(
        api, source_dir, target_cpu=target_cpu, is_debug=debug, is_clang=clang)
    return

  with api.osx_sdk('mac'):
    # Win/MSVC builds need this GN arg set to false in order for MSVC
    # builds to work. See the discussion on
    # https://dawn-review.googlesource.com/c/dawn/+/222337 for more context.
    extra_gn_args = {}
    if api.platform.is_win and not clang:
      extra_gn_args['use_custom_libcxx'] = False

    with _gn_build(
        source_dir,
        'default targets',
        api,
        target_cpu=target_cpu,
        is_debug=debug,
        is_clang=clang,
        is_component_build=False,
        dawn_use_swiftshader=False,
        dawn_build_node_bindings=build_dawn_node and
        not dawn_node_uses_swiftshader,
        **extra_gn_args,
    ) as build:
      # Build default targets, and specifically the unittest binaries.
      (_, dawn_unittests, tint_unittests) = build('default', 'dawn_unittests',
                                                  'tint_unittests')
      if api.platform.is_win and not debug:
        (dawn_end2end_tests_warp,) = build('dawn_end2end_tests')
      if build_dawn_node and not dawn_node_uses_swiftshader:
        (dawn_node,) = build('dawn_node')

    if not api.platform.is_win:
      with _gn_build(
          source_dir,
          'fuzzer targets',
          api,
          target_cpu=target_cpu,
          is_debug=debug,
          is_clang=clang,
          is_component_build=False,
          dawn_use_swiftshader=False,
          use_libfuzzer=True,
          **extra_gn_args,
      ) as build:
        build('fuzzers')

    # Component build and run dawn_end2end_tests with SwiftShader
    # When using SwiftShader a component build should be used.
    # See anglebug.com/4396.
    with _gn_build(
        source_dir,
        'component build with Swiftshader',
        api,
        target_cpu=target_cpu,
        is_debug=debug,
        is_clang=clang,
        is_component_build=True,
        dawn_use_swiftshader=True,
        dawn_build_node_bindings=build_dawn_node and dawn_node_uses_swiftshader,
        **extra_gn_args,
    ) as build:
      (dawn_end2end_tests_swiftshader,) = build('dawn_end2end_tests')
      if build_dawn_node and dawn_node_uses_swiftshader:
        (dawn_node,) = build('dawn_node')

  if build_dawn_node:
    _run_dawn_node_test(api, source_dir, dawn_node, dawn_node_uses_swiftshader)

  api.step('Run the Dawn unittests', [dawn_unittests])
  api.step('Run the Dawn unittests with the wire',
           [dawn_unittests, '--use-wire'])

  tint_unittests_cmd = [tint_unittests]
  if api.platform.is_win and debug:
    # Exclude DeathTests from Windows Debug builds due to excessive runtimes.
    # See crbug.com/346814503.
    tint_unittests_cmd.append('--gtest_filter=-*DeathTest.*')
  api.step('Run the Tint unittests', tint_unittests_cmd)

  if api.platform.is_win and not debug:
    # dawn_end2end_tests on WARP is slow to run in Debug
    # (20m to 40m in Debug, vs 5m in Release).
    api.step('Run the Dawn end2end tests with WARP',
             [dawn_end2end_tests_warp, '--adapter-vendor-id=0x1414'])

  api.step('Run the Dawn end2end tests with SwiftShader',
           [dawn_end2end_tests_swiftshader, '--adapter-vendor-id=0x1AE0'])
  api.step(
      'Run the Dawn end2end tests with ANGLE/SwiftShader on gles 3.1 and no extensions',
      [
          dawn_end2end_tests_swiftshader, '--backend=opengles',
          '--use-angle=swiftshader',
          '--enable-toggles=gl_force_es_31_and_no_extensions'
      ])

def GenTests(api):
  yield api.test(
      'linux',
      api.siso.properties(),
      api.platform('linux', 64),
      api.buildbucket.ci_build(
          project='dawn', builder='linux', git_repo=DAWN_REPO),
  )
  yield api.test(
      'linux_gcc',
      api.platform('linux', 64),
      api.properties(clang=False),
      api.siso.properties(),
      api.buildbucket.ci_build(
          project='dawn', builder='linux', git_repo=DAWN_REPO),
  )
  yield api.test(
      'mac',
      api.siso.properties(),
      api.platform('mac', 64),
      api.buildbucket.ci_build(
          project='dawn', builder='mac', git_repo=DAWN_REPO),
  )
  yield api.test(
      'win',
      api.siso.properties(),
      api.platform('win', 64),
      api.buildbucket.ci_build(
          project='dawn', builder='win', git_repo=DAWN_REPO),
  )
  yield api.test(
      'win_clang',
      api.siso.properties(),
      api.platform('win', 64),
      api.properties(clang=True),
      api.buildbucket.ci_build(
          project='dawn', builder='win', git_repo=DAWN_REPO),
  )
  yield api.test(
      'win_rel_msvc_x86',
      api.siso.properties(),
      api.platform('win', 64),
      api.properties(clang=False, debug=False, target_cpu='x86'),
      api.buildbucket.ci_build(
          project='dawn', builder='win', git_repo=DAWN_REPO),
  )
  yield api.test(
      'win_dbg_msvc_x64',
      api.siso.properties(),
      api.platform('win', 64),
      api.properties(clang=False, debug=True),
      api.buildbucket.ci_build(
          project='dawn', builder='win', git_repo=DAWN_REPO),
  )
  yield api.test(
      'linux_gen_fuzz_corpus',
      api.siso.properties(),
      api.platform('linux', 64),
      api.properties(gen_fuzz_corpus=True),
      api.buildbucket.ci_build(
          project='dawn', builder='linux', git_repo=DAWN_REPO),
  )
