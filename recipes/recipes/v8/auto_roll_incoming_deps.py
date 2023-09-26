# Copyright 2022 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from abc import ABC, abstractmethod
from contextlib import contextmanager
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb2
from PB.recipe_engine import result as result_pb2

from recipe_engine.post_process import (DoesNotRun, DoesNotRunRE,
                                        DropExpectation, MustRun,
                                        SummaryMarkdown)
from recipe_engine.recipe_api import Property
from recipe_engine.config import ConfigGroup, Dict, Single, List

from collections import namedtuple

import re

DEPS = [
  'depot_tools/depot_tools',
  'depot_tools/gclient',
  'depot_tools/gerrit',
  'depot_tools/git',
  'depot_tools/gitiles',
  'recipe_engine/buildbucket',
  'recipe_engine/cipd',
  'recipe_engine/context',
  'recipe_engine/json',
  'recipe_engine/path',
  'recipe_engine/properties',
  'recipe_engine/raw_io',
  'recipe_engine/step',
  'recipe_engine/url',
  'v8',
]


PROPERTIES = {
    # Configuration of the auto-roller
    'autoroller_config':
        Property(
            kind=ConfigGroup(
                # Subject of the rolling CLs; (trusted) or (reviewed) is
                # appended
                subject=Single(str),
                # Configuration parameters of the project where dependencies
                # will be rolled in. The source is always Chromium project.
                target_config=ConfigGroup(
                    # Solution name to be used for project checkout
                    solution_name=Single(str, required=True),
                    # Project name. Together with the 'base_url' it will form
                    # the location for the project
                    project_name=Single(str, required=True),
                    # The name of the account used to create the roll CL
                    account=Single(str),
                    # Template for the commit message used with regular
                    # dependencies
                    log_template=Single(str),
                    # Template for the commit message used with cipd
                    # dependencies
                    cipd_log_template=Single(str),
                    # Gerrit URL to be used for rolling CL review
                    gerrit_base_url=Single(str),
                    # Repo base URL together with 'project_name' to locate the
                    # repo where the rolling CL will be landed
                    base_url=Single(str),
                ),
                # List of (target side) dependencies to be excluded from rolling
                # with the current config
                excludes=Single(list, empty_val=None),
                # List of (target side) dependencies to be included when rolling
                # with the current config
                includes=Single(list, empty_val=None),
                # Specify the source to determine the next version, must be
                # `chromium`, `cipd`, `tip_of_tree`, or `auto` (default). E.g.
                #     ...
                #     "dependency_version_sources": {
                #         "devtools-frontend": "tip-of-tree"
                #     },
                #     ...
                dependency_version_sources=Dict(value_type=str),
                # Mapping between the dependency name in the target project and
                # the name in the source project
                deps_key_mapping=Dict(value_type=str),
                # List of reviewers of rolling CLs requiring a manual review
                reviewers=List(str),
                # Flag for rolling the binary chromium pin in target project
                roll_chromium_pin=Single(bool),
                # List of keys of supported script assisted rolls
                scripted_rolls=Single(list, empty_val=None),
                # Add extra log entries to the commit message.
                show_commit_log=Single(bool),
                # Bugs included in roll CL description
                bugs=Single(str),
            )),
}

# The following dependencies are trusted - if new deps are added, their projects
# need to be BCID L3 (http://go/bcid-ladder#level-3) compliant.
TRUSTED_ORIGIN_DEPS = {
    "https://chrome-infra-packages.appspot.com/fuchsia/third_party/aemu/linux-amd64",
    "https://chrome-infra-packages.appspot.com/p/fuchsia/qemu/linux-amd64",
    "https://chromium.googlesource.com/chromium/src/base/trace_event/common",
    "https://chromium.googlesource.com/chromium/src/build",
    "https://chromium.googlesource.com/chromium/src/buildtools",
    "https://chromium.googlesource.com/devtools/devtools-frontend",
    "https://chromium.googlesource.com/chromium/src/third_party/abseil-cpp",
    "https://chromium.googlesource.com/chromium/src/third_party/android_platform",
    "https://chromium.googlesource.com/chromium/src/third_party/fuchsia-gn-sdk",
    "https://chromium.googlesource.com/chromium/src/third_party/instrumented_libraries",
    "https://chromium.googlesource.com/chromium/src/third_party/jinja2",
    "https://chromium.googlesource.com/chromium/src/third_party/markupsafe",
    "https://chromium.googlesource.com/chromium/src/third_party/zlib",
    "https://chromium.googlesource.com/infra/luci/luci-py/client/libs/logdog",
    "https://chromium.googlesource.com/chromium/src/tools/clang",
}


BASE_URL = 'https://chromium.googlesource.com/'
CIPD_DEP_URL_PREFIX = 'https://chrome-infra-packages.appspot.com/'
CHROMIUM_PIN_CL_SUBJECT = 'Update Chromium PINS'
GERRIT_BASE_URL = 'https://chromium-review.googlesource.com'
MAX_COMMIT_LOG_ENTRIES = 8
STORAGE_URL = ('https://commondatastorage.googleapis.com/'
               'chromium-browser-snapshots/%s/LAST_CHANGE')
