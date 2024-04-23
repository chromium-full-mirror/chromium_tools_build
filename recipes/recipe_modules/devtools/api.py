# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from contextlib import contextmanager
from pathlib import Path
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
    depot_tools_path = self.m.path.checkout_dir / 'third_party'
    with self.m.context(env_prefixes={'PATH': [depot_tools_path]}):
      yield

  def clean_out_dir(self, builder_config, clobber):
    if clobber:
      dir_to_clean = 'Release'
    elif self.is_debug(builder_config):
      dir_to_clean = 'Debug'
    else:
      return
    path_to_clean = self.m.path.checkout_dir.joinpath('out', dir_to_clean)
    self.m.file.rmtree('clean outdir', path_to_clean)

  def is_debug(self, builder_config):
    return builder_config == 'Debug'

  def rdb_node_script(self, step_name, script, args=None):
    rdb_wrapper = self.m.resultdb.wrap([])
    self.run_node_script(step_name, script, args, wrapper=rdb_wrapper)

  def run_node_script(self, step_name, script, args=None, **kwargs):
    with self.m.context(cwd=self.m.path.checkout_dir):
      sc_path = self.m.path.join('third_party', 'node', 'node.py')
      node_args = ['--output', self.m.path.join('scripts', 'test', script)]
      node_args.extend(args or [])
      self.m.step(step_name, ["vpython3", "-u", sc_path] + node_args, **kwargs)

  def run_python_script(self, step_name, script, args=None, **kwargs):
    with self.m.context(cwd=self.m.path.checkout_dir):
      sc_path = self.m.path.join('scripts', 'test', script)
      args = args or []
      return self.m.step(step_name, ["vpython3", "-u", sc_path] + args,
                         **kwargs)

  def is_shuffled_run(self):
    return 'shuffled' in self.m.buildbucket.builder_name.lower()

  def run_e2e(self, builder_config, args=None, run_mode='regular'):
    args = list(args or [])
    mode_modifiers = dict(
        regular=([], ''),
        parallel=(['--jobs=4'], ' (Parallel)'),
        sequential=(['--mocha-fgrep=[sequential]'], ' (Sequential)'),
    )

    extra_args, suffix = mode_modifiers[run_mode]
    args += extra_args

    self.m.devtools.rdb_node_script('E2E tests' + suffix, 'run_test_suite.js', [
        "--test-suite-path=gen/test/e2e", "--test-suite-source-dir=test/e2e",
        "--test-server-type='hosted-mode'", "--target=" + builder_config
    ] + args)

  def get_dimensions_for_platform(self):
    os_names = dict(
        linux='Ubuntu-22.04',
        mac='Mac-13',
        win='Windows-10-19045',
    )
    cpu_dimensions = dict(
        arm='arm64',
        intel='x86-64',
    )

    return {
        'cpu': cpu_dimensions[self.m.platform.arch],
        'os': os_names[self.m.platform.name],
        'pool': 'chromium.tests',
    }

  def archive_to_cas(self):
    return self.m.cas.archive('archive', self.m.path.checkout_dir)

  def divided_e2e_commands(self,
                           builder_config,
                           shards=4,
                           file_pattern='',
                           iterations=1):
    shuffle=self.is_shuffled_run()
    modified_commands = []
    shuffled = ['--shuffle'] if shuffle else []
    raw_commands = self.m.devtools.run_python_script(
        'divide test run',
        'e2e_divider.py',
        [
            f'--jobs={shards}',
            f'--test-file-pattern={file_pattern}',
            f'--iterations={iterations}',
        ] + shuffled,
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
          "--test-server-type='hosted-mode'", "--target=" +
          builder_config, config
      ] + ([file_pattern] if shuffle or shards > 1 else [])
      modified_commands.append(modified_command)

    return modified_commands

  def trigger_test_swarming_tasks(self,
                                  step_name,
                                  cas_digest,
                                  commands,
                                  task_output_dir=None,
                                  env=None,
                                  args=None,
                                  rdb_test_type=None):
    args = list(args or [])
    tasks = []
    if not env:
      env = {}

    self.m.chromium_swarming.default_priority = (
        30 if self.m.tryserver.is_tryserver else 25)

    for i in range(len(commands)):
      if commands[i][0].startswith('ITERATIONS='):
        env['ITERATIONS'] = commands[i].pop(0).split('=')[1]
      full_command = ["vpython3", "-u"] + commands[i] + args
      task = self.m.chromium_swarming.task(
          name=f'{step_name} (Shard #{i})',
          raw_cmd=full_command,
          task_output_dir=task_output_dir,
          cas_input_root=cas_digest,
          env=env)

      task_slice = task.request[0]
      task_dimensions = task_slice.dimensions
      task_dimensions.update(self.m.devtools.get_dimensions_for_platform())
      task_slice = task_slice.with_dimensions(**task_dimensions)
      task.request = task.request.with_slice(0, task_slice)

      if rdb_test_type:
        request = task.request.with_resultdb()
        wrapped_cmd = self.m.v8_tests.resultdb.wrap(
            self.m,
            full_command,
            base_tags=[('test_type', rdb_test_type)],
        )
        request_slice = request[0].with_command(wrapped_cmd)
        task.request = request.with_slice(0, request_slice)

      self.m.chromium_swarming.trigger_task(task)

      tasks.append(task)

    return tasks

  @contextmanager
  def collect_screenshots_on_trybot(self, bucket):
    if self.m.tryserver.is_tryserver:
      with self.collect_screenshots(bucket):
        yield
    else:
      yield

  @contextmanager
  def collect_screenshots(self, bucket):
    update_env = {
      "FORCE_UPDATE_ALL_GOLDENS": True,
      "THROW_AFTER_GOLDENS_UPDATE": True,
    }
    with self.m.context(env=update_env):
      self.m.git(
          'commit', '-am', '---', name='commit current patch', ok_ret='any')
      yield
      with self.m.step.nest('upload screenshots'):
        self.m.git('add', 'test/interactions/goldens', name='stage goldens')
        tmp_dir = self.m.path.mkdtemp('screenshots')
        patch_file = tmp_dir / 'screenshot.patch'
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
    cache_dir = self.m.path.cache_dir / 'builder'
    with self.m.context(cwd=cache_dir):
      yield

  # TODO(liviurau): remove this temp hack after devtools refactoring that
  # involve .gitignore are done
  def _git_clean(self):
    with self.m.context(cwd=self.m.path.checkout_dir):
      self.m.git('clean', '-xf', '--', 'front_end')
