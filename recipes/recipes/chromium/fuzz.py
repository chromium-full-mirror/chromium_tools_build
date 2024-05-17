# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import itertools
import re

from recipe_engine import post_process
from recipe_engine.post_process import StepCommandRE, DropExpectation, LogEquals
from recipe_engine.engine_types import freeze
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb
from RECIPE_MODULES.build import chromium
from RECIPE_MODULES.build.attr_utils import attrs, attrib

DEPS = [
    'archive',
    'code_coverage',
    'chromium',
    'chromium_checkout',
    'depot_tools/depot_tools',
    'depot_tools/tryserver',
    'filter',
    'gn',
    'reclient',
    'recipe_engine/context',
    'recipe_engine/file',
    'recipe_engine/json',
    'recipe_engine/path',
    'recipe_engine/platform',
    'recipe_engine/properties',
    'recipe_engine/raw_io',
    'recipe_engine/step',
]


@attrs()
class FuzzEngineSpec(chromium.BuilderSpec):

  archive_prefix = attrib(str, default='libfuzzer')
  v8_targets_only = attrib(bool, default=False)
  ios_targets_only = attrib(bool, default=False)
  upload_bucket = attrib(str, default=None)
  upload_directory = attrib(str, default=None)
  collect_fuzz_coverage = attrib(bool, default=False)