CFT_LKGR_URL = 'https://googlechromelabs.github.io/chrome-for-testing/last-known-good-versions.json'

# Some dependent repositories still use the deprecated term as their main branch
RETSAM = 'retsam'[::-1]
CHROME_VAR = 'chrome'



# Custom vars by project. They are added to the gclient solution when
# determining current deps versions.
GCLIENT_CUSTOM_VARS = {
  'https://chromium.googlesource.com/chromium/src': {
    'checkout_fuchsia_no_hooks': True,
  },
  'https://chromium.googlesource.com/v8/v8': {
    'checkout_fuchsia_no_hooks': True,
  },
}


class DepUpdate:

  def __init__(self, name, next_version, commit_msg_lines):
    self.name = name
    self.next_version = next_version
    self.commit_msg_lines = commit_msg_lines



def setup_gclient(api, autoroller_config):
  target_config = autoroller_config['target_config']

  gclient_config = api.gclient.make_config()
  soln = gclient_config.solutions.add()
  soln.name = target_config['solution_name']
  soln.url = target_config['base_url'] + target_config['project_name']
  soln.revision = 'HEAD'

  api.gclient.c = gclient_config
  api.gclient.apply_config('chromium')

  # Allow rolling all os deps.
  api.gclient.c.target_os.add('android')
  api.gclient.c.target_os.add('win')


def setup_target_repository(api):
  # NOTE: Besides the name, this actually does a checkout of the first solution
  #       defined in gclient (autoroller_config -> target_config ->
  #       solution_name), and might be something else, e.g. devtools-frontend.
  api.v8.checkout(ignore_input_commit=True, set_output_commit=False)


def discard_local_changes(api):
  with api.context(
      cwd=api.path['checkout'],
      env_prefixes={'PATH': [api.v8.depot_tools_path]}):
    api.git('checkout', '-f', 'origin/main')
    api.git('branch', '-D', 'roll', ok_ret='any')
    api.git('clean', '-ffd')
    api.git('new-branch', 'roll')


def get_deps(api, base_url, name, project_name):
  # Make a fake spec. Gclient is not nice to us when having two solutions
  # side by side. The latter checkout kills the former's gclient file.
  repo_url = base_url + project_name
  custom_vars = GCLIENT_CUSTOM_VARS.get(repo_url, {})
  spec = 'solutions=[%s]' % {
    'managed': False,
    'name': name,
    'url': repo_url,
    'custom_vars': custom_vars,
    'deps_file': 'DEPS',
  }

  # Read local deps information. Each deps has one line in the format:
  # path/to/deps: repo@revision
  with api.context(cwd=api.v8.checkout_root):
    step_result = api.gclient(
        f'get {name} deps',
        ['revinfo', '--deps', 'all', '--spec', spec],
        stdout=api.raw_io.output_text(),
    )

  # Transform into dict. Skip the solution prefix in keys (e.g. src/).
  deps = {}
  for line in step_result.stdout.strip().splitlines():
    tokens = line.strip().split(' ')
    if len(tokens) != 2:
      raise Exception(f"malformatted DEPS entry '{tokens}'")

    key, value = tokens

    # Remove trailing colon.
    key = key.rstrip(':')

    # Skip the deps entry to the solution itself.
    if not '/' in key:
      continue

    # Strip trailing solution name (e.g. src/).
    key = '/'.join(key.split('/')[1:])

    deps[key] = value

  # Log DEPS output.
  step_result.presentation.logs['deps'] = api.json.dumps(
      deps, indent=2).splitlines()
  return deps


def get_key_mapper(autoroller_config):
  """Override keys between destination (key) and source (value) based on recipe
  config."""
  custom_mapping = autoroller_config.get('deps_key_mapping', {})
  return lambda key: custom_mapping.get(key, key)


def get_recent_instance_id(api, package_name):
  """Returns the latest uploaded cipd instance id for a package.

  If a ref named `latest` is used, prefer this instance. Otherwise select the
  most recently uploaded instance.
  """
  instances = api.cipd.instances(package_name, 0)

  for instance in instances:
    if instance.refs and 'latest' in instance.refs:
      return instance.pin.instance_id

  return instances[0].pin.instance_id


def get_tot_revision(api, name, target_loc):
  def ls_remote(branch):
    step_result = api.git(
        'ls-remote',
        target_loc,
        f'refs/heads/{branch}',
        name=f'look up {name.replace("/", "_")} ({branch})',
        stdout=api.raw_io.output_text(),
    )
    return step_result.stdout.strip()

  # Fallback to the deprecated naming scheme still used by some deps, if there
  # is no head for the main branch
  for branch in ['main', RETSAM]:
    head = ls_remote(branch).split('\t')[0]
    if head:
      return head


