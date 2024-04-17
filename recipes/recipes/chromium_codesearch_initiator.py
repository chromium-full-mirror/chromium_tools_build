# Copyright 2018 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""A recipe for picking and tagging a stable revision for chromium/src.

This recipe picks a commit of codesearch/chromium/src at HEAD, adds a ref to
it so it won't be garabage collected once a new synthetic commit is created
and then triggers the other codesearch recipes with both the chosen commit hash
and the hash to the parent commit as parameters. This ensures that codesearch
index packs (used to generate xrefs) are all generated from the same revision,
but are marked by kythe with the synthetic commit hash so cross references can
linked by commit hash.
"""

from PB.recipes.build.chromium_codesearch_initiator import InputProperties

PROPERTIES = InputProperties

DEPS = [
    'depot_tools/git',
    'recipe_engine/buildbucket',
    'recipe_engine/context',
    'recipe_engine/file',
    'recipe_engine/path',
    'recipe_engine/properties',
    'recipe_engine/raw_io',
    'recipe_engine/scheduler',
    'recipe_engine/step',
    'recipe_engine/time',
    'recipe_engine/url',
]


def RunSteps(api, properties):
  env = {
      # Turn off the low speed limit, since checkout will be long.
      'GIT_HTTP_LOW_SPEED_LIMIT': '0',
      'GIT_HTTP_LOW_SPEED_TIME': '0',
  }

  checkout_dir = api.path.cache_dir.join('builder')
  if not api.file.glob_paths('Check for existing checkout', checkout_dir,
                             'src'):
    with api.context(cwd=checkout_dir, env=env):
      api.git('clone', '--progress', properties.source_repo, 'src')

  api.path.checkout_dir = checkout_dir.join('src')
  with api.context(cwd=api.path.checkout_dir, env=env):
    # Discard any commits from previous runs.
    api.git('reset', '--hard', 'HEAD')

    api.git('fetch')

    mirror_hash = api.git(
        'rev-parse',
        'FETCH_HEAD',
        name='fetch mirror hash',
        stdout=api.raw_io.output_text()).stdout.strip()

    mirror_unix_timestamp = int(
        api.git(
            'log',
            '-1',
            '--format=%ct',
            'FETCH_HEAD',
            name='fetch mirror timestamp',
            stdout=api.raw_io.output_text()).stdout.strip())

    commit_hash = api.git(
        'rev-parse',
        'FETCH_HEAD^',
        name='fetch source hash',
        stdout=api.raw_io.output_text()).stdout.strip()

    unix_timestamp = int(
        api.git(
            'log',
            '-1',
            '--format=%ct',
            'FETCH_HEAD^',
            name='fetch source timestamp',
            stdout=api.raw_io.output_text()).stdout.strip())

    # The head of source_repo will be lost the next time a synthetic commit
    # is added to it. Add a ref to the current head so that it doesn't get
    # garbage collected, and references to it in codesearch links stay valid.
    api.git('push', properties.source_repo,
            mirror_hash + ':refs/kythe/' + commit_hash)

    # Trigger the codesearch builders in the same project.
    api.scheduler.emit_trigger(
        api.scheduler.BuildbucketTrigger(
            properties={
                'root_solution_revision': commit_hash,
                'root_solution_revision_timestamp': unix_timestamp,
                'codesearch_mirror_revision': mirror_hash,
                'codesearch_mirror_revision_timestamp': mirror_unix_timestamp
            }),
        project=api.buildbucket.build.builder.project,
        jobs=properties.builders)


def GenTests(api):
  platforms = ('android', 'chromiumos', 'fuchsia', 'lacros', 'linux', 'mac',
               'win')
  yield api.test(
      'basic',
      api.buildbucket.generic_build(project='infra'),
      api.properties(
          builders=['codesearch-gen-chromium-%s' % p for p in platforms],
          source_repo=(
              'https://chromium.googlesource.com/codesearch/chromium/src'),
      ),
      api.step_data('fetch mirror hash',
                    api.raw_io.stream_output_text('a' * 40, stream='stdout')),
      api.step_data('fetch mirror timestamp',
                    api.raw_io.stream_output_text('100', stream='stdout')),
      api.step_data('fetch source hash',
                    api.raw_io.stream_output_text('b' * 40, stream='stdout')),
      api.step_data('fetch source timestamp',
                    api.raw_io.stream_output_text('50', stream='stdout')),
  )
