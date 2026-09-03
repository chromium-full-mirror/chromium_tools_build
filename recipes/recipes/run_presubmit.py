# Copyright (c) 2013 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from PB.recipe_engine import result as result_pb2
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb2

from recipe_engine import post_process
import textwrap

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import (
    angle,
    libyuv,
    v8,
    webrtc,
)
from RECIPE_MODULES.depot_tools import (
    bot_update,
    gclient,
    git,
    presubmit,
    tryserver,
)
from RECIPE_MODULES.recipe_engine import (
    buildbucket,
    context,
    cv,
    file,
    findings,
    json,
    path,
    platform,
    properties,
    resultdb,
    step,
)


@dataclass
class DEPS(RecipeScriptApi):
  angle: angle.API
  bot_update: bot_update.API
  buildbucket: buildbucket.API
  context: context.API
  cv: cv.API
  file: file.API
  findings: findings.API
  gclient: gclient.API
  git: git.API
  json: json.API
  libyuv: libyuv.API
  path: path.API
  platform: platform.API
  presubmit: presubmit.API
  properties: properties.API
  resultdb: resultdb.API
  step: step.API
  tryserver: tryserver.API
  v8: v8.API
  webrtc: webrtc.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  bot_update: bot_update.TEST_API
  buildbucket: buildbucket.TEST_API
  context: context.TEST_API
  cv: cv.TEST_API
  gclient: gclient.TEST_API
  git: git.TEST_API
  json: json.TEST_API
  path: path.TEST_API
  platform: platform.TEST_API
  presubmit: presubmit.TEST_API
  properties: properties.TEST_API
  resultdb: resultdb.TEST_API
  step: step.TEST_API
  tryserver: tryserver.TEST_API


def _limitSize(message_list, char_limit=450):
  """Returns a list of strings within a certain character length.

  Args:
     * message_list (List[str]) - The message to truncate as a list
       of lines (without line endings).
  """
  hint = ('**The complete output can be'
          ' found at the bottom of the presubmit stdout.**')
  char_count = 0
  for index, message in enumerate(message_list):
    char_count += len(message)
    if char_count > char_limit:
      total_errors = len(message_list)
      oversized_msg = (
          '**Error size > %d chars, there are %d more error(s) (%d total)**'
      ) % (char_limit, total_errors - index, total_errors)
      if index == 0:
        # Show at minimum part of the first error message
        first_message = message_list[index].replace('\n\n', '\n')
        return ['\n\n'.join(_limitSize(first_message.splitlines()))]
      return message_list[:index] + [oversized_msg, hint]
  return message_list


# Make sure backslash is escaped first so that backslashes added to escape other
# characters are not themselves escaped
_MARKDOWN_TRANSLATION_TABLE = str.maketrans(
    # Escape characters that markdown inteprets as some kind of formatting
    {c: '\\' + c for c in '\\`*_{}[]()#+-.!'}
    # markdown represents line breaks 2 spaces
    # replacing the \n with \n\n because \n gets replaced with an empty space.
    # This way it will work on both markdown and plain text.
    | {'\n': '\n\n'})


def _translateTextToMarkdown(s):
  return s.translate(_MARKDOWN_TRANSLATION_TABLE)