def get_commit_log(api, repo, commit):
  subject = commit["message"].splitlines()[0]
  author = commit["author"]["name"]
  commit_url = api.url.join(repo, f'+/{commit["commit"][:7]}')
  return f"{subject} ({author})\n{commit_url}"


def commit_messages_log_entries(api, repo, from_commit, to_commit):
  """Returns list of log entries to be added to commit message.

  Args:
    api: Recipes api.
    repo: Gitiles url to rolled repository.
    from_commit: Parent of first rolled commit.
    to_commit: Newest rolled commit.
  """
  step_test_data = lambda: api.json.test_api.output({
    'log': [
      {
        'commit': 'deadbeef',
        'author': {'name': 'Tex'},
        'message': 'Commit 1\n\nsecond line',
      },
      {
        'commit': 'beefdead',
        'author': {'name': 'Mex'},
        'message': 'Commit 0\n\nsecond line',
      },
    ],
  })
  commits, _ = api.gitiles.log(
      url=repo,
      ref=f'{from_commit}..{to_commit}',
      step_test_data=step_test_data,
  )
  ellipse = [] if len(commits) < MAX_COMMIT_LOG_ENTRIES else ['...']
  return [
      get_commit_log(api, repo, c) for c in commits[:MAX_COMMIT_LOG_ENTRIES]
  ] + ellipse


def get_dependency_version_source(
    autoroller_config, dependency_name, is_chromium_dep, is_cipd_dep):
  sources = autoroller_config.get('dependency_version_sources', {})
  source = sources.get(dependency_name, 'auto')

  # If a specific version source is defined, the specific source should be used
  if source != 'auto':
    return source

  # Otherwise, the roller tries to update the dependency from the following
  # sources: 1. chromium/src, 2. cipd, 3. tip of tree
  if is_chromium_dep:
    return 'chromium'

  if is_cipd_dep:
    return 'cipd'

  return 'tip_of_tree'


def get_dep_updates(api, autoroller_config):
  target_config = autoroller_config['target_config']

  chromium_deps = get_deps(
      api, 'https://chromium.googlesource.com/', 'src', 'chromium/src')

  target_name = target_config['solution_name']
  target_project = target_config['project_name']
  target_base_url = target_config['base_url']
  target_deps = get_deps(api, target_base_url, target_name, target_project)

  key_mapper = get_key_mapper(autoroller_config)
  cipd_log_template = target_config['cipd_log_template']
  git_log_template = target_config['log_template']

  target_dep_names = sorted(target_deps.keys())

  # Filter rolled deps based on includes and excludes params
  excludes = autoroller_config.get('excludes')
  includes = autoroller_config.get('includes')
  assert excludes is None or includes is None, (
      "Either excludes or includes can be declared, not both.")

  target_dep_names = [
      k for k in target_dep_names if excludes is None or k not in excludes]
  target_dep_names = [
      k for k in target_dep_names if includes is None or k in includes]

  trusted_updates = []
  untrusted_updates = []
  failed_deps = []
  for target_name in target_dep_names:
    source_name = key_mapper(target_name)

    target_value = target_deps[target_name]
    target_location, target_version = target_value.split('@', 1)
    clean_target_location = re.sub(r'\.git$', '', target_location)

    is_cipd_dep = target_location.startswith(CIPD_DEP_URL_PREFIX)

    chromium_value = chromium_deps.get(source_name)

    # Determine the recent version and if it can be trusted
    next_version = None
    is_trusted = clean_target_location in TRUSTED_ORIGIN_DEPS
    version_source = get_dependency_version_source(
        autoroller_config, target_name, bool(chromium_value), is_cipd_dep)

    if version_source == 'chromium':
      chromium_location, chromium_version = chromium_value.split('@', 1)

      # Do not roll the dependency if the location has changed: The gclient tool
      # does not have commands that allow overriding the repo, hence we'll need
      # to make changes like this manually. However, this should not block
      # updating other DEPS and creating roll CL, hence just create a failing
      # step and continue.
      if target_location != chromium_location:
        message = (
            f'dep {target_name} has changed repo from {target_location} to '
            f'{chromium_location}')
        step_result = api.step(message, cmd=None)
        step_result.presentation.status = api.step.FAILURE
        failed_deps.append(target_name)
        continue

      next_version = chromium_version
      # We trust this roll since we assume all referenced dependencies in
      # chromium to be trusted.
      is_trusted = True

    if version_source == 'cipd':
      cipd_name = target_location[len(CIPD_DEP_URL_PREFIX):]
      next_version = get_recent_instance_id(api, cipd_name)

    if version_source == 'tip_of_tree':
      next_version = get_tot_revision(api, target_name, target_location)

    if not next_version:
      api.step.active_result.presentation.status = 'FAILURE'
      continue

    api.step.active_result.presentation.step_text += next_version

    # Update target dependency if changes exist
    if target_version == next_version:
      continue

    # Construct commit message lines
    commit_msg_lines = []
    if is_cipd_dep:
      # Unfortunately CIPD does not provide a way to generate a link that
      # lists all versions from v8_rev to new_ver. Even just creating a link
      # to a list of versions of a DEP is complicated as package name can
      # contain ${platform}, which can usually be resolved to multiple
      # distinct packages.
      path, _ = target_name.split(':')
      commit_msg_lines.append(cipd_log_template %
                              (path, target_version, next_version))
    else:
      params = (
          target_name, clean_target_location, target_version[:7],
          next_version[:7])
      commit_msg_lines.append(git_log_template % params)
      if autoroller_config['show_commit_log']:
        commit_msg_lines.extend(
            commit_messages_log_entries(api, clean_target_location,
                                        target_version, next_version))
    (trusted_updates if is_trusted else untrusted_updates).append(
        DepUpdate(
            name=target_name,
            next_version=next_version,
            commit_msg_lines=commit_msg_lines,
        ))

  return trusted_updates, untrusted_updates, failed_deps


