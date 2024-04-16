# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from . import commons
from .handler_base import RollHandler
from abc import ABC

import re

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
    "https://chromium.googlesource.com/chromium/src/third_party/fuzztest",
    "https://chromium.googlesource.com/chromium/src/third_party/google_benchmark",
    "https://chromium.googlesource.com/chromium/src/third_party/instrumented_libraries",
    "https://chromium.googlesource.com/chromium/src/third_party/jinja2",
    "https://chromium.googlesource.com/chromium/src/third_party/markupsafe",
    "https://chromium.googlesource.com/chromium/src/third_party/zlib",
    "https://chromium.googlesource.com/infra/luci/luci-py/client/libs/logdog",
    "https://chromium.googlesource.com/chromium/src/tools/clang",
}

CIPD_DEP_URL_PREFIX = 'https://chrome-infra-packages.appspot.com/'
CIPD_LOG_TEMPLATE = "Rolling %s: %s..%s"
GIT_LOG_TEMPLATE = "Rolling %s: %s/+log/%s..%s"
MAX_COMMIT_LOG_ENTRIES = 8

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

# Some dependent repositories still use the deprecated term as their main branch
RETSAM = 'retsam'[::-1]


class DEPSRollHandler(RollHandler, ABC):

  def __init__(self, module, autoroller_config, updates=None):
    super().__init__(module, autoroller_config)
    self.updates = updates

  def apply_changes(self):
    return [update for update in self.updates if self.set_dep(update)]

  def set_dep(self, update):
    with self.api.context(cwd=self.api.path.checkout_dir):
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
           ] + [commons.roll_origin_line(self.api)]

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
    return commons.commit_msg_lines_w_reviewes(
        super().commit_msg_lines(changes), self.config['reviewers'])


class DepUpdate:

  def __init__(self, name, next_version, commit_msg_lines):
    self.name = name
    self.next_version = next_version
    self.commit_msg_lines = commit_msg_lines


def get_dep_updates(api, autoroller_config):
  chromium_deps = get_deps(
      api,
      'https://chromium.googlesource.com/chromium/src',
      'src',
  )

  target = commons.get_targeted_solution(api)
  target_deps = get_deps(api, target.url, target.name)

  key_mapper = get_key_mapper(chromium_deps, target_deps)

  target_dep_names = sorted(target_deps.keys())
  target_dep_names = filter_deps(autoroller_config, target_dep_names)

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
    sources = autoroller_config.get('dependency_version_sources', {})
    version_source = get_dependency_version_source(sources, target_name,
                                                   bool(chromium_value),
                                                   is_cipd_dep)

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
      commit_msg_lines.append(CIPD_LOG_TEMPLATE %
                              (path, target_version, next_version))
    else:
      params = (target_name, clean_target_location, target_version[:7],
                next_version[:7])
      commit_msg_lines.append(GIT_LOG_TEMPLATE % params)
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


def filter_deps(autoroller_config, dep_names):
  excludes = autoroller_config.get('excludes')
  includes = autoroller_config.get('includes')

  assert excludes is None or includes is None, (
      'Either excludes or includes can be declared, not both.')
  assert excludes is None or all(e in dep_names for e in excludes), (
      'At least one excluded dep does not exist. Found '
      f'{", ".join(dep_names)}')
  assert includes is None or all(i in dep_names for i in includes), (
      'At least one included dep does not exist. Found '
      f'{", ".join(dep_names)}')

  dep_names = [
      dn for dn in dep_names if excludes is None or dn not in excludes
  ]
  dep_names = [
      dn for dn in dep_names if includes is None or dn in includes
  ]

  return dep_names


def handle_failed_deps(api, failed_deps):
  if not failed_deps:
    return

  message = f'Failed to update deps: {", ".join(failed_deps)}'
  raise api.step.StepFailure(message)


def get_deps(api, repo_url, name):
  # Make a fake spec. Gclient is not nice to us when having two solutions
  # side by side. The latter checkout kills the former's gclient file.
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


def get_key_mapper(source_deps, target_deps):
  """Override keys between destination (key) and source (value) based on
  the dependency's location."""
  source_names_by_location = {
      value.split('@', 1)[0]: name for name, value in source_deps.items()
  }
  target_names_by_location = {
      value.split('@', 1)[0]: name for name, value in target_deps.items()
  }
  source_locations = set(source_names_by_location.keys())
  target_locations = set(target_names_by_location.keys())
  common_locations = source_locations & target_locations

  automatic_mapping = {
      target_names_by_location[loc]: source_names_by_location[loc]
      for loc in common_locations
  }

  return lambda key: automatic_mapping.get(key, key)


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
              'author': {
                  'name': 'Tex'
              },
              'message': 'Commit 1\n\nsecond line',
          },
          {
              'commit': 'beefdead',
              'author': {
                  'name': 'Mex'
              },
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


def get_dependency_version_source(sources, dependency_name, is_chromium_dep,
                                  is_cipd_dep):
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
