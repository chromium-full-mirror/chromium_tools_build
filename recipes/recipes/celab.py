# Copyright 2018 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import re

from recipe_engine import post_process

from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb

from RECIPE_MODULES.build import chromium_tests_builder_config as ctbc

DEPS = [
    'builder_group',
    'chromium',
    'chromium_checkout',
    'chromium_tests',
    'depot_tools/bot_update',
    'depot_tools/gclient',
    'depot_tools/gsutil',
    'infra/zip',
    'recipe_engine/buildbucket',
    'recipe_engine/cipd',
    'recipe_engine/context',
    'recipe_engine/file',
    'recipe_engine/json',
    'recipe_engine/path',
    'recipe_engine/platform',
    'recipe_engine/properties',
    'recipe_engine/step',
    'recipe_engine/time',
]

CELAB_REPO = 'https://chromium.googlesource.com/enterprise/cel'
CHROMIUM_REPO = 'https://chromium.googlesource.com/chromium/src'


def _get_bin_directory(api, bin_root):
  bin_dir = bin_root
  if api.platform.is_linux:
    bin_dir = bin_dir.join('linux_amd64', 'bin')
  elif api.platform.is_win:
    bin_dir = bin_dir.join('windows_amd64', 'bin')
  return bin_dir


def _get_ctl_binary_name(api):
  suffix = '.exe' if api.platform.is_win else ''
  return 'cel_ctl' + suffix


def _get_python_packages(api, checkout):
  """Returns the full path of the python whl package files."""
  out_dir = checkout.join('out')
  return api.file.glob_paths('find python packages', out_dir, '*.whl',
    test_data=['test.whl'])


def RunSteps(api):
  project = api.buildbucket.build.builder.project

  if project == 'celab':
    _RunStepsCelab(api)
  elif project in ('chromium', 'chrome'):
    compile_failure = _RunStepsChromium(api)
    if compile_failure:
      return compile_failure
  else:
    raise ValueError(
        'Invalid `project`. Accepted values: celab, chromium, chrome.')


def _RunStepsCelab(api):
  checkout = _CheckoutCelabRepo(api)

  # Build CELab binaries from source.
  bin_dir = _BuildCelabFromSource(api, checkout)

  # Upload binaries (cel_ctl and resources/*, plus the python package of the
  # test framework) for CI builds
  bucket = api.buildbucket.build.builder.bucket
  if bucket == 'ci':
    _UploadCelabBinariesToStorage(api, checkout, bin_dir)

  # Run tests for CI/Try builders that specify it.
  tests = api.properties.get('tests')
  if tests:
    _RunTests(
        api,
        checkout.join('test'),
        checkout.join('scripts').join('tests'),
        '../../examples/schema/host/example.host.textpb',
        tests,
    )


def _GetCelabVersionFromVPython(api, path):
  output = api.file.read_text('read vpython file', path)

  anything_except_closing = '[^>]*'
  pattern = '<'
  pattern += anything_except_closing
  pattern += 'name:\s*"infra/celab/celab/windows-amd64"'
  pattern += anything_except_closing
  pattern += 'version:\s*"(.*)"'
  pattern += anything_except_closing
  pattern += '>'

  match = re.search(pattern, output)
  if match:
    return match.groups(1)[0]

  raise ValueError('Couldn\'t find CELab version in vpython file: %s' % path)


