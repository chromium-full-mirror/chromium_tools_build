# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from . import commons
from .handler_base import RollHandler
from .definitions import (
  Artifact,
  CipdDep,
  GcsDep,
  GitDep,
)

from abc import ABC
from dataclasses import dataclass
from functools import cached_property
import re
from typing import Dict, List, Literal, Optional


class DEPSRollHandler(RollHandler, ABC):
  def __init__(self, module, source_dir, autoroller_config, updates=None):
    super().__init__(module, source_dir, autoroller_config)
    self.updates = updates

  def apply_changes(self):
    # NOTE: If multiple artifacts within the same GCS dep are updated in the
    # same roll, we call gclient setdep once for each artifact, but update the
    # whole GCS entry each time.
    with self.api.context(cwd=self.source_dir):
      for update in self.updates:
        update.gclient_setdep()

    return self.updates

  def get_subject(self):
    return f'{self.config["subject"]} ({self.name()})'

  def commit_msg_lines(self, changes):
    return [
      line
      for c in changes
      for line in c.get_commit_message(self.config['show_commit_log'])
    ] + [commons.roll_origin_line(self.api)]

  def summary(self):
    return f'{len(self.updates)} {self.name()} dep(s)'


class TrustedRollHandler(DEPSRollHandler):
  def upload_flags(self):
    return ['--set-bot-commit', '--use-commit-queue']

  def name(self):
    return 'trusted'


class UntrustedRollHandler(DEPSRollHandler):
  def name(self):
    return 'reviewed'

  def commit_msg_lines(self, changes):
    return commons.commit_msg_lines_w_reviewes(
      super().commit_msg_lines(changes),
      self.config.get('manual_roll_reviewers'),
    )


def get_dep_updates(api, step_presentation, autoroller_config):
  source_artifacts = get_artifacts(api, 'src')
  source_artifacts_by_id = {a.identifier: a for a in source_artifacts}

  target = commons.get_targeted_solution(api)
  target_artifacts = get_artifacts(api, target.name)
  target_artifacts = filter_artifacts(autoroller_config, target_artifacts)

  step_presentation.logs['filtered deps'] = [repr(t) for t in target_artifacts]

  trusted_updates = []
  reviewed_updates = []
  for target_artifact in target_artifacts:
    source_artifact = source_artifacts_by_id.get(target_artifact.identifier)
    dependency_sources = autoroller_config.get('dependency_version_sources', {})
    source_system = dependency_sources.get(target_artifact.dep.path, 'chromium')
    if source_artifact and source_system == 'chromium':
      roll_from_chromium(source_artifact, target_artifact, trusted_updates)
    else:
      roll_latest_version(target_artifact, trusted_updates, reviewed_updates)

  return trusted_updates, reviewed_updates


def roll_from_chromium(source_artifact, target_artifact, trusted_updates):
  if source_artifact.version == target_artifact.version:
    return

  target_artifact.version = source_artifact.version
  trusted_updates.append(target_artifact)


def roll_latest_version(artifact, trusted_updates, reviewed_updates):
  if artifact.latest_version == artifact.version:
    return

  artifact.version = artifact.latest_version
  if artifact.is_trusted:
    trusted_updates.append(artifact)
  else:
    reviewed_updates.append(artifact)


def get_artifacts(api, project) -> List[Artifact]:
  deps = get_deps(api, project)

  artifacts = []
  for dep in deps:
    artifacts += dep.artifacts

  return artifacts


def filter_artifacts(autoroller_config, artifacts):
  excludes = autoroller_config.get('excludes')
  includes = autoroller_config.get('includes')
  dep_names = {a.dep.path for a in artifacts}

  assert excludes is None or includes is None, (
    'Either excludes or includes can be declared, not both.'
  )
  assert excludes is None or all(e in dep_names for e in excludes), (
    f'At least one excluded dep does not exist. Found {", ".join(dep_names)}'
  )
  assert includes is None or all(i in dep_names for i in includes), (
    f'At least one included dep does not exist. Found {", ".join(dep_names)}'
  )

  artifacts = [
    a for a in artifacts if excludes is None or a.dep.path not in excludes
  ]
  artifacts = [
    a for a in artifacts if includes is None or a.dep.path in includes
  ]

  return artifacts


def get_deps(api, name):
  """Parse the project's DEPS file at <checkout_root>/<name>/DEPS. Return a list
  of dependency objects with one entry for each dep path."""

  # Parse DEPS file.
  deps_file_path = api.v8.checkout_root / name / 'DEPS'
  deps_content = api.file.read_text(
    f'Read {name}/DEPS', deps_file_path, include_log=False
  )

  local_scope = {}
  global_scope = {
    'Str': lambda str_value: str_value,
    'Var': lambda var_name: local_scope['vars'][var_name],
    'deps_os': {},
  }
  exec(deps_content, global_scope, local_scope)
  deps = sorted(local_scope.get('deps', {}).items())

  deps_json = api.json.dumps(deps, indent=2)
  api.step.active_result.presentation.logs['deps'] = deps_json

  # Map to *Dep instances.
  dep_instances = []
  for path, entry in deps:
    if isinstance(entry, str):
      dep_instances.append(GitDep(api, path, {'url': entry}))
    elif 'url' in entry:
      dep_instances.append(GitDep(api, path, {'url': entry['url']}))
    elif entry['dep_type'] == 'cipd':
      dep_instances.append(CipdDep(api, path, {'packages': entry['packages']}))
    elif entry['dep_type'] == 'gcs':
      dep_instances.append(
        GcsDep(
          api,
          path,
          {
            'bucket': entry['bucket'],
            'objects': [
              {
                'object_name': o['object_name'],
                'sha256sum': o['sha256sum'],
                'size_bytes': o['size_bytes'],
                'generation': o['generation'],
              }
              for o in entry['objects']
            ],
          },
        )
      )

  return dep_instances
