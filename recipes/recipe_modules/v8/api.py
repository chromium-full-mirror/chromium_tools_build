# Copyright 2013 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import ast
import collections.abc
import contextlib
import re

from functools import cached_property

from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb
from PB.recipe_engine.result import RawResult

from RECIPE_MODULES.depot_tools import bot_update

from recipe_engine import recipe_api
from recipe_engine.config_types import Path
from recipe_engine.engine_types import freeze, thaw
from . import bisection

MILO_HOST = 'luci-milo.appspot.com'
V8_URL = 'https://chromium.googlesource.com/v8/v8'

V8_CI_AUTOROLL_BUILDER = (
    'v8-ci-autoroll-builder@chops-service-accounts.iam.gserviceaccount.com')

COMMIT_TEMPLATE = '%s/+/%%s' % V8_URL

# Regular expressions for v8 branch names.
RELEASE_BRANCH_RE = re.compile(r'^refs/branch-heads/\d+\.\d+$')

# Regular expressions for getting target bits from gn args.
TARGET_CPU_RE = re.compile(r'.*target_cpu\s+=\s+"([^"]*)".*')

# Factor by which the considered failure for bisection must be faster than the
# ongoing build's total.
BISECT_DURATION_FACTOR = 5

VERSION_LINE_RE = r'^#define %s\s+(\d*)$'
VERSION_LINE_REPLACEMENT = '#define %s %s'
V8_MAJOR = 'V8_MAJOR_VERSION'
V8_MINOR = 'V8_MINOR_VERSION'
V8_BUILD = 'V8_BUILD_NUMBER'
V8_PATCH = 'V8_PATCH_LEVEL'

LCOV_IMAGE = 'lcov:2018-01-18_17-03'

COVERAGE_URL = 'https://storage.googleapis.com/chromium-v8/coverage'

# Name of the file extension for profraw data files.
PROFRAW_FILE_EXTENSION = 'profraw'

# TODO(https://crbug.com/1265931): Infer this on the V8 side.
V8_EXECUTABLES = [
    'cctest',
    'd8',
    'inspector-test',
    'mkgrokdump',
    'v8_simple_inspector_fuzzer',
    'v8_simple_json_fuzzer',
    'v8_simple_multi_return_fuzzer',
    'v8_simple_parser_fuzzer',
    'v8_simple_regexp_fuzzer',
    'v8_simple_wasm_async_fuzzer',
    'v8_simple_wasm_code_fuzzer',
    'v8_simple_wasm_compile_fuzzer',
    'v8_simple_wasm_compile_all_fuzzer',
    'v8_simple_wasm_compile_simd_fuzzer',
    'v8_simple_wasm_compile_wasmgc_fuzzer',
    'v8_simple_wasm_deopt_fuzzer',
    'v8_simple_wasm_init_expr_fuzzer',
    'v8_simple_wasm_module_fuzzer',
    'v8_simple_wasm_streaming_fuzzer',
    'v8_unittests',
    'wasm_api_tests',
]

class V8Version:
  """A v8 version as used for tagging (with patch level), e.g. '3.4.5.1'."""

  def __init__(self, major, minor, build, patch):
    self.major = major
    self.minor = minor
    self.build = build
    self.patch = patch

  def __str__(self):
    patch_str = '.%s' % self.patch if self.patch and self.patch != '0' else ''
    return f'{self.major}.{self.minor}.{self.build}{patch_str}'

  def with_build(self, build):
    return V8Version(self.major, self.minor, build, 0)

  def with_patch(self, patch):
    return V8Version(self.major, self.minor, self.build, patch)

  def with_incremented_patch(self):
    return self.with_patch(str(int(self.patch) + 1))

  def with_increment_for_chromium(self, chromium_version):
    """Returns a version with an increment for dev or mini branches
    that's unlikely to clash with any other version.

    This pads a number in front of the existing build-level part of the
    version.

    We can't just update the patch level on the tip of such branches as e.g.
    on mini branches, the original XXXX and the mini branch XXXX_YY might get
    released with the same patch level otherwise. And the same canary/dev
    branches, which could theoretically clash with the current roll branch
    if it independently got patched before.

    These increments aren't very logical, they just aim to be unique, retain
    4 digit versions (as they are consumed like that in many places), with
    numbers that fit into an x86 int.
    """
    chromium_version = str(chromium_version)
    if '_' in chromium_version:
      # Mini branch.
      pad = int(chromium_version.rsplit('_', maxsplit=1)[-1])
    else:
      # Canary/dev branch.
      pad = int(chromium_version)
    assert pad > 0

    pad = f'{pad}0'
    if self.build.startswith(pad):
      # We already landed the disambiguating version on that branch. In this
      # case we can bump the patch level from here on.
      return self.with_incremented_patch()

    # Now we should end up with a typical V8 version's build level of 3 digits
    # or less. Just ensure it's not a different padded large version from
    # above.
    assert len(self.build) <= 4, 'Unsupported V8 version or branch.'

    return self.with_build(f'{pad}{self.build}')

  def update_version_file_blob(self, blob):
    """Takes a version file's text and returns it with this object's version.
    """
    def sub(label, value, text):
      return re.sub(
          VERSION_LINE_RE % label,
          VERSION_LINE_REPLACEMENT % (label, value),
          text,
          flags=re.M,
      )
    blob = sub(V8_MAJOR, self.major, blob)
    blob = sub(V8_MINOR, self.minor, blob)
    blob = sub(V8_BUILD, self.build, blob)
    return sub(V8_PATCH, self.patch, blob)

  def with_incremented_minor(self):
    new_minor = int(self.minor) + 1
    new_major = int(self.major)
    if new_minor == 10:
      new_minor = 0
      new_major += 1
    return V8Version(str(new_major), str(new_minor), 0, 0)


class Trigger:

  def __init__(self, api):
    self.api = api


  def buildbucket(self, requests, project, bucket, step_name):
    """Triggers builds via buildbucket.

    Args:
      requests: List of 2-tuples (builder_name, properties).
      project: Project to trigger builds in (defaults to same as parent).
      bucket: Bucket to trigger builds in (defaults to same as parent).
      step_name: Name of the triggering step that appear on the build.

    Returns:
      List of api.buildbucket.build_pb2.Build messages.
    """
    raise NotImplementedError()  # pragma: no cover

  def scheduler(self, builders, properties, test_spec):
    """Triggers builds via scheduler. Typically used by CI builders.

    Args:
      builders: List of builder names to trigger.
      properties: Properties common for every builder.
      test_spec: Test specification object with configurations per builder.
    """
    raise NotImplementedError()  # pragma: no cover