def _RunStepsChromium(api):
  tests = api.properties.get('tests')
  if not tests:
    raise ValueError('Chromium bots must define `tests`.')

  # Build Chromium binaries from source and get CELab from CIPD.
  checkout = _CheckoutChromiumRepo(api)
  test_root = checkout.join('chrome', 'test', 'enterprise', 'e2e')
  chromium_bin_dir, raw_result = _BuildChromiumFromSource(api, test_root)
  if raw_result.status != common_pb.SUCCESS:
    return raw_result

  version = _GetCelabVersionFromVPython(api, test_root.join('.vpython3'))
  celab_bin_dir = _GetCelabFromCipd(api, version)

  # Run tests for all chromium bots.
  cel_ctl = celab_bin_dir.join(_get_ctl_binary_name(api))
  omaha_updater = chromium_bin_dir.join('updater.exe')
  omaha_installer = chromium_bin_dir.join('UpdaterSetup.exe')
  installer = chromium_bin_dir.join('mini_installer.exe')
  chromedriver = chromium_bin_dir.join('chromedriver.exe')
  test_py_args = '--cel_ctl=%s' % cel_ctl
  test_py_args += ' --test_arg=--omaha_updater=%s' % omaha_updater
  test_py_args += ' --test_arg=--omaha_installer=%s' % omaha_installer
  test_py_args += ' --test_arg=--chrome_installer=%s' % installer
  test_py_args += ' --test_arg=--chromedriver=%s' % chromedriver
  _RunTests(
    api,
    test_root,
    test_root.join('infra'),
    'template.host.textpb',
    tests,
    test_py_args)


def _GetCelabFromCipd(api, version):
  packages_root = api.path['start_dir'].join('packages')
  ensure_file = api.cipd.EnsureFile().add_package(
      'infra/celab/celab/${platform}', version)
  api.cipd.ensure(packages_root, ensure_file)
  return _get_bin_directory(api, packages_root)


def _CheckoutCelabRepo(api):
  # Checkout the CELab repo
  go_root = api.path['start_dir'].join('go')
  src_root = go_root.join('src', 'chromium.googlesource.com', 'enterprise')
  api.file.ensure_directory('init src_root if not exists', src_root)

  with api.context(cwd=src_root):
    api.gclient.set_config('celab')
    api.bot_update.ensure_checkout()
    api.gclient.runhooks()
  return api.path['checkout']


def _BuildCelabFromSource(api, checkout):
  go_root = api.path['start_dir'].join('go')

  # Install Go & Protoc
  packages_root = api.path['start_dir'].join('packages')
  ensure_file = api.cipd.EnsureFile()
  ensure_file.add_package('infra/3pp/tools/go/${platform}', 'version:2@1.18.1')
  ensure_file.add_package('infra/tools/protoc/${platform}',
                          'protobuf_version:v3.6.1')
  ensure_file.add_package('infra/third_party/cacert', 'date:2017-01-18')
  api.cipd.ensure(packages_root, ensure_file)

  add_paths = [
    go_root.join('bin'),
    packages_root,
    packages_root.join('bin'),
  ]

  # Build CELab
  cert_file = packages_root.join('cacert.pem')
  goenv = {'GOPATH': go_root, 'GIT_SSL_CAINFO': cert_file}
  with api.context(cwd=checkout, env=goenv, env_suffixes={'PATH': add_paths}):
    api.step('install deps',
             ['python3', 'build.py', 'deps', '--install', '--verbose'])
    api.step('build', ['python3', 'build.py', 'build', '--verbose'])
    api.step('create python package',
             ['python3', 'build.py', 'create_package', '--verbose'])

  return _get_bin_directory(api, checkout.join('out'))


def _CheckoutChromiumRepo(api):
  project = api.buildbucket.build.builder.project

  with api.chromium.chromium_layout():
    builder_config = {
        'chromium_config': 'chromium',
        'gclient_config': 'chromium',
        'chromium_apply_config': ['mb'],
        'chromium_config_kwargs': {
            'BUILD_CONFIG': 'Release',
            'TARGET_BITS': 64,
        },
    }

    if project == 'chrome':
      builder_config['gclient_apply_config'] = [
          'chrome_internal',
          'checkout_pgo_profiles',
      ]

    builder_config = ctbc.BuilderSpec.create(**builder_config)

    api.chromium_tests.configure_build(builder_config)
    api.chromium_checkout.ensure_checkout(builder_config)
    api.chromium.runhooks()

  return api.path['checkout']


def _BuildChromiumFromSource(api, test_root):
  with api.chromium.chromium_layout():
    compile_targets = [
        'chrome/updater', 'chrome/installer/mini_installer', 'chromedriver'
    ]
    raw_result = api.chromium_tests.run_mb_and_compile(
        api.chromium.get_builder_id(),
        compile_targets,
        isolated_targets=[],
        name_suffix=' (with patch)')

  return api.chromium.output_dir, raw_result


