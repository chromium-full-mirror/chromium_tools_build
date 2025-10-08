# Copyright 2022 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations


DEPS = [
    'flaky_reproducer',
    'recipe_engine/json',
    'recipe_engine/step',
]


def RunSteps(api):
  api.flaky_reproducer.check_monorail_comment_posted('123')
  api.flaky_reproducer.post_summary_to_monorail('123', 'summary')


from recipe_engine import post_process


def GenTests(api):

  def generate_issue_result(labels=None):
    if labels is None:
      labels = []
    return {
        "name":
            "projects/chromium/issues/1160019",
        "labels": [{
            "derivation": "EXPLICIT",
            "label": label
        } for label in labels]
    }

  yield api.test(
      'good',
      api.step_data(
          'check_monorail_comment_posted.GetIssue projects/chromium/issues/123',
          api.json.output_stream(generate_issue_result()),
      ),
      api.step_data(
          'post_summary_to_monorail.ModifyIssues projects/chromium/issues/123',
          api.json.output_stream(generate_issue_result()),
      ),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'already-posted',
      api.step_data(
          'check_monorail_comment_posted.GetIssue projects/chromium/issues/123',
          api.json.output_stream(generate_issue_result(['flaky-reproduced'])),
      ),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'issue-not-exists',
      api.step_data(
          'check_monorail_comment_posted.GetIssue projects/chromium/issues/123',
          retcode=5,
      ),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )
