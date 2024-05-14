# Copyright (c) 2018 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

DEPS = [
    'depot_tools/bot_update',
    'depot_tools/depot_tools',
    'depot_tools/gclient',
    'depot_tools/osx_sdk',
    'depot_tools/tryserver',
    'recipe_engine/buildbucket',
    'recipe_engine/cipd',
    'recipe_engine/path',
    'recipe_engine/platform',
    'recipe_engine/properties',
    'recipe_engine/step',
]

from recipe_engine.recipe_api import Property

PROPERTIES = {
  'target_cpu': Property(default=None, kind=str),
}

def RunSteps(api, target_cpu):
  # 1. Checkout the source
  src_cfg = api.gclient.make_config()
  src_cfg.got_revision_mapping['client'] = 'got_revision'
  soln = src_cfg.solutions.add()
  soln.name = 'client'
  soln.url = 'https://chromium.googlesource.com/infra/goma/client'
  api.gclient.c = src_cfg
  update_result = api.bot_update.ensure_checkout(
      clobber=False, gerrit_no_reset=True)
  api.gclient.runhooks()
  source_dir = update_result.source_root.path

  # 2. Build
  build_out_dir = source_dir / 'out'
  build_target = 'Release'
  build_dir = build_out_dir / build_target

  with api.osx_sdk('mac'):
    # 2-1. gn
    gn_args = ['is_debug=false', 'enable_revision_check=true']
    assert api.platform.bits == 64
    if target_cpu == 'arm64':
      gn_args += ['target_cpu="arm64"']
    else:
      gn_args += ['cpu_arch="x64"']

    if api.tryserver.is_tryserver:
      gn_args.append('dcheck_always_on=true')
      gn_args.append('use_link_time_optimization=false')
    else:
      gn_args.append('use_link_time_optimization=true')
    api.step(
        name='gn',
        cmd=[
            'python3', api.depot_tools.gn_py_path,
            '--root=%s' % str(source_dir), 'gen', build_dir,
            '--args=%s' % ' '.join(gn_args)
        ])

    # 2-2. ninja
    ninja_path = source_dir.joinpath('third_party', 'ninja', 'ninja')
    api.step('build', [ninja_path, '-C', build_dir])

    # 3. Run test
    # Not on arm because we're cross-building and the intel builder can't run
    # arm test binaries.
    if target_cpu != 'arm64':
      api.step(
          name='tests',
          cmd=[
              'python3',
              source_dir.joinpath('build', 'run_unittest.py'), '--build-dir',
              build_out_dir, '--target', build_target, '--non-stop'
          ])

  # 4. Create archive.
  platform = api.platform.name
  if target_cpu == 'arm64':
    platform += '-arm64'
  api.step(
      name='archive',
      cmd=[
          'python3',
          source_dir.joinpath('build', 'archive.py'), '--platform', platform,
          '--build_dir', build_out_dir, '--target_dir', build_target,
          '--dist_dir', api.path.tmp_base_dir
      ])

  # 5. Build CIPD package.
  # archive.py creates goma-<platform>/ in out/Release.
  root = build_out_dir.joinpath(build_target, 'goma-%s' % platform)
  pkg_file = api.path.tmp_base_dir / 'package.cipd'
  tag = '${platform}' if target_cpu != 'arm64' else platform
  pkg_name = 'infra/goma/client/' + tag
  api.cipd.build(root, pkg_file, pkg_name, install_mode='copy')

  # 6. Register CIPD package if prod.
  revision = api.buildbucket.build.input.gitiles_commit.id
  if revision and api.buildbucket.build.builder.bucket == 'prod':
    api.cipd.register(pkg_name, pkg_file, tags={'git_revision': revision},
                      refs=['latest'])


def GenTests(api):
  for platform in ['linux', 'mac', 'win']:
    target_cpus = [None]
    if platform == 'mac':
      target_cpus += ['arm64']
    for target_cpu in target_cpus:
      bot_name = platform
      if target_cpu:
        bot_name += '_' + target_cpu
      yield api.test(
          'goma_client_try_%s_rel' % bot_name,
          api.platform(platform, 64),
          api.properties(target_cpu=target_cpu),
          api.buildbucket.try_build(
              builder='%s_rel' % platform,
              git_repo='chromium.googlesource.com/infra/goma/client',
              change_number=456789,
              patch_set=12),
      )
      for bucket in ['prod', 'luci.goma-client.ci']:
        yield api.test(
            'goma_client_%s_%s_rel' % (bot_name, bucket),
            api.properties(target_cpu=target_cpu),
            api.platform(platform, 64),
            api.buildbucket.ci_build(
                bucket=bucket,
                git_repo='chromium.googlesource.com/infra/goma/client',
                revision='8b3cd40a25a512033cc8c0797e41de9ecfc2432c'),
        )
