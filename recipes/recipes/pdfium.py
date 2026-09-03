# Copyright 2014 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

# Python version used to run the recipe itself.
# Separately, python command used to run the steps in the recipe.
PYTHON_CMD = ['vpython3', '-u']

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import builder_group, siso
from RECIPE_MODULES.depot_tools import (
    bot_update,
    depot_tools,
    gclient,
    gsutil,
    osx_sdk,
    tryserver,
)
from RECIPE_MODULES.recipe_engine import (
    buildbucket,
    cas,
    cipd,
    context,
    defer,
    file,
    path,
    platform,
    properties,
    resultdb,
    step,
    swarming,
    time,
)


@dataclass
class DEPS(RecipeScriptApi):
  bot_update: bot_update.API
  buildbucket: buildbucket.API
  builder_group: builder_group.API
  cas: cas.API
  cipd: cipd.API
  context: context.API
  defer: defer.API
  depot_tools: depot_tools.API
  file: file.API
  gclient: gclient.API
  gsutil: gsutil.API
  osx_sdk: osx_sdk.API
  path: path.API
  platform: platform.API
  properties: properties.API
  resultdb: resultdb.API
  siso: siso.API
  step: step.API
  swarming: swarming.API
  time: time.API
  tryserver: tryserver.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  bot_update: bot_update.TEST_API
  buildbucket: buildbucket.TEST_API
  builder_group: builder_group.TEST_API
  context: context.TEST_API
  file: file.TEST_API
  gclient: gclient.TEST_API
  path: path.TEST_API
  platform: platform.TEST_API
  properties: properties.TEST_API
  siso: siso.TEST_API
  step: step.TEST_API
  swarming: swarming.TEST_API

from dataclasses import dataclass

from RECIPE_MODULES.recipe_engine.swarming.api import (TaskRequest,
                                                       TaskRequestMetadata)
from RECIPE_MODULES.recipe_engine.swarming.state import TaskState
from recipe_engine import post_process
from recipe_engine.config import Set
from recipe_engine.recipe_api import Property

PROPERTIES = {
    'brotli': Property(default=False, kind=bool),
    'component': Property(default=False, kind=bool),
    'memory_tool': Property(default=None, kind=str),
    'partition_alloc': Property(default=True, kind=bool),
    'rel': Property(default=False, kind=bool),
    'renderers': Property(default=None, kind=Set(str)),
    'run_skia_gold': Property(default=True, kind=bool),
    'rust': Property(default=False, kind=bool),
    'skia': Property(default=False, kind=bool),
    'skip_test': Property(default=False, kind=bool),
    'swarming': Property(default=None, kind=dict),
    'target_cpu': Property(default=None, kind=str),
    'target_os': Property(default=None, kind=str),
    'use_cxx23': Property(default=None, kind=bool),
    'v8': Property(default=True, kind=bool),
    'xfa': Property(default=False, kind=bool),
}

# test_runner.py test types.
_CORPUS_TEST_TYPE = 'corpus'
_JAVASCRIPT_TEST_TYPE = 'javascript'
_PIXEL_TEST_TYPE = 'pixel'

# Renderer types. `None` indicates the compiled-in default renderer (no explicit
# `--use-renderer` flag).
_AGG_RENDERER = 'agg'
_GDI_RENDERER = 'gdi'
_SKIA_RENDERER = 'skia'

_DEFAULT_RENDERERS = frozenset([None, _AGG_RENDERER, _SKIA_RENDERER])
_ALL_RENDERERS = _DEFAULT_RENDERERS.union([_GDI_RENDERER])

# Default swarming priority for test tasks. Test child tasks should have a
# higher priority (lower value) than the orchestrator parent task, to avoid
# priority inversion.
_DEFAULT_SWARMING_TEST_PRIORITY = 25

# Default swarming execution timeout for completing test tasks (in seconds).
_DEFAULT_SWARMING_EXECUTION_TIMEOUT_SECS = 20 * 60

# Default swarming expiration for scheduling test tasks (in seconds).
_DEFAULT_SWARMING_EXPIRATION_SECS = 5 * 60

# Default swarming time when a task can be silent (in seconds).
_DEFAULT_SWARMING_IO_TIMEOUT_SECS = 3 * 60

# Relative path for CIPD packages installed by swarming tasks.
_SWARMING_CIPD_PATH = 'packages'

# Relative path for vpython cache used by swarming tasks.
_SWARMING_VPYTHON_PATH = 'vpython_cache'

# Environment prefixes for swarming tasks.
_SWARMING_ENV_PREFIXES = {
    'PATH': [_SWARMING_CIPD_PATH],
    'VPYTHON_VIRTUALENV_ROOT': [_SWARMING_VPYTHON_PATH],
}

# Named caches for swarming tasks.
_SWARMING_NAMED_CACHES = {
    'vpython_cache': _SWARMING_VPYTHON_PATH,
}

# Prefix for task invocations to remove before sending to ResultDB.
_SWARMING_INVOCATION_PREFIX = 'invocations/'


@dataclass
class _DefaultOption:
  """Base class for test_runner.py test options. Returns default options."""

  # The name of the option, as displayed in the test step.
  name: str = ''

  # The suffix used in test suite names, with underscores, not spaces.
  test_suite_suffix: str = ''

  # Additional command line argument to run tests with.
  additional_arg: str = ''

  # Whether this option disables JavaScript or not.
  disable_javascript: bool = False

  # Whether this option disables XFA or not.
  disable_xfa: bool = False

  # The renderers this option is compatible with.
  renderers: frozenset = _ALL_RENDERERS


@dataclass
class _JavascriptDisabledOption(_DefaultOption):
  """A test_runner.py test option to disable JavaScript."""

  name: str = 'javascript disabled'
  test_suite_suffix: str = 'javascript_disabled'
  additional_arg: str = '--disable-javascript'
  disable_javascript: bool = True


@dataclass
class _XfaDisabledOption(_DefaultOption):
  """A test_runner.py test option to disable XFA."""

  name: str = 'xfa disabled'
  test_suite_suffix: str = 'xfa_disabled'
  additional_arg: str = '--disable-xfa'
  disable_xfa: bool = True


@dataclass
class _OneshotOption(_DefaultOption):
  """A test_runner.py test option to enable one-shot rendering."""

  name: str = 'oneshot rendering enabled'
  test_suite_suffix: str = 'oneshot'
  additional_arg: str = '--render-oneshot'
  renderers: frozenset = _DEFAULT_RENDERERS


@dataclass
class _ReverseByteOrderOption(_DefaultOption):
  """A test_runner.py test option to enable reverse byte order rendering."""

  name: str = 'reverse byte order'
  test_suite_suffix: str = 'reverse_byte_order'
  additional_arg: str = '--reverse-byte-order'
  renderers: frozenset = _DEFAULT_RENDERERS


@dataclass
class _TestRequest:
  """A request to run a test."""

  step_name: str
  command: str