class ProdTrigger(Trigger):
  def buildbucket(self, requests, project=None, bucket=None,
                  step_name='trigger'):
    project = project or self.api.buildbucket.INHERIT
    bucket = bucket or self.api.buildbucket.INHERIT

    build = self.api.buildbucket.build
    extra_tags = {
      'triggered_by': (
        f'build/milo/{build.builder.project}/{build.builder.bucket}/'
        f'{build.builder.builder}/{build.number}'
      ),
    }

    # Add user_agent:cq to child builds if the parent is also triggered by CQ.
    if any(tag.key == 'user_agent' and tag.value == 'cq'
           for tag in self.api.buildbucket.build.tags):
      extra_tags['user_agent'] = 'cq'

    return self.api.buildbucket.schedule([
      self.api.buildbucket.schedule_request(
        project=project,
        bucket=bucket,
        builder=builder_name,
        tags=self.api.buildbucket.tags(**extra_tags),
        properties=properties,
      ) for builder_name, properties in requests
    ], step_name=step_name)

  def scheduler(self, builders, properties, test_spec):
    with self.api.step.nest('trigger'):
      jobs = self._get_v8_jobs()
      pairs = self._builder_job_pairs(builders, jobs)
      scheduler_triggers = [(self._scheduler_trigger(builder_name,
                                                     properties,
                                                     test_spec), 'v8', [job_id])
                            for builder_name, job_id in pairs]
      self.api.scheduler.emit_triggers(scheduler_triggers, step_name='trigger')

  def _scheduler_trigger(self, builder_name, ci_properties, test_spec):
    return self.api.scheduler.BuildbucketTrigger(
        properties=dict(ci_properties,
                        **test_spec.as_properties_dict(builder_name)),
    )

  def _builder_job_pairs(self, builders, jobs):
    result = []
    for builder in builders:
      if builder in jobs:
        job = builder
      else:
        bucket = self.api.buildbucket.build.builder.bucket
        job = f'{bucket}-{builder}'
      result.append((builder, job))
    return result

  def _get_v8_jobs(self):
    args = [
        'prpc', 'call', '-format=json', 'luci-scheduler.appspot.com',
        'scheduler.Scheduler.GetJobs'
    ]
    input_data = {"project": "v8"}
    jobs_file = self.api.path.tmp_base_dir / 'jobs.json'
    self.api.step(
        "get V8 jobs",
        args,
        stdin=self.api.json.input(input_data),
        stdout=self.api.json.output(leak_to=jobs_file),
    )
    response = self.api.file.read_json(
        "read jobs json",
        jobs_file,
        test_data={'jobs': []},
    )
    return set(job['jobRef']['job'] for job in response['jobs'])


class LedTrigger(Trigger):
  def buildbucket(self, requests, project=None, bucket=None, step_name=None):
    bucket = bucket or self.api.buildbucket.build.builder.bucket
    project = project or self.api.buildbucket.build.builder.project
    for builder_name, properties in requests:
      self.api.led.trigger_builder(project, bucket, builder_name, properties)
    return []  # Empty list of production buildbucket builds.

  def scheduler(self, builders, properties, test_spec):
    bucket = self.api.buildbucket.build.builder.bucket
    project = self.api.buildbucket.build.builder.project
    for builder_name in builders:
      self.api.led.trigger_builder(
          project,
          bucket,
          builder_name,
          dict(properties, **test_spec.as_properties_dict(builder_name)),
      )