def _UploadCelabBinariesToStorage(api, checkout, bin_dir):
  cel_ctl = _get_ctl_binary_name(api)
  zip_out = api.path['start_dir'].join('cel.zip')
  pkg = api.zip.make_package(checkout.join('out'), zip_out)
  pkg.add_file(bin_dir.join(cel_ctl))
  pkg.add_directory(bin_dir.join('resources'))
  for package_file in _get_python_packages(api, checkout):
    pkg.add_file(package_file)
  pkg.zip('zip archive')

  today = api.time.utcnow().date()
  gs_dest = '%s/%s/%s/cel.zip' % (
    api.buildbucket.builder_name,
    today.strftime('%Y/%m/%d'),
    api.buildbucket.build.id)
  api.gsutil.upload(
    source=zip_out,
    bucket='celab',
    dest=gs_dest,
    name='upload CELab binaries',
    link_name='CELab binaries')


def _RunTests(api,
              test_root,
              test_scripts_root,
              host_file_template,
              tests,
              test_py_args=''):
  pool_name = api.properties.get('pool_name')
  pool_size = api.properties.get('pool_size')

  if not pool_name or not pool_size:
    raise ValueError('pool_name and pool_size must be defined with `tests`.')

  host_dir = api.path['start_dir'].join('hosts')
  logs_dir = api.path['start_dir'].join('logs')
  with api.step.nest('setup tests'):
    api.file.ensure_directory('init host_dir if not exists', host_dir)
    api.file.ensure_directory('init logs_dir if not exists', logs_dir)

    # Install required package for gsutil.
    packages_root = api.path['start_dir'].join('packages_tests')

    ensure_file = api.cipd.EnsureFile().add_package(
        'infra/gcloud/${platform}', 'version:251.0.0.chromium0')
    api.cipd.ensure(packages_root, ensure_file)
    add_paths = [packages_root.join('bin')]

    # Get a unique storage prefix for these tests (diff runs share the bucket)
    storage_prefix = 'test-run-%s' % api.buildbucket.build.id

    # Generate the host files that we'll use in ./run_tests.py.
    with api.context(cwd=test_scripts_root):
      api.step('generate host files', [
          'python3', 'generate_host_files.py', '--template', host_file_template,
          '--projects', ';'.join([
              '%s-%03d' % (pool_name, i) for i in range(1, pool_size + 1)
          ]), '--storage_bucket',
          '%s-assets' % pool_name, '--storage_prefix', storage_prefix,
          '--destination_dir', host_dir
      ])

  # Run our tests and catch test failures.
  storage_logs = '%s-logs' % pool_name
  with api.context(cwd=test_root, env_suffixes={'PATH': add_paths}):
    extra_args = []

    test_py_args += ' --no_external_access=True'
    extra_args += ['--test_py_args=%s' % test_py_args.strip()]

    include_tests = api.properties.get('include')
    if include_tests:
      extra_args += ['--include', include_tests]

    exclude_tests = api.properties.get('exclude')
    if exclude_tests:
      extra_args += ['--exclude', exclude_tests]

    try:
      api.step(
          'run all tests',
          [
              'vpython3', '-u', 'run_tests.py', '--tests', tests, '--hosts',
              host_dir, '--test_py', 'test.py', '--shared_provider_storage',
              '%s-assets' % pool_name, '--error_logs_dir', logs_dir,
              '--noprogress', '-v', '1'
          ] + extra_args,
      )
    except:
      # We upload *all* logs, including those we reupload in _ParseTestSummary.
      # It's better to upload (small) logs twice than to not upload them at
      # all. They are automatically deleted after 30 days (bucket policy).
      _ZipAndUploadDirectory(
        api,
        storage_logs,
        logs_dir,
        'all_logs.zip',
        'CELab Test Logs')

      raise
    finally:
      # TODO: Clean up storage prefix when the test run ends.
      #       It's already automatically deleted after 1 day.

      # Parse the test summary file and organize results in a readable way.
      _ParseTestSummary(api, storage_logs, logs_dir)


