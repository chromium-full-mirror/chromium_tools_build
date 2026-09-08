# Copyright 2019 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Recipe to test v8/node.js integration."""

from recipe_engine.recipe_api import Property
from recipe_engine.post_process import (Filter, SummaryMarkdownRE,
                                        DropExpectation)

from PB.go.chromium.org.luci.buildbucket.proto import (builds_service as
                                                       builds_service_pb2)
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb
from PB.google.rpc import code as rpc_code_pb2


from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import chromium, siso, v8
from RECIPE_MODULES.depot_tools import (
    bot_update,
    gclient,
    gsutil,
    tryserver,
)
from RECIPE_MODULES.infra import zip as zip_module
from RECIPE_MODULES.recipe_engine import (
    buildbucket,
    commit_position,
    context,
    file,
    json,
    path,
    platform,
    properties,
    step,
)


@dataclass
class DEPS(RecipeScriptApi):
  bot_update: bot_update.API
  buildbucket: buildbucket.API
  chromium: chromium.API
  commit_position: commit_position.API
  context: context.API
  file: file.API
  gclient: gclient.API
  gsutil: gsutil.API
  json: json.API
  path: path.API
  platform: platform.API
  properties: properties.API
  siso: siso.API
  step: step.API
  tryserver: tryserver.API
  v8: v8.API
  zip: zip_module.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  buildbucket: buildbucket.TEST_API
  platform: platform.TEST_API
  properties: properties.TEST_API
  siso: siso.TEST_API
  step: step.TEST_API
  v8: v8.TEST_API

PROPERTIES = {
    # Run in debug mode.
    'is_debug': Property(default=False, kind=bool),
    # List of tester names to trigger.
    'triggers': Property(default=None, kind=list),
    # Use V8 ToT (HEAD) revision instead of pinned.
    'v8_tot': Property(default=False, kind=bool),
}

ARCHIVE_PATH = 'chromium-v8/node-%s-rel'
ARCHIVE_LINK = 'https://storage.googleapis.com/%s/%%s' % ARCHIVE_PATH


def run_with_retry(api: DEPS, step_name, step_fun):
  """Runs `step_fun` and retries once on failure.

  Returns: True if a flake has been detected.
  """
  # First try. Be forgiving and bail out if it passes.
  try:
    step_fun(step_name=step_name)
    return False
  except api.step.StepFailure:
    api.step.active_result.presentation.status = api.step.SUCCESS

  # Second try. Let it raise if it fails again.
  step_fun(step_name=step_name + ' (retry)')

  # If the retry didn't raise, we found a flake. We report it in a separate
  # step that's ignored for tree closing.
  step_result = api.step(step_name + ' (flakes)', cmd=None)
  step_result.presentation.status = api.step.FAILURE
  return True


def RunSteps(api: DEPS, is_debug, triggers, v8_tot):
  with api.step.nest('initialization'):
    if is_debug:
      build_config = 'Debug'
      chromium_config = 'node_ci_debug'
    else:
      build_config = 'Release'
      chromium_config = 'node_ci'

    # Set up dependent modules.
    api.chromium.set_config(chromium_config, BUILD_CONFIG=build_config)
    api.gclient.set_config('node_ci')
    api.siso.enable_download_remoteexec_cfg_hook()
    revision = api.buildbucket.gitiles_commit.id or 'HEAD'
    if v8_tot:
      api.gclient.c.revisions['node-ci'] = 'HEAD'
      api.gclient.c.revisions['node-ci/v8'] = revision
      api.gclient.c.got_revision_reverse_mapping['got_revision'] = 'node-ci/v8'
    else:
      api.gclient.c.revisions['node-ci'] = revision

    # Check out.
    with api.context(cwd=api.path.cache_dir / 'builder'):
      update_result = api.bot_update.ensure_checkout()

    source_dir = update_result.source_root.path
    build_dir = api.v8.build_dir(source_dir)
    api.v8.runhooks(source_dir, build_dir)

  with api.step.nest('build'):
    buildtools_installation_path = source_dir.joinpath('buildtools', 'linux64')
    with (api.context(env_prefixes={'PATH': [buildtools_installation_path]}),
          api.chromium.guard_compile(build_dir)):
      if api.siso.enabled:
        api.chromium.c.gn_args.append('use_siso=true')
      api.chromium.run_gn(source_dir, build_dir, use_remoteexec=True)
      raw_result = api.chromium.compile(source_dir, build_dir)
      if raw_result.status != common_pb.SUCCESS:
        return raw_result

  # Archive node executable and trigger performance bots on V8 ToT builders.
  if v8_tot:
    revision = api.bot_update.last_returned_properties['got_revision']
    revision_cp = api.bot_update.last_returned_properties['got_revision_cp']
    _, revision_number = api.commit_position.parse(revision_cp)
    revision_number = str(revision_number)

    with api.step.nest('archive') as parent:
      archive_name = ('node-%s-rel-%s-%s.zip' %
                      (api.platform.name, revision_number, revision))
      zip_file = api.path.cleanup_dir / archive_name

      # Zip build.
      package = api.zip.make_package(build_dir, zip_file)
      package.add_file(build_dir.joinpath('node'), api.path.join('bin', 'node'))
      package.zip('zipping')

      # Upload to google storage bucket.
      api.gsutil.upload(
        zip_file,
        ARCHIVE_PATH % api.platform.name,
        archive_name,
        args=['-a', 'public-read'],
      )

      parent.links['download'] = (
          ARCHIVE_LINK % (api.platform.name, archive_name))

    if triggers:
      api.v8.trigger.buildbucket(
          [(builder_name, {
            'revision': revision,
            'parent_got_revision': revision,
            'parent_got_revision_cp': revision_cp,
            'parent_buildername': api.buildbucket.builder_name,
          }) for builder_name in triggers],
          project='v8-internal',
          bucket='ci')

  # Run tests.
  has_flakes = False
  with api.context(cwd=source_dir / 'node'):
    run_cctest = lambda step_name: api.step(step_name,
                                            [build_dir / 'node_cctest'])
    has_flakes |= run_with_retry(api, 'run cctest', run_cctest)

    suites = [
      ('addons', True),
      ('default', False),
      ('js-native-api', True),
      ('node-api', True),
    ]
    for suite, use_test_root in suites:
      args = [
          '-p',
          'tap',
          '-j8',
          '--mode=%s' % api.chromium.c.build_config_fs.lower(),
          '--flaky-tests',
          'run',
          '--shell',
          build_dir / 'node',
      ]
      if use_test_root:
        args += ['--test-root', build_dir.joinpath('gen', 'node', 'test')]
      run_test = lambda step_name: api.v8.python(
          name=step_name,
          script=api.path.join('tools', 'test.py'),
          args=args + [suite],
      )
      has_flakes |= run_with_retry(api, 'test ' + suite, run_test)

  # Make flakes visible on the waterfall. This is not tracked for tree closing.
  # Ignore flakes on tryjobs.
  if has_flakes and not api.tryserver.is_tryserver:
    raise api.step.StepFailure('Flakes in build')