def _createSummaryMarkdown(step_json):
  """Returns a string with data on errors, warnings, and notifications.

  Extracts the number of errors, warnings and notifications
  from the dictionary(step_json).

  Then it lists all the errors line by line.

  Args:
      * step_json = {
        'errors': [
          {
            'message': string,
            'long_text': string,
            'items: [string],
            'fatal': boolean
          }
        ],
        'notifications': [
          {
            'message': string,
            'long_text': string,
            'items: [string],
            'fatal': boolean
          }
        ],
        'warnings': [
          {
            'message': string,
            'long_text': string,
            'items: [string],
            'fatal': boolean
          }
        ]
      }
  """
  errors = step_json['errors']
  warnings = step_json['warnings']
  notifications = step_json['notifications']

  if not (errors or warnings or notifications):
    return ''

  def maybePlural(count, noun):
    return ('1 %s' % noun) if count == 1 else ('%d %ss' % (count, noun))

  description = 'There are %s, %s, and %s.\n\nHere are the errors:' % (
      maybePlural(len(errors), 'error'),
      maybePlural(len(warnings), 'warning'),
      maybePlural(len(notifications), 'notification'),
  )
  error_messages = []

  for error in errors:
    error_messages.append('**ERROR**\n\n%s\n\n%s' % (
        _translateTextToMarkdown(error['message']),
        _translateTextToMarkdown(error['long_text']),
    ))

  error_messages = _limitSize(error_messages)
  # Description is not counted in the total message size.
  # It is inserted afterward to ensure it is the first message seen.
  error_messages.insert(0, description)
  if warnings or notifications:
    error_messages.append('To see notifications and warnings,'
                          ' look at the stdout of the presubmit step.')
  return '\n\n'.join(error_messages)


def _RunStepsInternal(api):
  repo_name = api.properties.get('repo_name')

  # TODO(nodir): remove repo_name and repository_url properties.
  # They are redundant with api.tryserver.gerrit_change_repo_url.
  gclient_config = None
  if repo_name:
    api.gclient.set_config(repo_name)
  else:
    gclient_config = api.gclient.make_config()
    solution = gclient_config.solutions.add()
    solution.url = api.properties.get('repository_url',
                                      api.tryserver.gerrit_change_repo_url)
    # Solution name shouldn't matter for most users, particularly if there is no
    # DEPS file, but if someone wants to override it, fine.
    solution.name = api.properties.get('solution_name', 's')
    gclient_config.got_revision_mapping[solution.name] = 'got_revision'

  update_result = api.bot_update.ensure_checkout(gclient_config=gclient_config)
  got_revision_properties = api.bot_update.get_project_revision_properties(
      # Replace path.sep with '/', since most recipes are written assuming '/'
      # as the delimiter. This breaks on windows otherwise.
      update_result.patch_root.name.replace(api.path.sep, '/'),
      gclient_config or api.gclient.c)
  upstream = update_result.properties.get(got_revision_properties[0])

  patch_dir = update_result.patch_root.path
  with api.context(cwd=patch_dir):
    # TODO(hinoka): Extract email/name from issue?
    api.git(
        '-c',
        'user.email=commit-bot@chromium.org',
        '-c',
        'user.name=The Commit Bot',
        '-c',
        'diff.ignoreSubmodules=all',
        'commit',
        '-a',
        '-m',
        'Committed patch',
        name='commit-git-patch',
        infra_step=False)

  if api.properties.get('runhooks'):
    with api.context(cwd=update_result.source_root.path):
      api.gclient.runhooks()

  presubmit_args = [
      '--issue',
      api.tryserver.gerrit_change.change,
      '--patchset',
      api.tryserver.gerrit_change.patchset,
      '--gerrit_url',
      'https://%s' % api.tryserver.gerrit_change.host,
      '--gerrit_project', api.tryserver.gerrit_change.project,
      '--gerrit_branch', api.tryserver.gerrit_change_target_ref,
      '--gerrit_fetch',
  ]
  if api.cv.active and api.cv.run_mode == api.cv.DRY_RUN:
    presubmit_args.append('--dry_run')

  presubmit_args.extend([
      '--root',
      patch_dir,
      '--commit',
      '--verbose',
      '--verbose',
      '--skip_canned',
      'CheckTreeIsOpen',
      '--upstream',
      upstream,  # '' if not in bot_update mode.
  ])

  env = {}
  if repo_name in ['build', 'build_internal']:
    # This should overwrite the existing pythonpath which includes references to
    # the local build checkout (but the presubmit scripts should only pick up
    # the scripts from presubmit_build checkout).
    env['PYTHONPATH'] = ''

  raw_result = result_pb2.RawResult()
  with api.context(env=env):
    # 8 minutes seems like a reasonable upper bound on presubmit timings.
    # According to event mon data we have, it seems like anything longer than
    # this is a bug, and should just instant fail.
    #
    # https://crbug.com/917479 This is a problem on luci-py, bump to 15
    # minutes.
    default_timeout = 900 if repo_name == 'luci_py' else 480
    timeout = api.properties.get('timeout') or default_timeout
    # ok_ret='any' causes all exceptions to be ignored in this step
    presubmit_step = api.presubmit(
        *presubmit_args, timeout=timeout, ok_ret='any')
    if presubmit_step.exc_result.retcode != 0:
      presubmit_step.presentation.status = 'FAILURE'
    # Set recipe result values
    if (step_json := presubmit_step.json.output):
      raw_result.summary_markdown = _createSummaryMarkdown(step_json)
      if api.tryserver.is_tryserver and api.resultdb.enabled:
        api.presubmit.upload_findings_from_result(step_json)

    if presubmit_step.exc_result.retcode == 0:
      raw_result.status = common_pb2.SUCCESS
      return raw_result

    if api.step.active_result.exc_result.had_timeout:
      # TODO(iannucci): Shouldn't we also mark failure on timeouts?
      raw_result.status = common_pb2.FAILURE
      summary = []
      if raw_result.summary_markdown:
        summary.append(raw_result.summary_markdown)
      summary.append('Timeout occurred during presubmit step.')
      raw_result.summary_markdown = '\n\n'.join(summary)
    elif presubmit_step.exc_result.retcode == 1:
      raw_result.status = common_pb2.FAILURE
      api.tryserver.set_test_failure_tryjob_result()
    else:
      raw_result.status = common_pb2.INFRA_FAILURE
      api.tryserver.set_invalid_test_results_tryjob_result()
    # Handle unexpected errors not caught by json output
    if raw_result.summary_markdown == '':
      raw_result.status = common_pb2.INFRA_FAILURE
      raw_result.summary_markdown = (
          'Something unexpected occurred while running presubmit checks.'
          ' Please [file a bug](https://crbug.com/new?component=1456211)')
  return raw_result


