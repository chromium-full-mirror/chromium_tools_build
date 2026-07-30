# Copyright 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Recipe for building and deploying the www.chromium.org static website."""

from recipe_engine.post_process import DropExpectation
from recipe_engine.recipe_api import Property

from PB.recipe_engine import result as result_pb2
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb

from RECIPE_MODULES.depot_tools.gclient import CONFIG_CTX

DEPS = [
    'depot_tools/bot_update',
    'depot_tools/gclient',
    'depot_tools/gerrit',
    'depot_tools/tryserver',
    'recipe_engine/buildbucket',
    'recipe_engine/context',
    'recipe_engine/path',
    'recipe_engine/properties',
    'recipe_engine/raw_io',
    'recipe_engine/runtime',
    'recipe_engine/step',
]


def RunSteps(api):
  api.gclient.set_config('chromium_website')
  with api.context(cwd=api.path.cache_dir / 'builder'):
    update_result = api.bot_update.ensure_checkout()
  api.gclient.runhooks()

  source_dir = update_result.source_root.path
  with api.context(cwd=source_dir):
    npmw_path = source_dir.joinpath('npmw')
    api.step('build', [npmw_path, 'build'])

    if api.m.tryserver.is_tryserver:
      channel_id = 'cl%d-ps%d' % (api.m.tryserver.gerrit_change.change,
                                  api.m.tryserver.gerrit_change.patchset)
      cmd = [npmw_path, 'deploy:preview', channel_id]
    else:
      cmd = [npmw_path, 'deploy:prod']

    out = api.step(
        'deploy', cmd, stdout=api.raw_io.output(add_output_log=True)).stdout
    out = out.decode('utf-8').strip()

    # pylint: disable=line-too-long
    msg = (
        "Deployed site but don't know where.\n"
        "Please [file a bug](http://crbug.com/new?component=1456097&template=1923269)\n"
    )
    # pylint: enable=line-too-long

    for line in out.splitlines():
      if api.m.tryserver.is_tryserver:
        if 'Channel URL' in line:
          for word in line.split():
            if word.startswith('https://chromium-website'):
              msg = 'Preview this change at %s' % word
      else:
        if 'Hosting URL' in line:
          msg = 'Deployed to %s' % line.split()[-1]

  if api.m.tryserver.is_tryserver:
    api.m.gerrit.add_message(
        host=f'https://{api.m.tryserver.gerrit_change.host}',
        change=api.m.tryserver.gerrit_change.change,
        revision=api.m.tryserver.gerrit_change.patchset,
        message=msg,
        automatic_attention_set_update=False)

  return result_pb2.RawResult(
      status=common_pb.SUCCESS,
      summary_markdown=msg,
  )

def GenTests(api):
  # pylint: disable=line-too-long
  yield api.test(
      'presubmit',
      api.buildbucket.try_build(
          project='chromium-website',
          bucket='chromium-website/try',
          builder='chromium-website-try-builder',
          git_repo='https://chromium.googlesource.com/website.git',
          change_number=123456,
          patch_set=7),
      api.step_data(
          'deploy',
          stdout=api.raw_io.output(
              '\x1B[1mChannel URL:\x1B[22m https://chromium-website-staging-d9f4a-cl123456-ps7.web.app [channel id]\n'
          )),
      api.post_process(DropExpectation),
  )
  # pylint: enable=line-too-long

  yield api.test(
      'postsubmit',
      api.step_data(
          'deploy',
          stdout=api.raw_io.output(
              '\x1B[1m Hosting URL:\x1B[22m https://site.web.app\n')),
      api.post_process(DropExpectation),
  )


@CONFIG_CTX()
def chromium_website(c):
  s = c.solutions.add()
  s.name = 'chromium_website'
  s.url = 'https://chromium.googlesource.com/website.git'