BUILDERS = freeze({
    'chromium.coverage': {
        'builders': {
            'linux-fuzz-coverage':
                FuzzEngineSpec.create(
                    chromium_config='chromium_clang',
                    chromium_apply_config=['clobber'],
                    chromium_config_kwargs={
                        'BUILD_CONFIG': 'Release',
                        'TARGET_PLATFORM': 'linux',
                        'TARGET_BITS': 64,
                        'HOST_PLATFORM': 'linux',
                    },
                    collect_fuzz_coverage=True,
                    gclient_apply_config=['use_clang_coverage'],
                ),
        }
    },
    'chromium.fuzz': {
        'builders': {
            'Libfuzzer Upload Chrome OS ASan':
                FuzzEngineSpec.create(
                    chromium_config='chromium_clang',
                    chromium_apply_config=['clobber'],
                    chromium_config_kwargs={
                        'BUILD_CONFIG': 'Release',
                        'TARGET_PLATFORM': 'chromeos',
                        'TARGET_BITS': 64,
                    },
                    gclient_apply_config=['chromeos'],
                    archive_prefix='libfuzzer-chromeos',
                    upload_bucket='chromium-browser-libfuzzer',
                    upload_directory='chromeos-asan',
                ),
            'Libfuzzer Upload iOS Catalyst Debug':
                FuzzEngineSpec.create(
                    chromium_config='chromium_clang',
                    chromium_apply_config=[
                        'clobber',
                        'mac_toolchain',
                    ],
                    gclient_apply_config=['ios'],
                    chromium_config_kwargs={
                        'BUILD_CONFIG': 'Debug',
                        'TARGET_BITS': 64,
                        'TARGET_PLATFORM': 'ios',
                        'HOST_PLATFORM': 'mac',
                    },
                    archive_prefix='libfuzzer-ios',
                    upload_bucket='chromium-browser-libfuzzer',
                    upload_directory='ios-catalyst-debug',
                    ios_targets_only=True,
                ),
            'Libfuzzer Upload Linux32 ASan':
                FuzzEngineSpec.create(
                    chromium_config='chromium_clang',
                    chromium_apply_config=['clobber'],
                    chromium_config_kwargs={
                        'BUILD_CONFIG': 'Release',
                        'TARGET_PLATFORM': 'linux',
                        'TARGET_BITS': 32,
                    },
                    upload_bucket='chromium-browser-libfuzzer',
                    upload_directory='asan',
                ),
            'Libfuzzer Upload Linux32 V8-ARM ASan':
                FuzzEngineSpec.create(
                    chromium_config='chromium_clang',
                    chromium_apply_config=['clobber'],
                    chromium_config_kwargs={
                        'BUILD_CONFIG': 'Release',
                        'TARGET_PLATFORM': 'linux',
                        'TARGET_BITS': 32,
                    },
                    archive_prefix='libfuzzer-v8-arm',
                    upload_bucket='chromium-browser-libfuzzer',
                    upload_directory='asan-arm-sim',
                    v8_targets_only=True,
                ),
            'Libfuzzer Upload Linux32 V8-ARM ASan Debug':
                FuzzEngineSpec.create(
                    chromium_config='chromium_clang',
                    chromium_apply_config=['clobber'],
                    chromium_config_kwargs={
                        'BUILD_CONFIG': 'Debug',
                        'TARGET_PLATFORM': 'linux',
                        'TARGET_BITS': 32,
                    },
                    archive_prefix='libfuzzer-v8-arm',
                    upload_bucket='chromium-browser-libfuzzer',
                    upload_directory='asan-arm-sim',
                    v8_targets_only=True,
                ),
            'Libfuzzer Upload Linux ASan':
                FuzzEngineSpec.create(
                    chromium_config='chromium_clang',
                    chromium_apply_config=['clobber'],
                    chromium_config_kwargs={
                        'BUILD_CONFIG': 'Release',
                        'TARGET_PLATFORM': 'linux',
                        'TARGET_BITS': 64,
                    },
                    upload_bucket='chromium-browser-libfuzzer',
                    upload_directory='asan',
                ),
            'Libfuzzer Upload Linux ASan Debug':
                FuzzEngineSpec.create(
                    chromium_config='chromium_clang',
                    chromium_apply_config=['clobber'],
                    chromium_config_kwargs={
                        'BUILD_CONFIG': 'Debug',
                        'TARGET_PLATFORM': 'linux',
                        'TARGET_BITS': 64,
                    },
                    upload_bucket='chromium-browser-libfuzzer',
                    upload_directory='asan',
                ),
            'Libfuzzer Upload Linux V8-ARM64 ASan':
                FuzzEngineSpec.create(
                    chromium_config='chromium_clang',
                    chromium_apply_config=['clobber'],
                    chromium_config_kwargs={
                        'BUILD_CONFIG': 'Release',
                        'TARGET_PLATFORM': 'linux',
                        'TARGET_BITS': 64,
                    },
                    archive_prefix='libfuzzer-v8-arm64',
                    upload_bucket='chromium-browser-libfuzzer',
                    upload_directory='asan-arm64-sim',
                    v8_targets_only=True,
                ),
            'Libfuzzer Upload Linux V8-ARM64 ASan Debug':
                FuzzEngineSpec.create(
                    chromium_config='chromium_clang',
                    chromium_apply_config=['clobber'],
                    chromium_config_kwargs={
                        'BUILD_CONFIG': 'Debug',
                        'TARGET_PLATFORM': 'linux',
                        'TARGET_BITS': 64,
                    },
                    archive_prefix='libfuzzer-v8-arm64',
                    upload_bucket='chromium-browser-libfuzzer',
                    upload_directory='asan-arm64-sim',
                    v8_targets_only=True,
                ),
            'Libfuzzer Upload Linux MSan':
                FuzzEngineSpec.create(
                    chromium_config='chromium_clang',
                    chromium_apply_config=['clobber', 'msan'],
                    chromium_config_kwargs={
                        'BUILD_CONFIG': 'Release',
                        'TARGET_PLATFORM': 'linux',
                        'TARGET_BITS': 64,
                    },
                    upload_bucket='chromium-browser-libfuzzer',
                    upload_directory='msan',
                ),
            'Libfuzzer Upload Linux UBSan':
                FuzzEngineSpec.create(
                    chromium_config='chromium_clang',
                    chromium_apply_config=['clobber'],
                    chromium_config_kwargs={
                        'BUILD_CONFIG': 'Release',
                        'TARGET_PLATFORM': 'linux',
                        'TARGET_BITS': 64,
                    },
                    upload_bucket='chromium-browser-libfuzzer',
                    upload_directory='ubsan',
                ),
            'Libfuzzer Upload Mac ASan':
                FuzzEngineSpec.create(
                    chromium_config='chromium_clang',
                    chromium_apply_config=['clobber'],
                    chromium_config_kwargs={
                        'BUILD_CONFIG': 'Release',
                        'TARGET_PLATFORM': 'mac',
                        'TARGET_BITS': 64,
                    },
                    upload_bucket='chromium-browser-libfuzzer',
                    upload_directory='asan',
                ),
            'Libfuzzer Upload Windows ASan':
                FuzzEngineSpec.create(
                    chromium_config='chromium_clang',
                    chromium_apply_config=['clobber'],
                    chromium_config_kwargs={
                        'BUILD_CONFIG': 'Release',
                        'TARGET_PLATFORM': 'win',
                        'TARGET_BITS': 64,
                    },
                    upload_bucket='chromium-browser-libfuzzer',
                    upload_directory='asan',
                ),
            'Afl Upload Linux ASan':
                FuzzEngineSpec.create(
                    chromium_config='chromium_clang',
                    chromium_apply_config=['clobber'],
                    chromium_config_kwargs={
                        'BUILD_CONFIG': 'Release',
                        'TARGET_PLATFORM': 'linux',
                        'TARGET_BITS': 64,
                    },
                    upload_bucket='chromium-browser-afl',
                    upload_directory='asan',
                    archive_prefix='afl',
                ),
            'Centipede Upload Linux ASan':
                FuzzEngineSpec.create(
                    chromium_config='chromium_clang',
                    chromium_apply_config=['clobber'],
                    chromium_config_kwargs={
                        'BUILD_CONFIG': 'Release',
                        'TARGET_PLATFORM': 'linux',
                        'TARGET_BITS': 64,
                    },
                    upload_bucket='chromium-browser-centipede',
                    upload_directory='asan',
                    archive_prefix='centipede',
                ),
            'Centipede High End Upload Linux ASan':
                FuzzEngineSpec.create(
                    chromium_config='chromium_clang',
                    chromium_apply_config=['clobber'],
                    chromium_config_kwargs={
                        'BUILD_CONFIG': 'Release',
                        'TARGET_PLATFORM': 'linux',
                        'TARGET_BITS': 64,
                    },
                    upload_bucket='chromium-browser-centipede',
                    upload_directory='asan',
                    archive_prefix='centipede-high-end',
                ),
        },
    },
    'tryserver.chromium.linux': {
        'builders': {
            'linux-libfuzzer-asan-rel':
                FuzzEngineSpec.create(
                    chromium_config='chromium',
                    chromium_config_kwargs={
                        'BUILD_CONFIG': 'Release',
                        'TARGET_PLATFORM': 'linux',
                        'TARGET_BITS': 64,
                    },
                ),
            'linux-afl-asan-rel':
                FuzzEngineSpec.create(
                    chromium_config='chromium',
                    chromium_config_kwargs={
                        'BUILD_CONFIG': 'Release',
                        'TARGET_PLATFORM': 'linux',
                        'TARGET_BITS': 64,
                    },
                ),
            'linux-centipede-asan-rel':
                FuzzEngineSpec.create(
                    chromium_config='chromium',
                    chromium_config_kwargs={
                        'BUILD_CONFIG': 'Release',
                        'TARGET_PLATFORM': 'linux',
                        'TARGET_BITS': 64,
                    },
                ),
        },
    },
    'tryserver.chromium.win': {
        'builders': {
            'win-libfuzzer-asan-rel':
                FuzzEngineSpec.create(
                    chromium_config='chromium_clang',
                    chromium_config_kwargs={
                        'BUILD_CONFIG': 'Release',
                        'TARGET_PLATFORM': 'win',
                        'TARGET_BITS': 64,
                    },
                ),
        },
    },
})


