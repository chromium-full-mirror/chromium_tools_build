# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from google.protobuf import timestamp_pb2
from PB.go.chromium.org.luci.buildbucket.proto.build import Build
from PB.go.chromium.org.luci.buildbucket.proto.builder_common import BuilderID
from PB.go.chromium.org.luci.buildbucket.proto.common import (
  FAILURE,
  SUCCESS,
  StringPair,
)
from PB.go.chromium.org.luci.cv.api.v0 import run as run_pb
from PB.go.chromium.org.luci.cv.api.v0 import service_runs as service_runs_pb
from PB.go.chromium.org.luci.cv.api.v0 import tryjob as tryjob_pb

from recipe_engine.recipe_api import Property
from recipe_engine.post_process import DropExpectation, StepSuccess, StepFailure

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import v8_roll_watcher
from RECIPE_MODULES.depot_tools import (
  depot_tools,
  gerrit,
  git,
)
from RECIPE_MODULES.recipe_engine import (
  buildbucket,
  change_verifier,
  context,
  file,
  json,
  path,
  properties,
  proto,
  raw_io,
  resultdb,
  step,
)


@dataclass
class DEPS(RecipeScriptApi):
  buildbucket: buildbucket.API
  change_verifier: change_verifier.API
  context: context.API
  depot_tools: depot_tools.API
  file: file.API
  gerrit: gerrit.API
  git: git.API
  json: json.API
  path: path.API
  properties: properties.API
  proto: proto.API
  raw_io: raw_io.API
  resultdb: resultdb.API
  step: step.API
  v8_roll_watcher: v8_roll_watcher.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  buildbucket: buildbucket.TEST_API
  file: file.TEST_API
  json: json.TEST_API
  properties: properties.TEST_API
  proto: proto.TEST_API
  raw_io: raw_io.TEST_API


PROPERTIES = {
  'watched_rollers': Property(help='Roller descriptors', default=[], kind=list)
}

CV_RUN_STATUS_FAILED = 66
TRYJOB_STATUS_FAILED_PERMANENTLY = 3


def RunSteps(api: DEPS, watched_rollers):
  return api.v8_roll_watcher.process_rollers(watched_rollers)