class V8Api(recipe_api.RecipeApi):
  VERSION_FILE = 'include/v8-version.h'

  def __init__(self, properties, *args, **kwargs):
    super().__init__(*args, **kwargs)
    self.bot_config = None
    self.revision = None
    self.revision_cp = None
    self.revision_number = None
    self.always_isolate_targets = properties.get('always_isolate_targets', [])
    self.recipe_result = RawResult(status=common_pb.SUCCESS)
    self._build_config = None

  @property
  def dry_run(self):
    """Indicate that we don't want to alter external state."""
    return self.m.properties.get('dry_run') or self.m.runtime.is_experimental

  # TODO(machenbach): Temporary convenience method to update recipe
  # dependencies.
  def update_test_configs(self, *args, **kwargs):  # pragma: no cover
    return self.m.v8_tests.update_test_configs(*args, **kwargs)

  # TODO(machenbach): Temporary convenience method to update recipe
  # dependencies.
  @property
  def TEST_SPEC(self):  # pragma: no cover
    return self.m.v8_tests.TEST_SPEC

  # TODO(machenbach): Temporary convenience method to update recipe
  # dependencies.
  @property
  def isolated_tests(self):  # pragma: no cover
    return self.m.v8_tests.isolated_tests

  @property
  def trigger(self):
    if self.m.led.launched_by_led:
      return LedTrigger(self.m)
    return ProdTrigger(self.m)

  @property
  def trigger_prod(self):
    return ProdTrigger(self.m)

  def _python(self, name, exe, script, args, **kwargs):
    cmd = [exe, '-u', script] + list(args or [])
    return self.m.step(name, cmd, **kwargs)

  def python(self, name, script, args=None, **kwargs):
    return self._python(name, 'python3', script, args, **kwargs)

  def vpython(self, name, script, args=None, **kwargs):
    return self._python(name, 'vpython3', script, args, **kwargs)

  def get_bot_config(self, binary_size_tracking, clusterfuzz_archive, coverage,
                     enable_swarming, target_arch, target_platform,
                     track_build_dependencies, triggers, triggers_proxy):
    """Get bot_config dict with src-side properties.

    Args:
      binary_size_tracking: Additional configurations to enable binary size
          tracking.
      clusterfuzz_archive: Additional configurations set for archiving builds to
          GS buckets for clusterfuzz.
      coverage: Optional coverage setting.
      enable_swarming: Switch to enable/disable swarming.
      target_arch: Config value for TARGET_ARCH in chromium recipe module.
      target_platform: Config value for TARGET_PLATFORM in chromium recipe
          module.
      track_build_dependencies: Weather to track and upload build-dependencies.
      triggers: List of tester names to trigger on success.
      triggers_proxy: Weather to trigger the internal trigger proxy.

    Returns:
      A bot_config dict.
    """
    # TODO(machenbach): Turn the bot_config dict into a proper class.
    bot_config = {}
    if not self.m.properties.get('parent_buildername'):
      # Builders and builder_testers both build and need the following set of
      # default chromium configs:
      bot_config['chromium_apply_config'] = [
          'default_compiler', 'mb', 'mb_no_luci_auth'
      ]
    bot_config['v8_config_kwargs'] = dict(
        bot_config.get('v8_config_kwargs', {}))
    # Update only specified properties.
    for k, v in (
        ('TARGET_ARCH', target_arch),
        ('TARGET_PLATFORM', target_platform)):
      if v is not None:
        bot_config['v8_config_kwargs'][k] = v
    if coverage is not None:
      bot_config['coverage'] = coverage
    if enable_swarming is not None:
      bot_config['enable_swarming'] = enable_swarming
    if binary_size_tracking is not None:
      bot_config['binary_size_tracking'] = binary_size_tracking
    if clusterfuzz_archive is not None:
      bot_config['clusterfuzz_archive'] = clusterfuzz_archive
    if track_build_dependencies is not None:
      bot_config['track_build_dependencies'] = track_build_dependencies
    # Make mutable copy.
    bot_config['triggers'] = list(bot_config.get('triggers', []))
    bot_config['triggers'].extend(triggers or [])
    # TODO(machenbach): Temporarily also dedupe, during migrating triggers src
    # side. Should be removed when everything has migrated.
    bot_config['triggers'] = sorted(list(set(bot_config['triggers'])))
    bot_config['triggers_proxy'] = triggers_proxy
    return freeze(bot_config)

  def get_test_roots(self, source_dir):
    """Returns the list of default and extensible test root directories.

    A test root is a directory with the following layout:
    <root>/infra/testing/config.pyl (optional)
    <root>/infra/testing/builders.pyl
    <root>/test/<test suites> (optional)

    By default, the V8 checkout is a test root, and all matching directories
    under v8/custom_deps.

    Returns: List of paths to test roots.
    """
    result = [source_dir]
    custom_deps_dir = source_dir / 'custom_deps'
    self.m.file.ensure_directory('ensure custom_deps dir', custom_deps_dir)
    for path in self.m.file.listdir('list test roots', custom_deps_dir):
      if self.m.path.exists(path.joinpath('infra', 'testing', 'builders.pyl')):
        assert self.bot_type == 'builder_tester', (
            'Separate test checkouts are only supported on builder_testers. '
            'For separate builders and testers, the test configs need to be '
            'transferred as properties')
        result.append(path)
    return result

  def load_dynamic_test_configs(self, root):
    """Add test configs from configured location.

    The test configs in <root>/infra/testing/config.pyl are expected to follow
    the same structure as the TEST_CONFIGS dict in testing.py.

    Args:
      test_checkout: Path to test root, can either be the V8 checkout or an
          additional test checkout.
    Returns: Test config dict.
    """
    test_config_path = root.joinpath('infra', 'testing', 'config.pyl')

    # Fallback for branch builders.
    if not self.m.path.exists(test_config_path):
      return {}

    try:
      # Eval python literal file.
      test_configs = ast.literal_eval(self.m.file.read_text(
          f'read test config ({self.m.path.basename(root)})',
          test_config_path,
          test_data='{}',
      ))
    except SyntaxError as e:  # pragma: no cover
      raise self.m.step.InfraFailure(
          f'Failed to parse test config "{test_config_path}": {e}')

    for test_config in test_configs.values():
      # This configures the test runner to set the test root to the
      # test_checkout location for all tests from this checkout.
      # TODO(machenbach): This is starting to get hacky. The test config
      # dicts should be refactored into classes similar to test specs. Maybe
      # the extra configurations from test configs could be added to test
      # specs.
      test_config['test_root'] = str(root / 'test')

    return test_configs

  def _configure_clusterfuzz_builders(self):
    if self.bot_config.get('clusterfuzz_archive'):
      self.m.chromium.apply_config('default_target_v8_clusterfuzz')

  def _configure_perf_builders(self):
    if self.m.builder_group.for_current == 'client.v8.perf':
      self.m.chromium.apply_config('default_target_d8')

  def _update_dict(self, initial, update):
    for key, update_value in update.items():
      initial_value = initial.get(key)
      is_nested = (
          isinstance(initial_value, collections.abc.Mapping) and
          isinstance(update_value, collections.abc.Mapping))
      if is_nested:
        self._update_dict(initial_value, update_value)
      else:
        initial[key] = update_value

  def _set_repo_bot_config(self, bot_config, revision=None):
    """Override the bot config based on custom builder properties in
    <repo>/infra/builder_properties.pyl.

    If the file does not exist in the repository, gitiles returns a 404 and
    this method does not substitute any configuration.
    """
    revision = self.get_revision(revision)

    if not self.m.properties.get('apply_repo_bot_config_override'):
      return bot_config

    builder_properties = self.m.gitiles.download_file(
        'https://chromium.googlesource.com/v8/v8',
        'infra/builder_properties.pyl',
        revision,
        accept_statuses=[200, 404],
        step_test_data=lambda: self.m.gitiles.test_api.make_encoded_file(''),
    )

    if not builder_properties:
      return bot_config

    builder_name = self.m.buildbucket.builder_name
    custom_properties = ast.literal_eval(builder_properties).get(builder_name)

    thawed_bot_config = thaw(bot_config)
    self._update_dict(thawed_bot_config, custom_properties or {})
    return freeze(thawed_bot_config)

  def apply_bot_config(self, bot_config, revision=None):
    """Entry method for using the v8 api."""
    with self.m.step.nest('Retrieve bot_config') as step:
      self.bot_config = self._set_repo_bot_config(bot_config, revision=revision)
      step.logs['merged bot_config'] = self.m.json.dumps(self.bot_config)

    kwargs = {}
    kwargs.update(self.bot_config.get('v8_config_kwargs', {}))

    self.set_config('v8', optional=True, **kwargs)
    self.m.v8_tests.set_config('v8')
    self.m.chromium.set_config('v8', **kwargs)
    self.m.gclient.set_config('v8', **kwargs)

    if self.m.chromium.c.TARGET_PLATFORM in ['android', 'fuchsia']:
      self.m.gclient.apply_config(self.m.chromium.c.TARGET_PLATFORM)

    if self.m.chromium.c.TARGET_PLATFORM == 'ios':
      self.m.gclient.apply_config('v8_ios')

    for c in self.bot_config.get('chromium_apply_config', []):
      self.m.chromium.apply_config(c)

    self._configure_clusterfuzz_builders()
    self._configure_perf_builders()

    # Infer gclient variable that instructs sysroot download.
    if (self.m.chromium.c.TARGET_PLATFORM != 'android' and
        self.m.chromium.c.TARGET_ARCH == 'arm'):
      # This grabs both sysroots to not be dependent on additional bitness
      # setting.
      self.m.gclient.c.target_cpu.add('arm')
      self.m.gclient.c.target_cpu.add('arm64')

    self.m.v8_tests.enable_swarming = self.bot_config.get(
        'enable_swarming', True)

  def set_gclient_custom_vars(self, gclient_vars):
    """Sets additional gclient custom variables."""
    for key, value in (gclient_vars or {}).items():
      self.m.gclient.c.solutions[0].custom_vars[key] = value

  def set_gclient_custom_deps(self, custom_deps):
    """Configures additional gclient custom_deps to be synced."""
    for name, path in (custom_deps or {}).items():
      self.m.gclient.c.solutions[0].custom_deps[name] = path

  def set_chromium_configs(self, clobber, default_targets):
    if clobber:
      self.m.chromium.c.clobber_before_runhooks = clobber
    if default_targets:
      self.m.chromium.c.compile_py.default_targets = default_targets

  @cached_property
  def checkout_root(self):
    path = self.m.path.cache_dir / 'builder'
    self.m.file.ensure_directory('ensure builder cache dir', path)
    return path

  def build_dir(self, source_dir: Path):
    # TODO(crbug.com/457899826): Either restore the call to
    # chromium.default_build_dir() below, or remove all the hard-coded
    # references to "out/build" throughout V8 recipe code.
    return source_dir / 'out' / self.m.chromium.c.build_config_fs

  def get_revision(self, revision=None):
    return revision or self.m.buildbucket.gitiles_commit.id or 'HEAD'

  def checkout(self, revision=None, **kwargs) -> bot_update.Result:
    # Fail early if a trybot patch is being applied under an unauthorized service account.
    if self.m.tryserver.gerrit_change:
      service_account_email = self.m.buildbucket.swarming_task_service_account or ""
      if 'try-builder' not in service_account_email:
        raise self.m.step.InfraFailure(
            f'Security check failed: Trybot patches can only be applied when '
            f'running under a try-builder service account (containing "try-builder"), '
            f'but the current service account is: {service_account_email or "None"}'
        )

    # Set revision for bot_update.
    revision = self.get_revision(revision)
    solution = self.m.gclient.c.solutions[0]
    branch = self.m.buildbucket.gitiles_commit.ref
    if RELEASE_BRANCH_RE.match(branch):
      revision = f'{branch}:{revision}'
    solution.revision = revision

    if self.m.siso.enabled:
      self.m.siso.enable_download_remoteexec_cfg_hook()

    with self.m.context(cwd=self.checkout_root):
      update_result = self.m.bot_update.ensure_checkout(**kwargs)

    self.parse_revision_props(
        self.m.bot_update.last_returned_properties['got_revision'],
        self.m.bot_update.last_returned_properties.get('got_revision_cp'))
    return update_result

  def parse_revision_props(self, got_revision, got_revision_cp=None):
    """Parses got_revision and got_revision_cp properties.

    Sets self.revision, self.revision_cp and self.revision_number.

    Normally this is called from self.checkout above, but it may also be useful
    on bots where we do not have a checkout but have these properties (e.g. set
    by the parent builder when triggering child) and need to parse them.

    Args:
      got_revision: Full git hash of the commit.
      got_revision_cp: Value of the Cr-Commit-Position commit footer, e.g.
          "refs/heads/main@{#12345}".
    """
    self.revision = got_revision
    self.revision_cp = got_revision_cp

    # Note, a commit position might not be available on feature branches.
    self.revision_number = None
    if self.revision_cp:
      _, self.revision_number = self.m.commit_position.parse(self.revision_cp)
      self.revision_number = str(self.revision_number)

  def runhooks(self, *args, **kwargs):
    self.m.chromium.runhooks(*args, **kwargs)

  @property
  def bot_type(self):
    if self.bot_config.get('triggers') or self.bot_config.get('triggers_proxy'):
      return 'builder'
    if self.m.properties.get('parent_buildername'):
      return 'tester'
    return 'builder_tester'

  @property
  def builderset(self):
    """
    Returns a list of names of this builder and all its triggered testers.
    It also converts the name of experimental builders to fit the naming
    convention.
    """
    return (
        [self.m.buildbucket.builder_name] +
        list(self.bot_config.get('triggers', []))
    )

  @property
  def should_build(self):
    return self.bot_type in ['builder', 'builder_tester']

  @property
  def should_test(self):
    return self.bot_type in ['tester', 'builder_tester']

  def extra_tests_from_test_spec(self, test_spec):
    """Returns runnable testing.BaseTest objects for each extra test specified
    in the test spec of the current builder. Note that it converts experimental
    builders name in order to fit the naming convention.
    """
    return [
      self.m.v8_tests.create_test(test)
      for test in test_spec.get_tests(self.m.buildbucket.builder_name)
    ]

  def dedupe_tests(self, high_prec_tests, low_prec_tests):
    """Dedupe tests with lower precedence."""
    high_prec_ids = {test.id for test in high_prec_tests}
    return high_prec_tests + [
      test for test in low_prec_tests if test.id not in high_prec_ids]

  def read_test_spec(self, root, builders):
    """Reads a test specification file under <root>/infra/testing/builders.pyl.

    Args:
      root: Path to checkout root with configurations.
      builders: List of strings. Strip down the test-spec to this list of
          builders.
    Returns: v8_builders.TestSpec object, filtered by the passed builders.
    """
    test_spec_file = root.joinpath('infra', 'testing', 'builders.pyl')

    # Fallback for branch builders.
    if not self.m.path.exists(test_spec_file):
      return self.m.v8_tests.EMPTY_TEST_SPEC

    try:
      # Eval python literal file.
      full_test_spec = ast.literal_eval(self.m.file.read_text(
          f'read test spec ({self.m.path.basename(root)})',
          test_spec_file,
          test_data='{}',
      ))
    except SyntaxError as e:  # pragma: no cover
      raise self.m.step.InfraFailure(
          f'Failed to parse test specification "{test_spec_file}": {e}')

    # Transform into object.
    test_spec = self.m.v8_tests.TEST_SPEC.from_python_literal(
        full_test_spec, builders)

    # Log test spec for debuggability.
    self.m.step.active_result.presentation.logs['test_spec'] = (
        test_spec.log_lines())

    return test_spec

  def isolate_tests(self, build_dir, isolate_targets):
    """Upload isolated tests to isolate server.

    Args:
      build_dir: Path to the directory containing built executables.
      isolate_targets: Targets to isolate.
    """
    # Special handling for 'perf' target, since perf tests are going to be
    # executed on an internal swarming server and thus need to be uploaded to
    # internal isolate server.
    if 'perf' in isolate_targets:
      isolate_targets.remove('perf')
      self.m.isolate.isolate_tests(
          build_dir,
          targets=['perf'],
          verbose=True,
          swarm_hashes_property_name=None,
          step_name='isolate tests (perf)',
      )
      self.m.v8_tests.isolated_tests.update(self.m.isolate.isolated_tests)
    if isolate_targets:
      self.m.isolate.isolate_tests(
          build_dir,
          targets=isolate_targets,
          verbose=True,
          swarm_hashes_property_name=None,
      )
      self.m.v8_tests.isolated_tests.update(self.m.isolate.isolated_tests)

      if (self.m.v8_tests.isolated_tests and
          not self.m.tryserver.is_tryserver and
          self.m.properties.get('recipe') != 'v8/compilator'):
        self.upload_isolated_json()

  def target_bits(self, build_dir):
    """Returns target bits (as int) inferred from V8's build artifacts."""
    build_config = self.get_build_config(build_dir)
    return 64 if '64' in build_config.get('target_cpu', 'x64') else 32

  @contextlib.contextmanager
  def ensure_osx_sdk_if_needed(self):
    """Ensures the sdk is installed for wrapped steps when building for ios.

    When building for mac we use the src-side hermetic toolchain.
    """
    if self.m.chromium.c.TARGET_PLATFORM == 'ios':
      with self.m.osx_sdk('ios'):
        yield
    else:
      yield

  def _upload_build_dependencies(self, deps):
    values = {
      'ext_h_avg_deps': deps['by_extension']['h']['avg_deps'],
      'ext_h_top100_avg_deps': deps['by_extension']['h']['top100_avg_deps'],
      'ext_h_top200_avg_deps': deps['by_extension']['h']['top200_avg_deps'],
      'ext_h_top500_avg_deps': deps['by_extension']['h']['top500_avg_deps'],
    }
    points = []
    root = '/'.join(['v8.infra', 'build_dependencies', ''])
    for k, v in sorted(values.items()):
      p = self.m.perf_dashboard.get_skeleton_point(
          root + k, self.revision_number, str(v))
      p['units'] = 'count'
      p['supplemental_columns'] = {
        'a_default_rev': 'r_v8_git',
        'r_v8_git': self.revision,
      }
      points.append(p)
    if points:
      self.m.perf_dashboard.add_point(points, halt_on_failure=True)

  def _track_binary_size(self, build_dir: Path, binary, category):
    """Track and upload binary size of configured binaries.

    Args:
      binary: Binary name joined to the build output folder.
      category: ChromePerf category for qualifying the graph names, e.g.
          linux32 or linux64.
    """
    size = self.m.file.filesizes('Check binary size', [build_dir / binary])[0]

    point_defaults = {
      'units': 'bytes',
      'supplemental_columns': {
        'a_default_rev': 'r_v8_git',
        'r_v8_git': self.revision,
      },
    }

    point = self.m.perf_dashboard.get_skeleton_point(
        '/'.join(['v8.infra', 'binary_size', binary]),
        self.revision_number,
        str(size),
        bot=category,
    )
    point.update(point_defaults)
    self.m.perf_dashboard.add_point([point], halt_on_failure=True)

  def compile(self,
              source_dir: Path,
              build_dir: Path,
              test_spec=None,
              mb_config_path=None,
              **kwargs):
    """Compile all desired targets and isolate tests.

    Args:
      source_dir: The path to the top-level repo.
      build_dir: The path to the directory containing built outputs.
      test_spec: Optional TestSpec object as returned by read_test_spec().
          Expected to contain only specifications for the current builder and
          all triggered builders. All corrensponding extra targets will also be
          isolated.
      mb_config_path: Path to the MB config file. Defaults to
          infra/mb/mb_config.py in the checkout.

    Returns:
      if there is a compile failure:
        RawResult object with compile step status and failure message
      else:
        None
    """
    with (self.ensure_osx_sdk_if_needed(),
          self.m.chromium.guard_compile(build_dir)):
      # Calculate targets to isolate from V8-side test specification. The
      # test_spec contains extra TestStepConfig objects for the current builder
      # and all its triggered builders.
      isolate_targets = self.m.v8_tests.isolate_targets_from_tests(test_spec)

      # Add the performance-tests isolate everywhere, where the perf-bot proxy
      # is triggered.
      if self.bot_config.get('triggers_proxy', False):
        isolate_targets = isolate_targets + ['perf']

      # Extra targets to isolate also on compilation errors.
      isolate_targets += self.always_isolate_targets

      # Sort and dedupe.
      isolate_targets = sorted(list(set(isolate_targets)))

      if self.m.chromium.c.project_generator.tool == 'mb':
        mb_config_rel_path = self.m.properties.get(
            'mb_config_path', 'infra/mb/mb_config.pyl')

        mb_config_path = mb_config_path or source_dir / mb_config_rel_path

        gn_args = self.m.chromium.mb_gen(
            source_dir,
            build_dir,
            self.m.chromium.get_builder_id(),
            mb_config_path=mb_config_path,
            isolated_targets=isolate_targets,
            gn_args_location=self.m.gn.LOGS)

        self.m.v8_tests.gn_args = gn_args.splitlines()

        # Create logs surfacing GN arguments. This information is critical to
        # developers for reproducing failures locally.
        presentation = self.m.step.active_result.presentation
        presentation.logs['gn_args'] = self.m.v8_tests.gn_args
      elif self.m.chromium.c.project_generator.tool == 'gn':
        self.m.chromium.run_gn(source_dir, build_dir)

      raw_result = self.m.chromium.compile(source_dir, build_dir, **kwargs)

      if raw_result.status != common_pb.SUCCESS:
        self.isolate_tests(build_dir, self.always_isolate_targets)
        return raw_result

      self.isolate_tests(build_dir, isolate_targets)

  @property
  def should_collect_post_compile_metrics(self):
    return (
        not self.is_pure_swarming_tester and self.should_build and (
            self.m.v8.bot_config.get('track_build_dependencies') or
            self.m.v8.bot_config.get('binary_size_tracking')))

  def collect_post_compile_metrics(self, source_dir: Path, build_dir: Path):
    with self.ensure_osx_sdk_if_needed():
      if self.bot_config.get('track_build_dependencies',
                             False) and not self._is_muted_branch():
        path = [self.depot_tools_path(source_dir), self.ninja_path(source_dir)]
        with self.m.context(env_prefixes={'PATH': path}):
          deps = self.vpython(
              name='track build dependencies (fyi)',
              script=self.resource('build-dep-stats.py'),
              args=[
                  '-C',
                  build_dir,
                  '-x',
                  '/third_party/',
                  '-o',
                  self.m.json.output(),
              ],
              step_test_data=self.test_api.example_build_dependencies,
              ok_ret='any',
          ).json.output
        if deps:
          self._upload_build_dependencies(deps)

      # Track binary size if specified.
      tracking_config = self.bot_config.get('binary_size_tracking', {})
      if tracking_config and not self._is_muted_branch():
        self._track_binary_size(
            build_dir,
            tracking_config['binary'],
            tracking_config['category'],
        )

  def _is_muted_branch(self):
    return self.m.buildbucket.build.builder.bucket not in ['ci', 'try']

  def depot_tools_path(self, source_dir):
    """Returns path to depot_tools pinned in the V8 checkout."""
    return source_dir.joinpath('third_party', 'depot_tools')

  def ninja_path(self, source_dir):
    """Returns path to ninja pinned in the V8 checkout."""
    return source_dir.joinpath('third_party', 'ninja')

  def _get_default_archive(self):
    return 'gs://chromium-v8/archives/%s/%s' % (
        self.m.builder_group.for_current,
        self.m.buildbucket.builder_name,
    )

  @property
  def isolated_archive_path(self):
    buildername = (self.m.properties.get('parent_buildername') or
                   self.m.buildbucket.builder_name)
    return 'chromium-v8/isolated/%s/%s' % (self.m.builder_group.for_current,
                                           buildername)

  def upload_isolated_json(self):
    self.m.gsutil.upload(
        self.m.json.input(self.m.v8_tests.isolated_tests),
        self.isolated_archive_path,
        f'{self.revision}.json',
        args=['-a', 'public-read'],
    )

  def get_build_config(self, build_dir):
    if self._build_config is None:
      build_config_path = build_dir / 'v8_build_config.json'
      self._build_config = self.m.file.read_json(
          'read build config',
          build_config_path,
          test_data=self.test_api.example_build_config())
    return self._build_config

  def get_build_type(self, build_dir):
    """Returns the given build type: 'debug' if gn args is_debug or
    dcheck_always_on are set, 'release' otherwise.
    """
    build_config = self.get_build_config(build_dir)
    debug = (
        build_config.get('is_DEBUG_defined', False) or
        build_config.get('DEBUG_defined', False))
    return 'debug' if debug else 'release'

  def maybe_create_clusterfuzz_archive(
      self,
      source_dir: Path,
      build_dir: Path,
      update_result: bot_update.Result,
  ):
    clusterfuzz_archive = self.bot_config.get('clusterfuzz_archive')
    if clusterfuzz_archive:
      kwargs = {}
      if clusterfuzz_archive.get('bitness'):
        kwargs['use_legacy'] = False
        kwargs['bitness'] = clusterfuzz_archive['bitness']
      self.m.archive.clusterfuzz_archive(
          revision_dir='v8',
          build_config=self.get_build_type(build_dir),
          source_dir=source_dir,
          archive_root=build_dir,
          update_properties=update_result.properties,
          gs_bucket=clusterfuzz_archive.get('bucket'),
          gs_acl='public-read',
          archive_prefix=clusterfuzz_archive.get('name'),
          archive_path=clusterfuzz_archive.get('archive_path'),
          use_archive_path=clusterfuzz_archive.get('use_archive_path', False),
          **kwargs)

  def download_isolated_json(self, revision):
    archive = 'gs://' + self.isolated_archive_path + f'/{revision}.json'
    self.m.gsutil.download_url(
        archive,
        self.m.json.output(),
        name='download isolated json',
        step_test_data=lambda: self.m.json.test_api.output(
            {'bot_default': '[dummy hash for bisection]/123'}),
    )
    step_result = self.m.step.active_result
    self.m.v8_tests.isolated_tests = step_result.json.output

  @contextlib.contextmanager
  def maybe_clang_coverage(self, source_dir: Path, build_dir: Path):
    """Context manager for wrapping a local test execution with
    coverage-collection logic (switched by the 'coverage' property).
    """
    if self.bot_config.get('coverage') != 'llvm':
      yield
    else:
      assert source_dir
      assert build_dir
      profile_path = self.m.path.cleanup_dir / 'profraw'
      profile_template = profile_path.joinpath('default-%%9m.profraw')
      try:
        with self.m.context(env={'LLVM_PROFILE_FILE': profile_template}):
          yield
      finally:
        with self.m.step.nest('Code coverage') as parent_presentation:
          with self.m.context(cwd=source_dir):
            profiles = self.find_profiles(profile_path)
            total_profile = self.merge_profiles(source_dir, profiles)
            report_dir = self.create_report(source_dir, build_dir,
                                            total_profile)
            link = self.upload_report(build_dir, report_dir)
            parent_presentation.links['report'] = link
            self.recipe_result = RawResult(
                status=common_pb.SUCCESS,
                summary_markdown=f'[Report]({link})')

  def find_profiles(self, profile_path):
    """Returns a list of paths to all raw profiles."""
    return [
      str(f)
      for f in self.m.file.listdir('List profraw files', profile_path)
      if str(f).endswith(PROFRAW_FILE_EXTENSION)
    ]

  def llvm_tool(self, source_dir, name):
    """Returns an absolute path to an llvm tool in the V8 checkout."""
    return source_dir / 'third_party/llvm-build/Release+Asserts/bin' / name

  def merge_profiles(self, source_dir, profiles):
    """Merges multiple raw profiles and returns a path to the total profile."""
    output_dir = self.m.path.cleanup_dir / 'profdata'
    total_profile = output_dir / 'total.profdata'
    self.m.file.ensure_directory('Ensure output directory', output_dir)

    self.m.step(
        'Merge profiles',
        cmd=[
            self.llvm_tool(source_dir, 'llvm-profdata'),
            'merge',
            '-o',
            total_profile,
            '--sparse',
        ] + profiles)

    return total_profile

  def create_report(self, source_dir, build_dir, total_profile):
    """Creates an html coverage report for a merged profile."""
    report_dir = self.m.path.cleanup_dir / 'report'
    self.m.file.ensure_directory('Ensure report directory', report_dir)

    cmd = [
        self.llvm_tool(source_dir, 'llvm-cov'),
        'show',
        '-format=html',
        f'-compilation-dir={build_dir}',
        f'-output-dir={report_dir}',
        f'-instr-profile={total_profile}',
        '-Xdemangler',
        'c++filt',
        '-Xdemangler',
        '-n',
    ]
    for exe in V8_EXECUTABLES:
      cmd.append('--object')
      cmd.append(str(build_dir / exe))

    self.m.step('Create report', cmd=cmd)

    return report_dir

  def upload_report(self, build_dir: Path, report_dir):
    """Uploads the coverage-report directory structure to google storage."""
    build_type = self.get_build_type(build_dir)
    type_suffix = 'rel' if build_type == 'release' else 'dbg'
    if self.m.tryserver.is_tryserver:
      dest = 'try/%s%d_%s/%d/%d/%d' % (
          self.m.platform.name,
          self.target_bits(build_dir),
          type_suffix,
          self.m.tryserver.gerrit_change_number,
          self.m.tryserver.gerrit_patchset_number,
          int(self.m.time.time()),
      )
    else:
      dest = 'ci/%s%d_%s/%s' % (
          self.m.platform.name,
          self.target_bits(build_dir),
          type_suffix,
          self.revision,
      )

    self.m.gsutil(
        [
          '-m', 'cp', '-a', 'public-read', '-R', report_dir,
          f'gs://chromium-v8/coverage/{dest}',
        ],
        'coverage report')

    return f'{COVERAGE_URL}/{dest}/index.html'

  @property
  def is_pure_swarming_tester(self):
    return (self.bot_type == 'tester' and
            self.bot_config.get('enable_swarming', True))

  def maybe_bisect(
      self,
      source_dir: Path,
      build_dir: Path,
      test_results,
      test_spec,
  ):
    """Build-local bisection for one failure."""
    if (self.bot_config.get('disable_auto_bisect') or
        self.m.properties.get('disable_auto_bisect')):
      return

    # Only bisect over failures not flakes. Rerun only the fastest test.
    try:
      failure = min(test_results.failures, key=lambda r: r.duration)
    except ValueError:
      return

    # Only bisect if the fastest failure is significantly faster than the
    # ongoing build's total.
    duration_factor = self.m.properties.get(
        'bisect_duration_factor', BISECT_DURATION_FACTOR)
    if (failure.duration * duration_factor >
        self.m.v8_tests.test_duration_sec):
      step_result = self.m.step(
          'Bisection disabled - test too slow', cmd=None)
      return

    # Don't retry failures during bisection.
    self.m.v8_tests.rerun_failures_count = 0

    # Suppress using shards to be able to rerun single tests.
    self.m.v8_tests.c.testing.may_shard = False

    test = self.m.v8_tests.create_test(failure.test_step_config)
    def test_func(_):
      return test.rerun(source_dir, failure_dict=failure.failure_dict)

    def is_bad(revision):
      with self.m.step.nest('Bisect ' + revision[:8]):
        if not self.is_pure_swarming_tester:
          self.checkout(revision, update_presentation=False)
        if self.bot_type == 'builder_tester':
          # TODO(machenbach): Only compile on demand. We could first check if
          # download_isolated_json already provides isolated targets for this
          # revision. Only compile if not.
          self.runhooks(source_dir, build_dir)
          compile_failure = self.compile(source_dir, build_dir, test_spec)
          if compile_failure:
            # TODO: Consider changing control flow
            # to handle returning of compile failures
            raise self.m.step.StepFailure(compile_failure.summary_markdown)
        elif self.bot_type == 'tester':
          if test.uses_swarming:
            self.download_isolated_json(revision)
          else:  # pragma: no cover
            raise self.m.step.InfraFailure('Swarming required for bisect.')
        else:  # pragma: no cover
          raise self.m.step.InfraFailure(
              f'Bot type {self.bot_type} not supported.')
        result = test_func(revision)
        if result.infra_failures:  # pragma: no cover
          raise self.m.step.InfraFailure(
              'Cannot continue bisection due to infra failures.')
        return result.failures

    with self.m.step.nest('Bisect'):
      # Setup bisection range ("from" exclusive).
      latest_previous, bisect_range = self.get_change_range()
      if len(bisect_range) <= 1:
        self.m.step('disabled - less than two changes', cmd=None)
        return

      if self.bot_type == 'tester':
        # Filter the bisect range to the revisions for which isolate hashes or
        # archived builds are available, depending on whether swarming is used
        # or not.
        available_bisect_range = self.get_available_range(bisect_range)
      else:
        available_bisect_range = bisect_range

    if is_bad(latest_previous):
      # If latest_previous is already "bad", the test failed before the current
      # build's change range, i.e. it is a recurring failure.
      # TODO: Try to be smarter here, fetch the build data from the previous
      # one or two builds and check if the failure happened in revision
      # latest_previous. Otherwise, the cost of calling is_bad is as much as
      # one bisect step.
      step_result = self.m.step(
          'Bisection disabled - recurring failure', cmd=None)
      step_result.presentation.status = self.m.step.WARNING
      return

    # Log available revisions to ease debugging.
    self.log_available_range(available_bisect_range)

    culprit = bisection.keyed_bisect(available_bisect_range, is_bad)
    culprit_range = self.calc_missing_values_in_sequence(
        bisect_range,
        available_bisect_range,
        culprit,
    )
    self.report_culprits(culprit_range)

  @staticmethod
  def _copy_property(src, dest, key):
    if key in src:
      dest[key] = src[key]

  def maybe_trigger(self, test_spec=None, **additional_properties):
    test_spec = test_spec or self.m.v8_tests.EMPTY_TEST_SPEC
    triggers = self.bot_config.get('triggers', [])
    triggers_proxy = self.bot_config.get('triggers_proxy', False)
    if not triggers and not triggers_proxy:
      return

    properties = {
      'parent_got_revision': self.revision,
      'parent_buildername': self.m.buildbucket.builder_name,
      'parent_build': self.m.buildbucket.build_url(),
      'parent_gn_args': self.m.v8_tests.gn_args,
    }
    if self.m.scheduler.triggers:
      sched_trs = self.m.scheduler.triggers
      if sched_trs[0].HasField('gitiles'):
        properties['oldest_gitiles_revision'] = sched_trs[0].gitiles.revision
      if sched_trs[-1].HasField('gitiles'):
        properties['newest_gitiles_revision'] = sched_trs[-1].gitiles.revision
    if self.revision_cp:
      properties['parent_got_revision_cp'] = self.revision_cp
    if self.m.tryserver.is_tryserver:
      properties.update(
        category=self.m.properties.get('category', 'manual_ts'),
        disable_auto_bisect=True,
        reason=str(self.m.properties.get('reason', 'ManualTS')),
        # On tryservers, set revision to the same as on the current bot, as it
        # is used to generate buildset tag in buildbucket_trigger below.
        revision=self.m.buildbucket.gitiles_commit.id or 'HEAD',
        patch_gerrit_url=f'https://{self.m.tryserver.gerrit_change.host}',
        patch_issue=self.m.tryserver.gerrit_change.change,
        patch_project=self.m.tryserver.gerrit_change.project,
        patch_set=self.m.tryserver.gerrit_change.patchset,
        patch_storage='gerrit',
      )
    else:
      # On non-tryservers, we can set the revision to whatever the
      # triggering builder checked out.
      properties['revision'] = self.revision

    if self.m.properties.get('testfilter'):
      properties.update(testfilter=list(self.m.properties['testfilter']))
    self._copy_property(self.m.properties, properties, 'extra_flags')

    swarm_hashes = self.m.v8_tests.isolated_tests
    if swarm_hashes:
      properties['swarm_hashes'] = swarm_hashes
    properties.update(**additional_properties)

    if triggers:
      if self.m.tryserver.is_tryserver:
        trigger_props = {}
        self._copy_property(self.m.properties, trigger_props, 'revision')
        trigger_props.update(properties)
        self.m.cv.record_triggered_builds(*self.trigger.buildbucket(
            [(builder_name,
              dict(
                  trigger_props,
                  **test_spec.as_properties_dict(builder_name),
              )) for builder_name in triggers],
            bucket='try.triggered',
        ))
      else:
        ci_properties = dict(properties)
        #TODO(liviurau): rename or remove this property
        if self.bot_config.get('triggers_proxy'):
          ci_properties['archive'] = self._get_default_archive()
        self.trigger.scheduler(triggers, ci_properties, test_spec)

    if triggers_proxy:
      proxy_properties = {'archive': self._get_default_archive()}
      proxy_properties.update(properties)
      self.trigger.buildbucket(
          [('v8_trigger_proxy', proxy_properties)],
          project='v8-internal',
          bucket='ci',
          step_name='trigger_internal')

  def get_change_range(self):
    if self.m.properties.get('override_triggers'):
      # This can be used for manual testing or on a staging builder that
      # simulates a change range.
      triggers = self.m.properties['override_triggers']
      step_result = self.m.step('Override triggers', cmd=None)
      step_result.presentation.logs['triggers'] = self.m.json.dumps(
        triggers, indent=2).splitlines()
      oldest_change = triggers[0]
      newest_change = triggers[-1]
    else:
      oldest_trigger = self.m.scheduler.triggers[0]
      if oldest_trigger.HasField('gitiles'):
        # This is a builder and has gitiles triggers with revision ranges.
        oldest_change = oldest_trigger.gitiles.revision
      else:
        # This is a tester and we pass down revision ranges from the parent
        # builder via buildbucket trigger properties.
        assert oldest_trigger.HasField('buildbucket')
        oldest_change = oldest_trigger.buildbucket.properties[
            'oldest_gitiles_revision']

      newest_trigger = self.m.scheduler.triggers[-1]
      if newest_trigger.HasField('gitiles'):
        newest_change = newest_trigger.gitiles.revision
      else:
        assert newest_trigger.HasField('buildbucket')
        newest_change = newest_trigger.buildbucket.properties[
            'newest_gitiles_revision']

    # Commits is a list of gitiles commit dicts in reverse chronological order.
    commits, _ = self.m.gitiles.log(
        url=V8_URL,
        ref=f'{oldest_change}~2..{newest_change}',
        limit=100,
        step_name='Get change range',
        step_test_data=self.test_api.example_bisection_range
    )

    # We get minimum two commits when the first and last commit are equal (i.e.
    # there was only one commit C). Commits will contain the latest previous
    # commit and C.
    assert len(commits) > 1

    return (
        # Latest previous.
        commits[-1]['commit'],
        # List of commits oldest -> newest, without the latest previous.
        [commit['commit'] for commit in reversed(commits[:-1])],
    )

  def get_available_range(self, bisect_range):
    assert self.bot_type == 'tester'
    archive_url_pattern = f'gs://{self.isolated_archive_path}/%s.json'
    # TODO(machenbach): Maybe parallelize this in a wrapper script.
    args = ['ls']
    available_range = []
    # Check all builds except the last as we already know it is "bad".
    for r in bisect_range[:-1]:
      step_result = self.m.gsutil(
          args + [archive_url_pattern % r],
          name=f'check build {r[:8]}',
          # Allow failures, as the tool will formally fail for any absent file.
          ok_ret='any',
          stdout=self.m.raw_io.output_text(),
          step_test_data=lambda: self.test_api.example_available_builds(r),
      )
      if r in step_result.stdout.strip():
        available_range.append(r)

    # Always keep the latest revision in the range. The latest build is
    # assumed to be "bad" and won't be tested again.
    available_range.append(bisect_range[-1])
    return available_range

  def calc_missing_values_in_sequence(
        self, sequence, subsequence, value):
    """Calculate a list of missing values from a subsequence.

    Args:
      sequence: The complete sequence including all values.
      subsequence: A subsequence from the sequence above.
      value: An element from subsequence.
    Returns: A subsequence from sequence [a..b], where b is the value and
             for all x in a..b-1 holds x not in subsequence. Also
             a-1 is either in subsequence or value was the first
             element in subsequence.
    """
    from_index = 0
    to_index = sequence.index(value) + 1
    index_on_subsequence = subsequence.index(value)
    if index_on_subsequence > 0:
      # Value is not the first element in subsequence.
      previous = subsequence[index_on_subsequence - 1]
      from_index = sequence.index(previous) + 1
    return sequence[from_index:to_index]

  def log_available_range(self, available_bisect_range):
    step_result = self.m.step('Available range', cmd=None)
    for revision in available_bisect_range:
      step_result.presentation.links[revision[:8]] = COMMIT_TEMPLATE % revision

  def report_culprits(self, culprit_range):
    assert culprit_range
    if len(culprit_range) > 1:
      text = 'Suspecting multiple commits'
    else:
      text = f'Suspecting {culprit_range[0][:8]}'

    step_result = self.m.step(text, cmd=None)
    for culprit in culprit_range:
      step_result.presentation.links[culprit[:8]] = COMMIT_TEMPLATE % culprit

  def read_version_file(self, source_dir, ref, step_name_desc):
    """Read and return the version-file content at a paricular ref."""
    with self.m.context(cwd=source_dir):
      return self.m.git(
          'show',
          f'{ref}:{self.VERSION_FILE}',
          name=f'Check {step_name_desc} version file',
          stdout=self.m.raw_io.output_text(),
      ).stdout

  def read_version_from_ref(self, source_dir, ref, step_name_desc):
    """Read and return the version at a paricular ref."""
    return V8Api.version_from_file(
        self.read_version_file(source_dir, ref, step_name_desc))

  @staticmethod
  def version_from_file(blob):
    major = re.search(VERSION_LINE_RE % V8_MAJOR, blob, re.M).group(1)
    minor = re.search(VERSION_LINE_RE % V8_MINOR, blob, re.M).group(1)
    build = re.search(VERSION_LINE_RE % V8_BUILD, blob, re.M).group(1)
    patch = re.search(VERSION_LINE_RE % V8_PATCH, blob, re.M).group(1)
    return V8Version(major, minor, build, patch)

  def latest_branches(self):
    branch_step = self.m.git(
        'branch',
        '-r',
        '--list',
        'branch-heads/*',
        stdout=self.m.raw_io.output_text(),
        name='last branches',
        step_test_data=self.test_api.example_latest_branches)
    output = branch_step.stdout
    branch_step.presentation.logs['stdout'] = output.splitlines()
    branch_pattern = re.compile(r"branch-heads/(\d+)\.(\d+)")
    versions = []
    for line in output.splitlines():
      m = branch_pattern.match(line.strip())
      if m:
        versions.append(int(m.group(1)) * 10 + int(m.group(2)))
    versions.sort()
    versions.reverse()
    return versions

  def git_output(self, *args, **kwargs):
    """Convenience wrapper."""
    step_result = self.m.git(
        *args, stdout=self.m.raw_io.output_text(), **kwargs)
    result = step_result.stdout
    step_result.presentation.logs['stdout'] = result.splitlines()
    return result.strip()

  def update_version_cl(self,
                        source_dir,
                        ref,
                        latest_version,
                        push_account,
                        bot_commit=False,
                        force_land=False):
    """Update the version on branch 'ref'.

      Args:
        source_dir: The path to the top-level repo.
        ref: Ref name where to change the version, e.g.
            refs/remotes/branch-heads/1.2.
        latest_version: The currently latest version to be updated in the
        version file.
        push_account: Account to be used for uploading the CL
        bot_commit: Use True to allow a bot commit.
        force_land: Use True to force land the CL.
      """
    self.m.git('branch', '-D', 'work', ok_ret='any')
    self.m.git('clean', '-ffd')

    # Create a fresh work branch.
    self.m.git('new-branch', 'work', '--upstream', ref)
    self.m.git(
        'config',
        'user.name',
        'V8 Autoroll',
        name='git config user.name',
    )
    self.m.git(
        'config',
        'user.email',
        push_account,
        name='git config user.email',
    )

    latest_version_file = self.read_version_file(source_dir, ref, 'latest')
    latest_version_file = latest_version.update_version_file_blob(
        latest_version_file)

    # Write file to disk.
    self.m.file.write_text(
        'Increment version',
        source_dir / self.m.v8.VERSION_FILE,
        latest_version_file,
    )

    # Commit and push changes.
    commit_cmd = ['commit', '-a']
    message_lines = [
        f'Version {latest_version}',
        '',
        f'Version incremented at {self.m.buildbucket.build_url()}',
    ]
    for line in message_lines:
      commit_cmd += ['-m', line]
    self.m.git(*commit_cmd)

    if self.dry_run:
      self.m.step('Dry-run commit', cmd=None)
    else:
      upload_cmd = [
          'cl', 'upload', '-f', '--bypass-hooks', '--send-mail', '--no-autocc']

      bot_commit_flag = '--set-bot-commit'
      if bot_commit and bot_commit_flag not in upload_cmd:
        upload_cmd.append(bot_commit_flag)

      self.m.git(*upload_cmd)

      if force_land:
        self.m.git('cl', 'land', '-f', '--bypass-hooks')


  def version_num2str(self, version_number):
    """Transforms a version number to a string formated version number
    (i.e. 102  to '10.2')
    """
    return '%s.%s' % divmod(version_number, 10)

  def version_from_text(self, version_text):
    version_components = version_text.split('.')
    if len(version_components) < 2:  # pragma: no cover
      return 0
    major, minor = version_components[:2]
    return int(major) * 10 + int(minor)

  def get_active_branches(self):
    milestones = self.m.chromiumdash.milestones(0, only_active=True)
    return sorted([m['v8_branch'] for m in milestones], reverse=True)

  def infer_active_branches(self, definitions):
    contents = ast.parse(definitions, mode='exec')
    for node in contents.body:
      if isinstance(node, ast.Assign) and \
         len(node.targets) == 1 and \
         isinstance(node.targets[0], ast.Name) and \
         node.targets[0].id == 'ACTIVE_BRANCHES':
        return [elt.value for elt in node.value.elts]
    return []

  def update_active_branches(self, active_branches):
    lines = [
        "# This file is auto-generated. Do not edit.",
        "ACTIVE_BRANCHES = [",
    ]
    for b in active_branches:
      lines.append(f'    "{b}",')
    lines.append("]")
    return "\n".join(lines) + "\n"

  def infer_beta_version(self, definitions):
    contents = ast.parse(definitions, mode='exec')
    defined_versions = contents.body[0].value.values
    return self.version_from_text(defined_versions[0].value)

  def update_infra_config(self, source_dir, definitions, hashtag=None):
    with self.m.step.nest('Update infra/config') as parent_step:
      self.git_output('checkout', 'infra/config')
      self.git_output('pull', ok_ret='any')
      self.git_output('branch', '-D', 'branch_cut_update', ok_ret='any')
      self.git_output('clean', '-ffd')
      self.git_output('checkout', '-b', 'branch_cut_update')
      self.git_output('branch', '--set-upstream-to=origin/infra/config')
      definitions_path = source_dir / 'definitions.star'
      self.m.file.write_text('Write branch definitions', definitions_path,
                             definitions)
      self.m.step('Lucicfg format', ['lucicfg', 'format'])
      self.m.step('Lucicfg generate', ['lucicfg', 'main.star'])
      self.git_output('commit', '-am', 'Update active branches')
      upload_cmd = [
          'cl', 'upload', '-f', '--bypass-hooks', '--send-mail',
          '--set-bot-commit', '--dry-run'
      ]
      if hashtag:
        upload_cmd.extend(['-t', hashtag])
      self.git_output(*upload_cmd)
      issue = self.get_cl_issue()
      parent_step.links[issue] = issue

  def update_main_version(self, source_dir):
    with self.m.step.nest('Update on main') as parent_step:
      branch_ref = 'main'
      self.git_output('checkout', branch_ref)
      version_at_branch_head = self.read_version_from_ref(
          source_dir, "HEAD", branch_ref)
      version_at_branch_head = version_at_branch_head.with_incremented_minor()
      self.update_version_cl(
          source_dir,
          branch_ref,
          version_at_branch_head,
          push_account=V8_CI_AUTOROLL_BUILDER,
          bot_commit=True,
      )
      issue = self.get_cl_issue()
      parent_step.links[issue] = issue

  def get_cl_issue(self):
    issue = self.git_output('cl', 'issue')
    return re.search('\((.*)\)', issue).group(1)
