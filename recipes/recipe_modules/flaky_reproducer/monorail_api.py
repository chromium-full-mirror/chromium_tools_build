# Copyright 2022 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""API for interacting with Monorail RPCs.

This is a simple wrapper for Monorail v3 API used by flaky_reproducer summary
report. It's not exported as a shared recipe_module because we only used very
limited methods in raw JSON without proto.
"""

from __future__ import annotations


class MonorailApi:
  def __init__(self, api):
    """
    Args:
      api (recipe_api.RecipeApi): RecipeApi with injected dependencies.
    """
    self.m = api

  def _run(self, step_name, rpc_endpoint, request_input):
    args = [
      'prpc',
      'call',
      '-use-id-token',
      '-audience',
      'https://monorail-prod.appspot.com',
      'api-dot-monorail-prod.appspot.com',
      rpc_endpoint,
    ]
    result = self.m.step(
      step_name,
      args,
      stdin=self.m.json.input(request_input),
      stdout=self.m.json.output(add_json_log=True),
    )
    result.presentation.logs['json.input'] = self.m.json.dumps(
      request_input, indent=2
    )
    return result.stdout

  @staticmethod
  def chromium_issue_name(issue_id):
    return 'projects/chromium/issues/{0}'.format(issue_id)

  def get_issue(self, issue_name):
    """Query single monorail issue.

    Args:
      issue_name (str): The name of the issue, format:
        projects/<project>/issues/<issue_id>.
    Returns:
      Issue object as dict.
    """
    return self._run(
      'GetIssue {0}'.format(issue_name),
      'monorail.v3.Issues.GetIssue',
      {
        'name': issue_name,
      },
    )

  def modify_issues(self, issue_name, comment_content=None, labels=None):
    """Modify a issue and add a comment.

    Args:
      issue_name (str): The name of the issue, format:
        projects/<project>/issues/<issue_id>.
      comment_content (str): The comment posted to the issue.
      labels (List of str): The labels added to the issue.
    Returns:
      Issue object as dict.
    """
    issue = {'name': issue_name}
    fields_mask = []
    if labels:
      fields_mask.append('labels')
      issue['labels'] = [{'label': v} for v in labels]

    return self._run(
      'ModifyIssues {0}'.format(issue_name),
      'monorail.v3.Issues.ModifyIssues',
      {
        'deltas': [
          {
            'issue': issue,
            'updateMask': ','.join(fields_mask),
          }
        ],
        'commentContent': comment_content,
      },
    )
