# Copyright 2024 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from contextlib import contextmanager
import re

from recipe_engine.recipe_api import Property

DEPS = [
    'builder_group',
    'chromium',
    'devtools',
    'build/perf_dashboard',
    'depot_tools/bot_update',
    'depot_tools/depot_tools',
    'depot_tools/gclient',
    'depot_tools/git',
    'depot_tools/gsutil',
    'depot_tools/tryserver',
    'recipe_engine/buildbucket',
    'recipe_engine/cipd',
    'recipe_engine/context',
    'recipe_engine/file',
    'recipe_engine/path',
    'recipe_engine/platform',
    'recipe_engine/properties',
    'recipe_engine/raw_io',
    'recipe_engine/step',
    'recipe_engine/url',
]

REPO_URL = 'https://chromium.googlesource.com/devtools/devtools-frontend.git'

# Bucket belongs to v8-infra project owned by v8-infra@
# webassembly-debugging-project-team@ group has read access to the bucket
GS_BUCKET = 'devtools-internal-dwarf-extension'
DWARF_BRANCH_PATTERN = re.compile(r'refs/heads/chrome-extensions/'
                                  r'dwarf/releases/\d+\.\d+\.(\d+)')

PROPERTIES = {
    'e2e_builder':
        Property(
            kind=bool,
            default=False,
            help='Whether the builder needs to build and run E2E tests'),
    'builder_config':
        Property(
            kind=str, default='Debug', help='The build configuration name'),
    'clobber':
        Property(
            kind=bool,
            help='Should the builder clean up the extensions/cxx_debugging/build/'
            ' folder before building',
            default=False),
    'upload_dwarf_binary':
        Property(
            kind=bool,
            default=False,
            help='Indicate if the builder uploads the binary to gc storage.'),
}


def RunSteps(api, e2e_builder, builder_config, clobber, upload_dwarf_binary):
  _configure(api)
  cwd = get_checkout_path(api, 'builder')
  with api.context(cwd=cwd):
    api.bot_update.ensure_checkout()
    api.gclient.runhooks()

  if clobber:
    path_to_clean = _repo_path(api).joinpath('build')
    api.file.rmtree('clean outdir', path_to_clean)

  if e2e_builder:
    with dwarf_ext_context(api, 'builder'):
      compile_n_test_e2e(api, builder_config, upload_dwarf_binary)
  else:
    extra_args = ['-release'] if builder_config == 'Release' else []
    build_dwarf_extension(api, target='builder', extra_build_args=extra_args)


def compile_n_test_e2e(api, builder_config, upload_dwarf_binary):
  compile_command = [
      "vpython3",
      "-u",
      _repo_path(api) / 'e2e' / 'runner.py',
      'compile',
      '-v',
      '--build-root',
      _repo_path(api) / 'build',
  ]
  version, patch = None, None
  if builder_config == 'Release' and not api.tryserver.is_tryserver:
    version, patch = resolve_version_patch(api)
    compile_command.append('--release-version')
    compile_command.append(version)
    compile_command.append('--patch-level')
    compile_command.append(patch)

  api.step('Build', compile_command)
  run_script(api, 'Run E2E Test Suite', 'e2e/runner.py',
             ['run', '-v', '--build-root',
              _repo_path(api) / 'build'])
  if upload_dwarf_binary:
    assert version, 'Version number missing!'
    upload_build(api, version, patch)


def git_output(api, *args, **kwargs):
  """Convenience wrapper."""
  step_result = api.git(*args, stdout=api.raw_io.output_text(), **kwargs)
  result = step_result.stdout
  step_result.presentation.logs['stdout'] = result.splitlines()
  return result.strip()


def get_gitiles_ref(api):
  scheduler_props = api.properties.get("$recipe_engine/scheduler", {})
  triggers_props = scheduler_props.get('triggers', [{}])[0]
  gitiles_props = triggers_props.get('gitiles', {})
  return gitiles_props.get('ref', None)


def resolve_version_patch(api):
  ref = get_gitiles_ref(api)
  if ref == 'refs/heads/main':
    return api.buildbucket.build.number, 0

  version = DWARF_BRANCH_PATTERN.search(ref).group(1)
  commit_count = git_output(api, 'rev-list', '--count', 'main..')
  return version, commit_count


def upload_build(api, version, patch):
  extension_dir = _repo_path(api).joinpath('build',
                                           'DevTools_CXX_Debugging.stage2')
  result = api.step(
      "Find extension zip file",
      [
          "find",
          extension_dir,
          "-name",
          # Stage 2 of the build generates a zip file of the form
          # cxx_dwarf_extension-MAJOR.MINOR.PATCH.BUILD.zip. We control only the
          # BUILD part of the name and the MAJOR.MINOR.PATCH can change in the
          # project source files, so we wildcard it.
          f"cxx_debugging_extension-*.{version}.{patch}.zip",
          "-printf",
          "%f",
      ],
      stdout=api.raw_io.output_text(),
      step_test_data=lambda: api.raw_io.test_api.stream_output_text(
          "cxx_dwarf_extension-1.2.456.0.zip"),
  )
  file_name = result.stdout.strip()
  result.presentation.logs['file_name'] = file_name

  assert file_name
  artifact_name = extension_dir / file_name

  name_prefix = '' if get_gitiles_ref(api) == 'refs/heads/main' else 'release_'
  upload_name = api.url.join("builds", name_prefix + file_name)
  api.gsutil.upload(
      source=artifact_name,
      bucket=GS_BUCKET,
      dest=upload_name,
      link_name=upload_name,
      metadata={
          'build_number': api.buildbucket.build.number,
          'builder_name': api.buildbucket.builder_name,
          'gitiles_commit': api.buildbucket.gitiles_commit.id,
      },
      name='upload dwarf binary',
  )


