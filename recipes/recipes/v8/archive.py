# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""
Recipe for archiving officially tagged v8 builds.
"""

import re

from recipe_engine.config import Single
from recipe_engine.post_process import (DoesNotRun, DropExpectation, Filter,
                                        MustRun)
from recipe_engine.recipe_api import Property

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import chromium, v8
from RECIPE_MODULES.depot_tools import git, gsutil
from RECIPE_MODULES.infra import zip as zip_module
from RECIPE_MODULES.recipe_engine import (
    buildbucket,
    file,
    json,
    path,
    platform,
    properties,
    raw_io,
    step,
)


@dataclass
class DEPS(RecipeScriptApi):
  buildbucket: buildbucket.API
  chromium: chromium.API
  file: file.API
  git: git.API
  gsutil: gsutil.API
  json: json.API
  path: path.API
  platform: platform.API
  properties: properties.API
  raw_io: raw_io.API
  step: step.API
  v8: v8.API
  zip: zip_module.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  chromium: chromium.TEST_API
  git: git.TEST_API
  json: json.TEST_API
  path: path.TEST_API
  platform: platform.TEST_API
  properties: properties.TEST_API
  raw_io: raw_io.TEST_API
  step: step.TEST_API
  v8: v8.TEST_API


PROPERTIES = {
    # One of Release|Debug.
    'build_config': Property(default=None, kind=str),
    # One of intel|arm|mips.
    'target_arch': Property(default=None, kind=str),
    # One of 32|64.
    'target_bits': Property(default=None, kind=Single((int, float))),
    # One of android|fuchsia|linux|mac|win.
    'target_platform': Property(default=None, kind=str),
    # Whether to upload archive.
    'upload_archive': Property(default=True, kind=bool),
}

ARCHIVE_LINK = 'https://storage.googleapis.com/chromium-v8/official/%s/%s'
BRANCH_RE = re.compile(r'^refs/(branch-heads/\d+\.\d+|heads/\d+\.\d+\.\d+)$')
RELEASE_BRANCH_RE = re.compile(r'^(?:refs/branch-heads/)?(\d+\.\d+)$')
FIRST_BUILD_IN_MILESTONE_RE = re.compile(r'^\d+\.\d+\.\d+$')


def make_archive(api: DEPS,
                 bot_config,
                 ref,
                 version,
                 archive_type,
                 step_suffix='',
                 archive_suffix='',
                 upload_archive=True):
  with api.step.nest('sync' + step_suffix):
    api.v8.apply_bot_config(bot_config)
    if archive_type == 'ref':
      api.chromium.c.gn_args.append('is_official_build=true')
      api.chromium.c.gn_args.append('chrome_pgo_phase=0')
    api.chromium.apply_config('clobber')
    api.chromium.apply_config('default_target_v8_archive')
    if api.chromium.c.BUILD_CONFIG == 'Debug':
      api.chromium.apply_config('slow_dchecks')
    elif archive_type in ['all', 'lib']:
      api.chromium.apply_config('v8_static_library')
    if api.chromium.c.BUILD_CONFIG == 'Release':
      api.chromium.c.gn_args.append('dcheck_always_on=false')
    if api.chromium.c.TARGET_PLATFORM == 'android':
      api.chromium.apply_config('v8_android')
    elif api.chromium.c.TARGET_ARCH == 'arm':
      api.chromium.apply_config('arm_hard_float')

    api.chromium.c.gn_args.append('use_siso=true')
    api.chromium.c.gn_args.append('use_remoteexec=true')

    # Opt out of using gyp environment variables.
    api.chromium.c.use_gyp_env = False
    update_result = api.v8.checkout()
    source_dir = update_result.source_root.path

    if not version:
      version = str(
          api.v8.read_version_from_ref(source_dir, api.v8.revision, 'head'))
      tags = set(x.strip() for x in api.git(
          'describe',
          '--tags',
          'HEAD',
          stdout=api.raw_io.output_text(),
      ).stdout.strip().splitlines())

      if version not in tags:
        api.step('Skipping due to missing tag.', cmd=None)
        return None, None

    # This automatically deletes build_dir since we specify clobber above.
    build_dir = api.v8.build_dir(source_dir)
    api.v8.runhooks(source_dir, build_dir)

  with api.step.nest('build' + step_suffix):
    compile_failure = api.v8.compile(source_dir, build_dir)
    if compile_failure:
      return None, compile_failure

  with api.step.nest('make archive' + step_suffix) as parent:
    # Make a list of files to archive.
    files = ['d8', 'icudtl.dat']
    file_list_test_data = [str(build_dir / f) for f in files]
    file_list = api.v8.python(
        'filter build files',
        source_dir.joinpath('tools', 'release', 'filter_build_files.py'),
        [
            '--dir',
            build_dir,
            '--platform',
            api.chromium.c.TARGET_PLATFORM,
            '--type',
            archive_type,
            '--json-output',
            api.json.output(),
        ],
        infra_step=True,
        step_test_data=lambda: api.json.test_api.output(file_list_test_data),
    ).json.output

    # Zip build.
    zip_file = api.path.cleanup_dir / 'archive.zip'
    package = api.zip.make_package(build_dir, zip_file)
    for f in file_list:
      package.add_file(api.path.abs_to_path(f))
    package.zip('zipping')

    if api.chromium.c.TARGET_ARCH != 'intel':
      # Only disambiguate non-intel architectures. This is closest to our naming
      # conventions.
      arch_name = '-%s' % api.chromium.c.TARGET_ARCH
    else:
      arch_name = ''

    if not upload_archive:
      if archive_type == 'ref':
        return None, None
      return version, None

    # Upload refbuild and trigger refbuild bundler.
    if archive_type == 'ref':
      platform = '%s%s%s%s' % (api.chromium.c.TARGET_PLATFORM, arch_name,
                               api.chromium.c.TARGET_BITS, archive_suffix)
      api.gsutil.upload(
          zip_file,
          'chromium-v8/official/refbuild',
          'v8-%s-rel.zip' % platform,
          args=['-a', 'public-read'],
          name='update refbuild binaries',
      )
      # Always trigger the ref-build bundler in prod as led doesn't work there.
      builder_config = [('v8_refbuild_bundler', {
          'revision': api.v8.revision,
          'platform': platform,
      })]
      api.v8.trigger_prod.buildbucket(
          builder_config,
          project='v8-internal',
          bucket='ci-ref',
          step_name='trigger refbuild bundler')
      return None, None

    # Upload to google storage bucket.
    build_config = 'rel' if api.chromium.c.BUILD_CONFIG == 'Release' else 'dbg'
    archive_prefix = (
        'v8-%s%s%s%s-%s' %
        (api.chromium.c.TARGET_PLATFORM, arch_name, api.chromium.c.TARGET_BITS,
        archive_suffix, build_config)
    )
    archive_name = '%s-%s.zip' % (archive_prefix, version)
    branch_match = RELEASE_BRANCH_RE.match(ref)
    gs_path_suffix = branch_match.group(1) if branch_match else 'canary'
    gs_path = 'chromium-v8/official/%s' % gs_path_suffix
    api.gsutil.upload(
        zip_file,
        gs_path,
        archive_name,
        args=['-a', 'public-read'],
        name='upload',
    )

    if gs_path_suffix == 'canary':
      api.gsutil.upload(
          api.json.input({'version': version}),
          gs_path,
          '%s-latest.json' % archive_prefix,
          args=['-a', 'public-read'],
          name='upload json',
      )

    parent.links['download'] = (
        ARCHIVE_LINK % (gs_path_suffix, archive_name))
    return version, None


def RunSteps(api: DEPS, build_config, target_arch, target_bits, target_platform,
             upload_archive):
  target_bits = int(target_bits)

  with api.step.nest('initialization'):
    # Ensure a proper branch is specified.
    ref = api.buildbucket.gitiles_commit.ref
    if not ref or not BRANCH_RE.match(ref):
      api.step('Skipping due to missing release branch.', cmd=None)
      return

    # Apply properties from cr-buildbucket.cfg.
    bot_config = {
        'chromium_apply_config': ['default_compiler', 'gn'],
        'v8_config_kwargs': {},
    }
    for key, value in (
        ('BUILD_CONFIG', build_config),
        ('TARGET_ARCH', target_arch),
        ('TARGET_BITS', target_bits),
        ('TARGET_PLATFORM', target_platform)):
      if value:
        bot_config['v8_config_kwargs'][key] = value

  if build_config == 'Debug':
    # Debug binaries require libraries to be present in the same archive to
    # run.
    version, compile_failure = make_archive(
        api,
        bot_config,
        ref,
        None,
        'all',
        upload_archive=upload_archive)
    if compile_failure:
      return compile_failure
  else:
    version, compile_failure = make_archive(
        api,
        bot_config,
        ref,
        None,
        'exe',
        upload_archive=upload_archive)
    if compile_failure:
      return compile_failure
    if not version:
      return
    version, compile_failure = make_archive(
        api,
        bot_config,
        ref,
        version,
        'lib',
        ' (libs)',
        '-libs',
        upload_archive=upload_archive)
    if compile_failure:
      return compile_failure

    # Upload first build for the latest milestone to a known location. We use
    # these binaries for running reference perf tests.
    if (RELEASE_BRANCH_RE.match(ref) and
        FIRST_BUILD_IN_MILESTONE_RE.match(version)):
      version, compile_failure = make_archive(
          api,
          bot_config,
          ref,
          version,
          'ref',
          ' (ref)',
          upload_archive=upload_archive)
      if compile_failure:
        return compile_failure


def GenTests(api: TEST_DEPS):
  def test_defaults(name, platform, build_config, status='SUCCESS', **kwargs):
    return api.test(
        api.v8.test_name('client.v8.official', 'V8 Foobar', name),
        api.chromium.ci_build(
            builder_group='client.v8.official',
            project='v8',
            git_repo='https://chromium.googlesource.com/v8/v8',
            git_ref='refs/branch-heads/3.4',
            builder='V8 Foobar',
            revision='a' * 40),
        api.properties(build_config=build_config, **kwargs),
        api.platform(platform, 64),
        api.v8.version_file(17, 'head', prefix='sync.'),
        api.override_step_data('sync.git describe',
                               api.raw_io.stream_output_text('3.4.3.17')),
        api.v8.check_param_equals(
            'sync.bot_update', '--revision', 'v8@refs/branch-heads/' +
            '3.4:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'),
        api.post_process(MustRun, 'sync.clobber', 'sync.gclient runhooks',
                         'build.gn', 'build.compile', 'make archive.zipping',
                         'make archive.gsutil upload'),
        status=status,
    )

  def filter_steps(is_release):
    expected_steps = [
        'build.gn', 'build.compile', 'make archive',
        'make archive.filter build files', 'make archive.zipping',
        'make archive.gsutil upload',
    ]
    if is_release:
      expected_steps.extend([
        'build (libs)', 'build (libs).gn', 'build (libs).compile',
        'make archive (libs)', 'make archive (libs).filter build files',
        'make archive (libs).zipping', 'make archive (libs).gsutil upload',
      ])

    return api.post_process(Filter(*expected_steps))

  # Test standard run on linux.
  yield (
      test_defaults('linux', 'linux', build_config='Debug', target_bits=64) +
      filter_steps(is_release=False)
  )

  # Test Android-specific things.
  yield (
      test_defaults(
          'android', 'linux', build_config='Release',
          target_arch='arm', target_bits=64, target_platform='android') +
      # Make sure bot_update specifies target_os on Android builders.
      api.v8.check_in_param(
          'sync.bot_update', '--spec-path',
          'target_os = [\'android\']') +
      filter_steps(is_release=True)
  )

  # Test Arm-specific things.
  yield (
      test_defaults(
          'arm', 'linux', build_config='Release',
          target_arch='arm', target_bits=32) +
      # Make sure bot_update specifies target_cpu on Arm builders.
      api.v8.check_in_param(
          'sync.bot_update', '--spec-path',
          'target_cpu = [\'arm\', \'arm64\']') +
      filter_steps(is_release=True)
  )

  # Test Windows-specific things.
  yield (
      test_defaults(
          'windows', 'win', build_config='Release', target_bits=64) +
      # Check that _x64 suffix is correctly removed on windows.
      api.v8.check_param_equals(
          'make archive.filter build files',
          '--dir',
          '[CACHE]\\builder\\v8\\out\\build') +
      # Show GN configs to be resiliant to changes of chromium configs.
      api.post_process(Filter('build.gn'))
  )

  # Test Mac-specific things.
  yield (
      test_defaults(
          'mac', 'mac', build_config='Release', target_bits=64) +
      # Show GN configs to be resiliant to changes of chromium configs.
      api.post_process(Filter('build.gn'))
  )

  # Test bailout on missing branch.
  builder_group = 'client.v8.official'
  buildername = 'V8 Foobar'
  yield api.test(
      api.v8.test_name(builder_group, buildername, 'no_branch'),
      api.chromium.ci_build(
          builder_group=builder_group,
          project='v8',
          git_repo='https://chromium.googlesource.com/v8/v8',
          builder=buildername,
          revision='a' * 40),
      api.properties(build_config='Release', target_bits=64),
      api.post_process(
          MustRun, 'initialization.Skipping due to missing release branch.'),
      api.post_process(DoesNotRun, 'sync.gclient runhooks', 'build.gn',
                       'build.compile', 'make archive.zipping',
                       'make archive.gsutil upload'),
      api.post_process(DropExpectation),
      status='SUCCESS',
  )

  # Test bailout on missing tag.
  builder_group = 'client.v8.official'
  buildername = 'V8 Foobar'
  yield api.test(
      api.v8.test_name(builder_group, buildername, 'no_tag'),
      api.chromium.ci_build(
          builder_group=builder_group,
          project='v8',
          git_repo='https://chromium.googlesource.com/v8/v8',
          builder=buildername,
          git_ref='refs/branch-heads/3.4',
          revision='a' * 40),
      api.properties(build_config='Release', target_bits=64),
      api.v8.version_file(17, 'head', prefix='sync.'),
      api.override_step_data('sync.git describe',
                             api.raw_io.stream_output_text('3.4.3.17-blabla')),
      api.post_process(MustRun, 'sync.Skipping due to missing tag.'),
      api.post_process(DoesNotRun, 'sync.gclient runhooks', 'build.gn',
                       'build.compile', 'make archive.zipping',
                       'make archive.gsutil upload'),
      api.post_process(DropExpectation),
      status='SUCCESS',
  )

  # Test refbuilds.
  builder_group = 'client.v8.official'
  buildername = 'V8 Foobar'
  yield api.test(
      api.v8.test_name(builder_group, buildername, 'update_beta'),
      api.chromium.ci_build(
          builder_group=builder_group,
          project='v8',
          git_repo='https://chromium.googlesource.com/v8/v8',
          builder=buildername,
          git_ref='refs/branch-heads/3.4',
          revision='a' * 40),
      api.properties(build_config='Release', target_bits=64),
      api.v8.version_file(0, 'head', prefix='sync.'),
      api.override_step_data('sync.git describe',
                             api.raw_io.stream_output_text('3.4.3')),
      api.post_process(Filter().include_re('.*ref.*')),
      status='SUCCESS',
  )

  # Test canary upload.
  builder_group = 'client.v8.official'
  buildername = 'V8 Foobar'
  yield api.test(
      api.v8.test_name(builder_group, buildername, 'canary'),
      api.chromium.ci_build(
          builder_group=builder_group,
          project='v8',
          git_repo='https://chromium.googlesource.com/v8/v8',
          builder=buildername,
          git_ref='refs/heads/3.4.3',
          revision='a' * 40),
      api.properties.generic(build_config='Release', target_bits=64),
      api.v8.version_file(1, 'head', prefix='sync.'),
      api.override_step_data('sync.git describe',
                             api.raw_io.stream_output_text('3.4.3.1')),
      api.post_process(
          Filter(
              'make archive.gsutil upload',
              'make archive.gsutil upload json',
          )),
      status='SUCCESS',
  )

  # Test coverage for compile failures
  yield api.test(
      api.v8.test_name('client.v8.official', 'V8 Foobar',
                       'debug_compile_failure'),
      api.chromium.ci_build(
          builder_group='client.v8.official',
          project='v8',
          git_repo='https://chromium.googlesource.com/v8/v8',
          builder='V8 Foobar',
          git_ref='refs/branch-heads/3.4',
          revision='a' * 40),
      api.properties(build_config='Debug', target_bits=64),
      api.platform('linux', 64),
      api.v8.version_file(17, 'head', prefix='sync.'),
      api.override_step_data('sync.git describe',
                             api.raw_io.stream_output_text('3.4.3.17')),
      api.v8.check_param_equals(
          'sync.bot_update', '--revision', 'v8@refs/branch-heads/' +
          '3.4:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'),
      api.step_data('build.compile', retcode=1),
      api.post_process(DropExpectation),
      status='FAILURE',
  )

  yield api.test(
      api.v8.test_name('client.v8.official', 'V8 Foobar',
                       'release_compile_failure'),
      api.chromium.ci_build(
          builder_group='client.v8.official',
          project='v8',
          git_repo='https://chromium.googlesource.com/v8/v8',
          builder='V8 Foobar',
          git_ref='refs/branch-heads/3.4',
          revision='a' * 40),
      api.properties(build_config='Release', target_bits=64),
      api.platform('linux', 64),
      api.v8.version_file(17, 'head', prefix='sync.'),
      api.override_step_data('sync.git describe',
                             api.raw_io.stream_output_text('3.4.3.17')),
      api.v8.check_param_equals(
          'sync.bot_update', '--revision', 'v8@refs/branch-heads/' +
          '3.4:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'),
      api.step_data('build.compile', retcode=1),
      api.post_process(DropExpectation),
      status='FAILURE',
  )

  yield api.test(
      api.v8.test_name('client.v8.official', 'V8 Foobar',
                       'release_libs_compile_failure'),
      api.chromium.ci_build(
          builder_group='client.v8.official',
          project='v8',
          git_repo='https://chromium.googlesource.com/v8/v8',
          builder='V8 Foobar',
          git_ref='refs/branch-heads/3.4',
          revision='a' * 40),
      api.properties(build_config='Release', target_bits=64),
      api.platform('linux', 64),
      api.v8.version_file(17, 'head', prefix='sync.'),
      api.override_step_data('sync.git describe',
                             api.raw_io.stream_output_text('3.4.3.17')),
      api.v8.check_param_equals(
          'sync.bot_update', '--revision', 'v8@refs/branch-heads/' +
          '3.4:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'),
      api.step_data('build (libs).compile', retcode=1),
      api.post_process(DropExpectation),
      status='FAILURE',
  )

  yield api.test(
      api.v8.test_name('client.v8.official', 'V8 Foobar',
                       'milestone_compile_failure'),
      api.chromium.ci_build(
          builder_group=builder_group,
          project='v8',
          git_repo='https://chromium.googlesource.com/v8/v8',
          builder=buildername,
          git_ref='refs/branch-heads/3.4',
          revision='a' * 40,
      ),
      api.properties(build_config='Release', target_bits=64),
      api.v8.version_file(0, 'head', prefix='sync.'),
      api.override_step_data('sync.git describe',
                             api.raw_io.stream_output_text('3.4.3')),
      api.step_data('build (ref).compile', retcode=1),
      api.post_process(DropExpectation),
      status='FAILURE',
  )

  yield api.test(
      api.v8.test_name('client.v8.official', 'V8 Foobar', 'no_upload'),
      api.chromium.ci_build(
          builder_group='client.v8.official',
          project='v8',
          git_repo='https://chromium.googlesource.com/v8/v8',
          git_ref='refs/branch-heads/3.4',
          builder='V8 Foobar',
          revision='a' * 40),
      api.properties(
          build_config='Release', target_bits=64, upload_archive=False),
      api.platform('linux', 64),
      api.v8.version_file(0, 'head', prefix='sync.'),
      api.override_step_data('sync.git describe',
                             api.raw_io.stream_output_text('3.4.3')),
      api.post_process(MustRun, 'sync.clobber', 'sync.gclient runhooks',
                       'build.gn', 'build.compile', 'make archive.zipping'),
      api.post_process(DoesNotRun, 'make archive.gsutil upload'),
      api.post_process(
          Filter('sync.bot_update', 'build.gn', 'build.compile',
                 'build (libs).gn')),
      status='SUCCESS',
  )