def _checkout_step(api, target_os, rust, skia, v8):
  solution_path = api.path.cache_dir / 'builder'
  api.file.ensure_directory('init cache if not exists', solution_path)

  with api.context(cwd=solution_path):
    # Checkout pdfium and its dependencies (specified in DEPS) using gclient.
    api.gclient.set_config('pdfium')
    if target_os:
      api.gclient.c.target_os = {target_os}
    api.gclient.c.got_revision_mapping['pdfium'] = 'got_revision'
    if api.siso.enabled:
      api.siso.enable_download_remoteexec_cfg_hook()
    if rust:
      api.gclient.c.solutions[0].custom_vars['checkout_rust'] = 'True'
    if not skia:
      api.gclient.c.solutions[0].custom_vars['checkout_skia'] = 'False'
    if not v8:
      api.gclient.c.solutions[0].custom_vars['checkout_v8'] = 'False'
    update_result = api.bot_update.ensure_checkout()

    api.gclient.runhooks()
    return update_result


def _generate_out_path(memory_tool, partition_alloc, skia, rust, xfa, v8, rel,
                       brotli, component):
  out_dir = 'release' if rel else 'debug'

  if skia:
    out_dir += '_skia'
  if rust:
    assert (skia)
    out_dir += '_rust'
  if xfa:
    out_dir += '_xfa'
  if v8:
    out_dir += '_v8'
  if brotli:
    out_dir += '_brotli'

  if component:
    out_dir += '_component'

  if memory_tool == 'asan':
    out_dir += '_asan'
  elif memory_tool == 'msan':
    out_dir += '_msan'
  elif memory_tool == 'ubsan':
    out_dir += '_ubsan'

  if not partition_alloc:
    out_dir += '_no_partition_alloc'

  return out_dir


# _gn_gen_builds() calls 'gn gen' and returns a dictionary of
# the used build configuration to be used by Gold.
def _gn_gen_builds(api, source_root, memory_tool, partition_alloc, skia, rust,
                   xfa, v8, target_cpu, rel, brotli, component, target_os,
                   use_cxx23, out_dir):
  gn_bool = {True: 'true', False: 'false'}
  # Generate build files by GN.
  gn_cmd = api.depot_tools.gn_py_path

  # Prepare the arguments to pass in.
  args = [
      'enable_rust=%s' % gn_bool[rust],
      'enable_rust_cxx=%s' % gn_bool[rust],
      'is_component_build=%s' % gn_bool[component],
      'is_debug=%s' % gn_bool[not rel],
      'pdf_enable_brotli=%s' % gn_bool[brotli],
      'pdf_enable_fontations=%s' % gn_bool[rust],
      'pdf_enable_rust_bmp=%s' % gn_bool[rust],
      'pdf_enable_rust_jpeg=%s' % gn_bool[rust],
      'pdf_enable_rust_png=%s' % gn_bool[rust],
      'pdf_enable_v8=%s' % gn_bool[v8],
      'pdf_enable_xfa=%s' % gn_bool[xfa],
      'pdf_is_standalone=true',
      'pdf_use_partition_alloc=%s' % gn_bool[partition_alloc],
      'pdf_use_skia=%s' % gn_bool[skia],
      'use_reclient=false',
      'use_remoteexec=true',
      'use_siso=true',
  ]

  if use_cxx23 is not None:
    args.append('use_cxx23=%s' % gn_bool[use_cxx23])

  # TODO(crbug.com/452209505): Enable clang modules when all Macs have Xcode 26
  # or later.
  if api.platform.is_mac:
    args.append('use_clang_modules=false')

  if api.platform.is_win and not memory_tool:
    args.append('symbol_level=1')

  if memory_tool == 'asan':
    args.extend(['is_asan=true', 'use_raw_ptr_asan_unowned_impl=true'])
    if not api.platform.is_win:
      # No LSAN support on Windows.
      args.append('is_lsan=true')
  elif memory_tool == 'msan':
    assert not api.platform.is_win
    args.append('is_msan=true')
  elif memory_tool == 'ubsan':
    assert not api.platform.is_win
    args.extend(['is_ubsan_security=true', 'is_ubsan_no_recover=true'])

  if target_os:
    args.append('target_os="%s"' % target_os)
  if target_cpu:
    args.append('target_cpu="%s"' % target_cpu)

  with api.context(cwd=source_root):
    api.step(
        'gn gen', PYTHON_CMD + [
            gn_cmd, '--check', '--root=' + str(source_root), 'gen',
            '//out/' + out_dir, '--args=' + ' '.join(args)
        ])

  # convert the arguments to key values pairs for gold usage.
  return _gold_build_config(args)


def _build_steps(api, source_root, out_dir):
  debug_path = source_root.joinpath('out', out_dir)
  ninja_cmd = ['ninja', '-C', debug_path]
  ninja_cmd.append('pdfium_all')

  with api.siso.context():
    api.siso.run_ninja(source_root, ninja_cmd, name='compile with siso')


def _request_all_javascript_tests(test_runner, xfa):
  test_runner.request_javascript_tests(_DefaultOption)
  test_runner.request_javascript_tests(_JavascriptDisabledOption)

  if xfa:
    test_runner.request_javascript_tests(_XfaDisabledOption)


def _request_all_pixel_tests(test_runner, skia, v8, xfa):
  test_runner.request_pixel_tests(_DefaultOption)
  test_runner.request_pixel_tests(_OneshotOption)

  # TODO(crbug.com/pdfium/994): Enable for Mac.
  # TODO(crbug.com/pdfium/1955): Enable for Skia.
  if (test_runner.api.platform.is_linux or
      test_runner.api.platform.is_win) and not skia:
    test_runner.request_pixel_tests(_ReverseByteOrderOption)

  if v8:
    test_runner.request_pixel_tests(_JavascriptDisabledOption)

    if xfa:
      test_runner.request_pixel_tests(_XfaDisabledOption)


def _request_all_corpus_tests(test_runner, skia, v8, xfa):
  test_runner.request_corpus_tests(_DefaultOption)
  test_runner.request_corpus_tests(_OneshotOption)

  # TODO(crbug.com/pdfium/994): Enable for Mac.
  # TODO(crbug.com/pdfium/1955): Enable for Skia.
  if (test_runner.api.platform.is_linux or
      test_runner.api.platform.is_win) and not skia:
    test_runner.request_corpus_tests(_ReverseByteOrderOption)

  if v8:
    test_runner.request_corpus_tests(_JavascriptDisabledOption)

    if xfa:
      test_runner.request_corpus_tests(_XfaDisabledOption)


def _run_tests(api, source_root, memory_tool, v8, xfa, skia, out_dir,
               build_config, revision, run_skia_gold, renderers, swarming):
  """Runs the tests and uploads the results to Gold."""
  resultdb = _ResultDb(
      api, source_root, base_variant={
          'builder': api.buildbucket.builder_name,
      })

  if renderers:
    embedder_test_renderers = renderers
    python_test_renderers = renderers
  else:
    if skia:
      embedder_test_renderers = [_AGG_RENDERER, _SKIA_RENDERER]
    else:
      embedder_test_renderers = [None]
    python_test_renderers = [None]
  test_runner = _TestRunner(api, source_root, memory_tool, resultdb, out_dir,
                            build_config, revision, run_skia_gold,
                            embedder_test_renderers, python_test_renderers,
                            swarming)

  # pdfium_unittests:
  test_runner.request_unit_tests()

  # pdfium_embeddertests:
  test_runner.request_embedder_tests()

  # run_javascript_tests.py:
  if v8:
    _request_all_javascript_tests(test_runner, xfa)

  # run_pixel_tests.py:
  _request_all_pixel_tests(test_runner, skia, v8, xfa)

  # run_corpus_tests.py:
  _request_all_corpus_tests(test_runner, skia, v8, xfa)

  test_runner.run_tests()


