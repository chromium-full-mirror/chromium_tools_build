# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import abc
import re

from dataclasses import dataclass
from functools import cached_property
from typing import List, Optional, TypedDict, Union

# The following dependencies are trusted - if new deps are added, their projects
# need to be BCID L3 (http://go/bcid-ladder#level-3) compliant.
TRUSTED_ORIGIN_CIPD_DEPS = {
    "https://chrome-infra-packages.appspot.com/fuchsia/third_party/aemu/linux-amd64",
    "https://chrome-infra-packages.appspot.com/p/fuchsia/qemu/linux-amd64",
}

TRUSTED_ORIGIN_GIT_DEPS = {
    "https://chromium.googlesource.com/deps/inspector_protocol",
    "https://chromium.googlesource.com/devtools/devtools-frontend",
    "https://chromium.googlesource.com/infra/luci/luci-py/client/libs/logdog",
}

TRUSTED_ORIGIN_PREFIXES = {
    "https://chromium.googlesource.com/chromium/src/",
}

CIPD_DEP_URL_PREFIX = 'https://chrome-infra-packages.appspot.com/'

# Some dependent repositories still use the deprecated term as their main branch
RETSAM = 'retsam'[::-1]

SIMPLE_LOG_TEMPLATE = "Rolling %s: %s..%s"
GIT_LOG_TEMPLATE = "Rolling %s: %s/+log/%s..%s"
MAX_COMMIT_LOG_ENTRIES = 8

# Specs: Mirror the structure of a deps entry in the DEPS file.
GitSpec = TypedDict('GitSpec', {'url': str})

CipdPackage = TypedDict('CipdPackage', {
    'package': str,
    'version': str,
})

CipdSpec = TypedDict('CipdSpec', {'packages': List[CipdPackage]})

GcsObject = TypedDict('GcsObject', {
    'object_name': str,
    'sha256sum': str,
    'size_bytes': int,
    'generation': int,
})

GcsSpec = TypedDict('GcsSpec', {'bucket': str, 'objects': List[GcsObject]})


def get_location_version(entry):
  assert '@' in entry, (
      f'Invalid format: Expected location@version, found {entry}.')
  return entry.split('@', 1)


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


def get_commit_log(api, repo, commit):
  subject = commit["message"].splitlines()[0]
  author = commit["author"]["name"]
  commit_url = api.url.join(repo, f'+/{commit["commit"][:7]}')
  return f"{subject} ({author})\n{commit_url}"


@dataclass
class BaseDep(abc.ABC):
  """Define a wrapper class for a deps entry. A dep is an entry in the DEPS.deps
  dictionary. It might include multiple artifacts (e.g. for cipd and gcs dep
  types)."""
  api: any
  path: str

  @cached_property
  @abc.abstractmethod
  def artifacts(self):
    """Return a list of artifacts for each deps entry."""


@dataclass
class GitDep(BaseDep):
  spec: GitSpec

  @property
  def canonical_location(self):
    location = self.spec['url'].split('@', maxsplit=1)[0]
    return re.sub(r'\.git$', '', location)

  @cached_property
  def artifacts(self):
    return [GitArtifact(self, self.spec)]


@dataclass
class CipdDep(BaseDep):
  spec: CipdSpec

  @cached_property
  def artifacts(self):
    return [
        CipdArtifact(self, package)
        for package in self.spec['packages']
        # TODO(https://crbug.com/500339449): We don't yet support CIPD deps
        # with a `version_file` entry.
        if 'version' in package
    ]


@dataclass
class GcsDep(BaseDep):
  spec: GcsSpec

  @cached_property
  def artifacts(self):
    return [
        GcsArtifact(self, o, idx) for idx, o in enumerate(self.spec['objects'])
    ]


@dataclass
class BaseArtifact(abc.ABC):
  """Define a wrapper class for an artifact, which is the smallest rollable
  entity supported by the autoroller."""
  dep: BaseDep
  artifact: any

  def __post_init__(self):
    self.initial_version = self.version  # pylint: disable=attribute-defined-outside-init

  @property
  @abc.abstractmethod
  def identifier(self) -> str:
    """Return a unique identifier for this artifact which is identical across
    versions and projects."""

  @property
  @abc.abstractmethod
  def version(self) -> str:  #pragma: nocover
    ...

  @version.setter
  @abc.abstractmethod
  def version(self, version) -> None:  #pragma: nocover
    ...

  @abc.abstractmethod
  def get_commit_message(self, show_commit_log) -> List[str]:  #pragma: nocover
    ...

  @property
  @abc.abstractmethod
  def gclient_revision(self) -> str:
    """Return the revision parameter to update this dependency via
    `gclient setdep`."""

  def gclient_setdep(self):
    name = self.dep.path.replace('/', '_')
    cmd = ['setdep', '-r', self.gclient_revision]
    return self.dep.api.gclient(f'setdep {name}', cmd)


class RollToLatestArtifact(abc.ABC):

  @cached_property
  @abc.abstractmethod
  def latest_version(self) -> str:  #pragma: nocover
    ...

  @property
  @abc.abstractmethod
  def is_trusted(self) -> bool:  #pragma: nocover
    ...


