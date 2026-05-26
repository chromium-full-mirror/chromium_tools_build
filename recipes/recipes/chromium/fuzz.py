# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import itertools

from recipe_engine import post_process
from recipe_engine.config_types import Path
from recipe_engine.post_process import LogEquals

from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb
from PB.recipes.build.chromium.fuzz import InputProperties

from RECIPE_MODULES.build import chromium_tests_builder_config as ctbc

DEPS = [
    'archive',
    'code_coverage',
    'chromium',
    'chromium_checkout',
    'chromium_tests',
    'chromium_tests_builder_config',
    'depot_tools/depot_tools',
    'depot_tools/gsutil',
    'depot_tools/tryserver',
    'filter',
    'gn',
    'recipe_engine/buildbucket',
    'recipe_engine/context',
    'recipe_engine/file',
    'recipe_engine/json',
    'recipe_engine/path',
    'recipe_engine/platform',
    'recipe_engine/properties',
    'recipe_engine/raw_io',
    'recipe_engine/step',
]

PROPERTIES = InputProperties

# The name of the manifest file that will be written to the archive's build
# directory to store metadata about the archive for use by ClusterFuzz.
MANIFEST_FILENAME = 'clusterfuzz_manifest.json'


def gn_refs(api, build_dir: Path, step_name, target, output_type='executable'):
  """Runs gn refs to calculate targets depending on target.
  Returns: the set of matched targets.
  """
  raw_output = api.gn.refs(
      build_dir, [target],
      output_type=output_type,
      step_name=step_name,
      output_format='label')
  # Filter out lines starting with "Warning: is_asan "
  return {
      line for line in raw_output if not line.startswith('Warning: is_asan ')
  }


def copy_path(api, source_dir: Path, build_dir: Path, path_name):
  """Copies the path_name, which could be a file or a directory
  into ${build_dir}/src_root.
  """
  assert path_name.startswith(
      '../../'), path_name + " is expected to start with ../../"
  relative_path = path_name[len('../../'):]
  src = source_dir / relative_path
  dest = build_dir / 'src_root' / relative_path
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


# TODO(506138501): Deduplicate in `filter_runtime_deps` and remove
# deduplication logic in `RunSteps` when removing schema v0 logic.
def filter_runtime_deps(runtime_deps: list[str],
                        gn_targets: set[str]) -> set[str]:
  """Get the subset of runtime dependencies for the specified targets.

  The list of runtime dependencies has the following format:
  some compiler warning
  some compiler warning2
  Target target1
  runtime_deps
    ./path/to/dependency1
    ../../path/to/dependency2
  Target target2
  runtime_deps
    ./path/to/dependency3
    ../../path/to/dependency4
  """
  result = set()
  is_collecting = False
  TARGET_PREFIX = 'Target '

  for line in runtime_deps:
    if line.startswith(TARGET_PREFIX):
      target_name = line[len(TARGET_PREFIX):]
      is_collecting = target_name in gn_targets
    elif is_collecting:
      # lines corresponding to runtime dependencies start with spaces.
      dependency = line.strip()
      if dependency and dependency != 'runtime_deps':
        result.add(dependency)
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


def get_target_cpu(api, gn_args):
  args = api.gn.parse_gn_args(gn_args)
  return str(args.get('target_cpu')).replace('"', '')


def gn_ls_with_filter(api, build_dir, inputs, **kwargs):
  """Wraps api.gn.ls to filter out 'is_asan' warnings."""
  raw_output = api.gn.ls(build_dir, inputs, **kwargs)
  return {
      line for line in raw_output if not line.startswith('Warning: is_asan ')
  }


