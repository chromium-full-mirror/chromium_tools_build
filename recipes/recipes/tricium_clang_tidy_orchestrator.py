# Copyright 2022 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import itertools
import json
from typing import NamedTuple

from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb2
from google.protobuf import duration_pb2
from recipe_engine import post_process

DEPS = [
    'chromium',
    'depot_tools/gerrit',
    'depot_tools/tryserver',
    'recipe_engine/buildbucket',
    'recipe_engine/json',
    'recipe_engine/platform',
    'recipe_engine/step',
    'recipe_engine/swarming',
    'recipe_engine/tricium',
]

# TODO(crbug.com/1153919): Figure out which subset of these are the best
# trade-off between coverage/cost and enable them.
_CHILD_BUILDERS = (
    'android-clang-tidy-rel',
    # TODO(1444563): Add back once error count is brought back down.
    #'linux-chromeos-clang-tidy-rel',
    'linux-clang-tidy-rel',
    #'linux-lacros-clang-tidy-rel',
    #'fuchsia-clang-tidy-rel',
    #'ios-clang-tidy-rel',
    #'mac-clang-tidy-rel',
    #'win10-clang-tidy-rel',
)

# Go's protobuf package outputs camelCase JSON, whereas Python prefers
# snake_case; handle that transformation here.
_TRICIUM_KEY_TRANSFORMATIONS = {
    'startLine': 'start_line',
    'endLine': 'end_line',
    'startChar': 'start_char',
    'endChar': 'end_char',
}

# A singular `Replacement` emitted by Tricium.
_TriciumReplacement = NamedTuple(
    '_TriciumReplacement',
    (
        ('path', str),
        ('replacement', str),
        ('start_line', int),
        ('end_line', int),
        ('start_char', int),
        ('end_char', int),
    ),
)


class _TriciumSuggestion(
    NamedTuple(
        '_TriciumSuggestion',
        (
            ('description', str),
            ('replacements', tuple[_TriciumReplacement]),
        ),
    )):
  """A `suggestion` emitted by Tricium."""

  def as_json_dict(self):
    """Converts `self` to a dict that can be serialized as JSON."""
    as_dict = self._asdict()
    as_dict['replacements'] = [x._asdict() for x in as_dict['replacements']]
    return as_dict


class _TriciumComment(
    NamedTuple(
        '_TriciumComment',
        (
            ('category', str),
            ('message', str),
            ('path', str),
            ('start_line', int),
            ('end_line', int),
            ('start_char', int),
            ('end_char', int),
            ('suggestions', tuple[_TriciumSuggestion]),
        ),
    )):
  """A full comment emitted by tricium.

  The intent is for it to have all of the information that can possibly be
  passed to api.tricium.add_comment.
  """

  def as_json_dict(self):
    """Converts `self` to a dict that can be serialized as JSON."""
    as_dict = self._asdict()
    as_dict['suggestions'] = [x.as_json_dict() for x in as_dict['suggestions']]
    return as_dict


def _parse_tricium_replacement(replacement):
  # Start with defaults, and update below to overwrite them as necessary.
  full_replacement = {
      'replacement': '',
      'start_line': 0,
      'end_line': 0,
      'start_char': 0,
      'end_char': 0,
  }
  for k, v in replacement.items():
    full_replacement[_TRICIUM_KEY_TRANSFORMATIONS.get(k, k)] = v
  return _TriciumReplacement(**full_replacement)


def _parse_tricium_suggestion_with_defaults(description='', replacements=()):
  return _TriciumSuggestion(
      description=description,
      replacements=tuple(_parse_tricium_replacement(x) for x in replacements),
  )


def _build_tricium_comment_with_defaults(category,
                                         message,
                                         path,
                                         start_line=0,
                                         end_line=0,
                                         start_char=0,
                                         end_char=0,
                                         suggestions=()):
  return _TriciumComment(category, message, path, start_line, end_line,
                         start_char, end_char, suggestions)


