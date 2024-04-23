# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from . import commons
from .handler_base import RollHandler

from abc import ABC
from dataclasses import dataclass
from functools import cached_property
import re
from typing import Dict, List, Literal, Optional


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


@dataclass
class DepUpdate:
  name: str
  next_version: str
  commit_msg_lines: str


@dataclass
class ChromiumDep:
  name: str
  location: str
  version: str


@dataclass
class TargetDep:
  name: str
  location: str
  version: str

  api: any
  config: Dict[Literal['dependency_version_sources'], Dict[str, str]]
  chromium_dep_by_location: Dict[str, ChromiumDep]

  def __repr__(self) -> str:
    trusted_label = 'trusted' if self.is_trusted else 'reviewed'
    next_version = self.next_version if not self.has_changed_location else None
    return (
        f"{self.name} @ {self.version} → "
        f"{self.source_system} @ {next_version} ({trusted_label}) from "
        f"{self.location}"
    )

  @property
  def is_trusted(self) -> bool:
    trusted_origin = self.canonical_location in TRUSTED_ORIGIN_DEPS
    from_chromium = self.source_system == 'chromium'
    return trusted_origin or from_chromium

  @property
  def is_cipd(self) -> bool:
    return self.location.startswith(CIPD_DEP_URL_PREFIX)

  @property
  def canonical_location(self) -> str:
    return canonical_location(self.location)

  @property
  def source_system(self) -> Literal['chromium', 'cipd', 'tip_of_tree']:
    manual_sources = self.config.get('dependency_version_sources', {})

    if self.name in manual_sources:
      return manual_sources.get(self.name)

    if self.canonical_location in self.chromium_dep_by_location:
      return 'chromium'

    if self.is_cipd:
      return 'cipd'

    return 'tip_of_tree'

  @property
  def has_changed_location(self) -> bool:
    chromium_location = self.chromium_location
    return chromium_location and chromium_location != self.canonical_location

  @property
  def chromium_location(self) -> Optional[str]:
    chromium_locations_by_name = {
        dep.name: location
        for location, dep in self.chromium_dep_by_location.items()
    }

    return chromium_locations_by_name.get(self.name)

  @property
  def next_version(self) -> str:
    source = self.source_system

    if source == 'chromium':
      chromium_dep = self.chromium_dep_by_location[self.canonical_location]
      return chromium_dep.version

    if source == 'cipd':
      return self.recent_cipd_version

    assert source == 'tip_of_tree', f'Invalid source system {source}'

    return self.tip_of_tree_version

  @cached_property
  def recent_cipd_version(self):
    assert self.source_system == 'cipd', (
        f'Cannot determine cipd version, dependency is rolled from '
        f'{self.source_system}.')

    # If a ref named `latest` is used in cipd, prefer this instance. Otherwise
    # select the most recently uploaded instance.
    cipd_name = self.location[len(CIPD_DEP_URL_PREFIX):]
    instances = self.api.cipd.instances(cipd_name, 0)
    for instance in instances:
      if instance.refs and 'latest' in instance.refs:
        return instance.pin.instance_id

    return instances[0].pin.instance_id

  @cached_property
  def tip_of_tree_version(self):
    assert self.source_system == 'tip_of_tree', (
        f'Cannot determine version from tip of tree, dependency is rolled from '
        f'{self.source_system}.')

    # Fallback to the deprecated naming scheme still used by some deps, if there
    # is no head for the main branch.
    for branch in ['main', RETSAM]:
      head_revision = self.api.git(
          'ls-remote',
          self.location,
          f'refs/heads/{branch}',
          name=f'look up {self.name.replace("/", "_")} ({branch})',
          stdout=self.api.raw_io.output_text(),
      ).stdout.strip().split('\t')[0]
      if head_revision:
        return head_revision

    assert False, (
        f'Cannot determine revision for {self.name} at {self.location}.')