class _ResultDb:

  def __init__(self, api, source_root, *, base_variant):
    self.api = api
    self.base_variant = base_variant

    self.result_adapter_path = str(
        source_root.joinpath('tools', 'resultdb', 'result_adapter'))
    if self.api.platform.is_win:
      self.result_adapter_path += '.exe'

  def wrap(self,
           command,
           *,
           test_id_prefix='',
           base_variant=None,
           base_tags=None):
    """Wraps an invocation with native ResultSink support."""
    if not self.api.resultdb.enabled:
      return command

    variant = dict(self.base_variant)
    variant.update(base_variant or {})

    tags = set(base_tags or [])

    return self.api.resultdb.wrap(
        command,
        test_id_prefix=test_id_prefix,
        base_variant=variant,
        base_tags=list(tags),
    )

  def wrap_gtest(self, gtest_command, **kwargs):
    """Wraps an invocation of a GoogleTest test runner."""
    if not self.api.resultdb.enabled:
      return gtest_command

    result_file_path = self.api.path.mkstemp()
    artifact_directory_path = self.api.path.dirname(result_file_path)
    return self.wrap([
        self.result_adapter_path,
        'gtest_json',
        '-artifact-directory',
        artifact_directory_path,
        '-result-file',
        result_file_path,
        '--',
    ] + gtest_command + [
        f'--gtest_output=json:{result_file_path}',
    ], **kwargs)


class _Swarming:

  @dataclass
  class _TestTask:
    """State for a swarmed test task."""

    # The test task name (typically the step name).
    name: str

    # The test request.
    test_request: _TestRequest

    # The task request to trigger.
    task_request: TaskRequest

    # The triggered task request.
    task: TaskRequestMetadata = None

  def __init__(self, api, source_dir, out_dir, properties):
    self.api = api
    self.source_dir = source_dir
    self.out_dir = out_dir

    assert 'dimensions' in properties
    self.properties = properties

    # TODO(crbug.com/1465963): Use pool with `task_template_deployment` instead.
    self.ensure_file = self.api.cipd.EnsureFile()
    self.ensure_file.add_package('infra/tools/rdb/${platform}', 'latest',
                                 _SWARMING_CIPD_PATH)
    self.ensure_file.add_package('infra/tools/luci/vpython3/${platform}',
                                 'latest', _SWARMING_CIPD_PATH)

    self.test_inputs_digest = None
    self.requests = {}

  def _ensure_test_inputs(self):
    if self.test_inputs_digest:
      return

    test_inputs = self.api.file.read_json(
        'read test inputs list',
        self.source_dir.joinpath(self.out_dir, 'test_runner_py.json'),
        test_data=[
            [
                self.source_dir,
                '.vpython3',
            ],
            [
                self.source_dir,
                self.api.path.join(self.out_dir, 'snapshot_blob.bin'),
            ],
            [
                self.source_dir,
                self.api.path.join('testing', 'resources'),
            ],
        ],
        include_log=False)
    assert test_inputs

    archive_paths = []
    for entry_root, entry_path in test_inputs:
      archive_paths.append(self.api.path.abs_to_path(entry_root) / entry_path)

    self.test_inputs_digest = self.api.cas.archive('archive test inputs',
                                                   self.source_dir,
                                                   *archive_paths)

  def request_task(self, test_request, *, env):
    assert test_request.step_name not in self.requests, (
        f'Duplicate request for "{test_request.step_name}"')
    self._ensure_test_inputs()

    # Construct the task request with a single slice.
    priority = self.properties.get('priority', _DEFAULT_SWARMING_TEST_PRIORITY)
    service_account = self.properties.get('service_account', '')
    # yapf: disable
    task_request = (
        self.api.swarming.task_request().
            with_name(test_request.step_name).
            with_priority(priority).
            with_resultdb().
            with_service_account(service_account))
    # yapf: enable

    # TODO(crbug.com/1465963): Try to make this idempotent after switching to
    # `task_template_deployment`.
    expiration_secs = self.properties.get('expiration_secs',
                                          _DEFAULT_SWARMING_EXPIRATION_SECS)
    execution_timeout_secs = self.properties.get(
        'execution_timeout_secs', _DEFAULT_SWARMING_EXECUTION_TIMEOUT_SECS)
    task_slice = task_request[0]
    # yapf: disable
    task_slice = (
        task_slice.
            with_command(test_request.command).
            with_cas_input_root(self.test_inputs_digest).
            with_dimensions(**self.properties['dimensions']).
            with_cipd_ensure_file(self.ensure_file).
            with_env_vars(**env).with_env_prefixes(**_SWARMING_ENV_PREFIXES).
            with_expiration_secs(expiration_secs).
            with_execution_timeout_secs(execution_timeout_secs).
            with_io_timeout_secs(_DEFAULT_SWARMING_IO_TIMEOUT_SECS).
            with_named_caches(_SWARMING_NAMED_CACHES))
    # yapf: enable

    # Start tracking the task.
    request = _Swarming._TestTask(
        name=test_request.step_name,
        test_request=test_request,
        task_request=task_request.with_slice(0, task_slice),
    )
    self.requests[request.name] = request

  def trigger_tasks(self):
    # Gather untriggered task requests.
    task_requests = [
        request.task_request
        for request in self.requests.values()
        if not request.task
    ]
    assert task_requests

    # Trigger tasks and add the corresponding ResultDB invocation.
    invocations = []
    for task in self.api.swarming.trigger('trigger tasks', task_requests):
      request = self.requests[task.name]

      assert not request.task
      request.task = task

      assert task.invocation.startswith(_SWARMING_INVOCATION_PREFIX)
      invocations.append(
          task.invocation.removeprefix(_SWARMING_INVOCATION_PREFIX))

    assert len(task_requests) == len(invocations)
    self.api.resultdb.include_invocations(invocations)

  def collect_tasks(self):
    # Gather triggered requests and key by task ID.
    requests_by_id = {
        request.task.id: request
        for request in self.requests.values()
        if request.task
    }
    if not requests_by_id:
      return False, None

    # Attempt to collect the first task that finishes.
    forward_progress = False
    expired_test_requests = []
    task_results = self.api.swarming.collect(
        'collect tasks', [request.task for request in requests_by_id.values()],
        eager=True)
    for result in task_results:
      request = requests_by_id[result.id]
      assert request

      if result.state is None or not result.finalized:
        # Need to collect the task again.
        continue
      forward_progress = True

      if result.state == TaskState.EXPIRED:
        # Let the caller handle any expired tasks.
        expired_test_requests.append(request.test_request)
      else:
        self._report_task_result(result)

      del self.requests[request.name]

    assert forward_progress
    return True, expired_test_requests

  def _report_task_result(self, result):
    if result.state != TaskState.COMPLETED:
      result.analyze()

    status = self.api.step.SUCCESS if result.success else self.api.step.FAILURE
    self.api.step.empty(result.name, status=status, log_text=result.output)