def _parse_comments_from_json_list(json_list):
  results = []
  # Use `**s` in cases below so we crash if unknown keys are found. That
  # indicates that this code should be updated to deal with the new keys.
  for x in json_list:
    x = {_TRICIUM_KEY_TRANSFORMATIONS.get(k, k): v for k, v in x.items()}
    if 'suggestions' in x:
      x['suggestions'] = tuple(
          _parse_tricium_suggestion_with_defaults(**s)
          for s in x['suggestions'])
    results.append(_build_tricium_comment_with_defaults(**x))
  return results


def _should_skip_linting(api):
  revision_info = api.gerrit.get_revision_info(
      'https://%s' % api.tryserver.gerrit_change.host,
      api.tryserver.gerrit_change.change, api.tryserver.gerrit_change.patchset)

  # TODO(gbiv): It may be nice to be more consistent in how we check for
  # whether something's lintable. At the time of writing, this heuristic is
  # what's being used for linux-clang-tidy-rel's recipe, but:
  # - tricium_simple does something different entirely
  # - this heuristic doesn't perfectly cover all reverts
  # - it'd be nice to skip autogenerated CLs entirely (crbug.com/872381)
  commit_message = revision_info['commit']['message']
  return commit_message.startswith('Revert')


def _build_textual_bot_list(bots, conjunction):
  """Converts a list of bots into a human readable string listing them."""
  bots = ['`%s`' % x for x in bots]
  if not bots:
    return ''

  if len(bots) == 1:
    return bots[0]

  return ' '.join((', '.join(bots[:-1]), conjunction, bots[-1]))


def _note_observed_on(platforms, all_platforms, lint):
  """Returns a lint noting where the given lint was observed.

  >>> _note_observed_on(['foo'], ['foo', 'bar'], some_lint)
  some_lint.replace(
      message=lint.message + '\n\n(Lint observed on foo, but not bar)')
  >>> _note_observed_on(['foo', 'bar'], ['foo', 'bar'], some_lint)
  some_lint.replace(
      message=lint.message + '\n\n(Lint observed on foo and bar)')
  >>> _note_observed_on(['foo'], ['foo', 'bar', 'baz'], some_lint)
  some_lint.replace(
      message=lint.message + '\n\n(Lint observed on foo, but not bar or baz)')
  """
  msg = 'Lint observed on ' + _build_textual_bot_list(
      platforms,
      conjunction='and',
  )
  platforms_set = set(platforms)
  not_observed_on = _build_textual_bot_list(
      (p for p in all_platforms if p not in platforms_set),
      conjunction='or',
  )
  if not_observed_on:
    msg += ', but not on ' + not_observed_on

  return lint._replace(message=lint.message + '\n\n(%s)' % msg)


def _fixup_lint_paths(lint):
  # Filter out third_party/dawn from path if present. Chromium will not have third_party/dawn
  # entries in the results because Dawn is pulled in from Deps. These show up when running
  # clang-tidy on changes in the Dawn gerrit where the prefix needs to be stripped.
  fixup_path = lambda x: x.removeprefix('third_party/dawn/')
  fixup_replacements = lambda replacements: tuple(
      x._replace(path=fixup_path(x.path)) for x in replacements)
  return lint._replace(
      path=fixup_path(lint.path),
      suggestions=tuple(
          suggestion._replace(
              replacements=fixup_replacements(suggestion.replacements))
          for suggestion in lint.suggestions),
  )


def _dedup_and_fixup_tricium_lints(all_platforms, lints):
  merged_lints = []
  for platform, platform_lints in lints.items():
    for l in platform_lints:
      merged_lints.append((_fixup_lint_paths(l), platform))

  # _TriciumComments contain data which is unhashable, but comparable. While
  # the comparison order may not always be intuitive, we don't care; it's
  # deterministic, and we only ultimately care about having identical lints
  # placed adjacent to each other, so we may deduplicate them in < n^2 time.
  merged_lints.sort()

  results = []
  for lint, items in itertools.groupby(merged_lints, lambda x: x[0]):
    observed_on = [x for _, x in items]
    results.append(_note_observed_on(observed_on, all_platforms, lint))
  return results