@dataclass
class GitArtifact(BaseArtifact, RollToLatestArtifact):
  dep: GitDep
  artifact: GitSpec

  def __repr__(self):
    return (f"(git) {self.dep.path} · Source: {self.location} · "
            f"Version: {self.version}")

  @property
  def identifier(self):
    return f'git:{self.dep.canonical_location}'

  @property
  def location(self):
    return get_location_version(self.artifact['url'])[0]

  @property
  def version(self):
    return get_location_version(self.artifact['url'])[1]

  @version.setter
  def version(self, version):
    self.artifact['url'] = f'{self.location}@{version}'

  @cached_property
  def latest_version(self):
    # Request latest revision from the git repository's main branch.
    # Fallback to the deprecated naming scheme still used by some deps, if there
    # is no head for the main branch.
    api = self.dep.api
    for branch in ['main', RETSAM]:
      head_revision = api.git(
          'ls-remote',
          self.location,
          f'refs/heads/{branch}',
          name=f'look up {self.dep.path.replace("/", "_")} ({branch})',
          stdout=api.raw_io.output_text(),
      ).stdout.strip().split('\t')[0]

      if head_revision:
        return head_revision

    assert False, (
        f'Cannot determine revision for {self.dep.path} at {self.location}.')

  @property
  def is_trusted(self):
    trusted_origin = self.dep.canonical_location in TRUSTED_ORIGIN_GIT_DEPS

    trusted_prefix = any(
        self.dep.canonical_location.startswith(prefix)
        for prefix in TRUSTED_ORIGIN_PREFIXES)

    return trusted_origin or trusted_prefix

  def get_commit_message(self, show_commit_log):
    params = (
        self.dep.path,
        self.dep.canonical_location,
        self.initial_version[:7],
        self.version[:7],
    )
    messages = [GIT_LOG_TEMPLATE % params]

    if show_commit_log:
      messages.extend(
          commit_messages_log_entries(
              self.dep.api,
              self.dep.canonical_location,
              self.initial_version,
              self.version,
          ))

    return messages

  @property
  def gclient_revision(self):
    return f'{self.dep.path}@{self.version}'


@dataclass
class CipdArtifact(BaseArtifact, RollToLatestArtifact):
  dep: CipdDep
  artifact: CipdPackage

  def __repr__(self):
    return (f"(cipd) {self.dep.path} · Source: {self.artifact['package']} · "
            f"Version: {self.version}")

  @property
  def identifier(self):
    return f'cipd:{self.artifact["package"]}'

  @property
  def version(self):
    return self.artifact['version']

  @version.setter
  def version(self, version):
    self.artifact['version'] = version

  @cached_property
  def latest_version(self):
    # If a ref named `latest` is used in cipd, prefer this instance. Otherwise
    # select the most recently uploaded instance.
    instances = self.dep.api.cipd.instances(self.artifact['package'], 0)
    for instance in instances:
      if instance.refs and 'latest' in instance.refs:
        return instance.pin.instance_id

    return instances[0].pin.instance_id

  @property
  def is_trusted(self):
    url = f'{CIPD_DEP_URL_PREFIX}{self.artifact["package"]}'
    return url in TRUSTED_ORIGIN_CIPD_DEPS

  def get_commit_message(self, show_commit_log):
    name = self.dep.path
    return [SIMPLE_LOG_TEMPLATE % (name, self.initial_version, self.version)]

  @property
  def gclient_revision(self):
    package = self.artifact["package"]
    package = re.sub(r"\${{([^}]+)}}", r"${\1}", package)
    return f'{self.dep.path}:{package}@{self.version}'


CLANG_MAPPING = lambda idx, oname: oname.split('-llvmorg-')[0]


@dataclass
class GcsArtifact(BaseArtifact):
  dep: GcsDep
  artifact: GcsObject
  index: int

  VERSION_KEYS = ['object_name', 'sha256sum', 'size_bytes', 'generation']

  ID_MAPPINGS = {
      'third_party/llvm-build/Release+Asserts':
          CLANG_MAPPING,
  }

  def __repr__(self):
    return (f"(gcs) {self.dep.path} · Source: {self.dep.spec['bucket']} · "
            f"Version: {self.version}")

  @property
  def identifier(self):
    path = self.dep.path
    if path.startswith('src/'):
      path = path[4:]

    id_mapping = self.ID_MAPPINGS.get(
        path, lambda idx, name: f'{path.replace("/", "_")}-{idx}')
    artifact_id = id_mapping(self.index, self.artifact['object_name'])

    return f'gcs:{self.dep.spec["bucket"]}/{artifact_id}'

  @property
  def version(self):
    return ','.join([str(self.artifact[k]) for k in self.VERSION_KEYS])

  @version.setter
  def version(self, version):
    version_parts = version.split(',')
    assert len(version_parts) == len(self.VERSION_KEYS), (
        f'Require a GCS version with {len(self.VERSION_KEYS)} parts, but found '
        f'{len(version_parts)}.')

    for key, value in zip(self.VERSION_KEYS, version_parts):
      self.artifact[key] = value

  @cached_property
  def latest_version(self):
    raise NotImplementedError(
        'GCS dependencies cannot roll to the latest version, but must be '
        'retrieved from the upstream repository unless explicitly excluded.')

  def get_commit_message(self, show_commit_log):
    return [
        SIMPLE_LOG_TEMPLATE %
        (self.identifier, self.initial_version, self.version)
    ]

  @property
  def gclient_revision(self):
    object_ids = [a.version for a in self.dep.artifacts]
    return f'{self.dep.path}@{"?".join(object_ids)}'


Artifact = Union[GitArtifact, CipdArtifact, GcsArtifact]