def gn_refs(api, step_name, target):
  """Runs gn refs to calculate targets depending on target.
  Returns: the list of matched targets.
  """
  return api.gn.refs(
      api.chromium.output_dir, [target],
      output_type='executable',
      step_name=step_name,
      output_format='label')


def copy_path(api, path_name):
  """Copies the path_name, which could be a file or a directory into ${build_dir}/src_root.
    """
  assert path_name.startswith(
      '../../'), path_name + " is expected to start with ../../"
  relative_path = path_name[len('../../'):]
  src = api.chromium_checkout.source_dir / relative_path
  dest = api.chromium.output_dir.joinpath('src_root', relative_path)
  src = api.path.abspath(str(src))
  dest = api.path.abspath(str(dest))
  if api.path.exists(dest):
    # Nothing to be done as this file/directory already exists in destination.
    return
  if api.path.isfile(src):
    dstfolder = api.path.dirname(dest)
    if not api.path.exists(dstfolder):
      # Make sure the destination folder exists.
      api.file.ensure_directory('ensure builder cache dir', dstfolder)
    api.file.copy('copying file:' + str(src), src, dest)
  else:
    api.file.copytree('copying directory:' + str(src), src, dest)


def extract_paths_to_copy(list_of_runtime_deps, set_of_gn_targets):
  """Get the subset of runtime dependencies for the specified targets.

  The list of runtime dependencies has the following format:
  some compiler warning
  some compiler warning2
  Target target1
  runtime deps
    ./path/to/dependency1
    ../../path/to/dependency2
  Target target2
  runtime deps
    ./path/to/dependency3
    ../../path/to/dependency4
  """
  result = set()
  size = len(list_of_runtime_deps)
  i = 0

  while i < size:
    target = list_of_runtime_deps[i]
    i += 1
    if target.startswith(
        'Target ') and target[len('Target '):] in set_of_gn_targets:
      while (i < size and not list_of_runtime_deps[i].startswith('Target ')):
        # lines corresponding to runtime_dependencies start with spaces.
        dependency = list_of_runtime_deps[i].strip()
        # Add runtime dependencies that are not already under
        # {build_dir}/ to the set of paths to copy.
        if dependency.startswith('../../'):
          result.add(dependency)
        i += 1
  return result