def RunSteps(api):
  assert api.tryserver.is_tryserver

  api.buildbucket.hide_current_build_in_gerrit()

  if _should_skip_linting(api):
    return

  # Limit children to a fraction of our timeout. If our timeout is very
  # long, 1 hour should be plenty.
  #
  # Child timeouts are most often caused by one of two things:
  #  1. Massive CLs
  #  2. Children trying very hard to find a header file (that other children
  #     were able to promptly find).
  #
  # In the former case, there's not much we can do to speed things up. In the
  # latter, our goal should be to post the lints of the children that finished
  # before the timeout, rather than having the orchestrator itself time out and
  # drop that information on the floor.
  my_execution_timeout = api.buildbucket.build.execution_timeout
  child_execution_timeout_secs = min(my_execution_timeout.ToSeconds() // 2,
                                     3600)

  with api.step.nest('schedule tidy builds'):
    build_requests = [
        api.buildbucket.schedule_request(
            x,
            swarming_parent_run_id=api.swarming.task_id,
            tags=api.buildbucket.tags(**{'hide-in-gerrit': 'true'}),
        ) for x in _CHILD_BUILDERS
    ]

    for req in build_requests:
      req.execution_timeout.FromSeconds(child_execution_timeout_secs)

    builds = api.buildbucket.schedule(build_requests, step_name='schedule')
    build_ids = [x.id for x in builds]
    build_dict = api.buildbucket.collect_builds(
        build_ids,
        fields=('output', 'status'),
        # Multiply by 1.5 here to account for slack in scheduling/etc.
        timeout=int(child_execution_timeout_secs * 1.5),
    )

    num_failures = sum(
        1 for x in build_dict.values() if x.status != common_pb2.SUCCESS)
    had_failures = num_failures != 0
    all_failures = num_failures == len(builds)

    if had_failures:
      presentation = api.step.active_result.presentation
      presentation.status = api.step.WARNING
      presentation.step_text = "%d/%d builds failed" % (num_failures,
                                                        len(builds))

    builds = [(x, build_dict[i]) for x, i in zip(_CHILD_BUILDERS, build_ids)]

  with api.step.nest('analyze lints'):
    lints = {}
    for builder_name, build_result in builds:
      properties = build_result.output.properties
      if 'tricium' not in properties:
        continue

      comments = json.loads(properties['tricium']).get('comments', ())
      lints[builder_name] = _parse_comments_from_json_list(comments)

    tricium_lints = _dedup_and_fixup_tricium_lints(_CHILD_BUILDERS, lints)

  with api.step.nest('emit comments'):
    for lint in tricium_lints:
      api.tricium.add_comment(**lint.as_json_dict())
    api.tricium.write_comments()

  if all_failures:
    # crbug.com/1343619: There are many reasons that a bot may fail (a broken
    # patch being one of the key ones). That said, if one's patches aren't
    # recent enough (mid-Jun 2022), they may see constant clang-tidy redness.
    # Make the fix obvious for those cases.
    raise api.step.StepFailure('All sub-linting tasks failed. This may be '
                               'because your change needs to be rebased past '
                               'src@522320443539084c901edd659dd29079c8aaadc0. '
                               'Please note that clang-tidy failures do not '
                               'block the CQ.')


def _get_tricium_comments(steps):
  write_results = steps['emit comments.write results']
  tricium_json = write_results.output_properties['tricium']
  comments = json.loads(tricium_json).get('comments')
  if comments:
    comments = _parse_comments_from_json_list(comments)
  return comments


def _tricium_has_no_comments(check, steps):
  comments = _get_tricium_comments(steps)
  check(not comments)


def _tricium_has_comment(check, steps, comment):
  comments = _get_tricium_comments(steps)
  check(comments)
  if comments:
    check(comment in comments)