def RunSteps(api, properties):
  builder_id, builder_config = (
      api.chromium_tests_builder_config.lookup_builder())
  api.chromium_tests.configure_build(builder_config)

  # Most CI fuzz bots clobber. But we don't want the trybots clobbering, since
  # some of them are on the CQ.
  should_clobber = (
      api.chromium.c.clobber_before_runhooks and not api.tryserver.is_tryserver)
  update_result = api.chromium_checkout.ensure_checkout()
  checkout_dir = update_result.checkout_dir
  source_dir = update_result.source_root.path
  build_dir = api.chromium.default_build_dir(source_dir)

  api.chromium.ensure_toolchains(checkout_dir)
  api.chromium.runhooks(source_dir, build_dir, clobber=should_clobber)
  gn_args = api.chromium.mb_gen(source_dir, build_dir, builder_id)
  with api.context(cwd=source_dir, env=api.chromium.get_env(source_dir)):
    if properties.fuzz_engine == 'fuzzilli':
      # compile d8 to run fuzzilli fuzz tests
      targets = ['d8']
      raw_result = api.chromium.compile(source_dir, build_dir, targets=targets)

      if raw_result.status != common_pb.SUCCESS:
        return raw_result
    else:
      # compile targets for libfuzzer or centipede
      gn_output_type = 'executable'
      if api.chromium.c.TARGET_PLATFORM == 'android':
        gn_output_type = 'shared_library'

      all_fuzzers = gn_refs(
          api,
          build_dir,
          'calculate all_fuzzers',
          '//testing/libfuzzer:fuzzing_engine',
          output_type=gn_output_type)
      if properties.v8_targets_only:
        # Some builders only need the V8 targets as the only difference is code
        # generated by V8 simulators.
        v8_fuzzers = gn_refs(
            api,
            build_dir,
            'calculate v8_fuzzers',
            '//v8:fuzzer_support',
            output_type=gn_output_type,
        )
        all_fuzzers = all_fuzzers & v8_fuzzers
      elif properties.ios_targets_only:
        ios_fuzzers = gn_refs(
            api,
            build_dir,
            'calculate ios_fuzzers',
            '//testing/libfuzzer:build_for_ios_clusterfuzz_job',
            output_type=gn_output_type)
        all_fuzzers = all_fuzzers & ios_fuzzers
      no_clusterfuzz = gn_refs(
          api,
          build_dir,
          'calculate no_clusterfuzz',
          '//testing/libfuzzer:no_clusterfuzz',
          output_type=gn_output_type)
      # If we're collecting coverage, build the main `chrome` binary to ensure
      # that all files that make up the binary show up in the coverage report.
      # Otherwise, the report includes only files that get built for at least
      # one fuzzer, which misses a big proportion of Chromium code, and we get a
      # false picture of how well the codebase is covered.
      if properties.collect_fuzz_coverage:
        # `gn ls ... chrome --as=output` will output `phony/chrome/chrome` [0],
        # which can be passed to `compile()` but doesn't represent a path to
        # the real `chrome(.exe)` executable. On Linux and Windows, we need to
        # give `gn ls` the `//chrome:chrome_initial` label that actually
        # outputs the executable.
        #
        # macOS uses app bundles to package chrome, so it isn't supported yet.
        #
        # [0]: https://gn.googlesource.com/gn/+/master/docs/reference.md#phony-rules
        assert api.platform.is_win or api.platform.is_linux, (
            f"{api.platform.name} doesn't support fuzz coverage yet")
        all_fuzzers.add('//chrome:chrome_initial')
      targets = sorted(all_fuzzers - no_clusterfuzz)

      api.step.active_result.presentation.logs['all_fuzzers'] = sorted(
          all_fuzzers)
      api.step.active_result.presentation.logs['no_clusterfuzz'] = (
          sorted(no_clusterfuzz))
      api.step.active_result.presentation.logs['targets'] = targets

      if api.tryserver.is_tryserver and not properties.collect_fuzz_coverage:
        # Filter out all targets that the patch doesn't affect.
        affected_files = api.chromium_checkout.get_files_affected_by_patch()
        test_targets, compile_targets = api.filter.analyze(
            source_dir, build_dir, affected_files, None, targets)
        targets = sorted(test_targets + compile_targets)
        if not targets:
          return

      # On android the fuzzers are built as shared libraries inside APKs, so the
      # strategy is to find the library but then recover the main target name by
      # removing the __library suffix
      if api.chromium.c.TARGET_PLATFORM == 'android':
        targets = [t.removesuffix('__library') for t in targets]

      # Up until now we work in terms of GN labels so that we can use
      # api.filter.analyze above in the trybot case. We now convert the
      # GN labels to ninja targets and pass them into compile.

      gn_targets = targets

      # `targets` can grow to be quite large, larger than can fit on a Windows
      # command line... We work around this by batching `gn ls` invocations.
      #
      # TODO(https://crbug.com/1477042): Define a static list of fuzzer targets
      # in the build instead of dynamically generating it based on `gn refs` in
      # this manner.
      target_set = set()
      for target_batch in batched(targets):
        target_set |= gn_ls_with_filter(
            api, build_dir, target_batch, output_format='output')

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
        raw_result = api.chromium.compile(
            source_dir, build_dir, targets=target_batch)

        if raw_result.status != common_pb.SUCCESS:
          return raw_result

    # Stop here if we're only running on a trybot.
    if api.tryserver.is_tryserver and not properties.collect_fuzz_coverage:
      return raw_result

    if not properties.collect_fuzz_coverage:
      assert (properties.upload_directory is not None)
      assert (properties.upload_bucket is not None)

    # Make sure 32 bit archives are distinguished from 64 bit ones.
    kwargs = {}
    if api.chromium.c.TARGET_BITS == 32:
      kwargs['use_legacy'] = False
      kwargs['bitness'] = 32

    if properties.collect_fuzz_coverage:
      try:
        corpora_dir = 'current-corpora-from-clusterfuzz'
        profdata_dir = str(
            api.chromium_checkout.source_dir.joinpath('out',
                                                      'profdata-output-dir'))
        api.step('make corpora directory', ['mkdir', corpora_dir])
        api.file.rmtree('ensure profdata directory blank', profdata_dir)
        api.step('make profdata directory', ['mkdir', '-p', profdata_dir])
        download_cmd = [
            'python3',
            'tools/code_coverage/download_fuzz_corpora.py',
            '--download-dir',
            corpora_dir,
            '--build-dir',
            build_dir,
            '--corpora-type',
            properties.fuzz_engine,
        ]
        if properties.fuzz_engine == 'fuzzilli':
          download_cmd.extend(['--arch', get_target_cpu(api, gn_args)])
        api.step('download corpora', download_cmd)

        # Figure out what we need to start X. Some fuzzers rely on an
        # X environment
        withxvfb_path = api.repo_resource('recipes', 'withxvfb.py')
        withxvfb_args = ['--target', api.chromium.c.build_config_fs]
        withxvfb_args.append('--build-dir=%s' % build_dir)

        run_cmd = ['python3', withxvfb_path] + withxvfb_args + [
            '--', 'python3', 'tools/code_coverage/run_all_fuzzers.py',
            '--fuzzer-binaries-dir', build_dir, '--fuzzer-corpora-dir',
            corpora_dir, '--profdata-outdir', profdata_dir, '--fuzzer',
            properties.fuzz_engine
        ]
        if properties.fuzz_engine != 'fuzzilli':
          target_list_dir = str(
              api.chromium_checkout.source_dir.joinpath('out',
                                                        'target-list-dir'))
          api.file.rmtree('ensure target list directory blank', target_list_dir)
          api.step('make target list directory',
                   ['mkdir', '-p', target_list_dir])
          run_cmd.extend(['--target-list-dir', target_list_dir])

        api.step('run all fuzzers', run_cmd)

        if properties.fuzz_engine != 'fuzzilli':
          # For libfuzzer or centipede, upload the succeeded targets in a
          # json file to GCS
          gcs_dir = f'fuzz-targets/{properties.fuzz_engine}/'
          revision = api.buildbucket.gitiles_commit.id[:7]
          targets_file = api.path.join(target_list_dir,
                                       properties.fuzz_engine + '.json')
          api.gsutil.upload(targets_file, 'code-coverage-data',
                            f'{gcs_dir}{revision}/')

          successful_targets = api.file.read_json('read successful targets',
                                                  targets_file)
          for target in successful_targets:
            profdata_path = api.path.join(profdata_dir, f'{target}.profdata')
            api.code_coverage.get_chromium_fuzz_coverage(
                api.chromium_checkout.source_dir,
                build_dir,
                profdata_path, {target},
                overall=False,
                use_cache=True)

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
            build_dir,
            profdata_path,
            targets,
            use_cache=True)
      except api.step.StepFailure:
        api.step.empty('could not process fuzz coverage')
        api.step('diagnostic: df -h', ['df', '-h'])
        api.step('diagnostic: df -ih', ['df', '-ih'])
        with api.context(cwd=source_dir):
          api.step('diagnostic: du -sh *', ['sh', '-c', 'du -sh *'])
        raise

    else:
      # copy data deps outside the build directory
      # Needed for tests
      api.path.mock_add_file('[CACHE]/builder/src/path2')

      with api.step.nest('collect all runtime dependencies') as step_result:
        set_of_gn_targets = set(gn_targets)
        raw_list_of_runtime_deps = api.gn.desc(
            build_dir,
            '*',
            'runtime_deps',
            step_name='get runtime dependencies with pattern *',
            use_cache=True,
        )
        runtime_deps = filter_runtime_deps(raw_list_of_runtime_deps,
                                           set_of_gn_targets)
        runtime_deps = sorted(runtime_deps)
        step_result.logs['runtime_dependencies'] = runtime_deps

      paths_to_archive = None
      if properties.archive_schema_version == 0:
        archive_root = build_dir
        with api.step.nest('copy runtime dependencies to build directory'):
          for path in runtime_deps:
            if path.startswith('../../'):
              copy_path(api, source_dir, build_dir, path)

      else:
        archive_root = source_dir
        # `runtime_deps` set can still contain unnormalized duplicates (e.g.,
        # `foo` and `foo/`) so using a dict to deduplicate normalized paths
        # from `relpath` with same sort order.
        paths_to_archive_dict = dict.fromkeys(
            api.path.relpath(build_dir / path, source_dir)
            for path in runtime_deps)
        paths_to_archive = list(paths_to_archive_dict)
        paths_to_archive.append(MANIFEST_FILENAME)

      fuzz_target_paths = [
          api.path.relpath(build_dir / target, archive_root)
          for target in targets
      ]

      manifest_dict = {
          'archive_schema_version': properties.archive_schema_version,
          'fuzz_targets': fuzz_target_paths,
      }

      api.file.write_json(
          'write archive manifest',
          archive_root / MANIFEST_FILENAME,
          manifest_dict,
      )

      api.archive.clusterfuzz_archive(
          source_dir=source_dir,
          archive_root=archive_root,
          update_properties=update_result.properties,
          gs_bucket=properties.upload_bucket,
          paths_to_archive=paths_to_archive,
          archive_prefix=properties.archive_prefix or 'libfuzzer',
          build_config=api.chromium.c.build_config_fs,
          archive_subdir_suffix=properties.upload_directory,
          gs_acl='public-read',
          **kwargs,
      )