def get_dep_updates(api, step_presentation, autoroller_config):
  chromium_dep_by_location = get_chromium_deps_by_location(api)

  target_deps = get_target_deps(api, autoroller_config,
                                chromium_dep_by_location)
  step_presentation.logs['filtered deps'] = [repr(td) for td in target_deps]

  trusted_updates = []
  untrusted_updates = []
  failed_deps = []
  for target_dep in target_deps:
    if target_dep.has_changed_location:
      # If the name of the dependency is available in chromium as well, but with
      # a different location, we assume that the location has changed. This
      # needs to be fixed manually as the gclient tool does not have commands
      # that allow overrideing the repository.
      # NOTE: The check does not cover dependencies which have a different name,
      # but had the same location in the past.
      message = (
          f'dep {target_dep.name} has changed repo from {target_dep.location} '
          f'to {target_dep.chromium_location}')
      step_result = api.step(message, cmd=None)
      step_result.presentation.status = api.step.FAILURE
      failed_deps.append(target_dep.name)
      continue

    version = target_dep.version
    next_version = target_dep.next_version

    # Update target dependency if changes exist
    if version == next_version:
      continue

    # Construct commit message lines
    commit_msg_lines = []
    if target_dep.is_cipd:
      # Unfortunately CIPD does not provide a way to generate a link that
      # lists all versions from v8_rev to new_ver. Even just creating a link
      # to a list of versions of a DEP is complicated as package name can
      # contain ${platform}, which can usually be resolved to multiple
      # distinct packages.
      path, _ = target_dep.name.split(':')
      commit_msg_lines.append(CIPD_LOG_TEMPLATE % (path, version, next_version))
    else:
      params = (target_dep.name, target_dep.canonical_location, version[:7],
                next_version[:7])
      commit_msg_lines.append(GIT_LOG_TEMPLATE % params)
      if autoroller_config['show_commit_log']:
        commit_msg_lines.extend(
            commit_messages_log_entries(api, target_dep.canonical_location,
                                        version, next_version))

    dep_update = DepUpdate(
        name=target_dep.name,
        next_version=next_version,
        commit_msg_lines=commit_msg_lines,
    )
    if target_dep.is_trusted:
      trusted_updates.append(dep_update)
    else:
      untrusted_updates.append(dep_update)

  return trusted_updates, untrusted_updates, failed_deps


def get_chromium_deps_by_location(api) -> Dict[str, ChromiumDep]:
  chromium_dep_by_name = get_deps(
      api,
      'https://chromium.googlesource.com/chromium/src',
      'src',
  )
  result = {}
  for name, entry in chromium_dep_by_name.items():
    # Skip GCS dependencies
    # TODO(336470709): Add support for rolling GCS deps
    if entry.startswith('gs://'):
      continue

    location, version = get_location_version(entry)
    clean_location = canonical_location(location)
    chromium_dep = ChromiumDep(name, location, version)
    result[clean_location] = chromium_dep

  return result


def get_target_deps(
    api, autoroller_config, chromium_dep_by_location) -> List[TargetDep]:
  target = commons.get_targeted_solution(api)
  target_dep_entry_by_name = get_deps(api, target.url, target.name)

  target_deps = []
  for name, dep_entry in target_dep_entry_by_name.items():
    location, version = get_location_version(dep_entry)
    target_deps.append(TargetDep(
        name,
        location,
        version,
        api,
        autoroller_config,
        chromium_dep_by_location,
    ))

  target_deps = filter_deps(autoroller_config, target_deps)
  target_deps = sorted(target_deps, key=lambda dep: dep.name)

  return target_deps


def filter_deps(autoroller_config, dependencies):
  excludes = autoroller_config.get('excludes')
  includes = autoroller_config.get('includes')
  dep_names = {d.name for d in dependencies}

  assert excludes is None or includes is None, (
      'Either excludes or includes can be declared, not both.')
  assert excludes is None or all(e in dep_names for e in excludes), (
      'At least one excluded dep does not exist. Found '
      f'{", ".join(dep_names)}')
  assert includes is None or all(i in dep_names for i in includes), (
      'At least one included dep does not exist. Found '
      f'{", ".join(dep_names)}')

  dependencies = [
      d for d in dependencies if excludes is None or d.name not in excludes
  ]
  dependencies = [
      d for d in dependencies if includes is None or d.name in includes
  ]

  return dependencies


def canonical_location(name):
  return re.sub(r'\.git$', '', name)


def get_location_version(entry):
  return entry.split('@', 1)


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
