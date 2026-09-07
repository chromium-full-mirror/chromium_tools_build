# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Recipe module for DevTools."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from recipe_engine import recipe_api

REPO_URL = 'https://chromium.googlesource.com/devtools/devtools-frontend.git'


class DevToolsAPI(recipe_api.RecipeApi):
  source_dir = None

  def configure(self,
                builder_config,
                is_official_build,
                force_host_cpu=None,
                devtools_bundle=True):
    self._configure_source(force_host_cpu)
    self._configure_build(builder_config, is_official_build, devtools_bundle)

  def update(self):
    with self._in_builder_cache():
      result = self.m.bot_update.ensure_checkout()
      self._git_clean(result.source_root.path)
      self.m.gclient.runhooks()
    self.source_dir = result.source_root.path
    return result

  def shallow_checkout(self, depth=2):
    source_dir = self.m.path.cleanup_dir / 'devtools-frontend'
    self.m.file.ensure_directory('ensure source dir', source_dir)
    with self.m.context(cwd=source_dir):
      self.m.git('init')
      if (self.m.tryserver.is_tryserver and
          self.m.tryserver.gerrit_change_fetch_ref):
        repo_url = self.m.tryserver.gerrit_change_repo_url or REPO_URL
        fetch_ref = self.m.tryserver.gerrit_change_fetch_ref
        self.m.git('fetch', repo_url, fetch_ref, f'--depth={depth}')
        self.m.git('checkout', 'FETCH_HEAD')
        self.m.git('reset', '--soft', 'HEAD~1')
      else:
        ref = (
            self.m.buildbucket.gitiles_commit.id or
            self.m.buildbucket.gitiles_commit.ref or 'refs/heads/main')
        self.m.git('fetch', REPO_URL, ref, f'--depth={depth}')
        self.m.git('checkout', 'FETCH_HEAD')
    self.source_dir = source_dir


  @contextmanager
  def depot_on_path(self):
    depot_tools_path = self.source_dir / 'third_party'
    with self.m.context(env_prefixes={'PATH': [depot_tools_path]}):
      yield

  def clean_out_dir(self, builder_config, clobber):
    if clobber:
      dir_to_clean = 'Release'
    elif self.is_debug(builder_config):
      dir_to_clean = 'Debug'
    else:
      return
    path_to_clean = self.source_dir.joinpath('out', dir_to_clean)
    self.m.file.rmtree('clean outdir', path_to_clean)

  def is_debug(self, builder_config):
    return builder_config == 'Debug'

  def rdb_node_script(self, step_name, script, args=None):
    rdb_wrapper = self.m.resultdb.wrap([])
    self.run_node_script(step_name, script, args, wrapper=rdb_wrapper)

  def run_node_script(self, step_name, script, args=None, **kwargs):
    with self.m.context(cwd=self.source_dir):
      sc_path = self.m.path.join('third_party', 'node', 'node.py')
      node_args = ['--output', self.m.path.join('scripts', 'test', script)]
      node_args.extend(args or [])
      self.m.step(step_name, ["vpython3", "-u", sc_path] + node_args, **kwargs)

  def run_python_script(self,
                        step_name,
                        script,
                        args=None,
                        **kwargs):
    with self.m.context(cwd=self.source_dir):
      sc_path = self.m.path.join('scripts', 'test', script)
      args = args or []
      return self.m.step(step_name, ["vpython3", "-u", sc_path] + args,
                         **kwargs)

  def run_e2e(self, builder_config, args=None, run_mode='regular'):
    args = list(args or [])
    mode_modifiers = {
        'regular': ([], ''),
        'parallel': (['--jobs=4'], ' (Parallel)'),
        'sequential': (['--mocha-fgrep=[sequential]'], ' (Sequential)'),
    }

    extra_args, suffix = mode_modifiers[run_mode]
    args += extra_args

    self.rdb_node_script('E2E tests' + suffix, 'run_test_suite.js', [
        "--test-suite-path=gen/test/e2e", "--test-suite-source-dir=test/e2e",
        "--test-server-type='hosted-mode'", "--target=" + builder_config
    ] + args)

  def get_dimensions_for_platform(self):
    os_names = {
        'linux': 'Ubuntu-22.04',
        'mac': 'Mac-26',
        'win': 'Windows-10-19045',
    }
    cpu_dimensions = {
        'arm': 'arm64',
        'intel': 'x86-64',
    }

    return {
        'cpu': cpu_dimensions[self.m.platform.arch],
        'os': os_names[self.m.platform.name],
        'pool': 'chromium.tests',
    }

  def archive_to_cas(self, builder_config=None):
    builder_config = (
        builder_config or
        (self.m.chromium.c and self.m.chromium.c.build_config_fs) or 'Release')
    build_dir = self.source_dir / 'out' / builder_config

    # Top-level heavy hitters to exclude
    excluded_top_level = {
        '.git',
        'build',
        'buildtools',
        'build_overrides',
        'docs',
        'extensions',
        'out',
        'third_party',
    }

    # third_party build toolchains to exclude
    excluded_third_party = {
        'chromium',
        'cmake',
        'emscripten-releases',
        'esbuild',
        'ninja',
        'rollup_libs',
        'siso',
        'typescript',
    }

    paths = []

    # 1. Include root files and non-excluded directories
    root_entries = self.m.file.listdir(
        'list root entries',
        self.source_dir,
        test_data=[
            'config',
            'front_end',
            'inspector_overlay',
            'mcp',
            'node_modules',
            'scripts',
            'test',
            'extension-api',
            'package.json',
            'eslint.config.mjs',
            '.stylelintrc.json',
            '.stylelintignore',
            '.gitignore',
            '.git',
            'build',
            'extensions',
            'out',
            'third_party',
        ])
    for entry in root_entries:
      if entry.name not in excluded_top_level:
        paths.append(entry)

    # 2. Include third_party test dependencies
    third_party_dir = self.source_dir / 'third_party'
    third_party_entries = self.m.file.listdir(
        'list third_party entries',
        third_party_dir,
        test_data=[
            'node',
            'chrome',
            'image_diff',
            'i18n',
            'depot_tools',
            'emscripten-releases',
        ])
    for entry in third_party_entries:
      if entry.name not in excluded_third_party:
        paths.append(entry)

    # 3. Include only the required build output artifacts for the active target
    build_artifacts = [
        build_dir / 'args.gn',
        build_dir / 'build.ninja',
        build_dir / 'gen',
        build_dir / 'resources',
    ]

    if self._test_data.enabled:
      for p in build_artifacts:
        self.m.path.mock_add_paths(p)
      if self.m.chromium.c and 'devtools_bundle=false' in self.m.chromium.c.gn_args:
        self.m.path.mock_remove_paths(build_dir / 'resources')

    for p in build_artifacts:
      if self.m.path.exists(p):
        paths.append(p)

    return self.m.cas.archive('archive', self.source_dir, *paths)

  def divided_e2e_commands(self,
                           builder_config,
                           shards=4,
                           file_pattern='',
                           iterations=1):
    modified_commands = []
    raw_commands = self.m.devtools.run_python_script(
        'divide test run',
        'e2e_divider.py', [
            f'--jobs={shards}',
            f'--test-file-pattern={file_pattern}',
            f'--iterations={iterations}',
        ],
        stdout=self.m.raw_io.output_text(
            add_output_log=True)).stdout.strip().split('\n')

    for command in raw_commands:
      split_command = command.split(' ')
      iterations = []
      if command.startswith('ITERATIONS='):
        iterations = [split_command.pop(0)]
      config = split_command[2]
      file_pattern = split_command[3]
      modified_command = iterations + [
          self.m.path.join('third_party', 'node', 'node.py'), "--output",
          self.m.path.join('scripts', 'test', 'run_test_suite.js'),
          f"--test-suite-path={Path('gen/test/e2e')}",
          f"--test-suite-source-dir={Path('test/e2e')}",
          "--test-server-type='hosted-mode'", "--target=" + builder_config,
          config
      ] + ([file_pattern] if shards > 1 else [])
      modified_commands.append(modified_command)

    return modified_commands

  def trigger_test_swarming_tasks(self,
                                  step_name,
                                  cas_digest,
                                  commands,
                                  task_output_dir=None,
                                  env=None,
                                  args=None,
                                  rdb_test_type=None,
                                  run_phase=None,
                                  target_dimensions=None):
    args = list(args or [])
    tasks = []
    if not env:
      env = {}

    self.m.chromium_swarming.default_priority = (
        30 if self.m.tryserver.is_tryserver else 25)

    for i, command in enumerate(commands):
      if command[0].startswith('ITERATIONS='):
        env['ITERATIONS'] = command.pop(0).split('=')[1]
      needs_vpy3 = command[0] != 'vpython3'
      full_command = (["vpython3", "-u"] if needs_vpy3 else []) + command + args
      task_input = self.m.step.empty('Full command')
      task_input.presentation.logs['command'] = full_command
      task = self.m.chromium_swarming.task(
          name=f'{step_name} (Shard #{i})',
          raw_cmd=full_command,
          task_output_dir=task_output_dir,
          cas_input_root=cas_digest,
          env=env)

      task_slice = task.request[0]
      task_dimensions = task_slice.dimensions
      task_dimensions.update(target_dimensions or
                             self.get_dimensions_for_platform())
      task_slice = task_slice.with_dimensions(**task_dimensions)
      task.request = task.request.with_slice(0, task_slice)

      if rdb_test_type:
        request = task.request.with_resultdb()
        wrapped_cmd = self.m.v8_tests.resultdb.wrap(
            self.m,
            full_command,
            base_tags=[('test_type', rdb_test_type), ('run_phase', run_phase)],
        )
        request_slice = request[0].with_command(wrapped_cmd)
        task.request = request.with_slice(0, request_slice)
      self.m.chromium_swarming.trigger_task(task)

      tasks.append(task)

    return tasks


  def _configure_source(self, force_host_cpu=None):
    src_cfg = self.m.gclient.make_config()
    soln = src_cfg.solutions.add()
    if force_host_cpu:
      soln.custom_vars = {'host_cpu': force_host_cpu}
    soln.name = 'devtools-frontend'
    soln.url = REPO_URL
    soln.revision = self.m.buildbucket.gitiles_commit.id or 'HEAD'
    src_cfg.got_revision_mapping[soln.name] = 'got_revision'
    self.m.gclient.c = src_cfg

  def _configure_build(self, builder_config, is_official_build,
                       devtools_bundle):
    build_cfg = self.m.chromium.make_config(BUILD_CONFIG=builder_config)
    build_cfg.build_config_fs = builder_config
    if is_official_build:
      build_cfg.gn_args.append('is_official_build=true')
    if not devtools_bundle:
      build_cfg.gn_args.append('devtools_bundle=false')
    self.m.chromium.c = build_cfg

  @contextmanager
  def _in_builder_cache(self):
    cache_dir = self.m.path.cache_dir / 'builder'
    with self.m.context(cwd=cache_dir):
      yield

  # TODO(liviurau): remove this temp hack after devtools refactoring that
  # involve .gitignore are done
  def _git_clean(self, source_dir):
    with self.m.context(cwd=source_dir):
      self.m.git('clean', '-xf', '--', 'front_end')

  def get_affected_files(self):
    if not self.source_dir:
      return []
    with self.m.context(cwd=self.source_dir):
      return self.m.v8.git_output('diff', '--name-only', '--format=',
                                  '--diff-filter=d', '--cached').splitlines()