def _repo_path(api):
  checkout_path = get_checkout_path(api, 'builder')
  return checkout_path.joinpath('devtools-frontend', 'extensions',
                                'cxx_debugging')


def _configure(api):
  c = api.gclient.make_config()
  soln = c.solutions.add()
  soln.name = 'devtools-frontend'
  soln.url = REPO_URL
  soln.revision = api.buildbucket.gitiles_commit.id or 'HEAD'
  soln.custom_vars.update(checkout_cxx_debugging_extension_deps=True)
  c.got_revision_mapping[soln.name] = 'got_revision'
  api.gclient.c = c

  build_cfg = api.chromium.make_config()
  build_cfg.build_config_fs = 'Release'
  build_cfg.gn_args.append('devtools_skip_typecheck=true')
  api.chromium.c = build_cfg


def run_script(api, step_name, script, args=None):
  sc_path = _repo_path(api) / script
  args = args or []
  api.step(step_name, ["vpython3", "-u", sc_path] + args)


### CPP Debug Extension ###
# TODO(liviurau): this code moved from the internal recipe module; it should
# be refactored and cleaned up
def get_checkout_path(api, target='head'):
  path = api.path.start_dir / target
  api.file.ensure_directory('Ensure checkout dir for %s exists' % path, path)
  return path


@contextmanager
def dwarf_ext_context(api, target='head'):
  """Set the context to run the dwarf_extension scripts. It adds the extension
  dependencies to the PATH."""

  dtf_path = get_checkout_path(api, target) / 'devtools-frontend'

  thirdparty_path = dtf_path / 'third_party'

  ext_path = dtf_path.joinpath('extensions', 'cxx_debugging')
  ext_thirdparty_path = ext_path / 'third_party'
  path_prefix = [
      thirdparty_path.joinpath('cmake', 'bin'),
      ext_thirdparty_path.joinpath('llvm-build', 'Release+Asserts', 'bin'),
      thirdparty_path.joinpath('node', 'linux', 'node-linux-x64', 'bin'),
      api.depot_tools.root,
  ]

  with api.context(env_prefixes={'PATH': path_prefix}, cwd=ext_path):
    yield


def build_dwarf_extension(api, target='head', extra_build_args=None):
  extra_build_args = extra_build_args or []

  dwarf_tools = api.path.join(
      get_checkout_path(api, target), 'devtools-internal', 'dwarf_extension',
      'tools')

  with api.step.nest('Build dwarf extension'), \
      dwarf_ext_context(api, target):
    exc = api.path.join(dwarf_tools, 'bootstrap.py')
    build_command = [
        'vpython3',
        '-u',
        exc,
        '-infra',
        get_checkout_path(api, target).joinpath('devtools-internal', 'out',
                                                'build'),
    ] + extra_build_args
    api.step('build', build_command)


# TODO(liviurau): add more asserting and expectation dropping to most of these
# tests in a follow up.
def GenTests(api):

  def scheduler_ref_propery(ref='refs/heads/main'):
    return api.properties(
        **
        {"$recipe_engine/scheduler": {
            'triggers': [{
                'gitiles': {
                    'ref': ref
                }
            }]
        }})

  yield api.test(
      'basic try',
      api.builder_group.for_current('tryserver.devtools-internal'),
      api.buildbucket.try_build(
          'devtools',
          'linux',
          git_repo=REPO_URL,
          change_number=91827,
          patch_set=1),
      api.properties(e2e_builder=True, clobber=True),
      status='SUCCESS',
  )

  yield api.test(
      'basic ci',
      api.buildbucket.ci_build(
          'devtools',
          'linux',
          git_repo=REPO_URL,
          revision='8b3cd40a25a512033cc8c0797e41de9ecfc2432c',
      ),
      api.properties(e2e_builder=False),
      status='SUCCESS',
  )

  yield api.test(
      'release',
      api.buildbucket.ci_build(
          'devtools',
          'linux',
          git_repo=REPO_URL,
          revision='8b3cd40a25a512033cc8c0797e41de9ecfc2432c',
      ),
      api.properties(builder_config='Release'),
      status='SUCCESS',
  )

  yield api.test(
      'release e2e',
      api.buildbucket.ci_build(
          'devtools',
          'linux',
          git_repo=REPO_URL,
          revision='8b3cd40a25a512033cc8c0797e41de9ecfc2432c',
      ),
      api.properties(e2e_builder=True, builder_config='Release'),
      scheduler_ref_propery(),
      status='SUCCESS',
  )

  def upload_dwarf_test(suffix, builder_config='Release'):
    return api.test(
        f'upload dwarf - {suffix}',
        api.buildbucket.ci_build(
            'devtools',
            'linux',
            git_repo=REPO_URL,
            revision='8b3cd40a25a512033cc8c0797e41de9ecfc2432c',
        ),
        api.properties(
            e2e_builder=True,
            builder_config=builder_config,
            upload_dwarf_binary=True),
        api.buildbucket.ci_build(
            project='devtools',
            bucket='ci',
            builder='dwarf_builder',
            build_number=456,
            git_repo=REPO_URL,
            revision="abcd"),
        status='SUCCESS',
    )

  yield upload_dwarf_test('in main') + scheduler_ref_propery()

  yield upload_dwarf_test('in release branch') + scheduler_ref_propery(
      'refs/heads/chrome-extensions/dwarf/releases/0.1.2345') + api.step_data(
          'git rev-list', api.raw_io.stream_output_text('9'))
