# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from . import commons

CV_RUN_STATUS_FAILED = 66
TRYJOB_STATUS_FAILED_PERMANENTLY = 3


def find_unexpected_results_in_last_cv_attempt(api, cl, test_regex):
  search_project = cl.project.split('/')[1]
  search_cl = (cl.host, cl.number)
  cv_runs = api.change_verifier.search_runs(search_project, search_cl)

  if not cv_runs:
    api.step.empty('No CV runs found')
    return
  last_cv_run = cv_runs[0]
  if last_cv_run.mode not in ['FULL_RUN', 'DRY_RUN']:
    api.step.empty(f'Last CV run mode is unsupported: {last_cv_run.mode}.')
    return
  if not last_cv_run.end_time.ToSeconds():
    api.step.empty('Last CV run has not finished yet.')
    return
  if last_cv_run.status != CV_RUN_STATUS_FAILED:
    api.step.empty('Last CV run did not fail.')
    return
  if last_cv_run.cls[0].patchset != cl.last_patch_number:
    api.step.empty('Last CV run was not for the latest patchset.')
    return
  api.step.empty('Last CV run failed.')
  buid_ids = [
    tryjob.result.buildbucket.id
    for tryjob in last_cv_run.tryjobs
    if tryjob.result.status == TRYJOB_STATUS_FAILED_PERMANENTLY
  ]
  results = api.resultdb.query(
    inv_ids=[f'build-{id}' for id in buid_ids],
    variants_with_unexpected_results=True,
    test_regex=test_regex,
  )
  if not results:
    api.step.empty('No unexpected results found.')
  return results


def test262_update_status_file(api, roller, cl):
  unexpected_results = find_unexpected_results_in_last_cv_attempt(
    api,
    cl,
    test_regex='//test262/.*',
  )
  if not unexpected_results:
    commons.mark_as_reported(api, roller, cl)
    return

  new_status_lines = collect_new_test_exceptions(
    api, roller, cl, unexpected_results
  )

  work_dir = api.path.mkdtemp()
  with api.context(cwd=work_dir):
    cl.prepare_local_checkout()
    update_test262_status_file(api, work_dir, new_status_lines)
    api.v8.git_output('commit', '-am', 'status file patch')
    api.v8.git_output('cl', 'upload', '-f', '--bypass-hooks', '--cq-dry-run')
    cl.set_tag(
      'test262_status_file_patched',
      'Mark CL as patched with new test262 status file',
    )
    cl.add_backlink_comment()
    commons.just_fail(
      api,
      roller,
      cl,
      title='CL needs review',
      message='Please review status file patch!',
    )


def collect_new_test_exceptions(api, roller, cl, unexpected_results):
  test_names = set()
  for inv in unexpected_results.values():
    for result in inv.test_results:
      test_names.add(test_name_in_status_file(result.test_id))
  test_names = sorted(test_names)

  api.step.empty(f'Found {len(test_names)} tests failing')

  return (
    ['', '####', '# Roll-watcher patch', '[ALWAYS, {']
    + [f"  '{test_name}': [SKIP]," for test_name in test_names]
    + ['}],', '# End roll-watcher patch', '####']
  )


def test_name_in_status_file(testId):
  return testId.split('//')[1].split('test262/')[1]


def update_test262_status_file(api, work_dir, new_status_lines):
  test262_status_path = work_dir.joinpath('test', 'test262', 'test262.status')
  status_file_lines = api.file.read_text(
    'Read test262 status file', test262_status_path
  ).splitlines()
  status_lines_before_eof = status_file_lines[:-1]
  eof_status_lines = status_file_lines[-1:]
  assert eof_status_lines == [']'], (
    f'Unexpected status file eof. {eof_status_lines}'
  )
  eof_status_lines.append('')  # Adjusting for stripped empty line.
  api.file.write_text(
    'Write test262 status file',
    test262_status_path,
    '\n'.join(status_lines_before_eof + new_status_lines + eof_status_lines),
  )