def _validate_renderers(context_name, renderers):
  if not _ALL_RENDERERS.issuperset(renderers):
    invalid = filter(lambda renderer: renderer not in _ALL_RENDERERS, renderers)
    raise ValueError(f'Invalid {context_name} renderer: {", ".join(invalid)}')
  return renderers


class _TestRunner:

  def __init__(self, api, source_dir, memory_tool, resultdb, out_dir,
               build_config, revision, run_skia_gold, embedder_test_renderers,
               python_test_renderers, swarming):
    self.api = api
    self.source_dir = source_dir
    self.resultdb = resultdb
    self.out_dir = self.api.path.join('out', out_dir)
    self.build_config = build_config
    self.env = self._create_sanitizer_envionment(memory_tool)
    self.embedder_test_renderers = _validate_renderers('pdfium_embeddertests',
                                                       embedder_test_renderers)
    self.python_test_renderers = _validate_renderers('pdfium_test',
                                                     python_test_renderers)
    self.swarming = _Swarming(api, source_dir, self.out_dir,
                              swarming) if swarming else None

    self.local_requests = []

    self.test_runner_py_args = [
        '--build-dir',
        self.out_dir,
    ]

    # Add Skia Gold flags if the "run_skia_gold" property is true.
    if run_skia_gold:
      self.test_runner_py_args.extend([
          '--gold_output_dir',
          self._join_out_dir('gold_output'),
          '--run-skia-gold',
          '--git-revision',
          revision,
          '--buildbucket-id',
          str(self.api.buildbucket.build.id),
      ])

      # Add the trybot information if this is a trybot run.
      if self.api.tryserver.gerrit_change:
        self.test_runner_py_args.extend([
            '--gerrit-issue',
            str(self.api.tryserver.gerrit_change.change),
            '--gerrit-patchset',
            str(self.api.tryserver.gerrit_change.patchset),
        ])

  def _join_root_dir(self, *paths):
    if self.swarming:
      return self.api.path.join('', *paths)
    return self.source_dir.joinpath(*paths)

  def _join_out_dir(self, *paths):
    if self.swarming:
      return self.api.path.join(self.out_dir, *paths)
    return self.source_dir.joinpath(self.out_dir, *paths)

  def _create_sanitizer_envionment(self, memory_tool):
    """Sets environment variables required by sanitizer tools."""
    env = {}
    COMMON_SANITIZER_OPTIONS = ['allocator_may_return_null=1']
    COMMON_UNIX_SANITIZER_OPTIONS = [
        'detect_leaks=1',
        'symbolize=1',
        # Note: deliberate lack of comma.
        'external_symbolizer_path='
        'third_party/llvm-build/Release+Asserts/bin/llvm-symbolizer',
    ]
    if memory_tool == 'asan':
      options = []
      options.extend(COMMON_SANITIZER_OPTIONS)
      if not self.api.platform.is_win:
        options.extend(COMMON_UNIX_SANITIZER_OPTIONS)
      env.update({'ASAN_OPTIONS': ' '.join(options)})
    elif memory_tool == 'msan':
      assert not self.api.platform.is_win
      options = []
      options.extend(COMMON_SANITIZER_OPTIONS)
      options.extend(COMMON_UNIX_SANITIZER_OPTIONS)
      env.update({'MSAN_OPTIONS': ' '.join(options)})
    elif memory_tool == 'ubsan':
      assert not self.api.platform.is_win
      options = []
      options.extend(COMMON_SANITIZER_OPTIONS)
      options.extend(COMMON_UNIX_SANITIZER_OPTIONS)
      env.update({'UBSAN_OPTIONS': ' '.join(options)})

    return env

  def request_unit_tests(self):
    self._request_gtest('unittests', target=('', 'pdfium_unittests'))

  def request_embedder_tests(self):
    for renderer in self.embedder_test_renderers:
      if renderer not in _DEFAULT_RENDERERS:
        continue

      test_name = 'embeddertests'
      args = []

      if renderer:
        test_name = f'{test_name} ({renderer})'
        args.append(f'--use-renderer={renderer}')

      self._request_gtest(
          test_name,
          target=('', 'pdfium_embeddertests'),
          args=args,
          test_suite_suffix=renderer)

  def request_javascript_tests(self, option):
    self._request_python_tests(_JAVASCRIPT_TEST_TYPE, option)

  def request_pixel_tests(self, option):
    self._request_python_tests(_PIXEL_TEST_TYPE, option)

  def request_corpus_tests(self, option):
    self._request_python_tests(_CORPUS_TEST_TYPE, option, remote=True)

  def _request_python_tests(self, test_type, option, remote=False):
    for renderer in self.python_test_renderers:
      if renderer not in option.renderers:
        continue

      test_name = f'{test_type} tests'
      test_suite_suffix = option.test_suite_suffix

      if option.name:
        test_name = f'{test_name} ({option.name})'

      if renderer:
        test_name = f'{test_name} ({renderer})'
        test_suite_suffix = f'{test_suite_suffix}_{renderer}'

      self._request_test_runner_py(
          test_name,
          test_type=test_type,
          args=_get_modifiable_script_args(self.api, self.build_config, option,
                                           renderer),
          test_suite_suffix=test_suite_suffix,
          remote=remote)

  def _request_gtest(self,
                     step_name,
                     *,
                     target,
                     args=None,
                     test_suite_suffix=None):
    target_path, target_name = target

    test_path = str(self._join_out_dir(target_name))
    if self.api.platform.is_win:
      test_path += '.exe'

    variant = {
        'test_suite': _get_test_suite(target_name, test_suite_suffix),
    }

    tags = [
        ('step_name', step_name),
    ]

    command = self.resultdb.wrap_gtest(
        [test_path] + (args or []),
        test_id_prefix=f'ninja://{target_path}:{target_name}/',
        base_variant=variant,
        base_tags=tags)
    self.local_requests.append(
        _TestRequest(step_name=step_name, command=command))

  def _request_test_runner_py(self, step_name, *, test_type, args,
                              test_suite_suffix, remote):
    test_path = self._join_root_dir('testing', 'tools',
                                    f'run_{test_type}_tests.py')

    variant = {
        'test_suite': _get_test_suite(test_type, test_suite_suffix),
    }

    tags = [
        ('step_name', step_name),
    ]

    command = self.resultdb.wrap(
        PYTHON_CMD + [test_path] + self.test_runner_py_args + args,
        test_id_prefix=f'ninja://testing/tools:run_{test_type}_tests/',
        base_variant=variant,
        base_tags=tags)
    request = _TestRequest(step_name=step_name, command=command)
    if remote and self.swarming:
      self.swarming.request_task(request, env=self.env)
    else:
      self.local_requests.append(request)

  def run_tests(self):
    """Runs previously requested tests."""
    try:
      if self.swarming:
        self.swarming.trigger_tasks()

      # Defer individual failures until the end of this block.
      with self.api.defer.context() as defer:
        collecting = bool(self.swarming)
        while self.local_requests or collecting:
          if self.local_requests:
            with self.api.context(cwd=self.source_dir, env=self.env):
              for request in self.local_requests:
                defer(self.api.step, request.step_name, request.command)
            self.local_requests.clear()

          if collecting:
            collecting, expired_requests = self.swarming.collect_tasks()
            if expired_requests:
              # Fall back to executing expired tasks locally.
              # TODO(crbug.com/pdfium/1933): Execute concurrently with swarming.
              self.local_requests.extend(expired_requests)
    finally:
      self.local_requests.clear()