def _sanitize_nonalpha(*chunks):
  return '_'.join(
      ''.join(c if c.isalnum() else '_' for c in text)
      for text in chunks
  )


def GenTests(api: TEST_DEPS):
  def test(buildername, platform, is_trybot=False, suffix='', status='SUCCESS',
           **properties):
    buildbucket_kwargs = {
        'project': 'v8',
        'git_repo': 'https://chromium.googlesource.com/v8/node-ci',
        'builder': buildername,
        'build_number': 571,
        'revision': 'a' * 40,
    }
    if is_trybot:
      properties_fn = api.properties.tryserver
      buildbucket_fn = api.buildbucket.try_build
      buildbucket_kwargs['change_number'] = 456789
      buildbucket_kwargs['patch_set'] = 12
    else:
      properties_fn = api.properties.generic
      buildbucket_fn = api.buildbucket.ci_build
    return api.test(
        _sanitize_nonalpha('full', buildername) + suffix,
        properties_fn(**properties),
        buildbucket_fn(**buildbucket_kwargs),
        api.platform(platform, 64),
        api.siso.properties(),
        api.v8.hide_infra_steps(),
        status=status,
    )

  # Test CI builder on node-ci group.
  yield test(
      'Node-CI Foobar',
      platform='linux',
  ) + api.post_process(Filter('initialization.bot_update')
  )

  # Test try builder on node-ci group.
  yield test(
      'node_ci_foobar_rel',
      platform='linux',
      is_trybot=True,
  )

  # Test CI builder on V8 group.
  yield test(
      'V8 Foobar',
      platform='linux',
      triggers=['v8_foobar_perf'],
      v8_tot=True,
  )

  # Test CI builder on V8 group.
  yield (test(
      'V8 Foobar',
      platform='linux',
      suffix='_trigger_fail',
      triggers=['v8_foobar_perf'],
      v8_tot=True,
      status='INFRA_FAILURE',
  ) + api.buildbucket.simulated_schedule_output(
      builds_service_pb2.BatchResponse(
          responses=[
              dict(
                  error=dict(
                      code=rpc_code_pb2.PERMISSION_DENIED,
                      message='foobar',
                  ))
          ],),
      step_name='trigger',
  ) + api.post_process(Filter('trigger', '$result')))

  # Test CI builder on V8 group with consistent test failures.
  yield (test(
      'V8 Foobar',
      platform='linux',
      suffix='_test_failure',
      triggers=['v8_foobar_perf'],
      v8_tot=True,
      status='FAILURE',
  ) + api.step_data('test default', retcode=1) +
         api.step_data('test default (retry)', retcode=1) +
         api.post_process(Filter('test default', 'test default (retry)')))

  # Test CI builder on V8 group with flakes.
  yield (test(
      'V8 Foobar',
      platform='linux',
      suffix='_flake',
      triggers=['v8_foobar_perf'],
      v8_tot=True,
      status='FAILURE',
  ) + api.step_data('test default', retcode=1) +
         api.post_process(SummaryMarkdownRE, 'Flakes in build') +
         api.post_process(
             Filter('test default', 'test default (retry)',
                    'test default (flakes)')))

  # Test that flakes are ignored on trybot.
  yield (
      test(
          'flakes_on_trybot',
          platform='linux',
          is_trybot=True,
      ) +
      # When this step fails it is retried. The retry's test data passes by
      # default, which is reported as a flake.
      api.step_data('test default', retcode=1) +
      api.post_process(DropExpectation)
  )

  yield (test(
      'compile_failure',
      platform='linux',
      is_trybot=True,
      status='FAILURE',
  ) + api.step_data('build.compile', retcode=1) +
         api.post_process(DropExpectation))

  yield (
    test(
      'debug_mode',
      platform='linux',
      is_debug=True,
    ) +
    api.post_process(Filter('build.gn'))
  )

  yield (test(
      'node_ci_foobar_rel_rbe',
      platform='linux',
      v8_tot=True,
      **{'$build/v8': {
          'use_remoteexec': True
      }}) + api.siso.properties() +
         api.post_process(Filter('initialization.bot_update', 'build.gn')))