# Zips the content of a directory and uploads the zip file to a given bucket.
def _ZipAndUploadDirectory(api, bucket, directory, zip_filename, display_name):
  zip_out = api.path['start_dir'].join(zip_filename)
  pkg = api.zip.make_package(directory, zip_out)
  pkg.add_directory(directory)
  pkg.zip('zip logs archive')

  today = api.time.utcnow().date()
  gs_dest = '%s/%s/%s/%s' % (
    api.buildbucket.builder_name,
    today.strftime('%Y/%m/%d'),
    api.buildbucket.build.id,
    zip_filename)
  return api.gsutil.upload(
    source=zip_out,
    bucket=bucket,
    dest=gs_dest,
    name='upload %s' % display_name,
    link_name=display_name)


# Parses the summary.json file created by run_tests.py, organizes the steps
# presentation of tests and creates separate zips for each test logs.
def _ParseTestSummary(api, storage_logs, logs_dir):
  summary_path = logs_dir.join('summary.json')

  with api.step.nest('test summary') as summary_step:
    summary_presentation = summary_step.presentation
    tests_summary = api.json.read('parse summary', summary_path).json.output

    if not tests_summary:
      return None

    for test in tests_summary:
      try:
        with api.step.nest(test) as test_step:
          test_presentation = test_step.presentation
          result = tests_summary[test]

          test_presentation.status = api.step.SUCCESS

          if not result['success']:
            test_presentation.status = api.step.FAILURE
            summary_presentation.status = api.step.FAILURE

            if 'output' in result:
              logs = api.file.read_text('read logs', result['output'])
              test_presentation.logs['test.py output'] = logs.splitlines()

            # Upload logs if they exist (test fails after Deployment starts)
            compute_logs_dir = logs_dir.join(test)
            if api.path.exists(compute_logs_dir):
              upload_step = _ZipAndUploadDirectory(
                api,
                storage_logs,
                compute_logs_dir,
                test + '.zip',
                'Compute logs')

              # Merge the gsutil links in the Test step.
              upload_presentation = upload_step.presentation
              for link in upload_presentation.links:
                test_presentation.links[link] = upload_presentation.links[link]
      except Exception as e:
        summary_presentation.logs['exception %s' % test] = repr(e).splitlines()

    return tests_summary


