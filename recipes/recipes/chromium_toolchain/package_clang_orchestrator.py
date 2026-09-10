# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Recipe to trigger toolchain packagers with the latest llvm revision."""

from PB.go.chromium.org.luci.buildbucket.proto import build as build_pb
from PB.go.chromium.org.luci.buildbucket.proto import (
  builder_common as builder_common_pb,
)
from PB.go.chromium.org.luci.buildbucket.proto import (
  builds_service as builds_service_pb,
)
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb
from PB.recipe_engine.result import RawResult
from recipe_engine import post_process

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.recipe_engine import buildbucket, step, url


@dataclass
class DEPS(RecipeScriptApi):
  buildbucket: buildbucket.API
  step: step.API
  url: url.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  buildbucket: buildbucket.TEST_API


def RunSteps(api: DEPS):
  repo = (
    'https://chromium.googlesource.com/external/github.com/llvm/llvm-project'
  )
  head_url = repo + '/+/refs/heads/main?format=JSON'
  rev = api.url.get_json(
    head_url,
    step_name='get the latest llvm revision',
    log=True,
    strip_prefix=api.url.GERRIT_JSON_PREFIX,
    default_test_data={'commit': 'abcd'},
  ).output['commit']
  rev_url = '%s/+/%s' % (repo, rev)
  rev_step = api.step.empty('revision ' + rev)
  rev_step.presentation.links['Gitiles URL'] = rev_url

  packge_builders = [
    'toolchain-packager-linux',
    'toolchain-packager-mac',
    'toolchain-packager-mac-arm',
    'toolchain-packager-windows',
  ]

  requests = [
    api.buildbucket.schedule_request(
      builder=b,
      bucket='official.toolchain',
      can_outlive_parent=False,
      properties={
        'llvm_revision': rev,
      },
    )
    for b in packge_builders
  ]
  builds = api.buildbucket.run(
    requests,
    timeout=6 * 60 * 60 + 60 * 10,  # max execuation timeout + buffer.
    url_title_fn=lambda b: b.builder.builder,
  )

  # Summarize build results.
  failure = []
  infra_failure = []
  summary_md = ''
  for b in builds:
    summary_md += '%s (%s)<br/>' % (
      b.builder.builder,
      common_pb.Status.Name(b.status),
    )
    if b.status == common_pb.SUCCESS:
      pass
    elif b.status == common_pb.INFRA_FAILURE:
      infra_failure.append(b.builder.builder)
    else:
      failure.append(b.builder.builder)

  if any(infra_failure):
    status = common_pb.INFRA_FAILURE
    summary_md = 'Some packagers failed for infra failure<br/>' + summary_md
  elif any(failure):
    status = common_pb.FAILURE
    summary_md = (
      "Some packagers couldn't complete successfully<br/>" + summary_md
    )
  else:
    status = common_pb.SUCCESS
    summary_md = 'All packagers completed successfully<br/>' + summary_md

  return RawResult(status=status, summary_markdown=summary_md)


def GenTests(api: TEST_DEPS):
  yield api.test(
    'full',
    api.post_process(post_process.StatusSuccess),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'failure',
    api.buildbucket.simulated_schedule_output(
      builds_service_pb.BatchResponse(
        responses=[
          dict(schedule_build=build_pb.Build(id=1)),
          dict(schedule_build=build_pb.Build(id=2)),
        ]
      ),
      step_name='buildbucket.run.schedule',
    ),
    api.buildbucket.simulated_collect_output(
      [
        build_pb.Build(
          id=1,
          status=common_pb.Status.SUCCESS,
          builder=builder_common_pb.BuilderID(builder='packager1'),
        ),
        build_pb.Build(
          id=2,
          status=common_pb.Status.FAILURE,
          builder=builder_common_pb.BuilderID(builder='packager2'),
        ),
      ],
      step_name='buildbucket.run.collect',
    ),
    api.expect_status('FAILURE'),
    api.post_process(post_process.StatusFailure),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'infra failure',
    api.buildbucket.simulated_schedule_output(
      builds_service_pb.BatchResponse(
        responses=[
          dict(schedule_build=build_pb.Build(id=1)),
          dict(schedule_build=build_pb.Build(id=2)),
          dict(schedule_build=build_pb.Build(id=3)),
        ]
      ),
      step_name='buildbucket.run.schedule',
    ),
    api.buildbucket.simulated_collect_output(
      [
        build_pb.Build(
          id=1,
          status=common_pb.Status.SUCCESS,
          builder=builder_common_pb.BuilderID(builder='packager1'),
        ),
        build_pb.Build(
          id=2,
          status=common_pb.Status.FAILURE,
          builder=builder_common_pb.BuilderID(builder='packager2'),
        ),
        build_pb.Build(
          id=3,
          status=common_pb.Status.INFRA_FAILURE,
          builder=builder_common_pb.BuilderID(builder='packager3'),
        ),
      ],
      step_name='buildbucket.run.collect',
    ),
    api.expect_status('INFRA_FAILURE'),
    api.post_process(post_process.StatusException),
    api.post_process(post_process.DropExpectation),
  )
