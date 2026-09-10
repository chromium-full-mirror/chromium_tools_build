# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from . import commons


def try_update_screenshots(api, roller, cl):
  if cl.has_tag(roller.screenshot_builders_triggered_tag):
    apply_screenshot_patches(api, roller, cl)
  else:
    trigger_screenshot_builders(api, roller, cl)


def apply_screenshot_patches(api, roller, cl):
  with api.step.nest('Apply screenshot patches'):
    if is_unable_to_apply(api, roller, cl):
      return
    work_dir = api.path.mkdtemp()
    with api.context(cwd=work_dir):
      cl.prepare_local_checkout()
      patches_found = [
        apply_patch_from_screenshot_builder(api, roller, builder, cl)
        for builder in roller.screenshot_builders
      ]
      outcome_title = None
      outcome_message = 'No screenshot patches available'
      if any(patches_found):
        api.v8.git_output('commit', '-am', 'update screenshots')
        api.v8.git_output(
          'cl', 'upload', '-f', '--bypass-hooks', '--cq-dry-run'
        )
        cl.set_tag(
          roller.screenshots_applied_tag,
          'Mark CL as patched with new screenshots',
        )
        cl.add_backlink_comment()
        outcome_title = 'CL needs review'
        outcome_message = 'Please review screenshot patch!'
      commons.just_fail(
        api, roller, cl, title=outcome_title, message=outcome_message
      )


def is_unable_to_apply(api, roller, cl):
  screenshot_builds = cl.contains_builds_from(roller.screenshot_builders)
  if not screenshot_builds:
    api.step('No screenshot builds found', [])
    return True
  if any(build.is_in_progress() for build in screenshot_builds):
    api.step('Builders still in progress...', [])
    return True
  if any(build.has_failed() for build in screenshot_builds):
    commons.just_fail(
      api, roller, cl, message='Unable to apply due to failed builder!'
    )
    return True
  return False


def apply_patch_from_screenshot_builder(api, roller, builder, cl):
  with api.step.nest('Apply screenshot patch from {}'.format(builder)):
    patch_dir = api.path.mkdtemp()
    patch_platform = builder.split('_')[-2]
    local_path = patch_dir.joinpath(patch_platform + '.patch')
    api.gsutil.download(
      roller.gs_bucket,
      cl.gs_location(builder),
      local_path,
      name='Download patch from {}'.format(builder),
    )
    if api.file.read_text(
      'read patch for {}'.format(patch_platform), local_path
    ):
      api.v8.git_output('apply', local_path)
      return True
    api.step('Empty patch', [])
    return False


def trigger_screenshot_builders(api, roller, cl):
  if cl.is_cq_in_progress():
    api.step('Builders still in progress...', [])
    return
  if cl.has_mixed_failures(roller.allowed_failure_steps):
    api.step('Some failures do not refer to screenshots...', [])
    cl.set_tag(
      roller.screenshots_unavailable_tag,
      'Tag CL for no screenshots patch available',
    )
    commons.just_fail(
      api, roller, cl, 'Failure cause is other than screenshots'
    )
    return
  api.buildbucket.schedule(
    [
      api.buildbucket.schedule_request(
        project=roller.project.split('/')[1],
        bucket='try',
        builder=builder_name,
        gerrit_changes=[cl.as_gerrit_obj],
      )
      for builder_name in roller.screenshot_builders
    ],
    step_name='Trigger screenshots builders',
  )
  cl.set_tag(
    roller.screenshot_builders_triggered_tag,
    'Tag CL for later screenshots retrieval',
  )