def GenTests(api):
  yield api.test(
      'basic_try',
      api.buildbucket.try_build(
          project='celab', bucket='try', git_repo=CELAB_REPO),
  )
  yield api.test(
      'basic_ci_linux',
      api.platform('linux', 64),
      api.buildbucket.ci_build(
          project='celab', bucket='ci', git_repo=CELAB_REPO),
  )
  yield api.test(
      'basic_ci_windows',
      api.platform('win', 64),
      api.buildbucket.ci_build(
          project='celab', bucket='ci', git_repo=CELAB_REPO),
  )
  yield api.test(
      'failed_tests_ci_linux',
      api.platform('linux', 64),
      api.properties(tests='*', pool_name='celab-ci', pool_size=5),
      api.buildbucket.ci_build(
          project='celab', bucket='ci', git_repo=CELAB_REPO),
      api.step_data('run all tests', retcode=1),
      api.step_data(
          'test summary.parse summary',
          api.json.output({
              '1st test': {
                  'success': False,
                  'output': '/some/file'
              },
              '2nd test': {
                  'success': True,
                  'output': '/other/file'
              },
              '3rd test': {
                  'success': False,
                  'output': '/missing'
              }
          })),
      api.step_data('test summary.1st test.read logs',
                    api.file.read_text('first\ntest\nlogs')),
      api.step_data('test summary.3rd test.read logs',
                    api.file.errno('EEXIST')),
      api.path.exists(api.path['start_dir'].join('logs', '1st test')),
  )
  yield api.test(
      'failed_tests_no_summary_ci_linux',
      api.platform('linux', 64),
      api.properties(tests='*', pool_name='celab-ci', pool_size=5),
      api.buildbucket.ci_build(
          project='celab', bucket='ci', git_repo=CELAB_REPO),
      api.step_data('run all tests', retcode=1),
      api.step_data('test summary.parse summary', retcode=1),
  )
  yield api.test(
      'windows_quick_tests',
      api.properties(
          tests='sample.test.*',
          include='quick_test',
          exclude='long_test',
          pool_name='celab-try',
          pool_size=5),
      api.platform('win', 64),
      api.buildbucket.ci_build(
          project='celab',
          bucket='try',
          builder='windows-quick-tests',
          git_repo=CELAB_REPO),
  )
  yield api.test(
      'misconfigured_tests',
      api.properties(tests='sample.test'),
      api.platform('win', 64),
      api.buildbucket.ci_build(
          project='celab',
          bucket='try',
          builder='misconfigured-quick-tests',
          git_repo=CELAB_REPO),
      api.expect_exception('ValueError'),
  )
  yield api.test(
      'chromium_try',
      api.builder_group.for_current('tryserver.chromium.win'),
      api.properties(
          tests='chromium.test',
          pool_name='chromium-try',
          pool_size=5,
          bot_id='test_bot'),
      api.platform('win', 64),
      api.buildbucket.try_build(
          project='chromium',
          bucket='luci.chromium.try',
          builder='win-celab-try-rel',
          git_repo=CHROMIUM_REPO),
      api.step_data(
          'read vpython file',
          api.file.read_text('''wheel: <
              name: "infra/celab/celab/windows-amd64"
              version: "celab_package_version"
                             >''')),
      api.step_data(
          'test summary.parse summary',
          api.json.output({'1st test': {
              'success': False,
              'output': '/file'
          }})),
  )
  yield api.test(
      'chromium_no_tests',
      api.builder_group.for_current('tryserver.chromium.win'),
      api.properties(bot_id='test_bot'),
      api.platform('win', 64),
      api.buildbucket.try_build(
          project='chromium',
          bucket='luci.chromium.try',
          builder='win-celab-try-rel',
          git_repo=CHROMIUM_REPO),
      api.expect_exception('ValueError'),
  )
  yield api.test(
      'chromium_no_celab_package',
      api.builder_group.for_current('tryserver.chromium.win'),
      api.properties(tests='chromium.test', bot_id='test_bot'),
      api.platform('win', 64),
      api.buildbucket.try_build(
          project='chromium',
          bucket='luci.chromium.try',
          builder='win-celab-try-rel',
          git_repo=CHROMIUM_REPO),
      api.step_data(
          'read vpython file',
          api.file.read_text('''wheel: <
              name: "infra/other/package"
              version: "package_version"
                             >''')),
      api.expect_exception('ValueError'),
  )
  yield api.test(
      'invalid_project',
      api.buildbucket.ci_build(project='other-project'),
      api.expect_exception('ValueError'),
  )
  yield api.test(
      'compile_failure',
      api.builder_group.for_current('tryserver.chromium.win'),
      api.properties(
          tests='chromium.test',
          pool_name='chromium-try',
          pool_size=5,
          bot_id='test_bot'),
      api.platform('win', 64),
      api.buildbucket.try_build(
          project='chromium',
          bucket='luci.chromium.try',
          builder='win-celab-try-rel',
          git_repo=CHROMIUM_REPO),
      api.step_data('compile (with patch)', retcode=1),
      api.post_process(post_process.StatusFailure),
      api.post_process(post_process.DropExpectation),
  )
  yield api.test(
      'chrome_try',
      api.builder_group.for_current('tryserver.chrome.win'),
      api.properties(
          tests='chrome.test',
          pool_name='chrome-try',
          pool_size=5,
          bot_id='test_bot'),
      api.platform('win', 64),
      api.buildbucket.try_build(
          project='chrome',
          bucket='luci.chrome.try',
          builder='win-celab-try-rel',
          git_repo=CHROMIUM_REPO),
      api.step_data(
          'read vpython file',
          api.file.read_text('''wheel: <
              name: "infra/celab/celab/windows-amd64"
              version: "celab_package_version"
                             >''')),
      api.step_data(
          'test summary.parse summary',
          api.json.output({'1st test': {
              'success': False,
              'output': '/file'
          }})),
  )
