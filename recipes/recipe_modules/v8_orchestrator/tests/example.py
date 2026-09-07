# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import json

from PB.go.chromium.org.luci.buildbucket.proto import build as build_pb2
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb
from PB.recipe_engine import result as result_pb2
from recipe_engine.post_process import (DropExpectation, MustRun,
                                        SummaryMarkdown)
from recipe_engine.recipe_api import Property
from google.protobuf import json_format
from google.protobuf import struct_pb2
from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import v8_orchestrator
from RECIPE_MODULES.recipe_engine import (
    buildbucket,
    file,
    json as json_module,
    properties,
    step,
)


@dataclass
class DEPS(RecipeScriptApi):
  buildbucket: buildbucket.API
  file: file.API
  json: json_module.API
  properties: properties.API
  step: step.API
  v8_orchestrator: v8_orchestrator.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  buildbucket: buildbucket.TEST_API
  json: json_module.TEST_API
  properties: properties.TEST_API
  step: step.TEST_API

PROPERTIES = {
    'revision': Property(kind=str, default=None),
    'step_suffix': Property(kind=str, default=None),
    'gerrit_changes': Property(kind=list, default=None),
}


def RunSteps(api: DEPS, revision, step_suffix, gerrit_changes):
  if gerrit_changes is None:
    gerrit_changes = api.v8_orchestrator.INHERIT

  handler = api.v8_orchestrator.create_compilator_handler(
      step_suffix=step_suffix)
  build = handler.trigger_compilator(
      'some-builder', revision=revision, gerrit_changes=gerrit_changes)
  sub_build = handler.launch_compilator_watcher(build)
  return result_pb2.RawResult(
        status=sub_build.status, summary_markdown=sub_build.summary_markdown)


def GenTests(api: TEST_DEPS):
  def subbuild_data(
      summary='All good!',
      status=common_pb.SUCCESS,
      step_name='compilator steps'):
    sub_build = build_pb2.Build(
        id=54321,
        status=status,
        summary_markdown=summary,
        output=dict(
            properties=json_format.Parse(
                api.json.dumps({'prop': 'value'}), struct_pb2.Struct())))
    return api.step_data(step_name, api.step.sub_build(sub_build))

  def StepStdinContains(check, step_odict, step, substr):
    check('stdin for step %s contained %s' % (step, substr), substr
          in step_odict[step].stdin)

  def StepStdinDoesNotContain(check, step_odict, step, substr):
    check('stdin for step %s contained %s' % (step, substr), substr
          not in step_odict[step].stdin)

  yield api.test(
      'basic try',
      api.buildbucket.try_build(builder='v8_foobar'),
      subbuild_data(),
      api.post_check(
          StepStdinContains,
          'trigger compilator',
          (
              '"gerritChanges": [{"change": "123456", "host": "chromium-review.'
              'googlesource.com", "patchset": "7", "project": "project"}]'
          )
      ),
      api.post_process(MustRun, 'trigger compilator'),
      api.post_process(MustRun, 'compilator steps'),
      api.post_process(SummaryMarkdown, 'All good!'),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'basic ci',
      api.buildbucket.ci_build(builder='V8 Foobar'),
      api.properties(revision='abcd'),
      subbuild_data(),
      api.post_check(StepStdinContains, 'trigger compilator', '"bucket": "ci"'),
      api.post_check(StepStdinContains, 'trigger compilator',
                     '"revision": "abcd"'),
      api.post_check(StepStdinDoesNotContain, 'trigger compilator',
                     'gerritChanges'),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'compiler failure',
      api.buildbucket.ci_build(builder='V8 Foobar'),
      api.properties(revision='abcd'),
      subbuild_data('Compile failed', common_pb.FAILURE),
      api.expect_status('FAILURE'),
      api.post_process(SummaryMarkdown, 'Compile failed'),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'no subbuild',
      api.buildbucket.ci_build(builder='V8 Foobar'),
      api.properties(revision='abcd'),
      api.expect_status('INFRA_FAILURE'),
      api.post_process(SummaryMarkdown, 'sub_build missing from step'),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'step suffix',
      api.buildbucket.try_build(builder='v8_foobar'),
      api.properties(step_suffix='primary'),
      subbuild_data(step_name='compilator steps (primary)'),
      api.post_process(MustRun, 'trigger compilator (primary)'),
      api.post_process(MustRun, 'compilator steps (primary)'),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'no gerrit changes try',
      api.buildbucket.try_build(builder='v8_foobar'),
      api.properties(gerrit_changes=[]),
      subbuild_data(),
      api.post_check(StepStdinDoesNotContain, 'trigger compilator',
                     'gerritChanges'),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'custom gerrit changes ci',
      api.buildbucket.ci_build(builder='V8 Foobar'),
      subbuild_data(),
      api.properties(gerrit_changes=[
          common_pb.GerritChange(
              host='chromium-review.googlesource.com',
              project='project',
              change=123456,
              patchset=7,
          ),
      ]),
      api.post_check(
          StepStdinContains, 'trigger compilator',
          ('"gerritChanges": [{"change": "123456", "host": "chromium-review.'
           'googlesource.com", "patchset": "7", "project": "project"}]')),
      api.post_process(DropExpectation),
  )