def upload_cl(api,
              subject,
              upload_flags,
              commit_msg_lines,
              bugs_label,
              add=False):
  """
  Verify that the local checkout is dirty, commit changes and upload a CL with
  the given subject and reviewers. If the local checkout is not dirty, we do
  nothing.

  Returns the URL to the uploaded CL, or None if no CL was uploaded.
  """
  # Check for a difference. If no deps changed, the diff is empty.
  with api.context(cwd=api.path['checkout']):
    step_result = api.git(
        'status', '-s', '-uno',
        stdout=api.raw_io.output_text(),
    )
  diff = step_result.stdout.strip()
  step_result.presentation.logs['diff'] = diff.splitlines()

  if not diff:
    return None

  if add:
    # Add all files to the commit
    api.git('add', '-A')

  # Create a rolling CL. Ignore submodule updates
  args = ['-c', 'diff.ignoreSubmodules=all', 'commit', '-a', '-m', subject]

  for commit_line in commit_msg_lines:
    args.extend(['-m', commit_line])

  kwargs = {'stdout': api.raw_io.output_text()}
  with api.context(
      cwd=api.path['checkout'],
      env_prefixes={'PATH': [api.v8.depot_tools_path]}):
    api.git(*args, **kwargs)
    api.git('show')
    upload_args = [
        'cl',
        'upload',
        '-f',
        '--use-commit-queue',
        '--bypass-hooks',
        '--send-mail',
    ]

    if bugs_label is not None:
      upload_args += ['-b', bugs_label]

    upload_args.extend(upload_flags)
    step_result = api.git(*upload_args, stdout=api.raw_io.output_text())

    # Extract the cl link from stdout
    cl_link = re.search(r'https:\/\/.*\/\+\/\d+', step_result.stdout).group(0)

    return cl_link


class RollHandler(ABC):

  def __init__(self, api, autoroller_config):
    self.api = api
    self.config = autoroller_config
    self.enabled = True
    self.add_new_files = False

  def roll(self, summary):
    if self.enabled:
      with self.api.step.nest(f'Update {self.name()} deps') as step:
        with self.roll_contex():
          step.presentation.step_text = self.summary()
          self.abandon_active_cls()
          discard_local_changes(self.api)
          changes = self.apply_changes()
          cl_link = upload_cl(
              self.api,
              subject=self.get_subject(),
              upload_flags=self.upload_flags(),
              commit_msg_lines=self.commit_msg_lines(changes),
              bugs_label=self.config.get('bugs', None),
              add=self.add_new_files,
          )
          if cl_link:
            step.presentation.links['CL'] = cl_link
            summary.append(self.summary())

  @contextmanager
  def roll_contex(self):
    with self.api.context(
        cwd=self.api.path['checkout']), self.api.depot_tools.on_path():
      yield

  def abandon_active_cls(self):
    """Ensure no other active roll exists. If it does, abandon the old one."""
    target_config = self.config['target_config']

    commits = self.api.gerrit.get_changes(
        target_config['gerrit_base_url'],
        query_params=[
            ('project', target_config['project_name']),
            # TODO(sergiyb): Use api.service_account.default().get_email() when
            # https://crbug.com/846923 is resolved.
            ('owner', target_config['account']),
            ('status', 'open'),
            ('subject', f'"{self.get_subject()}"'),
        ],
        limit=20,
        step_test_data=self.api.gerrit.test_api.get_empty_changes_response_data,
    )

    # Querying gerrit with a subject is not exact, so filter the results for precise match.
    commits = [c for c in commits if c['subject'] == self.get_subject()]

    for commit in commits:
      self.api.gerrit.abandon_change(target_config['gerrit_base_url'],
                                     commit['_number'], 'stale roll')

      step_result = self.api.step('Previous roll failed', cmd=None)
      step_result.presentation.step_text = 'Notify sheriffs!'
      step_result.presentation.status = 'FAILURE'

  @abstractmethod
  def name(self):
    pass  # pragma: no cover

  @abstractmethod
  def summary(self):
    pass  # pragma: no cover

  @abstractmethod
  def apply_changes(self):
    pass  # pragma: no cover

  @abstractmethod
  def get_subject(self):
    pass  # pragma: no cover

  def upload_flags(self):
    return []

  @abstractmethod
  def commit_msg_lines(self, changes):
    pass  # pragma: no cover


