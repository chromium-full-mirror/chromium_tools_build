# Copyright 2013 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from RECIPE_MODULES.depot_tools.gclient import CONFIG_CTX
from RECIPE_MODULES.depot_tools.gclient.config import ChromiumGitURL


@CONFIG_CTX()
def v8_bare(c):
  soln = c.solutions.add()
  soln.name = 'v8'
  soln.url = ChromiumGitURL(c, 'v8', 'v8')


@CONFIG_CTX(includes=['v8_bare'])
def v8(c):
  c.got_revision_reverse_mapping['got_revision'] = 'v8'
  # Needed to get the testers to properly sync the right revision.
  # TODO(infra): Upload full buildspecs for every build to isolate and then use
  # them instead of this gclient garbage.
  c.parent_got_revision_mapping['parent_got_revision'] = 'got_revision'

  p = c.repo_path_map
  p['https://chromium.googlesource.com/chromium/deps/icu'] = (
    'v8/third_party/icu',
    'HEAD',
  )
  p['https://chromium.googlesource.com/catapult'] = (
    'v8/third_party/catapult',
    'HEAD',
  )


@CONFIG_CTX(includes=['v8'])
def v8_ios(c):
  c.target_os.add('ios')


@CONFIG_CTX(includes=['v8'])
def v8_with_bazel(c):
  c.solutions[0].custom_vars['download_prebuilt_bazel'] = True


@CONFIG_CTX()
def node_ci(c):
  soln = c.solutions.add()
  soln.name = 'node-ci'
  soln.url = ChromiumGitURL(c, 'v8', 'node-ci')
  c.got_revision_reverse_mapping['got_node_js_revision'] = 'node-ci/node'
  c.got_revision_reverse_mapping['got_v8_revision'] = 'node-ci/v8'
  c.got_revision_reverse_mapping['got_node_ci_revision'] = 'node-ci'
  c.repo_path_map['https://chromium.googlesource.com/v8/v8'] = (
    'node-ci/v8',
    'HEAD',
  )

  # Default to node-ci-centric revisions in UI.
  c.got_revision_reverse_mapping['got_revision'] = 'node-ci'