def GenTests(api):

  def generate_test(
      is_try=False,
      is_coverage=False,
      is_ios=False,
      is_v8=False,
      coverage_metadata_failure=False,
      engine='libfuzzer',
      archive_schema_version=0,
      drop_expectation=True,
  ):
    test = api.properties(
        upload_bucket='chromium-browser-libfuzzer',
        upload_directory='fuzz',
        v8_targets_only=is_v8,
        ios_targets_only=is_ios,
        collect_fuzz_coverage=is_coverage,
        fuzz_engine=engine,
        archive_schema_version=archive_schema_version,
    )
    if engine != 'fuzzilli':
      targets = api.raw_io.output_text('target1\ntarget2\ntarget3\n'
                                       'fuzzer.exe\n')
      test += api.step_data('calculate all_fuzzers', stdout=targets)
      test += api.step_data(
          'calculate no_clusterfuzz', stdout=api.raw_io.output_text('target1'))
      if is_v8:
        test += api.step_data('calculate v8_fuzzers', stdout=targets)
      if is_ios:
        test += api.step_data('calculate ios_fuzzers', stdout=targets)
      # Tryjobs provide their own mocks for 'list gn targets'
      if not is_try:
        test += api.step_data('list gn targets', stdout=targets)

    if not (is_try or is_coverage):
      test += api.post_process(post_process.MustRun,
                               'collect all runtime dependencies')
      # this will lead to us having ../../path2 and ../../path4 as runtime
      # dependencies to copy
      step_output = ('some warning1\n\n'
                     'some warning2\n'
                     'Target target1\n'
                     'runtime_deps\n'
                     '  ./target1\n'
                     '  ../../path14\n'
                     '  ./path15\n\n\n'
                     'Target target2\n'
                     'runtime_deps\n'
                     '  ./target2\n'
                     '  ../../path1\n'
                     '  ../../path2\n'
                     'Target target3\n'
                     'runtime_deps\n'
                     '  ./target3\n'
                     '  ./path3\n'
                     '  ../../path4\n'
                     'Target fuzzer.exe\n'
                     'runtime_deps\n'
                     '  ./fuzzer.exe\n'
                     'Target target5\n'
                     'runtime_deps\n'
                     '  ./path16\n'
                     '  ../../path17\n')

      test += api.step_data(
          'collect all runtime dependencies.get runtime '
          'dependencies with pattern *',
          stdout=api.raw_io.output_text(step_output),
      )
      # ../../path1, ../../path2 and ../../path4 need to be copied.
      test += api.post_process(
          LogEquals, 'collect all runtime dependencies', 'runtime_dependencies',
          '../../path1\n../../path2\n../../path4\n./fuzzer.exe\n./path3\n'
          './target2\n./target3')
      if archive_schema_version == 0:
        test += api.post_process(
            post_process.MustRun,
            'copy runtime dependencies to build directory')
        test += api.post_process(
            post_process.MustRun,
            'copy runtime dependencies to build directory.copying file:'
            '[CACHE]/builder/src/path2',
        )
        test += api.post_process(
            post_process.MustRun,
            'copy runtime dependencies to build directory.copying directory:'
            '[CACHE]/builder/src/path4',
        )
      test += api.post_process(post_process.MustRun, 'gsutil upload')
    if is_coverage:
      test += api.post_process(post_process.MustRun,
                               'process fuzz coverage (overall)')
      retcode = 0
      if coverage_metadata_failure:
        retcode = 1
      if engine != 'fuzzilli':
        test += api.step_data(
            'read successful targets',
            api.file.read_json(['target1', 'target2', 'target3']))
        test += api.post_process(post_process.MustRun,
                                 'process fuzz coverage (target1)')
        test += api.post_process(post_process.MustRun,
                                 'process fuzz coverage (target2)')
        test += api.post_process(post_process.MustRun,
                                 'process fuzz coverage (target3)')
      test += api.step_data(
          'process fuzz coverage (overall).generate coverage metadata',
          retcode=retcode)
    if is_ios:
      test += api.properties(xcode_build_version='12345')
    if archive_schema_version != 0:
      test += api.post_process(
          post_process.DoesNotRun,
          'copy runtime dependencies to build directory',
      )
      if not is_try:
        # Verify that targets and runtime_deps are in the archive.
        # targets in test are fuzzer.exe, target1, target2, target3
        # runtime_deps in test (filtered) are ../../path1, ../../path2,
        # ../../path4, ./path3
        expected_paths = ('["path1", '
                          '"path2", '
                          '"path4", '
                          '"out/1826-some-ci-bot/fuzzer.exe", '
                          '"out/1826-some-ci-bot/path3", '
                          '"out/1826-some-ci-bot/target2", '
                          '"out/1826-some-ci-bot/target3", '
                          '"clusterfuzz_manifest.json"]')
        test += api.post_process(
            post_process.StepCommandContains,
            'zipping',
            [expected_paths],
        )
        expected_manifest_dict = ('{"archive_schema_version": 1, '
                                  '"fuzz_targets": ['
                                  '"out/1826-some-ci-bot/fuzzer.exe", '
                                  '"out/1826-some-ci-bot/target1", '
                                  '"out/1826-some-ci-bot/target2", '
                                  '"out/1826-some-ci-bot/target3"]}')
        test += api.post_process(
            LogEquals,
            'write archive manifest',
            'clusterfuzz_manifest.json',
            expected_manifest_dict,
        )
    if drop_expectation:
      test += api.post_process(post_process.DropExpectation)
    return test

  yield api.test(
      'linux-32',
      api.chromium_tests_builder_config.ci_build(
          builder_group='chromium.fuzz',
          builder='some-ci-bot',
          builder_db=ctbc.BuilderDatabase.create({
              'chromium.fuzz': {
                  'some-ci-bot':
                      ctbc.BuilderSpec.create(
                          chromium_config='chromium',
                          chromium_apply_config=['clobber'],
                          gclient_config='chromium',
                          chromium_config_kwargs={
                              'TARGET_BITS': 32,
                          },
                      ),
              },
          })),
      api.platform.name('linux'),
      api.post_process(post_process.MustRun, 'clobber'),
      generate_test(),
  )

  yield api.test(
      'linux-dbg',
      api.chromium_tests_builder_config.ci_build(
          builder_group='chromium.fuzz',
          builder='some-ci-bot',
          builder_db=ctbc.BuilderDatabase.create({
              'chromium.fuzz': {
                  'some-ci-bot':
                      ctbc.BuilderSpec.create(
                          chromium_config='chromium',
                          gclient_config='chromium',
                          chromium_config_kwargs={
                              'BUILD_CONFIG': 'Debug',
                          },
                      ),
              },
          }),
      ),
      api.path.exists(api.path.cache_dir /
                      'builder/src/out/1826-some-ci-bot/src_root/path1'),
      api.platform.name('linux'),
      generate_test(),
  )
  yield api.test(
      'schema_v1',
      api.chromium_tests_builder_config.ci_build(
          builder_group='chromium.fuzz',
          builder='some-ci-bot',
          builder_db=ctbc.BuilderDatabase.create({
              'chromium.fuzz': {
                  'some-ci-bot':
                      ctbc.BuilderSpec.create(
                          chromium_config='chromium',
                          gclient_config='chromium',
                      ),
              },
          }),
      ),
      api.platform.name('linux'),
      generate_test(archive_schema_version=1),
  )

  yield api.test(
      'v8',
      api.chromium_tests_builder_config.ci_build(
          builder_group='chromium.fuzz',
          builder='some-ci-bot',
          builder_db=ctbc.BuilderDatabase.create({
              'chromium.fuzz': {
                  'some-ci-bot':
                      ctbc.BuilderSpec.create(
                          chromium_config='chromium',
                          gclient_config='chromium',
                      ),
              },
          })),
      api.platform.name('linux'),
      generate_test(is_v8=True),
  )

  yield api.test(
      'ios',
      api.chromium_tests_builder_config.ci_build(
          builder_group='chromium.fuzz',
          builder='some-ci-bot',
          builder_db=ctbc.BuilderDatabase.create({
              'chromium.fuzz': {
                  'some-ci-bot':
                      ctbc.BuilderSpec.create(
                          chromium_config='chromium',
                          gclient_config='chromium',
                          chromium_config_kwargs={
                              'TARGET_PLATFORM': 'ios',
                          },
                      ),
              },
          })),
      api.platform.name('mac'),
      generate_test(is_ios=True),
  )

  yield api.test(
      'android',
      api.chromium_tests_builder_config.ci_build(
          builder_group='chromium.fuzz',
          builder='some-ci-bot',
          builder_db=ctbc.BuilderDatabase.create({
              'chromium.fuzz': {
                  'some-ci-bot':
                      ctbc.BuilderSpec.create(
                          chromium_config='chromium',
                          gclient_config='chromium',
                          chromium_config_kwargs={
                              'TARGET_PLATFORM': 'android',
                          },
                      ),
              },
          })),
      api.platform.name('linux'),
      generate_test(drop_expectation=False),
      api.step_data(
          'calculate all_fuzzers',
          stdout=api.raw_io.output_text(
              '//foo:target1__library\n//foo:target2__library\n'
              '//foo:fuzzer.exe__library\n')),
      api.step_data(
          'list gn targets',
          stdout=api.raw_io.output_text('fuzzer.exe\ntarget1\ntarget2')),
      api.step_data(
          'collect all runtime dependencies.'
          'get runtime dependencies with pattern *',
          stdout=api.raw_io.output_text('Target //foo:target1\n'
                                        'runtime_deps\n'
                                        '  ../../path1\n'
                                        '  ../../path2\n'
                                        '  ../../path4\n'
                                        '  ./path3\n'
                                        '  ./target2\n'
                                        '  ./target3\n'
                                        'Target //foo:fuzzer.exe\n'
                                        'runtime_deps\n'
                                        '  ./fuzzer.exe\n')),
      api.post_check(post_process.StepCommandContains, 'list gn targets',
                     ['//foo:target1']),
      api.post_check(post_process.StepCommandDoesNotContain, 'list gn targets',
                     ['//foo:target1__library']),
  )

  yield api.test(
      'coverage',
      api.chromium_tests_builder_config.ci_build(
          builder_group='chromium.fuzz',
          builder='some-ci-bot',
          builder_db=ctbc.BuilderDatabase.create({
              'chromium.fuzz': {
                  'some-ci-bot':
                      ctbc.BuilderSpec.create(
                          chromium_config='chromium',
                          gclient_config='chromium',
                      ),
              },
          })),
      api.platform.name('linux'),
      generate_test(is_coverage=True),
  )

  yield api.test(
      'coverage_metadata_generation_failure',
      api.chromium_tests_builder_config.ci_build(
          builder_group='chromium.fuzz',
          builder='some-ci-bot',
          builder_db=ctbc.BuilderDatabase.create({
              'chromium.fuzz': {
                  'some-ci-bot':
                      ctbc.BuilderSpec.create(
                          chromium_config='chromium',
                          gclient_config='chromium',
                      ),
              },
          })),
      api.platform.name('linux'),
      api.expect_status('FAILURE'),
      generate_test(is_coverage=True, coverage_metadata_failure=True),
  )

  yield api.test(
      'fuzzilli-coverage',
      api.chromium_tests_builder_config.ci_build(
          builder_group='chromium.fuzz',
          builder='some-ci-bot',
          builder_db=ctbc.BuilderDatabase.create({
              'chromium.fuzz': {
                  'some-ci-bot':
                      ctbc.BuilderSpec.create(
                          chromium_config='chromium',
                          gclient_config='chromium',
                      ),
              },
          })),
      api.platform.name('linux'),
      generate_test(is_coverage=True, engine='fuzzilli'),
  )

  yield api.test(
      'centipede-coverage',
      api.chromium_tests_builder_config.ci_build(
          builder_group='chromium.fuzz',
          builder='some-ci-bot',
          builder_db=ctbc.BuilderDatabase.create({
              'chromium.fuzz': {
                  'some-ci-bot':
                      ctbc.BuilderSpec.create(
                          chromium_config='chromium',
                          gclient_config='chromium',
                      ),
              },
          })),
      api.platform.name('linux'),
      generate_test(is_coverage=True, engine='centipede'),
  )

  yield api.test(
      'd8-compile-failure',
      api.chromium_tests_builder_config.ci_build(
          builder_group='chromium.fuzz',
          builder='some-ci-bot',
          builder_db=ctbc.BuilderDatabase.create({
              'chromium.fuzz': {
                  'some-ci-bot':
                      ctbc.BuilderSpec.create(
                          chromium_config='chromium',
                          gclient_config='chromium',
                      ),
              },
          })),
      api.platform.name('linux'),
      api.properties(fuzz_engine='fuzzilli'),
      api.step_data('compile', retcode=1),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'compile_failure',
      api.chromium_tests_builder_config.ci_build(
          builder_group='chromium.fuzz',
          builder='Libfuzzer Upload Mac ASan',
          builder_db=ctbc.BuilderDatabase.create({
              'chromium.fuzz': {
                  'Libfuzzer Upload Mac ASan':
                      ctbc.BuilderSpec.create(
                          chromium_config='chromium',
                          gclient_config='chromium',
                      ),
              },
          })),
      api.platform.name('mac'),
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
      api.chromium_tests_builder_config.try_build(
          builder_group='tryserver.chromium.linux',
          builder='linux-libfuzzer-asan-rel',
          builder_db=ctbc.BuilderDatabase.create({
              'chromium.fuzz': {
                  'fuzz-ci-bot':
                      ctbc.BuilderSpec.create(
                          chromium_config='chromium',
                          gclient_config='chromium',
                      ),
              },
          }),
          try_db=ctbc.TryDatabase.create({
              'tryserver.chromium.linux': {
                  'linux-libfuzzer-asan-rel':
                      ctbc.TrySpec.create_for_single_mirror(
                          'chromium.fuzz', 'fuzz-ci-bot'),
              },
          })),
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
      api.chromium_tests_builder_config.try_build(
          builder_group='tryserver.chromium.linux',
          builder='linux-libfuzzer-asan-rel',
          builder_db=ctbc.BuilderDatabase.create({
              'chromium.fuzz': {
                  'fuzz-ci-bot':
                      ctbc.BuilderSpec.create(
                          chromium_config='chromium',
                          chromium_apply_config=['clobber'],
                          gclient_config='chromium',
                      ),
              },
          }),
          try_db=ctbc.TryDatabase.create({
              'tryserver.chromium.linux': {
                  'linux-libfuzzer-asan-rel':
                      ctbc.TrySpec.create_for_single_mirror(
                          'chromium.fuzz', 'fuzz-ci-bot'),
              },
          })),
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
      api.post_process(post_process.DoesNotRun, 'clobber'),
  )

  yield api.test(
      'basic_linux_tryjob_with_compile_many_targets',
      api.chromium_tests_builder_config.try_build(
          builder_group='tryserver.chromium.linux',
          builder='linux-libfuzzer-asan-rel',
          builder_db=ctbc.BuilderDatabase.create({
              'chromium.fuzz': {
                  'fuzz-ci-bot':
                      ctbc.BuilderSpec.create(
                          chromium_config='chromium',
                          gclient_config='chromium',
                      ),
              },
          }),
          try_db=ctbc.TryDatabase.create({
              'tryserver.chromium.linux': {
                  'linux-libfuzzer-asan-rel':
                      ctbc.TrySpec.create_for_single_mirror(
                          'chromium.fuzz', 'fuzz-ci-bot'),
              },
          })),
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

  yield api.test(
      'filter_asan_warning',
      api.chromium_tests_builder_config.ci_build(
          builder_group='chromium.fuzz',
          builder='some-ci-bot',
          builder_db=ctbc.BuilderDatabase.create({
              'chromium.fuzz': {
                  'some-ci-bot':
                      ctbc.BuilderSpec.create(
                          chromium_config='chromium',
                          gclient_config='chromium',
                      ),
              },
          })),
      api.platform.name('linux'),
      api.properties(
          upload_bucket='chromium-browser-libfuzzer',
          upload_directory='fuzz',
      ),
      api.step_data(
          'calculate all_fuzzers',
          stdout=api.raw_io.output_text('Warning: is_asan is not defined\n'
                                        '//foo/bar:target1\n')),
      api.step_data(
          'calculate no_clusterfuzz', stdout=api.raw_io.output_text('')),
      api.step_data(
          'list gn targets', stdout=api.raw_io.output_text('target1')),
      api.post_check(post_process.StepCommandDoesNotContain, 'list gn targets',
                     ['Warning: is_asan is not defined']),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'filter_asan_warning_in_gn_ls',
      api.chromium_tests_builder_config.ci_build(
          builder_group='chromium.fuzz',
          builder='some-ci-bot',
          builder_db=ctbc.BuilderDatabase.create({
              'chromium.fuzz': {
                  'some-ci-bot':
                      ctbc.BuilderSpec.create(
                          chromium_config='chromium',
                          gclient_config='chromium',
                      ),
              },
          })),
      api.platform.name('linux'),
      api.properties(
          upload_bucket='chromium-browser-libfuzzer',
          upload_directory='fuzz',
      ),
      api.step_data(
          'calculate all_fuzzers',
          stdout=api.raw_io.output_text('//foo/bar:target1\n')),
      api.step_data(
          'calculate no_clusterfuzz', stdout=api.raw_io.output_text('')),
      api.step_data(
          'list gn targets',
          stdout=api.raw_io.output_text('Warning: is_asan is not defined\n'
                                        'target1\n')),
      api.post_check(post_process.StepCommandContains, 'compile', ['target1']),
      api.post_check(post_process.StepCommandDoesNotContain, 'compile',
                     ['Warning: is_asan is not defined']),
      api.post_process(post_process.DropExpectation),
  )