class DEPSRollHandler(RollHandler, ABC):

  def __init__(self, api, autoroller_config, updates=None):
    super().__init__(api, autoroller_config)
    self.updates = updates

  def apply_changes(self):
    return [update for update in self.updates if self.set_dep(update)]

  def set_dep(self, update):
    with self.api.context(cwd=self.api.path['checkout']):
      clean_name = update.name.replace('/', '_')
      step_result = self.api.gclient(
          f'setdep {clean_name}',
          ['setdep', '-r', f'{update.name}@{update.next_version}'],
          ok_ret='any',
      )
    if step_result.retcode != 0:
      step_result.presentation.status = self.api.step.WARNING
      return False
    return True

  def get_subject(self):
    return f'{self.config["subject"]} ({self.name()})'

  def commit_msg_lines(self, changes):
    return [line for c in changes for line in c.commit_msg_lines
           ] + [roll_origin_line(self.api)]

  def summary(self):
    return f'{len(self.updates)} {self.name()} dep(s)'


class TrustedRollHandler(DEPSRollHandler):

  def upload_flags(self):
    return ['--set-bot-commit']

  def name(self):
    return 'trusted'


class UntrustedRollHandler(DEPSRollHandler):

  def name(self):
    return 'reviewed'

  def commit_msg_lines(self, changes):
    return commit_msg_lines_w_reviewes(super().commit_msg_lines(changes),
                                       self.config['reviewers'])


class ChromiumPinRollHandler(RollHandler):

  def __init__(self, api, autoroller_config):
    super().__init__(api, autoroller_config)
    self.enabled = self.config['roll_chromium_pin']

  def name(self):
    return 'chromium pin'

  def summary(self):
    return f'1 {self.name()}'

  def apply_changes(self):
    current_value = self.current_raw_value(self.api)
    new_value = self.get_latest_version(self.api)
    needs_update = self.should_roll(current_value, new_value)
    if needs_update:
      self.api.gclient(f'set {CHROME_VAR} deps',
                       ['setdep', f'--var={CHROME_VAR}={new_value}'])
      return f'Chromium pin updated to {new_value}'
    return None

  def current_raw_value(self, api):
    try:
      step_result = api.gclient(
          f'get {CHROME_VAR} deps', ['getdep', f'--var={CHROME_VAR}'],
          stdout=api.raw_io.output_text())
      # The first line contains the commit position number. Strip the rest.
      return step_result.stdout.strip().splitlines()[0].strip()
    except Exception:
      api.step.empty(f'Failed get dep {CHROME_VAR}')
      return None  # Ensure no roll attempt

  def get_latest_version(self, api):
    return api.url.get_json(
        CFT_LKGR_URL,
        step_name=f'check latest {CHROME_VAR}',
        default_test_data={
            'channels': {
                'Canary': {
                    'version': '123.0.4500.6'
                }
            }
        }).output['channels']['Canary']['version']

  def version_tuple(self, version):
    return tuple(map(int, version.split('.')))

  def should_roll(self, current, latest):
    return current and (self.version_tuple(current) <
                        self.version_tuple(latest))

  def get_subject(self):
    return CHROMIUM_PIN_CL_SUBJECT

  def upload_flags(self):
    return ['--set-bot-commit']

  def commit_msg_lines(self, changes):
    lines = []
    if changes:
      lines.append(changes)
    lines.append(roll_origin_line(self.api))
    return lines


class SriptedRollsFactory:
  SupportedScript = namedtuple('SupportedScript', ['title', 'exe', 'args'])

  def __init__(self, autoroller_config):
    self.config = autoroller_config

  def supported(self):
    """Returns a dict of supported scripted rolls. The key is the script key
    and the value is a tuple of the title and the path elements to the script.
    """
    return {
        'puppeteer-core':
            SriptedRollsFactory.SupportedScript(
                'Puppeteer Core', 'scripts/deps/roll_front_end_third_party.py',
                ['puppeteer-core', 'puppeteer', 'lib/esm']),
        'puppeteer-replay':
            SriptedRollsFactory.SupportedScript(
                'Puppeteer Replay',
                'scripts/deps/roll_front_end_third_party.py',
                ['@puppeteer/replay', 'puppeteer-replay', 'lib']),
        # Add more scripts here
    }

  def get_rollers(self, api):
    script_keys = self.config.get('scripted_rolls', [])
    return [
        ScriptedRollHandler(api, self.config,
                            self.supported()[key], key) for key in script_keys
    ]