# How many elements to return per batch by `batched()`.
#
# Picked by checking how many arguments was finally too much for the Windows
# command line in https://crbug.com/1477042: 629!
BATCH_SIZE = 500


def batched(iterable):
  """Batch `iterable` into lists of length `BATCH_SIZE`.

  The last batch may be shorter.

  For example, for `BATCH_SIZE == 3`, `batched('ABCDEFG')` yields:

    ['A', 'B', 'C']
    ['D', 'E', 'F']
    ['G']

  Shamelessly cribbed from the `itertools` python module's docs.
  """
  it = iter(iterable)

  # `islice(it, n)` returns an iterator over the first `n` elements of `it`.
  while batch := list(itertools.islice(it, BATCH_SIZE)):
    yield batch


def RunSteps(api):
  builder_id, bot_config = api.chromium.configure_bot(BUILDERS, ['mb'])
  update_result = api.chromium_checkout.ensure_checkout(
      clobber=bot_config.clobber)

  api.chromium.ensure_toolchains()
  api.chromium.runhooks()
  api.chromium.mb_gen(builder_id)

  source_dir = update_result.source_root.path
  with api.context(cwd=source_dir, env=api.chromium.get_env()):
    all_fuzzers = gn_refs(api, 'calculate all_fuzzers',
                          '//testing/libfuzzer:fuzzing_engine')
    if bot_config.v8_targets_only:
      # Some builders only need the V8 targets as the only difference is code
      # generated by V8 simulators.
      v8_fuzzers = gn_refs(api, 'calculate v8_fuzzers', '//v8:fuzzer_support')
      all_fuzzers = all_fuzzers & v8_fuzzers
    elif bot_config.ios_targets_only:
      ios_fuzzers = gn_refs(
          api, 'calculate ios_fuzzers',
          '//testing/libfuzzer:build_for_ios_clusterfuzz_job')
      all_fuzzers = all_fuzzers & ios_fuzzers
    no_clusterfuzz = gn_refs(api, 'calculate no_clusterfuzz',
                             '//testing/libfuzzer:no_clusterfuzz')
    # If we're collecting coverage, we also want to know the coverage
    # of the main Chromium binary, so let's build that too.
    if bot_config.collect_fuzz_coverage:
      all_fuzzers.add("chrome")
    targets = sorted(all_fuzzers - no_clusterfuzz)

    api.step.active_result.presentation.logs['all_fuzzers'] = sorted(
        all_fuzzers)
    api.step.active_result.presentation.logs['no_clusterfuzz'] = (
        sorted(no_clusterfuzz))
    api.step.active_result.presentation.logs['targets'] = targets

    outdir = api.chromium.output_dir
    if api.tryserver.is_tryserver and not bot_config.collect_fuzz_coverage:
      # Filter out all targets that the patch doesn't affect.
      affected_files = api.chromium_checkout.get_files_affected_by_patch()
      test_targets, compile_targets = api.filter.analyze(
          affected_files, None, targets)
      targets = sorted(test_targets + compile_targets)
      if not targets:
        return

    # Up until now we work in terms of GN labels so that we can use
    # api.filter.analyze above in the trybot case. We now convert the GN labels
    # to ninja targets and pass them into compile.

    gn_targets = targets

    # `targets` can grow to be quite large, larger than can fit on a Windows
    # command line... We work around this by batching `gn ls` invocations.
    #
    # TODO(https://crbug.com/1477042): Define a static list of fuzzer targets
    # in the build instead of dynamically generating it based on `gn refs` in
    # this manner.
    target_set = set()
    for target_batch in batched(targets):
      target_set |= api.gn.ls(outdir, target_batch, output_format='output')

    targets = list(target_set)
    targets.sort()  # Ensure stable order for tests.

    # For iOS, the target list from |api.gn.refs| is a list of paths like
    # obj/.../XXX_fuzzer. The last part of the path is the target name to be
    # compiled.
    if api.chromium.c.TARGET_PLATFORM == 'ios':
      targets = [target.split('/')[-1] for target in targets]

    # Same as above, the list of targets can grow so large that Windows chokes
    # on a single `ninja` invocation against the full list.
    #
    # TODO(https://crbug.com/1477042): Define a static target that builds all
    # fuzzers for ClusterFuzz instead of dynamically generating it like this.
    raw_result = None
    for target_batch in batched(targets):
      raw_result = api.chromium.compile(targets=target_batch, use_reclient=True)

      if raw_result.status != common_pb.SUCCESS:
        return raw_result

    # Stop here if we're only running on a trybot.
    if api.tryserver.is_tryserver and not bot_config.collect_fuzz_coverage:
      return raw_result

    if not bot_config.collect_fuzz_coverage:
      assert (bot_config.upload_directory is not None)
      assert (bot_config.upload_bucket is not None)

    # Make sure 32 bit archives are distinguished from 64 bit ones.
    kwargs = {}
    if api.chromium.c.TARGET_BITS == 32:
      kwargs['use_legacy'] = False
      kwargs['bitness'] = 32

    if bot_config.collect_fuzz_coverage:
      with api.step.nest('process fuzz coverage') as step_result:
        try:
          corpora_dir = 'current-corpora-from-clusterfuzz'
          profdata_dir = str(
              api.chromium_checkout.source_dir.joinpath('out',
                                                        'profdata-output-dir'))
          build_dir = "out/Release"
          api.step('make corpora directory', ['mkdir', corpora_dir])
          api.file.rmtree('ensure profdata directory blank', profdata_dir)
          api.step('make profdata directory', ['mkdir', '-p', profdata_dir])
          api.step('download corpora', [
              'python3', 'tools/code_coverage/download_fuzz_corpora.py',
              '--download-dir', corpora_dir, '--build-dir', build_dir
          ])
          # Figure out what we need to start X. Some fuzzers rely on an
          # X environment
          withxvfb_path = api.repo_resource('recipes', 'withxvfb.py')
          withxvfb_args = ['--target', api.chromium.c.build_config_fs]
          withxvfb_args.append('--build-dir=%s' % build_dir)

          api.step(
              'run all fuzzers', ['python3', withxvfb_path] + withxvfb_args + [
                  '--', 'python3', 'tools/code_coverage/run_all_fuzzers.py',
                  '--fuzzer-binaries-dir', build_dir, '--fuzzer-corpora-dir',
                  corpora_dir, '--profdata-outdir', profdata_dir
              ])
          profdata_path = api.chromium_checkout.source_dir.joinpath(
              'total_fuzz_coverage.profdata')
          llvm_profdata_path = api.chromium_checkout.source_dir.joinpath(
              'third_party', 'llvm-build', 'Release+Asserts', 'bin',
              'llvm-profdata')
          api.step('merge all fuzzers', [
              'python3', 'tools/code_coverage/merge_all_profdata.py',
              '--profdata-dir', profdata_dir, '--outfile', profdata_path,
              '--llvm-profdata', llvm_profdata_path
          ])

          api.code_coverage.get_chromium_fuzz_coverage(
              api.chromium_checkout.source_dir,
              api.chromium_checkout.source_dir.joinpath('out', 'Release'),
              profdata_path, profdata_dir)
        except api.step.StepFailure:
          step_result.logs[
              'fuzz coverage logs'] = "Could not process fuzz coverage"

    else:
      # copy data deps outside the build directory
      # Needed for tests
      api.path.mock_add_file('[CACHE]/builder/src/out/Release/src_root/path1')
      api.path.mock_add_file('[CACHE]/builder/src/path2')

      paths_to_copy = set()
      with api.step.nest(
          'generate runtime dependencies to copy') as step_result:
        set_of_gn_targets = set(gn_targets)
        list_of_runtime_deps = api.gn.desc(
            api.chromium.output_dir,
            "*",
            'runtime_deps',
            step_name='get runtime dependencies with pattern *')
        paths_to_copy = extract_paths_to_copy(list_of_runtime_deps,
                                              set_of_gn_targets)
        paths_to_copy = sorted(paths_to_copy)
        step_result.logs['runtime_dependencies_to_copy'] = paths_to_copy

      with api.step.nest('copy runtime dependencies to build directory'):
        for path_name in paths_to_copy:
          copy_path(api, path_name)

      api.archive.clusterfuzz_archive(
          build_dir=api.chromium.output_dir,
          update_properties=update_result.properties,
          gs_bucket=bot_config.upload_bucket,
          archive_prefix=bot_config.archive_prefix,
          archive_subdir_suffix=bot_config.upload_directory,
          gs_acl='public-read',
          **kwargs)