def GenTests(api: TEST_DEPS):
  default_roller = {
    'name': 'roller',
    'subject': 'Update dependencies',
    'review-host': 'review.googlesource.com',
    'project': 'v8/v8',
    'account': 'autoroll@service-accounts.com',
  }

  test262_importer = {
    'name': 'test262',
    'subject': '[test262] Roll test262',
    'review-host': 'chromium-review.googlesource.com',
    'project': 'v8/v8',
    'account': 'liviurau@google.com',
    'criteria': ['-hashtag:test262_status_file_patched'],
    'failure_recovery': ['test262_update_status_file'],
  }

  def roller(roller, **kwargs):
    config = dict(roller)
    config.update(**kwargs)
    return api.properties(watched_rollers=[config])

  def find_fake_cls(roller, **kwargs):
    cl_data = {
      '_number': 123,
      'subject': roller['subject'],
      'project': 'v8/v8',
      'revisions': {
        'last': {
          '_number': 1,
          'fetch': {
            'http': {
              'url': '$git-url',
              'ref': '$ref',
            }
          },
        },
      },
      'hashtags': [],
    }
    cl_data.update(**kwargs)
    return api.override_step_data(
      "Roller: '{}'.gerrit Find open CLs".format(roller['name']),
      api.json.output([cl_data]),
    )

  def find_fake_builds(roller, *builds):
    return api.buildbucket.simulated_search_results(
      list(builds),
      step_name="Roller: '{}'.Checking CL 123.buildbucket.search".format(
        roller['name']
      ),
    )

  def build(build_id, status, builder_name=None, experimental=False):
    exp_tag = StringPair(
      key='cq_experimental', value='true' if experimental else 'false'
    )
    return Build(
      id=build_id,
      status=status,
      builder=BuilderID(builder=builder_name),
      tags=[exp_tag],
    )

  def find_fake_cv(roller, runs=None):
    return api.step_data(
      f"Roller: '{roller['name']}'.Checking CL 123."
      "luci-change-verifier.SearchRuns.request page 1",
      stdout=api.proto.output(service_runs_pb.SearchRunsResponse(runs=runs)),
    )

  def fake_cv_runs(
    mode="FULL_RUN",
    status=CV_RUN_STATUS_FAILED,
    cls=None,
    tryjobs=None,
    end_time=1234,
  ):
    return [
      run_pb.Run(
        id='projects/prj/runs/1',
        mode=mode,
        status=status,
        end_time=timestamp_pb2.Timestamp(seconds=end_time),
        cls=cls,
        tryjobs=tryjobs,
      )
    ]

  def fake_cv_cls(patchset=1):
    return [run_pb.GerritChange(change=123, patchset=patchset)]

  def fake_cv_tryjobs():
    return [
      tryjob_pb.Tryjob(
        result=tryjob_pb.Tryjob.Result(
          status=TRYJOB_STATUS_FAILED_PERMANENTLY,
          buildbucket=tryjob_pb.Tryjob.Result.Buildbucket(
            id=1234,
          ),
        ),
      )
    ]

  def find_fake_unexpected_results(roller, raw_results=None):
    return api.override_step_data(
      f"Roller: '{roller['name']}'.Checking CL 123.rdb query",
      stdout=api.raw_io.output_text(raw_results),
    )

  def fake_test262_status_file():
    return api.override_step_data(
      "Roller: 'test262'.Checking CL 123.Read test262 status file",
      api.file.read_text('\n[\n...\n###\n]\n'),
    )

  yield api.test(
    'no-cls',
    roller(default_roller),
    status='SUCCESS',
  )

  yield api.test(
    'roller-with-stale-cls',
    roller(default_roller),
    find_fake_cls(default_roller),
    status='SUCCESS',
  )

  yield api.test(
    'roll-failing-in-cq-default',
    roller(default_roller),
    find_fake_cls(default_roller),
    find_fake_builds(
      default_roller,
      build(1, SUCCESS),
      build(2, FAILURE),
    ),
    status='FAILURE',
  )

  yield api.test(
    'roll-failing-in-cq-just-pass',
    roller(
      default_roller,
      criteria=['hashtags:sometag'],
      failure_recovery=['just_pass'],
    ),
    find_fake_cls(default_roller),
    find_fake_builds(
      default_roller,
      build(1, SUCCESS),
      build(2, FAILURE),
    ),
  )

  yield api.test(
    'test262_failure_recovery',
    roller(test262_importer),
    find_fake_cls(test262_importer),
    find_fake_builds(
      test262_importer,
      build(1, FAILURE),
      build(2, FAILURE, builder_name='some_v8_builder'),
    ),
    find_fake_cv(
      test262_importer,
      fake_cv_runs(cls=fake_cv_cls(), tryjobs=fake_cv_tryjobs()),
    ),
    find_fake_unexpected_results(
      test262_importer,
      raw_results='{"invocationId": "1", "testResult": '
      '{"testId": "//test262/sometest1//default"}}'
      '\n{"invocationId": "1", "testResult": '
      '{"testId": "//test262/sometest2//default"}}',
    ),
    fake_test262_status_file(),
    api.post_process(
      StepFailure, "Roller: 'test262'.Checking CL 123.CL needs review"
    ),
    status='FAILURE',
  )

  yield api.test(
    'test262_no_failures',
    roller(test262_importer),
    find_fake_cls(test262_importer),
    find_fake_builds(
      test262_importer,
      build(1, FAILURE),
      build(2, FAILURE, builder_name='some_v8_builder'),
    ),
    find_fake_cv(
      test262_importer,
      fake_cv_runs(cls=fake_cv_cls(), tryjobs=fake_cv_tryjobs()),
    ),
    find_fake_unexpected_results(test262_importer),
    api.post_process(
      StepSuccess,
      "Roller: 'test262'.Checking CL 123.No unexpected results found.",
    ),
    api.post_process(DropExpectation),
    status='SUCCESS',
  )

  yield api.test(
    'test262_wrong_patchset',
    roller(test262_importer),
    find_fake_cls(test262_importer),
    find_fake_builds(
      test262_importer,
      build(1, FAILURE),
      build(2, FAILURE, builder_name='some_v8_builder'),
    ),
    find_fake_cv(test262_importer, fake_cv_runs(cls=fake_cv_cls(patchset=2))),
    api.post_process(
      StepSuccess,
      "Roller: 'test262'.Checking CL 123.Last CV run was not for the latest"
      " patchset.",
    ),
    api.post_process(DropExpectation),
    status='SUCCESS',
  )

  yield api.test(
    'test262_wrong_status',
    roller(test262_importer),
    find_fake_cls(test262_importer),
    find_fake_builds(
      test262_importer,
      build(1, FAILURE),
      build(2, FAILURE, builder_name='some_v8_builder'),
    ),
    find_fake_cv(test262_importer, fake_cv_runs(status=-1)),
    api.post_process(
      StepSuccess, "Roller: 'test262'.Checking CL 123.Last CV run did not fail."
    ),
    api.post_process(DropExpectation),
    status='SUCCESS',
  )

  yield api.test(
    'test262_not_finished',
    roller(test262_importer),
    find_fake_cls(test262_importer),
    find_fake_builds(
      test262_importer,
      build(1, FAILURE),
      build(2, FAILURE, builder_name='some_v8_builder'),
    ),
    find_fake_cv(test262_importer, fake_cv_runs(end_time=None)),
    api.post_process(
      StepSuccess,
      "Roller: 'test262'.Checking CL 123.Last CV run has not finished yet.",
    ),
    api.post_process(DropExpectation),
    status='SUCCESS',
  )

  yield api.test(
    'test262_wrong_mode',
    roller(test262_importer),
    find_fake_cls(test262_importer),
    find_fake_builds(
      test262_importer,
      build(1, FAILURE),
      build(2, FAILURE, builder_name='some_v8_builder'),
    ),
    find_fake_cv(test262_importer, fake_cv_runs(mode="UNSUPORTED")),
    api.post_process(
      StepSuccess,
      "Roller: 'test262'.Checking CL 123.Last CV run mode is unsupported: "
      "UNSUPORTED.",
    ),
    api.post_process(DropExpectation),
    status='SUCCESS',
  )

  yield api.test(
    'test262_no_cv_runs',
    roller(test262_importer),
    find_fake_cls(test262_importer),
    find_fake_builds(
      test262_importer,
      build(1, FAILURE),
      build(2, FAILURE, builder_name='some_v8_builder'),
    ),
    find_fake_cv(test262_importer),
    api.post_process(
      StepSuccess, "Roller: 'test262'.Checking CL 123.No CV runs found"
    ),
    api.post_process(DropExpectation),
    status='SUCCESS',
  )