class ScriptedRollHandler(RollHandler):

  def __init__(self, api, autoroller_config, script, key):
    super().__init__(api, autoroller_config)
    self.add_new_files = True
    self.script = script
    self.key = key
    self.updated = False

  def name(self):
    return self.script.title

  def apply_changes(self):
    self.api.step(f'Run {self.name()} script', [
        'python3', '-u', self.api.path['checkout'].join(
            *self.script.exe.split('/')), *self.script.args
    ])

  def get_subject(self):
    return f'Roll {self.key}'

  def commit_msg_lines(self, _):
    return commit_msg_lines_w_reviewes(
      [
        'In case of failures or errors, reach out to someone from '
        'config/owner/RECORDER_OWNERS.',
        roll_origin_line(self.api)
      ],
      self.config['reviewers']
    )

  def summary(self):
    return self.name()


def commit_msg_lines_w_reviewes(commit_msg_lines, reviewers):
  return [
      ('This roll requires a manual review. See http://go/reviewed-rolls for '
       'guidance.')
  ] + commit_msg_lines + [f'R={",".join(reviewers)}']


def roll_origin_line(api):
  return f'\nRoll created at {api.buildbucket.build_url()}'


def handle_failed_deps(api, failed_deps):
  if not failed_deps:
    return

  message = f'Failed to update deps: {", ".join(failed_deps)}'
  raise api.step.StepFailure(message)


def set_defaults(autoroller_config):
  autoroller_config.setdefault('roll_chromium_pin', False)
  autoroller_config.setdefault('scripted_rolls', [])
  target_config = autoroller_config['target_config']
  target_config.setdefault('gerrit_base_url', GERRIT_BASE_URL)
  target_config.setdefault('base_url', BASE_URL)


def RunSteps(api, autoroller_config):
  set_defaults(autoroller_config)
  summary = []

  with api.step.nest('Setup'):
    setup_gclient(api, autoroller_config)
    setup_target_repository(api)

  with api.step.nest('Find updated deps'):
    discard_local_changes(api)
    trusted_updates, untrusted_updates, failed = get_dep_updates(
        api, autoroller_config)

  TrustedRollHandler(api, autoroller_config, trusted_updates).roll(summary)
  UntrustedRollHandler(api, autoroller_config, untrusted_updates).roll(summary)

  with api.step.nest('Check failed deps'):
    handle_failed_deps(api, failed)

  ChromiumPinRollHandler(api, autoroller_config).roll(summary)

  if autoroller_config['scripted_rolls']:
    with api.step.nest('Scripted rolls'):
      for roller in SriptedRollsFactory(autoroller_config).get_rollers(api):
        roller.roll(summary)

  result = result_pb2.RawResult()
  result.status = common_pb2.SUCCESS
  if summary:
    result.summary_markdown = 'updated ' + ', '.join(summary)
  return result


