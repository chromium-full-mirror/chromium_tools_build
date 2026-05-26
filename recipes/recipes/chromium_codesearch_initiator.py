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
from recipe_engine import post_process

import datetime

PROPERTIES = InputProperties

DEPS = [
    'depot_tools/git',
    'depot_tools/gitiles',
    'recipe_engine/buildbucket',
    'recipe_engine/cipd',
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


def RevisionFromGob(api, properties):
  commits, _ = api.gitiles.log(
      properties.source_repo,
      'refs/heads/main',
      limit=2,
      step_name='fetch main revision',
  )

  props = {}
  if properties.no_synthetic_commit:
    props["root_solution_revision"] = commits[0]["commit"].strip()
    props["root_solution_revision_timestamp"] = int(
        datetime.datetime.strptime(
            commits[0]["committer"]["time"].strip(),
            "%a %b %d %H:%M:%S %Y").replace(
                tzinfo=datetime.timezone.utc).timestamp())

  else:
    props["codesearch_mirror_revision"] = commits[0]["commit"].strip()
    props["codesearch_mirror_revision_timestamp"] = int(
        datetime.datetime.strptime(
            commits[0]["committer"]["time"].strip(),
            "%a %b %d %H:%M:%S %Y").replace(
                tzinfo=datetime.timezone.utc).timestamp())
    props["root_solution_revision"] = commits[1]["commit"].strip()
    props["root_solution_revision_timestamp"] = int(
        datetime.datetime.strptime(
            commits[1]["committer"]["time"].strip(),
            "%a %b %d %H:%M:%S %Y").replace(
                tzinfo=datetime.timezone.utc).timestamp())

  return props


def RevisionFromGit(api, properties):
  env = {
      # Turn off the low speed limit, since checkout will be long.
      'GIT_HTTP_LOW_SPEED_LIMIT': '0',
      'GIT_HTTP_LOW_SPEED_TIME': '0',
  }

  checkout_dir = api.path.cache_dir / 'builder'
  if not api.file.glob_paths('Check for existing checkout', checkout_dir,
                             'src'):
    with api.context(cwd=checkout_dir, env=env):
      api.git('clone', '--progress', properties.source_repo, 'src')

  with api.context(cwd=checkout_dir / 'src', env=env):
    # Discard any commits from previous runs.
    api.git('reset', '--hard', 'HEAD')

    api.git('fetch')

    props = {}
    if properties.no_synthetic_commit:
      props['root_solution_revision'] = api.git(
          'rev-parse',
          'FETCH_HEAD',
          name='fetch source hash',
          stdout=api.raw_io.output_text()).stdout.strip()

      props['root_solution_revision_timestamp'] = int(
          api.git(
              'log',
              '-1',
              '--format=%ct',
              'FETCH_HEAD',
              name='fetch source timestamp',
              stdout=api.raw_io.output_text()).stdout.strip())
    else:
      props['codesearch_mirror_revision'] = api.git(
          'rev-parse',
          'FETCH_HEAD',
          name='fetch mirror hash',
          stdout=api.raw_io.output_text()).stdout.strip()

      props['codesearch_mirror_revision_timestamp'] = int(
          api.git(
              'log',
              '-1',
              '--format=%ct',
              'FETCH_HEAD',
              name='fetch mirror timestamp',
              stdout=api.raw_io.output_text()).stdout.strip())

      props['root_solution_revision'] = api.git(
          'rev-parse',
          'FETCH_HEAD^',
          name='fetch source hash',
          stdout=api.raw_io.output_text()).stdout.strip()

      props['root_solution_revision_timestamp'] = int(
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
      api.git(
          'push', properties.source_repo,
          f'{props["codesearch_mirror_revision"]}:refs/kythe/{props["root_solution_revision"]}'
      )

      return props


def RunSteps(api, properties):
  checkout_dir = api.path.cache_dir / 'builder'
  props = (
      RevisionFromGob(api, properties) if properties.fetch_revision_from_gob
      else RevisionFromGit(api, properties))

  if api.buildbucket.build.builder.builder == 'codesearch-gen-chrome-internal-initiator':
    # Trigger the codesearch builders in the same project.
    # For internal project, do no optimize the workflow
    api.scheduler.emit_trigger(
        api.scheduler.BuildbucketTrigger(properties=props),
        project=api.buildbucket.build.builder.project,
        jobs=properties.builders)
    return

  # Fan out the children codesearch builders and wait for completions.
  build_requests = [
      api.buildbucket.schedule_request(
          builder=b,
          properties=props,
          project=api.buildbucket.build.builder.project,
      ) for b in properties.builders
  ]
  builders = api.buildbucket.schedule(build_requests)
  api.buildbucket.collect_builds([b.id for b in builders],
                                 step_name='wait for children builders',
                                 timeout=14400)

  gcloud_path = checkout_dir / 'gcloudsdk'
  ensure_file = api.cipd.EnsureFile()
  ensure_file.add_package('infra/3pp/tools/gcloud/${platform}',
                          'version:2@463.0.0.chromium.4')
  api.cipd.ensure(gcloud_path, ensure_file)
  with api.context(env_prefixes={'PATH': [gcloud_path / 'bin']}):
    cmd = [
        'gcloud',
        'pubsub',
        'topics',
        'publish',
        'projects/chromium-build-stats/topics/codesearch_luci_notifications',
        f'--message="{api.buildbucket.build.id}"',
    ]
    api.step('notify completion', cmd)


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

  yield api.test(
      'no-synthetic-commit',
      api.buildbucket.generic_build(project='infra'),
      api.properties(
          builders=['codesearch-gen-chromium-%s' % p for p in platforms],
          source_repo=('https://chromium.googlesource.com/chromium/src'),
          no_synthetic_commit=True,
      ),
      api.step_data('fetch source hash',
                    api.raw_io.stream_output_text('b' * 40, stream='stdout')),
      api.step_data('fetch source timestamp',
                    api.raw_io.stream_output_text('50', stream='stdout')),
  )

  yield api.test(
      'internal-basic',
      api.buildbucket.generic_build(
          project='infra', builder='codesearch-gen-chrome-internal-initiator'),
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
      api.post_process(post_process.DoesNotRun, 'wait for children builders'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'basic-revision-from-gob',
      api.buildbucket.generic_build(project='infra'),
      api.properties(
          builders=['codesearch-gen-chromium-%s' % p for p in platforms],
          source_repo=(
              'https://chromium.googlesource.com/codesearch/chromium/src'),
          fetch_revision_from_gob=True,
      ),
      api.step_data('fetch main revision',
                    api.gitiles.make_log_test_data('main', n=2)),
  )

  yield api.test(
      'no-synthetic-commit-revision-from-gob',
      api.buildbucket.generic_build(project='infra'),
      api.properties(
          builders=['codesearch-gen-chromium-%s' % p for p in platforms],
          source_repo=(
              'https://chromium.googlesource.com/codesearch/chromium/src'),
          fetch_revision_from_gob=True,
          no_synthetic_commit=True,
      ),
      api.step_data('fetch main revision',
                    api.gitiles.make_log_test_data('main', n=2)),
  )