def _get_test_suite(base_name, suffix=None):
  return f'{base_name}_{suffix}' if suffix else base_name


def _get_modifiable_script_args(api, build_config, option, renderer):
  """Get the list of additional arguments for Python-based tests that can be
  further modified based on test options.
  Returns a list that can be concatenated with the other script arguments.
  """
  additional_args = []

  # Add Skia Gold keys if `build_config` is non-empty.
  if build_config:
    keys = build_config.copy()

    # Add the test suite suffix. Note that the test suite base name already is
    # encoded in the Gold source type.
    if option.test_suite_suffix:
      keys['suite'] = option.test_suite_suffix

    # Add the OS from the builder name.
    builder_name = api.buildbucket.builder_name.strip()
    keys['os'] = builder_name.split('_')[0]

    keys['javascript_runtime'] = 'disabled' if (
        build_config['v8'] == 'false' or
        option.disable_javascript) else 'enabled'
    keys['xfa_runtime'] = 'disabled' if (build_config['xfa'] == 'false' or
                                         option.disable_javascript or
                                         option.disable_xfa) else 'enabled'

    if renderer:
      keys['renderer'] = renderer

    additional_args.extend(['--gold_key', _dict_to_str(keys)])

  if option.additional_arg:
    additional_args.append(option.additional_arg)

  if renderer:
    additional_args.append(f'--use-renderer={renderer}')

  return additional_args


def _dict_to_str(props):
  """Returns the given dictionary as a string of space-separated key/value
  pairs sorted by keys. Fails if the key or value is empty, or contains spaces.
  """
  ret = []
  for k in sorted(props.keys()):
    v = props[k]
    assert k and ' ' not in k, f'Invalid key "{k}"'
    assert v and ' ' not in v, f'Invalid value "{v}" for key "{k}"'
    ret += (k, v)
  return ' '.join(ret)


def _gold_build_config(args):
  """Extracts key value pairs from the arguments handed to 'gn gen'
  and returns them as a dictionary. Since these are used as
  parameters in Gold we strip common prefixes and disregard
  some arguments. i.e. 'use_sysroot' since we don't care about how
  a binary was built.  Only arguments that follow the
  'key=value' pattern are considered.
  """
  exclude_list = ['use_remoteexec', 'use_sysroot', 'is_component_build']
  strip_prefixes = ['is_', 'pdf_enable_', 'pdf_use_', 'pdf_']
  build_config = {}
  for arg in args:
    # Catch multiple k/v pairs separated by spaces.
    parts = arg.split()
    for p in parts:
      kv = [x.strip() for x in p.split('=')]
      if len(kv) == 2:
        k, v = kv
        if k not in exclude_list:
          for prefix in strip_prefixes:
            if k.startswith(prefix):
              k = k[len(prefix):]
              break
          build_config[k] = v
  return build_config


def _gen_try_build(api, builder):
  return api.buildbucket.try_build(
      project='pdfium',
      builder=builder,
      build_number=1234,
      git_repo='https://pdfium.googlesource.com/pdfium',
  )


def _gen_ci_build(api, builder):
  return api.buildbucket.ci_build(
      project='pdfium',
      builder=builder,
      build_number=1234,
      git_repo='https://pdfium.googlesource.com/pdfium',
  )


def _gen_local_build(api, builder):
  build_message = api.buildbucket.try_build_message(builder=builder)
  build_message.infra.ClearField('resultdb')
  return api.buildbucket.build(build_message)


def _gen_properties(api, **kwargs):
  updated_kwargs = {
      'bot_id': 'test_bot',
  }
  updated_kwargs.update(kwargs)
  return api.properties(**updated_kwargs) + api.siso.properties()


def RunSteps(api: DEPS, memory_tool, partition_alloc, skia, rust, xfa, v8,
             target_cpu, rel, run_skia_gold, brotli, component, skip_test,
             target_os, renderers, swarming, use_cxx23):
  update_result = _checkout_step(api, target_os, rust, skia, v8)
  source_dir = update_result.source_root.path
  revision = update_result.properties['got_revision']

  out_dir = _generate_out_path(memory_tool, partition_alloc, skia, rust, xfa,
                               v8, rel, brotli, component)

  with api.osx_sdk('mac'):
    # buildbot sets 'clobber' to the empty string which evaluates to false if
    # checked directly. Instead, check using the 'in' keyword.
    if 'clobber' in api.properties:
      api.file.rmtree('clobber', source_dir.joinpath('out', out_dir))

    build_config = _gn_gen_builds(api, source_dir, memory_tool, partition_alloc,
                                  skia, rust, xfa, v8, target_cpu, rel, brotli,
                                  component, target_os, use_cxx23, out_dir)
    if not run_skia_gold:
      build_config = {}
    _build_steps(api, source_dir, out_dir)

    if skip_test:
      return

    _run_tests(api, source_dir, memory_tool, v8, xfa, skia, out_dir,
               build_config, revision, run_skia_gold, renderers, swarming)