def GenTests(api):
  for test in api.chromium.gen_tests_for_builders(BUILDERS):
    test += api.reclient.properties()
    test += api.step_data(
        'calculate all_fuzzers',
        stdout=api.raw_io.output_text('target1\ntarget2\ntarget3\n')
    ) + api.step_data(
        'calculate no_clusterfuzz', stdout=api.raw_io.output_text('target1'))
    if not (("tryserver" in test.name) or ("fuzz_coverage" in test.name)):
      test += api.post_process(post_process.MustRun,
                               'generate runtime dependencies to copy')
      # this will lead to us having ../../path2 and ../../path4 as runtime dependencies to copy
      if not (("V8" in test.name) or ("Win" in test.name) or
              ("iOS" in test.name)):
        step_output = ('some warning1\n\n'
                       'some warning2\n'
                       'Target target1\n'
                       'runtime_deps\n'
                       '  ../../path14\n'
                       '  ./path15\n\n\n'
                       'Target target2\n'
                       'runtime_deps\n'
                       '  ../../path1\n'
                       '  ../../path2\n'
                       'Target target3\n'
                       'runtime_deps\n'
                       '  ./path3\n'
                       '  ../../path4\n'
                       'Target target5\n'
                       'runtime_deps\n'
                       '  ./path16\n'
                       '  ../../path17\n')

        test += api.step_data(
            'generate runtime dependencies to copy.get runtime dependencies with pattern *',
            stdout=api.raw_io.output_text(step_output))
        # ../../path1, ../../path2 and ../../path4 need to be copied.
        test += api.post_process(LogEquals,
                                 'generate runtime dependencies to copy',
                                 'runtime_dependencies_to_copy',
                                 '../../path1\n../../path2\n../../path4')
        test += api.post_process(
            post_process.MustRun,
            'copy runtime dependencies to build directory')
        test += api.post_process(
            post_process.MustRun,
            'copy runtime dependencies to build directory.copying file:[CACHE]/builder/src/path2'
        )
        test += api.post_process(
            post_process.MustRun,
            'copy runtime dependencies to build directory.copying directory:[CACHE]/builder/src/path4'
        )
      test += api.post_process(post_process.MustRun, 'gsutil upload')
    if "fuzz_coverage" in test.name:
      test += api.post_process(post_process.MustRun, 'process fuzz coverage')
      test += api.step_data(
          'process fuzz coverage.generate coverage metadata', retcode=1)
    test += api.post_process(post_process.DropExpectation)
    if 'Upload_iOS' in test.name:
      yield (test + api.properties(xcode_build_version='12345'))
    else:
      yield test

  yield api.test(
      'compile_failure',
      api.chromium.ci_build(
          builder_group='chromium.fuzz',
          builder='Libfuzzer Upload Mac ASan',
      ),
      api.platform.name('mac'),
      api.reclient.properties(),
      api.step_data(
          'calculate all_fuzzers',
          stdout=api.raw_io.output_text('//foo/bar:target1')),
      api.step_data(
          'list gn targets', stdout=api.raw_io.output_text('target1')),
      api.step_data('compile', retcode=1),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'basic_linux_tryjob_no_compile',
      api.chromium.try_build(
          builder_group='tryserver.chromium.linux',
          builder='linux-libfuzzer-asan-rel'),
      api.step_data(
          'calculate all_fuzzers',
          stdout=api.raw_io.output_text(
              '//foo/bar:target1\n//foo/bar:target2\n//foo/bar:target3')),
      api.step_data(
          'calculate no_clusterfuzz',
          stdout=api.raw_io.output_text('//foo/bar:target1')),
      api.filter.no_dependency(),
  )

  yield api.test(
      'basic_linux_tryjob_with_compile',
      api.chromium.try_build(
          builder_group='tryserver.chromium.linux',
          builder='linux-libfuzzer-asan-rel'),
      api.step_data(
          'calculate all_fuzzers',
          stdout=api.raw_io.output_text('\n'.join(
              ['//foo/bar:target1', '//foo/bar:target2',
               '//foo/bar:target3']))),
      api.step_data(
          'calculate no_clusterfuzz',
          stdout=api.raw_io.output_text('//foo/bar:target1')),
      api.filter.analyze_output(
          status='Found dependency',
          test_targets=[],
          compile_targets=['//foo/bar:target2'],
      ),
      api.step_data(
          'list gn targets', stdout=api.raw_io.output_text('target2')),
  )

  yield api.test(
      'basic_linux_tryjob_with_compile_many_targets',
      api.chromium.try_build(
          builder_group='tryserver.chromium.linux',
          builder='linux-libfuzzer-asan-rel'),
      api.step_data(
          'calculate all_fuzzers',
          stdout=api.raw_io.output_text('\n'.join(
              f'//foo/bar:target{i}' for i in range(1500)))),
      api.step_data(
          'calculate no_clusterfuzz', stdout=api.raw_io.output_text('')),
      api.step_data(
          'list gn targets',
          stdout=api.raw_io.output_text('\n'.join(
              f'target{i}' for i in range(500)))),
      api.step_data(
          'list gn targets (2)',
          stdout=api.raw_io.output_text('\n'.join(
              f'target{i}' for i in range(500, 1000)))),
      api.step_data(
          'list gn targets (3)',
          stdout=api.raw_io.output_text('\n'.join(
              f'target{i}' for i in range(1000, 1500)))),
      # So many targets get batched into several gn ls invocations.
      api.post_check(post_process.MustRun, 'list gn targets'),
      api.post_check(post_process.MustRun, 'list gn targets (2)'),
      api.post_check(post_process.MustRun, 'list gn targets (3)'),
      api.post_check(post_process.DoesNotRun, 'list gn targets (4)'),
      # Same goes for ninja invocations.
      api.post_check(post_process.MustRun, 'compile'),
      api.post_check(post_process.MustRun, 'compile (3)'),
      api.post_check(post_process.MustRun, 'compile (3)'),
      api.post_check(post_process.DoesNotRun, 'compile (4)'),
      api.post_process(post_process.DropExpectation),
  )