def GenTests(api):

  def test_data(tricium_data, bot_status_overrides=None, commit_message='foo'):

    test_data = sum([
        api.chromium.try_build(
            builder_group='tryserver.chromium.linux',
            builder='linux_chromium_compile_rel_ng',
            build_number=1234,
            patch_set=1),
        api.platform('linux', 64),
        api.override_step_data(
            'gerrit changes',
            api.json.output([{
                'revisions': {
                    'a' * 40: {
                        '_number': 1,
                        'commit': {
                            'author': {
                                'email': 'gbiv@google.com',
                            },
                            'message': commit_message,
                        }
                    }
                }
            }]))
    ], api.empty_test_data())

    if commit_message.startswith('Revert'):
      return test_data

    if bot_status_overrides is None:
      bot_status_overrides = {}

    # Magic unexported number from buildbucket/api.py; test build IDs are
    # generated sequentially from this, and there're a few other recipes that
    # depend on this number directly.
    base_id = 8922054662172514000
    build_ids = list(range(base_id, base_id + len(_CHILD_BUILDERS)))
    build_output = [
        api.buildbucket.try_build_message(
            build_id=i,
            status=bot_status_overrides.get(builder_name, 'SUCCESS'),
        ) for i, builder_name in zip(build_ids, _CHILD_BUILDERS)
    ]

    if tricium_data is not None:
      builder_indices = {n: i for i, n in enumerate(_CHILD_BUILDERS)}
      for builder_name, comments in tricium_data.items():
        n = builder_indices[builder_name]
        tricium_section = {}
        if comments:
          tricium_section['comments'] = [x.as_json_dict() for x in comments]
        build_output[n].output.properties['tricium'] = api.json.dumps(
            tricium_section)

    test_data += api.buildbucket.simulated_collect_output(
        build_output,
        step_name='schedule tidy builds.buildbucket.collect',
    )
    return test_data

  yield api.test(
      'skip_reverted_cl',
      test_data(tricium_data=None, commit_message='Revert foo'),
      api.post_process(post_process.DoesNotRun, 'schedule tidy builds'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'success_on_no_tricium_output',
      test_data(tricium_data=None),
      api.post_process(_tricium_has_no_comments),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'success_on_empty_tricium_output',
      test_data(tricium_data={name: [] for name in _CHILD_BUILDERS}),
      api.post_process(_tricium_has_no_comments),
      api.post_process(post_process.DropExpectation),
  )

  comment0 = _build_tricium_comment_with_defaults(
      category='some category',
      message='some message',
      path='foo.cpp',
  )
  yield api.test(
      'basic_tidy_output_works',
      test_data(tricium_data={_CHILD_BUILDERS[0]: [comment0]}),
      api.post_process(
          _tricium_has_comment,
          _note_observed_on([_CHILD_BUILDERS[0]], _CHILD_BUILDERS, comment0)),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'multibot_tidy_output_works',
      test_data(tricium_data={
          _CHILD_BUILDERS[0]: [comment0],
          _CHILD_BUILDERS[1]: [comment0],
      }),
      api.post_process(
          _tricium_has_comment,
          _note_observed_on([_CHILD_BUILDERS[0], _CHILD_BUILDERS[1]],
                            _CHILD_BUILDERS, comment0)),
      api.post_process(post_process.DropExpectation),
  )

  comment1 = _build_tricium_comment_with_defaults(
      category='some other category',
      message='some other message',
      path='foo2.cpp',
      suggestions=(_TriciumSuggestion(
          description='foo',
          replacements=(_TriciumReplacement(
              path='/path/to/foo.cc',
              replacement='replaced',
              start_line=0,
              end_line=0,
              start_char=0,
              end_char=0,
          ),),
      ),),
  )
  yield api.test(
      'multibot_multicomment_tidy_output_works',
      test_data(tricium_data={
          _CHILD_BUILDERS[0]: [comment0],
          _CHILD_BUILDERS[1]: [comment0, comment1],
      }),
      api.post_process(
          _tricium_has_comment,
          _note_observed_on([_CHILD_BUILDERS[0], _CHILD_BUILDERS[1]],
                            _CHILD_BUILDERS, comment0),
      ),
      api.post_process(
          _tricium_has_comment,
          _note_observed_on([_CHILD_BUILDERS[1]], _CHILD_BUILDERS, comment1),
      ),
      api.post_process(post_process.DropExpectation),
  )

  comment1_with_new_replacement = comment1._replace(
      suggestions=(_TriciumSuggestion(
          description='foo',
          replacements=(_TriciumReplacement(
              path='/path/to/foo.cc',
              replacement='replaced',
              start_line=0,
              end_line=0,
              start_char=0,
              end_char=1,
          ),),
      ),),)

  # crbug.com/1336328
  yield api.test(
      'messages_with_replacements_must_sort',
      test_data(
          tricium_data={
              _CHILD_BUILDERS[0]: [comment1],
              _CHILD_BUILDERS[1]: [comment1, comment1_with_new_replacement],
          }),
      api.post_process(
          _tricium_has_comment,
          _note_observed_on([_CHILD_BUILDERS[0], _CHILD_BUILDERS[1]],
                            _CHILD_BUILDERS, comment1),
      ),
      api.post_process(
          _tricium_has_comment,
          _note_observed_on([_CHILD_BUILDERS[1]], _CHILD_BUILDERS,
                            comment1_with_new_replacement),
      ),
      api.post_process(post_process.DropExpectation),
  )

  comment1_with_empty_replacement = comment1._replace(
      suggestions=(_TriciumSuggestion(
          description='foo',
          replacements=(_TriciumReplacement(
              path='/path/to/foo.cc',
              replacement='',
              start_line=0,
              end_line=0,
              start_char=0,
              end_char=1,
          ),),
      ),),)

  yield api.test(
      'messages_with_empty_replacements_must_work',
      test_data(
          tricium_data={
              _CHILD_BUILDERS[0]: [comment1_with_empty_replacement],
              _CHILD_BUILDERS[1]: [comment1_with_empty_replacement],
          }),
      api.post_process(
          _tricium_has_comment,
          _note_observed_on([_CHILD_BUILDERS[0], _CHILD_BUILDERS[1]],
                            _CHILD_BUILDERS, comment1_with_empty_replacement),
      ),
      api.post_process(post_process.DropExpectation),
  )

  step_failure = 'FAILURE'
  yield api.test(
      'single_bot_failure',
      test_data(
          bot_status_overrides={
              _CHILD_BUILDERS[0]: step_failure,
          },
          tricium_data={
              _CHILD_BUILDERS[0]: [comment0],
              _CHILD_BUILDERS[1]: [comment0],
          }),
      api.post_process(post_process.StepWarning, 'schedule tidy builds'),
      api.post_process(
          _tricium_has_comment,
          _note_observed_on([_CHILD_BUILDERS[0], _CHILD_BUILDERS[1]],
                            _CHILD_BUILDERS, comment0),
      ),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'all_bot_failure',
      test_data(
          bot_status_overrides={
              builder: step_failure for builder in _CHILD_BUILDERS
          },
          tricium_data={builder: [comment0] for builder in _CHILD_BUILDERS}),
      api.expect_status('FAILURE'),
      api.post_process(post_process.StepWarning, 'schedule tidy builds'),
      api.post_process(
          _tricium_has_comment,
          _note_observed_on(_CHILD_BUILDERS, _CHILD_BUILDERS, comment0),
      ),
      api.post_process(post_process.DropExpectation),
  )

  comment_dawn = _build_tricium_comment_with_defaults(
      category='some other category',
      message='some other message',
      path='third_party/dawn/src/tint/foo2.cpp',
      suggestions=(_TriciumSuggestion(
          description='foo',
          replacements=(_TriciumReplacement(
              path='third_party/dawn/src/tint/foo.cc',
              replacement='replaced',
              start_line=0,
              end_line=0,
              start_char=0,
              end_char=0,
          ),),
      ),),
  )
  comment_dawn_with_new_replacement = comment_dawn._replace(
      path='src/tint/foo2.cpp',
      suggestions=(_TriciumSuggestion(
          description='foo',
          replacements=(_TriciumReplacement(
              path='src/tint/foo.cc',
              replacement='replaced',
              start_line=0,
              end_line=0,
              start_char=0,
              end_char=0,
          ),),
      ),),
  )
  yield api.test(
      'filter_dawn_paths',
      test_data(tricium_data={_CHILD_BUILDERS[0]: [comment_dawn]}),
      api.post_process(
          _tricium_has_comment,
          _note_observed_on([_CHILD_BUILDERS[0]], _CHILD_BUILDERS,
                            comment_dawn_with_new_replacement),
      ),
      api.post_process(post_process.DropExpectation),
  )