def GenTests(api: TEST_DEPS):
  yield api.test(
      'win',
      api.platform('win', 64),
      api.builder_group.for_current('client.pdfium'),
      _gen_properties(api),
      _gen_ci_build(api, 'windows'),
  )
  yield api.test(
      'linux',
      api.platform('linux', 64),
      api.builder_group.for_current('client.pdfium'),
      _gen_properties(api),
      _gen_ci_build(api, 'linux'),
  )
  yield api.test(
      'linux_cxx23',
      api.platform('linux', 64),
      api.builder_group.for_current('client.pdfium'),
      _gen_properties(api, use_cxx23=True),
      _gen_ci_build(api, 'linux'),
  )
  yield api.test(
      'mac',
      api.platform('mac', 64),
      api.builder_group.for_current('client.pdfium'),
      _gen_properties(api),
      _gen_ci_build(api, 'mac'),
  )

  yield api.test(
      'win_no_v8',
      api.platform('win', 64),
      api.builder_group.for_current('client.pdfium'),
      _gen_properties(api, v8=False),
      _gen_ci_build(api, 'windows_no_v8'),
  )
  yield api.test(
      'linux_no_v8',
      api.platform('linux', 64),
      api.builder_group.for_current('client.pdfium'),
      _gen_properties(api, v8=False),
      _gen_ci_build(api, 'linux_no_v8'),
  )
  yield api.test(
      'mac_no_v8',
      api.platform('mac', 64),
      api.builder_group.for_current('client.pdfium'),
      _gen_properties(api, v8=False),
      _gen_ci_build(api, 'mac_no_v8'),
  )

  yield api.test(
      'win_component',
      api.platform('win', 64),
      api.builder_group.for_current('client.pdfium'),
      _gen_properties(api, component=True, xfa=True),
      _gen_ci_build(api, 'win_component'),
  )

  yield api.test(
      'win_gdi',
      api.platform('win', 64),
      api.builder_group.for_current('client.pdfium'),
      _gen_properties(api, xfa=True, renderers=['gdi']),
      _gen_ci_build(api, 'windows_gdi'),
  )
  yield api.test(
      'win_gdi_skia',
      api.platform('win', 64),
      api.builder_group.for_current('client.pdfium'),
      _gen_properties(api, skia=True, xfa=True, renderers=['gdi']),
      _gen_ci_build(api, 'windows_gdi_skia'),
  )
  yield api.test(
      'win_agg_gdi_skia',
      api.platform('win', 64),
      api.builder_group.for_current('client.pdfium'),
      _gen_properties(
          api, skia=True, xfa=True, renderers=['agg', 'gdi', 'skia']),
      _gen_ci_build(api, 'windows_agg_gdi_skia'),
  )

  yield api.test(
      'win_skia',
      api.platform('win', 64),
      api.builder_group.for_current('client.pdfium'),
      _gen_properties(api, skia=True, xfa=True),
      _gen_ci_build(api, 'windows_skia'),
  )

  yield api.test(
      'win_xfa_32',
      api.platform('win', 64),
      api.builder_group.for_current('client.pdfium'),
      _gen_properties(api, xfa=True, target_cpu='x86'),
      _gen_ci_build(api, 'windows_xfa_32'),
  )

  yield api.test(
      'win_xfa',
      api.platform('win', 64),
      api.builder_group.for_current('client.pdfium'),
      _gen_properties(api, xfa=True),
      _gen_ci_build(api, 'windows_xfa'),
  )

  yield api.test(
      'win_xfa_rel',
      api.platform('win', 64),
      api.builder_group.for_current('client.pdfium'),
      _gen_properties(api, xfa=True, rel=True),
      _gen_ci_build(api, 'windows_xfa_rel'),
  )

  yield api.test(
      'linux_component',
      api.platform('linux', 64),
      api.builder_group.for_current('client.pdfium'),
      _gen_properties(api, component=True, xfa=True),
      _gen_ci_build(api, 'linux_component'),
  )

  yield api.test(
      'linux_skia',
      api.platform('linux', 64),
      api.builder_group.for_current('client.pdfium'),
      _gen_properties(api, skia=True, xfa=True),
      _gen_ci_build(api, 'linux_skia'),
  )

  yield api.test(
      'linux_skia_rust',
      api.platform('linux', 64),
      api.builder_group.for_current('client.pdfium'),
      _gen_properties(api, skia=True, rust=True, xfa=True),
      _gen_ci_build(api, 'linux_skia_rust'),
  )

  yield api.test(
      'linux_xfa',
      api.platform('linux', 64),
      api.builder_group.for_current('client.pdfium'),
      _gen_properties(api, xfa=True),
      _gen_ci_build(api, 'linux_xfa'),
  )

  yield api.test(
      'linux_xfa_rel',
      api.platform('linux', 64),
      api.builder_group.for_current('client.pdfium'),
      _gen_properties(api, xfa=True, rel=True),
      _gen_ci_build(api, 'linux_xfa_rel'),
  )

  yield api.test(
      'mac_component',
      api.platform('mac', 64),
      api.builder_group.for_current('client.pdfium'),
      _gen_properties(api, component=True, xfa=True),
      _gen_ci_build(api, 'mac_component'),
  )

  yield api.test(
      'mac_skia',
      api.platform('mac', 64),
      api.builder_group.for_current('client.pdfium'),
      _gen_properties(api, skia=True, xfa=True),
      _gen_ci_build(api, 'mac_skia'),
  )

  yield api.test(
      'mac_xfa',
      api.platform('mac', 64),
      api.builder_group.for_current('client.pdfium'),
      _gen_properties(api, xfa=True),
      _gen_ci_build(api, 'mac_xfa'),
  )

  yield api.test(
      'mac_xfa_rel',
      api.platform('mac', 64),
      api.builder_group.for_current('client.pdfium'),
      _gen_properties(api, xfa=True, rel=True),
      _gen_ci_build(api, 'mac_xfa_rel'),
  )

  yield api.test(
      'linux_asan_lsan',
      api.platform('linux', 64),
      api.builder_group.for_current('client.pdfium'),
      _gen_properties(api, memory_tool='asan'),
      _gen_ci_build(api, 'linux_asan_lsan'),
  )

  yield api.test(
      'linux_msan',
      api.platform('linux', 64),
      api.builder_group.for_current('client.pdfium'),
      _gen_properties(api, memory_tool='msan', rel=True),
      _gen_ci_build(api, 'linux_msan'),
  )

  yield api.test(
      'linux_ubsan',
      api.platform('linux', 64),
      api.builder_group.for_current('client.pdfium'),
      _gen_properties(api, memory_tool='ubsan', rel=True),
      _gen_ci_build(api, 'linux_ubsan'),
  )

  yield api.test(
      'linux_no_partition_alloc',
      api.platform('linux', 64),
      api.builder_group.for_current('client.pdfium'),
      _gen_properties(api, partition_alloc=False),
      _gen_ci_build(api, 'linux'),
  )

  yield api.test(
      'linux_xfa_asan_lsan',
      api.platform('linux', 64),
      api.builder_group.for_current('client.pdfium'),
      _gen_properties(api, memory_tool='asan', xfa=True),
      _gen_ci_build(api, 'linux_xfa_asan_lsan'),
  )

  yield api.test(
      'linux_xfa_msan',
      api.platform('linux', 64),
      api.builder_group.for_current('client.pdfium'),
      _gen_properties(api, memory_tool='msan', rel=True, xfa=True),
      _gen_ci_build(api, 'linux_xfa_msan'),
  )

  yield api.test(
      'linux_xfa_ubsan',
      api.platform('linux', 64),
      api.builder_group.for_current('client.pdfium'),
      _gen_properties(api, memory_tool='ubsan', rel=True, xfa=True),
      _gen_ci_build(api, 'linux_xfa_ubsan'),
  )

  yield api.test(
      'win_asan',
      api.platform('win', 64),
      api.builder_group.for_current('client.pdfium'),
      _gen_properties(api, memory_tool='asan', rel=True),
      _gen_ci_build(api, 'windows_asan'),
  )

  yield api.test(
      'win_xfa_asan',
      api.platform('win', 64),
      api.builder_group.for_current('client.pdfium'),
      _gen_properties(api, memory_tool='asan', rel=True, xfa=True),
      _gen_ci_build(api, 'windows_xfa_asan'),
  )

  yield api.test(
      'try-linux-gerrit_xfa_asan_lsan',
      api.platform('linux', 64),
      api.builder_group.for_current('tryserver.client.pdfium'),
      _gen_properties(api, xfa=True, memory_tool='asan'),
      _gen_try_build(api, 'linux_xfa_asan_lsan'),
  )

  yield api.test(
      'android',
      api.platform('linux', 64),
      api.builder_group.for_current('client.pdfium'),
      _gen_properties(
          api, target_os='android', target_cpu='arm64', skip_test=True),
      _gen_ci_build(api, 'android'),
  )

  yield api.test(
      'android_32',
      api.platform('linux', 64),
      api.builder_group.for_current('client.pdfium'),
      _gen_properties(api, target_os='android', skip_test=True),
      _gen_ci_build(api, 'android'),
  )

  yield api.test(
      'clobber-linux_skia',
      api.platform('linux', 64),
      api.builder_group.for_current('client.pdfium'),
      _gen_properties(api, skia=True, xfa=True, clobber=''),
      _gen_ci_build(api, 'linux_skia'),
  )

  yield api.test(
      'clobber-mac_xfa_rel',
      api.platform('mac', 64),
      api.builder_group.for_current('client.pdfium'),
      _gen_properties(api, xfa=True, rel=True, clobber=''),
      _gen_ci_build(api, 'mac_xfa_rel'),
  )

  yield api.test(
      'clobber-win_xfa',
      api.platform('win', 64),
      api.builder_group.for_current('client.pdfium'),
      _gen_properties(api, xfa=True, clobber=''),
      _gen_ci_build(api, 'windows_xfa'),
  )

  yield api.test(
      'linux_skia_brotli',
      api.platform('linux', 64),
      api.builder_group.for_current('client.pdfium'),
      _gen_properties(api, skia=True, brotli=True, clobber=''),
      _gen_ci_build(api, 'linux_skia_brotli'),
  )

  yield api.test(
      'win_skia_brotli',
      api.platform('win', 64),
      api.builder_group.for_current('client.pdfium'),
      _gen_properties(api, skia=True, brotli=True, clobber=''),
      _gen_ci_build(api, 'windows_skia_brotli'),
  )

  yield api.test(
      'mac_skia_brotli',
      api.platform('mac', 64),
      api.builder_group.for_current('client.pdfium'),
      _gen_properties(api, skia=True, brotli=True, clobber=''),
      _gen_ci_build(api, 'mac_skia_brotli'),
  )

  yield api.test(
      'swarming-win',
      api.platform('win', 64),
      api.builder_group.for_current('client.pdfium'),
      _gen_properties(
          api,
          swarming={
              'dimensions': {
                  'pool': 'luci.flex.ci',
              },
              'expiration_secs':
                  3 * 3600,
              'service_account':
                  'pdfium-ci-builder@chops-service-accounts.iam.gserviceaccount.com',
          }),
      _gen_ci_build(api, 'windows'),
  )
  yield api.test(
      'swarming-try-win',
      api.platform('win', 64),
      api.builder_group.for_current('client.pdfium'),
      _gen_properties(
          api,
          swarming={
              'dimensions': {
                  'pool': 'luci.flex.try',
              },
              'execution_timeout_secs': 10 * 60,
              'priority': 42,
          }),
      _gen_try_build(api, 'windows'),
  )
  yield api.test(
      'swarming-eager-task',
      api.platform('linux', 64),
      api.builder_group.for_current('client.pdfium'),
      _gen_properties(api, swarming={
          'dimensions': {
              'pool': 'luci.flex.ci',
          },
      }),
      _gen_try_build(api, 'linux'),
      api.step_data(
          'collect tasks',
          api.swarming.collect([
              {
                  'error': 'context canceled',
                  'results': {
                      'name': 'corpus tests',
                      'state': 'PENDING',
                      'task_id': '0',
                  },
              },
              {
                  'error': 'context canceled',
                  'results': {
                      'name': 'corpus tests (oneshot rendering enabled)',
                      'state': 'PENDING',
                      'task_id': '1',
                  },
              },
              {
                  'output': 'hello world!',
                  'results': {
                      'exit_code': '0',
                      'name': 'corpus tests (reverse byte order)',
                      'state': 'COMPLETED',
                      'task_id': '2',
                  },
              },
              {
                  'error': 'context canceled',
                  'results': {
                      'name': 'corpus tests (javascript disabled)',
                      'state': 'PENDING',
                      'task_id': '3',
                  },
              },
          ])),
  )
  yield api.test(
      'swarming-eager-task-new-cli',
      api.platform('linux', 64),
      api.builder_group.for_current('client.pdfium'),
      _gen_properties(api, swarming={
          'dimensions': {
              'pool': 'luci.flex.ci',
          },
      }),
      _gen_ci_build(api, 'linux'),
      api.step_data(
          'collect tasks',
          api.swarming.collect([
              {
                  'output': 'hello world!',
                  'results': {
                      'exit_code': '0',
                      'name': 'corpus tests (reverse byte order)',
                      'state': 'COMPLETED',
                      'task_id': '2',
                  },
              },
              {
                  'results': {
                      'name': 'corpus tests (oneshot rendering enabled)',
                      'state': 'PENDING',
                      'task_id': '1',
                  },
              },
              {
                  'results': {
                      'name': 'corpus tests (javascript disabled)',
                      'state': 'PENDING',
                      'task_id': '3',
                  },
              },
          ])),
  )
  yield api.test(
      'swarming-expired-task',
      api.platform('linux', 64),
      api.builder_group.for_current('client.pdfium'),
      _gen_properties(api, swarming={
          'dimensions': {
              'pool': 'luci.flex.ci',
          },
      }),
      _gen_try_build(api, 'linux'),
      api.step_data(
          'collect tasks',
          api.swarming.collect([
              {
                  'error': 'context canceled',
                  'results': {
                      'name': 'corpus tests',
                      'state': 'PENDING',
                      'task_id': '0',
                  },
              },
              {
                  'results': {
                      'name': 'corpus tests (oneshot rendering enabled)',
                      'state': 'EXPIRED',
                      'task_id': '1',
                  },
              },
              {
                  'error': 'context canceled',
                  'results': {
                      'name': 'corpus tests (reverse byte order)',
                      'state': 'PENDING',
                      'task_id': '2',
                  },
              },
              {
                  'error': 'context canceled',
                  'results': {
                      'name': 'corpus tests (javascript disabled)',
                      'state': 'PENDING',
                      'task_id': '3',
                  },
              },
          ])),
  )

  yield api.test(
      'fail-unittests',
      api.platform('linux', 64),
      api.builder_group.for_current('client.pdfium'),
      _gen_properties(api),
      _gen_ci_build(api, 'linux'),
      api.step_data('unittests', retcode=1),
      api.expect_status('FAILURE'),
  )

  yield api.test(
      'fail-embeddertests',
      api.platform('linux', 64),
      api.builder_group.for_current('client.pdfium'),
      _gen_properties(api),
      _gen_ci_build(api, 'linux'),
      api.step_data('embeddertests', retcode=1),
      api.expect_status('FAILURE'),
  )

  yield api.test(
      'fail-javascript-tests',
      api.platform('linux', 64),
      api.builder_group.for_current('client.pdfium'),
      _gen_properties(api),
      _gen_ci_build(api, 'linux'),
      api.step_data('javascript tests', retcode=1),
      api.expect_status('FAILURE'),
  )

  yield api.test(
      'fail-javascript-tests-javascript-disabled',
      api.platform('linux', 64),
      api.builder_group.for_current('client.pdfium'),
      _gen_properties(api),
      _gen_ci_build(api, 'linux'),
      api.step_data('javascript tests (javascript disabled)', retcode=1),
      api.expect_status('FAILURE'),
  )

  yield api.test(
      'fail-javascript-tests-xfa-disabled',
      api.platform('linux', 64),
      api.builder_group.for_current('client.pdfium'),
      _gen_properties(api, xfa=True),
      _gen_ci_build(api, 'linux'),
      api.step_data('javascript tests (xfa disabled)', retcode=1),
      api.expect_status('FAILURE'),
  )

  yield api.test(
      'fail-pixel-tests',
      api.platform('linux', 64),
      api.builder_group.for_current('client.pdfium'),
      _gen_properties(api),
      _gen_ci_build(api, 'linux'),
      api.step_data('pixel tests', retcode=1),
      api.expect_status('FAILURE'),
  )

  yield api.test(
      'fail-pixel-tests-oneshot-rendering-enabled',
      api.platform('linux', 64),
      api.builder_group.for_current('client.pdfium'),
      _gen_properties(api),
      _gen_ci_build(api, 'linux'),
      api.step_data('pixel tests (oneshot rendering enabled)', retcode=1),
      api.expect_status('FAILURE'),
  )

  yield api.test(
      'fail-pixel-tests-reverse-byte-order',
      api.platform('linux', 64),
      api.builder_group.for_current('client.pdfium'),
      _gen_properties(api),
      _gen_ci_build(api, 'linux'),
      api.step_data('pixel tests (reverse byte order)', retcode=1),
      api.expect_status('FAILURE'),
  )

  yield api.test(
      'fail-pixel-tests-javascript-disabled',
      api.platform('linux', 64),
      api.builder_group.for_current('client.pdfium'),
      _gen_properties(api),
      _gen_ci_build(api, 'linux'),
      api.step_data('pixel tests (javascript disabled)', retcode=1),
      api.expect_status('FAILURE'),
  )

  yield api.test(
      'fail-pixel-tests-xfa-disabled',
      api.platform('linux', 64),
      api.builder_group.for_current('client.pdfium'),
      _gen_properties(api, xfa=True),
      _gen_ci_build(api, 'linux'),
      api.step_data('pixel tests (xfa disabled)', retcode=1),
      api.expect_status('FAILURE'),
  )

  yield api.test(
      'fail-corpus-tests',
      api.platform('linux', 64),
      api.builder_group.for_current('client.pdfium'),
      _gen_properties(api),
      _gen_ci_build(api, 'linux'),
      api.step_data('corpus tests', retcode=1),
      api.expect_status('FAILURE'),
  )

  yield api.test(
      'fail-corpus-tests-oneshot-rendering-enabled',
      api.platform('linux', 64),
      api.builder_group.for_current('client.pdfium'),
      _gen_properties(api),
      _gen_ci_build(api, 'linux'),
      api.step_data('corpus tests (oneshot rendering enabled)', retcode=1),
      api.expect_status('FAILURE'),
  )

  yield api.test(
      'fail-corpus-tests-reverse-byte-order',
      api.platform('linux', 64),
      api.builder_group.for_current('client.pdfium'),
      _gen_properties(api),
      _gen_ci_build(api, 'linux'),
      api.step_data('corpus tests (reverse byte order)', retcode=1),
      api.expect_status('FAILURE'),
  )

  yield api.test(
      'fail-corpus-tests-javascript-disabled',
      api.platform('linux', 64),
      api.builder_group.for_current('client.pdfium'),
      _gen_properties(api),
      _gen_ci_build(api, 'linux'),
      api.step_data('corpus tests (javascript disabled)', retcode=1),
      api.expect_status('FAILURE'),
  )

  yield api.test(
      'fail-corpus-tests-xfa-disabled',
      api.platform('linux', 64),
      api.builder_group.for_current('client.pdfium'),
      _gen_properties(api, xfa=True),
      _gen_ci_build(api, 'linux'),
      api.step_data('corpus tests (xfa disabled)', retcode=1),
      api.expect_status('FAILURE'),
  )

  yield api.test(
      'disable-skia-gold-linux',
      api.platform('linux', 64),
      api.builder_group.for_current('client.pdfium'),
      _gen_properties(api, run_skia_gold=False),
      _gen_ci_build(api, 'linux'),
  )

  yield api.test(
      'fail-invalid-renderer',
      api.platform('win', 64),
      api.builder_group.for_current('client.pdfium'),
      _gen_properties(api, xfa=True, renderers=['agg', 'fake', 'gdi']),
      _gen_ci_build(api, 'windows'),
      api.expect_exception('ValueError'),
  )

  yield api.test(
      'fail-swarming-infra',
      api.platform('linux', 64),
      api.builder_group.for_current('client.pdfium'),
      _gen_properties(api, swarming={
          'dimensions': {
              'pool': 'luci.flex.ci',
          },
      }),
      _gen_ci_build(api, 'linux'),
      api.step_data(
          'collect tasks',
          api.swarming.collect([
              {
                  'output': 'hello world!',
                  'results': {
                      'exit_code': '0',
                      'name': 'corpus tests',
                      'state': 'COMPLETED',
                      'task_id': '0',
                  },
              },
              {
                  'results': {
                      'name': 'corpus tests (oneshot rendering enabled)',
                      'state': 'BOT_DIED',
                      'task_id': '1',
                  },
              },
              {
                  'error': 'goodbye world!',
                  'results': {
                      'task_id': '2',
                  },
              },
              {
                  'output': 'hello world!',
                  'results': {
                      'exit_code': '0',
                      'name': 'corpus tests (javascript disabled)',
                      'state': 'COMPLETED',
                      'task_id': '3',
                  },
              },
          ])),
      api.expect_status('INFRA_FAILURE'),
  )
  yield api.test(
      'fail-swarming-command',
      api.platform('linux', 64),
      api.builder_group.for_current('client.pdfium'),
      _gen_properties(api, swarming={
          'dimensions': {
              'pool': 'luci.flex.ci',
          },
      }),
      _gen_ci_build(api, 'linux'),
      api.step_data(
          'collect tasks',
          api.swarming.collect([
              {
                  'output': 'hello world!',
                  'results': {
                      'exit_code': '0',
                      'name': 'corpus tests',
                      'state': 'COMPLETED',
                      'task_id': '0',
                  },
              },
              {
                  'output': 'hello world!',
                  'results': {
                      'exit_code': '1',
                      'name': 'corpus tests (oneshot rendering enabled)',
                      'state': 'COMPLETED',
                      'task_id': '1',
                  },
              },
              {
                  'output': 'hello world!',
                  'results': {
                      'exit_code': '0',
                      'name': 'corpus tests (reverse byte order)',
                      'state': 'COMPLETED',
                      'task_id': '2',
                  },
              },
              {
                  'output': 'hello world!',
                  'results': {
                      'exit_code': '1',
                      'name': 'corpus tests (javascript disabled)',
                      'state': 'COMPLETED',
                      'task_id': '3',
                  },
              },
          ])),
      api.expect_status('FAILURE'),
  )
  yield api.test(
      'fail-swarming-local-test',
      api.platform('linux', 64),
      api.builder_group.for_current('client.pdfium'),
      _gen_properties(api, swarming={
          'dimensions': {
              'pool': 'luci.flex.ci',
          },
      }),
      _gen_ci_build(api, 'linux'),
      api.step_data('unittests', retcode=1),
      api.expect_status('FAILURE'),
  )

  yield api.test(
      'local',
      api.siso.properties(),
      _gen_local_build(api, 'linux'),
  )
