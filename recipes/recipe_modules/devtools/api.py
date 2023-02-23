# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from contextlib import contextmanager
from recipe_engine import recipe_api

REPO_URL = 'https://chromium.googlesource.com/devtools/devtools-frontend.git'


class DevToolsAPI(recipe_api.RecipeApi):

  def configure(self, builder_config, is_official_build,
                devtools_skip_typecheck):
    self._configure_source()
    self._configure_build(builder_config, is_official_build,
                          devtools_skip_typecheck)

  def update(self):
    with self._in_builder_cache():
      self.m.bot_update.ensure_checkout()
      self._git_clean()
      self.m.gclient.runhooks()

  @contextmanager
  def depot_on_path(self):
    depot_tools_path = self.m.path['checkout'].join('third_party')
    with self.m.context(env_prefixes={'PATH': [depot_tools_path]}):
      yield

  def clean_out_dir(self, builder_config, clobber):
    if clobber:
      dir_to_clean = 'Release'
    elif self.is_debug(builder_config):
      dir_to_clean = 'Debug'
    else:
      return
    path_to_clean = self.m.path['checkout'].join('out', dir_to_clean)
    self.m.file.rmtree('clean outdir', path_to_clean)

  def is_debug(self, builder_config):
    return builder_config == 'Debug'

  def rdb_node_script(self, step_name, script, args=None):
    rdb_wrapper = self.m.resultdb.wrap([])
    self.run_node_script(step_name, script, args, wrapper=rdb_wrapper)

  def run_node_script(self, step_name, script, args=None, **kwargs):
    with self.m.context(cwd=self.m.path['checkout']):
      sc_path = self.m.path.join('third_party', 'node', 'node.py')
      node_args = ['--output', self.m.path.join('scripts', 'test', script)]
      node_args.extend(args or [])
      self.m.step(step_name, ["vpython3", "-u", sc_path] + node_args, **kwargs)

  def run_e2e(self, builder_config, args=None):
    args = args or []
    if 'parallel' in self.m.buildbucket.builder_name.lower():
      args += ['--jobs=8']
    self.m.devtools.rdb_node_script('E2E tests', 'run_test_suite.js', [
        "--test-suite-path=gen/test/e2e", "--test-suite-source-dir=test/e2e",
        "--test-server-type='hosted-mode'", "--target=" + builder_config
    ] + args)

  @contextmanager
  def collect_screenshots(self, bucket):
    update_env = {"FORCE_UPDATE_ALL_GOLDENS": True}
    with self.m.context(env=update_env):
      self.m.git('commit', '-am', '---', name='commit current patch')
      yield
      with self.m.step.nest('upload screenshots'):
        self.m.git('add', 'test/interactions/goldens', name='stage goldens')
        tmp_dir = self.m.path.mkdtemp('screenshots')
        patch_file = tmp_dir.join('screenshot.patch')
        self.m.git(
            'diff',
            '--staged',
            '--binary',
            '--output',
            patch_file,
            '--',
            'test/interactions/goldens',
            name='create patch')
        self.m.git('reset', name='unstage goldens')
        self.m.git('checkout', '--', '.', name='revert golden updates')
        self.m.git('clean', '-dff', name='remove newly created goldens')
        upload_name = self.m.url.join(
            'screenshots', self.m.buildbucket.builder_name,
            str(self.m.tryserver.gerrit_change.change),
            str(self.m.tryserver.gerrit_change.patchset), 'screenshot.patch')
        self.m.gsutil.upload(
            source=patch_file,
            bucket=bucket,
            dest=upload_name,
            link_name=upload_name,
            name='upload patch',
        )

  def _configure_source(self):
    src_cfg = self.m.gclient.make_config()
    soln = src_cfg.solutions.add()
    soln.name = 'devtools-frontend'
    soln.url = REPO_URL
    soln.revision = self.m.buildbucket.gitiles_commit.id or 'HEAD'
    src_cfg.got_revision_mapping[soln.name] = 'got_revision'
    self.m.gclient.c = src_cfg

  def _configure_build(self, builder_config, is_official_build,
                       devtools_skip_typecheck):
    build_cfg = self.m.chromium.make_config(BUILD_CONFIG=builder_config)
    build_cfg.build_config_fs = builder_config
    build_cfg.gn_args.append('devtools_dcheck_always_on=true')
    if is_official_build:
      build_cfg.gn_args.append('is_official_build=true')
    if devtools_skip_typecheck:
      build_cfg.gn_args.append('devtools_skip_typecheck=true')
    self.m.chromium.c = build_cfg

  @contextmanager
  def _in_builder_cache(self):
    cache_dir = self.m.path['cache'].join('builder')
    with self.m.context(cwd=cache_dir):
      yield

  # TODO(liviurau): remove this temp hack after devtools refactoring that
  # involve .gitignore are done
  def _git_clean(self):
    with self.m.context(cwd=self.m.path['checkout']):
      self.m.git('clean', '-xf', '--', 'front_end')