def RunSteps(api: DEPS):
  safe_buildername = ''.join(
      c if c.isalnum() else '_' for c in api.buildbucket.builder_name)
  # HACK to avoid invalidating caches when PRESUBMIT running
  # on special infra/config branch, which is typically orphan.
  if api.tryserver.gerrit_change_target_ref == 'refs/heads/infra/config':
    safe_buildername += '_infra_config'
  cwd = api.path.cache_dir.joinpath('builder', safe_buildername)
  api.file.ensure_directory('ensure builder cache dir', cwd)

  with api.context(cwd=cwd):
    return _RunStepsInternal(api)


def GenTests(api: TEST_DEPS):
  yield api.test(
      'expected_tryjob',
      api.buildbucket.try_build(
          builder='chromium_presubmit',
          git_repo='https://chromium.googlesource.com/chromium/src'),
      api.properties(repo_name='chromium'),
      api.step_data('presubmit', api.json.output({})),
  )

  REPOSITORIES = [
      ('angle', 'https://chromium.googlesource.com/angle/angle'),
      ('build', 'https://chromium.googlesource.com/chromium/tools/build'),
      ('catapult', 'https://chromium.googlesource.com/catapult'),
      ('chromium', 'https://chromium.googlesource.com/chromium/src'),
      ('depot_tools',
       'https://chromium.googlesource.com/chromium/tools/depot_tools'),
      ('gyp', 'https://chromium.googlesource.com/external/gyp'),
      ('nacl',
       'https://chromium.googlesource.com/native_client/src/native_client'),
      ('openscreen', 'https://chromium.googlesource.com/openscreen'),
      ('pdfium', 'https://pdfium.googlesource.com/pdfium'),
      ('skia', 'https://skia.googlesource.com/skia'),
      ('v8', 'https://chromium.googlesource.com/v8/v8'),
      ('webpagereplay', 'https://chromium.googlesource.com/webpagereplay'),
      ('webports', 'https://chromium.googlesource.com/webports'),
      ('webrtc', 'https://webrtc.googlesource.com/src'),
  ]
  for repo_name, url in REPOSITORIES:
    yield api.test(
        repo_name,
        api.buildbucket.try_build(
            builder='%s_presubmit' % repo_name,
            git_repo=url),
        api.properties(repo_name=repo_name),
        api.step_data(
            'presubmit',
            api.json.output({
                'errors': [],
                'notifications': [],
                'warnings': []
            })),
    )

  yield api.test(
      'chromium_timeout',
      api.buildbucket.try_build(
          builder='chromium_presubmit',
          git_repo='https://chromium.googlesource.com/chromium/src'),
      api.properties(repo_name='chromium'),
      api.step_data(
          'presubmit',
          api.json.output({
              'errors': [],
              'notifications': [],
              'warnings': []
          }),
          times_out_after=60 * 20),
      api.expect_status('FAILURE'),
      api.post_process(post_process.SummaryMarkdown,
                       'Timeout occurred during presubmit step.'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'chromium_timeout_with_error',
      api.buildbucket.try_build(
          builder='chromium_presubmit',
          git_repo='https://chromium.googlesource.com/chromium/src'),
      api.properties(repo_name='chromium'),
      api.step_data(
          'presubmit',
          api.json.output({
              'errors': [{
                  'message': 'Missing LGTM',
                  'long_text': 'Here are some suggested OWNERS: fake@',
                  'items': [],
                  'fatal': True
              }],
              'notifications': [],
              'warnings': []
          }),
          times_out_after=60 * 20),
      api.expect_status('FAILURE'),
      api.post_process(
          post_process.SummaryMarkdown,
          textwrap.dedent('''
              There are 1 error, 0 warnings, and 0 notifications.

              Here are the errors:

              **ERROR**

              Missing LGTM

              Here are some suggested OWNERS: fake@

              Timeout occurred during presubmit step.
              ''').strip()),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'chromium_long_running',
      api.buildbucket.try_build(
          builder='chromium_presubmit',
          git_repo='https://chromium.googlesource.com/chromium/src'),
      api.properties(timeout=60 * 20 + 5, repo_name='chromium'),
      api.step_data(
          'presubmit',
          api.json.output({
              'errors': [],
              'notifications': [],
              'warnings': []
          }),
          times_out_after=60 * 20),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'chromium_dry_run',
      api.cv(run_mode=api.cv.DRY_RUN),
      api.buildbucket.try_build(
          builder='chromium_presubmit',
          git_repo='https://chromium.googlesource.com/chromium/src'),
      api.properties(dry_run=True, repo_name='chromium'),
      api.step_data(
          'presubmit',
          api.json.output({
              'errors': [],
              'notifications': [],
              'warnings': []
          })),
  )

  yield api.test(
      'infra_with_runhooks',
      api.buildbucket.try_build(
          builder='chromium_presubmit',
          git_repo='https://chromium.googlesource.com/infra/infra'),
      api.properties(runhooks=True, repo_name='infra'),
      api.step_data(
          'presubmit',
          api.json.output({
              'errors': [],
              'notifications': [],
              'warnings': []
          })),
  )

  yield api.test(
      'recipes-py',
      api.buildbucket.try_build(
          builder='infra_presubmit',
          git_repo='https://chromium.googlesource.com/infra/luci/recipes-py'),
      api.properties(runhooks=True, repo_name='recipes_py'),
      api.step_data(
          'presubmit',
          api.json.output({
              'errors': [],
              'notifications': [],
              'warnings': []
          })),
  )

  yield api.test(
      'recipes-py-windows',
      api.buildbucket.try_build(
          builder='infra_presubmit',
          git_repo='https://chromium.googlesource.com/infra/luci/recipes-py'),
      api.properties(runhooks=True, repo_name='recipes_py'),
      api.platform('win', 64),
      api.step_data(
          'presubmit',
          api.json.output({
              'errors': [],
              'notifications': [],
              'warnings': []
          })),
  )

  yield api.test(
      'luci-py',
      api.buildbucket.try_build(
          builder='Luci-py Presubmit',
          git_repo='https://chromium.googlesource.com/infra/luci/luci-py'),
      api.properties(repo_name='luci_py'),
      api.step_data(
          'presubmit',
          api.json.output({
              'errors': [],
              'notifications': [],
              'warnings': []
          })),
  )

  yield api.test(
      'presubmit-failure',
      api.buildbucket.try_build(
          builder='chromium_presubmit',
          git_repo='https://chromium.googlesource.com/chromium/src'),
      api.properties(repo_name='chromium'),
      api.step_data(
          'presubmit',
          api.json.output({
              'errors': [
                  {
                      'message': 'Missing LGTM',
                      'long_text': 'Here are some suggested OWNERS: fake@',
                      'items': [],
                      'fatal': True
                  },
                  {
                      'message': 'Syntax error in fake.py',
                      'long_text': 'Expected "," after item in list',
                      'items': [],
                      'fatal': True
                  },
              ],
              'notifications': [{
                  'message': 'If there is a bug associated please add it.',
                  'long_text': '',
                  'items': [],
                  'fatal': False
              }],
              'warnings': [{
                  'message': 'Line 100 has more than 80 characters',
                  'long_text': '',
                  'items': [],
                  'fatal': False
              }]
          }),
          retcode=1),
      api.expect_status('FAILURE'),
      api.post_process(
          post_process.SummaryMarkdown,
          textwrap.dedent(r'''
              There are 2 errors, 1 warning, and 1 notification.

              Here are the errors:

              **ERROR**

              Missing LGTM

              Here are some suggested OWNERS: fake@

              **ERROR**

              Syntax error in fake\.py

              Expected "," after item in list

              To see notifications and warnings, look at the stdout of the presubmit step.
              ''').strip()),
      api.post_process(post_process.DropExpectation),
  )

  long_message = ('Here are some suggested OWNERS:' +
                  '\nreallyLongFakeAccountNameEmail@chromium.org' * 10)
  yield api.test(
      'presubmit-failure-long-message',
      api.buildbucket.try_build(
          builder='chromium_presubmit',
          git_repo='https://chromium.googlesource.com/chromium/src'),
      api.properties(repo_name='chromium'),
      api.step_data(
          'presubmit',
          api.json.output({
              'errors': [{
                  'message': 'Missing LGTM',
                  'long_text': long_message,
                  'items': [],
                  'fatal': True
              }],
              'notifications': [],
              'warnings': []
          }),
          retcode=1),
      api.expect_status('FAILURE'),
      api.post_process(
          post_process.SummaryMarkdown,
          textwrap.dedent(r'''
              There are 1 error, 0 warnings, and 0 notifications.

              Here are the errors:

              **ERROR**

              Missing LGTM

              Here are some suggested OWNERS:

              reallyLongFakeAccountNameEmail@chromium\.org

              reallyLongFakeAccountNameEmail@chromium\.org

              reallyLongFakeAccountNameEmail@chromium\.org

              reallyLongFakeAccountNameEmail@chromium\.org

              reallyLongFakeAccountNameEmail@chromium\.org

              reallyLongFakeAccountNameEmail@chromium\.org

              reallyLongFakeAccountNameEmail@chromium\.org

              reallyLongFakeAccountNameEmail@chromium\.org

              reallyLongFakeAccountNameEmail@chromium\.org

              **Error size > 450 chars, there are 1 more error(s) (13 total)**

              **The complete output can be found at the bottom of the presubmit stdout.**
              ''').strip()),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'presubmit-infra-failure',
      api.buildbucket.try_build(
          builder='chromium_presubmit',
          git_repo='https://chromium.googlesource.com/chromium/src'),
      api.properties(repo_name='chromium'),
      api.step_data(
          'presubmit',
          api.json.output({
              'errors': [{
                  'message': 'Infra Failure',
                  'long_text': '',
                  'items': [],
                  'fatal': True
              }],
              'notifications': [],
              'warnings': []
          }),
          retcode=2),
      api.expect_status('INFRA_FAILURE'),
      api.post_process(
          post_process.SummaryMarkdown,
          textwrap.dedent('''
              There are 1 error, 0 warnings, and 0 notifications.

              Here are the errors:

              **ERROR**

              Infra Failure

              ''').lstrip()),
      api.post_process(post_process.DropExpectation),
  )

  bug_msg = ('Something unexpected occurred while running presubmit checks.'
             ' Please [file a bug](https://crbug.com/new?component=1456211)')
  yield api.test(
      'presubmit-failure-no-json',
      api.buildbucket.try_build(
          builder='chromium_presubmit',
          git_repo='https://chromium.googlesource.com/chromium/src'),
      api.properties(repo_name='chromium'),
      api.step_data('presubmit', api.json.output(None, retcode=1)),
      api.expect_status('INFRA_FAILURE'),
      api.post_process(post_process.SummaryMarkdown, bug_msg),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'presubmit-infra-failure-no-json',
      api.buildbucket.try_build(
          builder='chromium_presubmit',
          git_repo='https://chromium.googlesource.com/chromium/src'),
      api.properties(repo_name='chromium'),
      api.step_data('presubmit', api.json.output(None, retcode=2)),
      api.expect_status('INFRA_FAILURE'),
      api.post_process(post_process.SummaryMarkdown, bug_msg),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'presubmit-failure-message-with-underscores',
      api.buildbucket.try_build(
          builder='chromium_presubmit',
          git_repo='https://chromium.googlesource.com/chromium/src'),
      api.properties(repo_name='chromium'),
      api.step_data(
          'presubmit',
          api.json.output({
              'errors': [{
                  'message': 'message_with_underscores',
                  'long_text': 'long_text_with_underscores',
                  'items': [],
                  'fatal': False,
              },],
              'notifications': [],
              'warnings': [],
          }),
          retcode=1),
      api.expect_status('FAILURE'),
      api.post_process(
          post_process.SummaryMarkdown,
          textwrap.dedent(r'''
              There are 1 error, 0 warnings, and 0 notifications.

              Here are the errors:

              **ERROR**

              message\_with\_underscores

              long\_text\_with\_underscores

              ''').strip()),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'repository_url_with_solution_name',
      api.buildbucket.try_build(
          builder='chromium_presubmit',
          git_repo='https://skia.googlesource.com/skia.git'),
      api.properties(solution_name='skia'),
      api.step_data(
          'presubmit',
          api.json.output({
              'errors': [],
              'notifications': [],
              'warnings': []
          })),
  )

  yield api.test(
      'v8_with_cache',
      api.buildbucket.try_build(
          builder='v8_presubmit',
          git_repo='https://chromium.googlesource.com/v8/v8'),
      api.properties(runhooks=True, repo_name='v8'),
  )

  yield api.test(
      'v8_with_cache_infra_config_branch',
      api.buildbucket.try_build(
          project='v8',
          builder='v8_presubmit',
          git_repo='https://chromium.googlesource.com/v8/v8'),
      api.properties(runhooks=True, repo_name='v8'),
      api.tryserver.gerrit_change_target_ref('refs/heads/infra/config'),
  )

  yield api.test(
      'upload_findings',
      api.buildbucket.try_build(
          builder='infra_presubmit',
          git_repo='https://chromium.googlesource.com/infra/luci/recipes-py'),
      api.step_data(
          'presubmit',
          api.json.output({
              'errors': [],
              'notifications': [],
              'warnings': [{
                  'message': 'this is a message',
                  'long_text': '',
                  'items': [],
                  'locations': [{
                      'file_path': 'path/to/file',
                  }],
                  'fatal': False
              }]
          })),
      api.post_process(
          post_process.StepSuccess,
          'upload presubmit results as findings',
      ), api.post_process(post_process.StatusSuccess),
      api.post_process(post_process.DropExpectation))