def GenTests(api):
  v8_deps_info = """v8: https://chromium.googlesource.com/v8/v8.git
v8/buildtools-mapped: https://chromium.googlesource.com/chromium/buildtools.git@5fd66957f08bb752dca714a591c84587c9d70762
src/tools/luci-go:infra/tools/luci/isolate/${platform}: https://chrome-infra-packages.appspot.com/infra/tools/luci/isolate/${platform}@git_revision:8b15ba47cbaf07a56f93326e39f0c8e5069c19e9
src/ninja:infra/3pp/tools/ninja/${platform}: https://chrome-infra-packages.appspot.com/infra/3pp/tools/ninja/${platform}@version:2@1.8.2.chromium.3
src/mock-skip-chromium-roll: mock/skip-chromium-roll.git@1
v8/mock-tot-rolled: https://chromium.googlesource.com/tot-rolled.git@d3f34f8dfaecc23202a6ef66957e83462d6c826d
v8/mock-tot-retsam-rolled: https://chromium.googlesource.com/tot-retsam-rolled.git@d3f34f8dfaecc23202a6ef66957e83462d6c826d
v8/tools/clang: https://chromium.googlesource.com/chromium/src/tools/clang@d3f34f8dfaecc23202a6ef66957e83462d6c826d
v8/tools/clang-reviewed: https://example.com/tools/clang-reviewed.git@d3f34f8dfaecc23202a6ef66957e83462d6c826d
v8/mock-set-dep-failing: mock/set-dep-failing.git@1
v8/mock-package-without-latest-ref:mock/package-without-latest-ref: https://chrome-infra-packages.appspot.com/mock/package-without-latest-ref@7fd66957f08bb752dca714a591c84587c9d70764
v8/mock-package-latest:mock/package-latest: https://chrome-infra-packages.appspot.com/mock/package-latest@6fd66957f08bb752dca714a591c84587c9d70763"""

  cr_deps_info = """src: https://chromium.googlesource.com/chromium/src.git
src/buildtools: https://chromium.googlesource.com/chromium/buildtools.git@5fd66957f08bb752dca714a591c84587c9d70762
src/tools/luci-go:infra/tools/luci/isolate/${platform}: https://chrome-infra-packages.appspot.com/infra/tools/luci/isolate/${platform}@git_revision:3d8f881462b1a93c7525499381fafc8a08691be7
src/ninja:infra/3pp/tools/ninja/${platform}: https://chrome-infra-packages.appspot.com/infra/3pp/tools/ninja/${platform}@version:2@1.8.2.chromium.4
src/mock-set-dep-failing: mock/set-dep-failing.git@2
src/mock-skip-chromium-roll: mock/skip-chromium-roll.git@2"""

  target_config_v8 = {
    'solution_name': 'v8',
    'project_name': 'v8/v8',
    'account': 'v8@example.com',
    'log_template': 'Rolling v8/%s: %s/+log/%s..%s',
    'cipd_log_template': 'Rolling v8/%s: %s..%s',
  }

  git_cl_info = """remote:
remote:   https://chromium-review.googlesource.com/c/chromium/tools/build/+/3840339 [v8] Remove deprecated roll recipe [WIP]
remote:"""

  autoroller_config = {
      'target_config': target_config_v8,
      'subject': 'Update V8 deps',
      'reviewers': [
          'anybody@chromium.org',
          'ciciobello@chromium.org',
      ],
      'deps_key_mapping': {
          'buildtools-mapped': 'buildtools',
      },
      'dependency_version_sources': {
        'mock-skip-chromium-roll': 'tip_of_tree',
      },
      'show_commit_log': True,
      'roll_chromium_pin': True,
      'scripted_rolls': ['puppeteer-core'],
      'bugs': 'none',
  }

  def base_template(testname, additional_v8_deps, additional_cr_deps):
    return [
        testname,
        api.properties(autoroller_config=autoroller_config),
        api.buildbucket.ci_build(
            project='v8',
            git_repo='https://chromium.googlesource.com/v8/v8',
            builder=testname,
        ),
        api.override_step_data(
            'Find updated deps.gclient get v8 deps',
            api.raw_io.stream_output_text(
                v8_deps_info + additional_v8_deps, stream='stdout'),
        ),
        api.override_step_data(
            'Find updated deps.gclient get src deps',
            api.raw_io.stream_output_text(
                cr_deps_info + additional_cr_deps, stream='stdout'),
        ),
    ]

  def template(
      testname,
      additional_v8_deps='',
      additional_cr_deps='',
      git_diff='some difference',
  ):
    result = base_template(testname, additional_v8_deps, additional_cr_deps) + [
        # CIPDs test api has a latest ref by default for each package. We over-
        # ride this behaviour for our mock package `without-latest-ref`.
        api.override_step_data(
            'Find updated deps.cipd instances mock/package-without-latest-ref',
            api.cipd._resultify({
                'instances': [{
                    'pin': {
                        'package':
                            'mock/package-without-latest-ref',
                        'instance_id':
                            api.cipd.make_resolved_version('no-latest'),
                    },
                    'registered_by': 'user:doe@developer.gserviceaccount.com',
                    'registered_ts': 1987654321,
                    'refs': None,
                }]
            })),
        api.override_step_data(
            'Update trusted deps.git status',
            api.raw_io.stream_output_text(git_diff, stream='stdout'),
        ),
        api.override_step_data(
            'Update reviewed deps.git status',
            api.raw_io.stream_output_text(git_diff, stream='stdout'),
        ),
        api.override_step_data(
            'Update trusted deps.gclient setdep mock-set-dep-failing',
            retcode=1),
        api.override_step_data(
            'Find updated deps.look up mock-tot-rolled (main)',
            api.raw_io.stream_output_text(
                'deadbeef\trefs/heads/main', stream='stdout'),
        ),
        api.override_step_data(
            'Find updated deps.look up mock-skip-chromium-roll (main)',
            api.raw_io.stream_output_text(
                '3\trefs/heads/main', stream='stdout'),
        ),
        api.override_step_data(
            'Find updated deps.look up mock-tot-retsam-rolled (main)',
            api.raw_io.stream_output_text('', stream='stdout'),
        ),
        api.override_step_data(
            f'Find updated deps.look up mock-tot-retsam-rolled ({RETSAM})',
            api.raw_io.stream_output_text(
                f'deadbeef\trefs/heads/{RETSAM}', stream='stdout'),
        ),
        api.override_step_data(
            'Find updated deps.look up tools_clang (main)',
            api.raw_io.stream_output_text(
                'deadbeef\trefs/heads/main', stream='stdout'),
        ),
        api.override_step_data(
            'Find updated deps.look up tools_clang-reviewed (main)',
            api.raw_io.stream_output_text(
                'deadbeef\trefs/heads/main', stream='stdout'),
        ),
        api.override_step_data(
            'Update chromium pin deps.gclient get chrome deps',
            api.raw_io.stream_output_text('123.0.4500.7', stream='stdout'),
        ),
    ]

    if git_diff:
      result += [
          api.override_step_data(
              'Update reviewed deps.git cl',
              api.raw_io.stream_output_text(git_cl_info, stream='stdout'),
          ),
          api.override_step_data(
              'Update trusted deps.git cl',
              api.raw_io.stream_output_text(git_cl_info, stream='stdout'),
          ),
      ]

    return result


  # Happy path
  yield api.test(
      *template('default') + [
          api.override_step_data(
              'Scripted rolls.Update Puppeteer Core deps.git status',
              api.raw_io.stream_output_text(
                  'diff generated by script', stream='stdout'),
          ),
          api.override_step_data(
              'Scripted rolls.Update Puppeteer Core deps.git cl',
              api.raw_io.stream_output_text(git_cl_info, stream='stdout'),
          ),
          api.post_process(
              SummaryMarkdown,
              'updated 4 trusted dep(s), 6 reviewed dep(s), Puppeteer Core')
      ],)

  # No chrome pin roll
  yield api.test(*template('missing key') + [
      api.override_step_data(
          'Update chromium pin deps.gclient get chrome deps',
          api.raw_io.stream_output_text(
              'Could not find any variable called chrome.', stream='stderr')),
      api.post_process(MustRun,
                       "Update chromium pin deps.Failed get dep chrome"),
      api.post_process(DropExpectation),
  ])

  # Stale rolls: If active roll CLs exists in gerrit, we abandon those first
  yield api.test(
      *(template('no-stale-roll') + [
          api.post_process(DoesNotRunRE, 'Update .* deps\.gerrit abandon'),
          api.post_process(DropExpectation),
      ]),
      status='SUCCESS')

  yield api.test(
      *(template('stale-roll') + [
          api.override_step_data(
              'Update trusted deps.gerrit changes',
              api.json.output([
                  {
                      '_number': '123',
                      'subject': 'Update V8 deps (trusted)'
                  },
              ])),
          api.override_step_data(
              'Update reviewed deps.gerrit changes',
              api.json.output([
                  {
                      '_number': '123',
                      'subject': 'Update V8 deps (reviewed)'
                  },
              ])),
          api.post_process(MustRun, 'Update trusted deps.gerrit abandon'),
          api.post_process(MustRun, 'Update trusted deps.Previous roll failed'),
          api.post_process(MustRun, 'Update reviewed deps.gerrit abandon'),
          api.post_process(MustRun,
                           'Update reviewed deps.Previous roll failed'),
          api.post_process(DropExpectation),
      ]),
      status='SUCCESS')

  # No version difference: There is no new dependency version, and we do not try
  # to update any dep (via `gclient setdep`).
  yield api.test(
      'no-version-difference',
      api.properties(autoroller_config=autoroller_config),
      api.buildbucket.ci_build(
          project='v8',
          git_repo='https://chromium.googlesource.com/v8/v8',
          builder='no-version-difference',
      ),
      api.override_step_data(
          'Find updated deps.gclient get src deps',
          api.raw_io.stream_output_text(
              'src: https://chromium.googlesource.com/chromium/src.git\n'
              'src/tools: https://example.com/chromium/tools.git@42',
              stream='stdout',
          ),
      ),
      api.override_step_data(
          'Find updated deps.gclient get v8 deps',
          api.raw_io.stream_output_text(
              'v8: https://chromium.googlesource.com/chromium/v8.git\n'
              'v8/tools: https://example.com/chromium/tools.git@42',
              stream='stdout',
          ),
      ),
      api.override_step_data(
          'Update chromium pin deps.gclient get chrome deps',
          api.raw_io.stream_output_text('123', stream='stdout'),
      ),
      api.post_process(DoesNotRunRE, r'^Update \w* deps\.gclient setdep .*'),
      api.post_process(
          DoesNotRun,
          'Update chromium pin deps.gclient set chromium_linux deps'),
      api.post_process(DropExpectation),
      status='SUCCESS',
  )

  # No update succeeded: If there is no dependency update, we don't create CLs
  yield api.test(*template(
      'no-depependency-update',
      git_diff=''
  ) + [
      api.post_process(DoesNotRunRE, r'^Update \w* deps\.git cl$'),
      api.post_process(DropExpectation),
  ], status='SUCCESS')

  # Malformed DEPS file: Raise an exception
  yield api.test(*base_template(
      'malformed-deps-file',
      additional_v8_deps='\nsrc/mock-malformed:  https://example.com/',
      additional_cr_deps='',
  ) + [
    api.expect_exception('Exception'),
    api.post_process(DropExpectation),
  ])

  # Changed locations: Fail if a dependency changes its repository
  yield api.test(*base_template(
      'changed-location',
      additional_v8_deps='\nv8/mock-changed-location: foo/changed-location@1',
      additional_cr_deps='\nsrc/mock-changed-location: bar/changed-location@2',
  ) + [
      api.post_process(DropExpectation),
  ], status='FAILURE')
