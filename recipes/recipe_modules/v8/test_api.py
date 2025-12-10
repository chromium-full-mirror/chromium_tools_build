# Copyright 2014 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

# Exposes the builder and recipe configurations to GenTests in recipes.

from __future__ import annotations

import argparse
from collections import OrderedDict
import re

from recipe_engine import recipe_test_api
from recipe_engine.post_process import Filter

from PB.go.chromium.org.luci.scheduler.api.scheduler.v1 import (
    triggers as triggers_pb2)

# Excerpt of the v8 version file.
VERSION_FILE_TMPL = """
#define V8_MAJOR_VERSION %d
#define V8_MINOR_VERSION %d
#define V8_BUILD_NUMBER %d
#define V8_PATCH_LEVEL %d
"""


def _sanitize_nonalpha(text):
  return ''.join(c if c.isalnum() else '_' for c in text)


class V8TestApi(recipe_test_api.RecipeTestApi):
  def example_scheduler_buildbucket_trigger(self, key='a'):
    trigger = triggers_pb2.Trigger(id=key)
    trigger.buildbucket.properties['oldest_gitiles_revision'] = 40*key
    trigger.buildbucket.properties['newest_gitiles_revision'] = 40*key
    return trigger

  def example_scheduler_gitiles_trigger(self, key='a'):
    return triggers_pb2.Trigger(
        id=key,
        gitiles=dict(
            repo='https://chromium.googlesource.com/v8/v8',
            ref='refs/heads/main',
            revision=key * 40,
        ))

  def example_bisection_range(self):
    # Gitiles returns changes in the order child -> parent.
    return self.m.json.output({
      'log': [
        {'commit': 'a3', 'msg': 'Cool commit 3'},
        {'commit': 'a2', 'msg': 'Cool commit 2'},
        {'commit': 'a1', 'msg': 'Cool commit 1'},
        {'commit': 'a0', 'msg': 'Cool commit 0'},
      ],
    })

  def example_bisection_range_one_change(self):
    # A1 is the single change in the range, while a0 is the latest previous
    # before the range.
    return self.m.json.output({
      'log': [
        {'commit': 'a1', 'msg': 'Cool commit 1'},
        {'commit': 'a0', 'msg': 'Cool commit 0'},
      ],
    })

  def example_available_builds(self, revision):
    # When 'gsutil ls' is called, it will only find builds for a1 or a3.
    available_builds = {
      'a1': 'gs://chromium-v8/v8-linux64-dbg/full-build-linux_a1.zip',
      'a3': 'gs://chromium-v8/v8-linux64-dbg/full-build-linux_a3.zip',
    }
    return self.m.raw_io.stream_output_text(
        available_builds.get(revision, ''),
        stream='stdout',
    )

  def example_latest_branches(self):
    return self.m.raw_io.stream_output_text(
        'branch-heads/11.1\nbranch-heads/11.2',
        stream='stdout',
    )

  def example_build_dependencies(self):
    return self.m.json.output({
      'avg_deps': 1.2,
      'by_extension': {
        'h': {
          'avg_deps': 53.7,
          'num_files': 53,
          'top100_avg_deps': 67.2,
          'top200_avg_deps': 55.1,
          'top500_avg_deps': 34.94,
        }
      },
      'num_files': 3615,
      'top100_avg_deps': 1.3,
    })

  def example_test_roots(self, *roots):
    """Simulates dynamically optained test-root directories."""
    return self.override_step_data(
        'initialization.list test roots',
        self.m.file.listdir(roots),
    )

  def example_test_config(self, test_config):
    """Simulates reading a simple test-config file.

    Args:
      test_config: The raw test config pyl text.
    """
    return self.m.file.read_text(test_config)

  def example_test_spec(self, builder, spec):
    """Simulates reading a simple test-spec file with one builder.

    Args:
      builder: Key the spec by this builder.
      spec: The raw test spec pyl text.
    """
    return self.m.file.read_text('{"%s": %s}' % (builder, spec))

  def example_build_config(self):
    """Fake config file with build information as written by V8's BUILD.gn."""
    return {
      "dcheck_always_on": False,
      "is_debug": False,
      "target_cpu": "x64",
      "v8_target_cpu": "x64",
    }

  def test_spec_in_checkout(self, buildername, test_spec, testername=None):
    """Simulates having a test specification in the checkout (i.e. on a
    builder_tester bot).

    If testername is specified, we simulate data for a pure compiler builder
    that's supposed to trigger a tester.
    """
    return (self.m.path.exists(
        self.m.path.cache_dir.joinpath('builder', 'v8', 'infra', 'testing',
                                       'builders.pyl')) +
            self.step_data(
                'initialization.read test spec (v8)',
                self.example_test_spec(testername or buildername, test_spec),
            ))

  def hide_infra_steps(self):
    """This hides some infra steps in the expectations which are tested
    sufficiently elsewhere.
    """
    return self.filter_exclude(
      'ensure builder cache dir',
      'read MB config',
      'tweak MB config',
      'read revision',
    )

  def filter_exclude(self, *step_names):
    """Exclude steps from the expectations."""
    return self.post_process(
        Filter().include_re(self.exclude_by_names_re(*step_names)))

  def exclude_by_names_re(self, *step_names):
    skip_fragments = map(re.escape, step_names)
    return r'^((?!(.*\.)?%s).)*$' % '|'.join(skip_fragments)

  def version_file(self,
                   patch_level,
                   desc,
                   count=1,
                   prefix='',
                   major=3,
                   minor=4,
                   build=3):
    # Recipe step name disambiguation.
    suffix = ' (%d)' % count if count > 1 else ''
    return self.override_step_data(
        '%sCheck %s version file%s' % (prefix, desc, suffix),
        self.m.raw_io.stream_output_text(
            VERSION_FILE_TMPL % (major, minor, build, patch_level),
            stream='stdout'),
    )

  def test_name(self, builder_group, buildername, suffix=''):
    return '_'.join(
        filter(bool, [
            'full',
            _sanitize_nonalpha(builder_group),
            _sanitize_nonalpha(buildername),
            suffix,
        ]))

  def test(self,
           builder_group,
           buildername,
           suffix='',
           parent_test_spec=None,
           parent_buildername=None,
           parent_bot_config=None,
           git_ref='refs/heads/main',
           experiments=None,
           status='SUCCESS',
           **kwargs):
    """Convenience method to generate test data for V8 recipe runs.

    Args:
      builder_group: The group of the builder to run.
      buildername: Buildername property as passed to the recipe.
      suffix: Test name suffix.
      parent_test_spec: Test-spec property passed to testers.
      parent_buildername: Name of the parent builder when simulating a child
          tester. Default value if no static config exists in builders.py.
      parent_bot_config: Content of the parent's bot_config when simulating a
          child tester. Default value if no static config exists in builders.py.
      git_ref: Ref from which a commit is fetched.

    """
    if parent_test_spec:
      kwargs.update(self.m.v8_tests.example_parent_test_spec_properties(
          buildername, parent_test_spec))

    if 'triggers' in kwargs:
      # Freezing the structure in builders.py turns lists into tuples. But
      # the recipe properties api requires type list for triggers just like in
      # prod.
      kwargs['triggers'] = list(kwargs['triggers'])

    if builder_group.startswith('tryserver'):
      properties_fn = self.m.properties.tryserver
    else:
      properties_fn = self.m.properties.generic

    test = recipe_test_api.RecipeTestApi.test(
        self.test_name(builder_group, buildername, suffix),
        properties_fn(
            parent_buildername=parent_buildername,
            gerrit_project='v8/v8',
            # TODO(sergiyb): Remove this property after archive module has been
            # migrated to new buildbucket properties.
            buildername=buildername,
            use_remoteexec=True,
            **dict(kwargs, **{'$build/v8': {
                'use_remoteexec': True
            }})),
        self.m.siso.properties(),
        self.m.builder_group.for_current(builder_group),
        self.m.platform('linux', 64),
        status=status,
    )
    if parent_buildername:
      test += self.m.properties(
          parent_got_revision='deafbeef' * 5,
          parent_got_revision_cp='refs/heads/main@{#20123}',
          parent_gn_args=['also_interesting = "absolutely"'],
          parent_build='https://someinfrasite.com/build/123',
      )
      test += self.m.scheduler(triggers=[
        self.example_scheduler_buildbucket_trigger('a'),
        self.example_scheduler_buildbucket_trigger('b'),
      ])
      if kwargs.get('enable_swarming', True):
        # Assume each tester is triggered with the required hashes for all
        # tests. Assume extra_isolate hashes for each extra test specified by
        # parent_test_spec property.
        buider_spec = kwargs.get('parent_test_spec', {})
        swarm_hashes = self.m.v8_tests._make_dummy_swarm_hashes(
            test[0] for test in buider_spec.get('tests', []))
        test += self.m.properties(
          parent_got_swarming_client_revision='[dummy swarming client hash]',
          swarm_hashes=swarm_hashes,
        )
    else:  # triggering builder
      test += self.m.scheduler(triggers=[
        self.example_scheduler_gitiles_trigger('a'),
        self.example_scheduler_gitiles_trigger('b'),
      ])

    if builder_group.startswith('tryserver'):
      test += self.m.properties(
          category='cq',
          master='tryserver.v8',
          reason='CQ',
          try_job_key='1234',
      ) + self.m.buildbucket.try_build(
          project='v8',
          revision='deadbeef'*5,
          builder=buildername,
          git_repo='https://chromium.googlesource.com/v8/v8',
          change_number=456789,
          patch_set=12,
          tags=self.m.buildbucket.tags(
              user_agent='cq',
              buildset='patch/gerrit/chromium-review.googlesource.com/456789/12'
          ),
          experiments=experiments,
      )
    else:
      test += self.m.buildbucket.ci_build(
          project='v8',
          git_repo='https://chromium.googlesource.com/v8/v8',
          builder=buildername,
          git_ref=git_ref,
          build_number=571,
          revision='deadbeef'*5,
          tags=self.m.buildbucket.tags(
              user_agent='luci-scheduler',
              buildset='commit/gitiles/chromium.googlesource.com/v8/v8/+/'
                       'deadbeefdeadbeefdeadbeefdeadbeefdeadbeef'
          ),
          experiments=experiments,
      )

    # Skip some swarming related steps in expectations.
    test += self.hide_infra_steps()

    # Only show the command for swarming trigger steps (i.e. drop logs).
    # List of (step-name regexp, tuple of fields to keep).
    keep_fields_spec = [
      ('trigger tests.\[trigger\].*', ('cmd',)),
    ]

    # TODO(machenbach): Add a better field/step dropping mechanism to the
    # engine.
    def keep_fields(_, steps):
      to_ret = OrderedDict()
      for name, step in steps.items():
        for rx, fields in keep_fields_spec:
          if re.match(rx, name):
            to_ret[name] = {
              k: v for k, v in step.to_step_dict().items()
              if k in fields or k == 'name'
            }
            break
        else:
          to_ret[name] = step
      return to_ret

    test += self.post_process(keep_fields)
    return test

  @staticmethod
  def _check_step(check, steps, step):
    return check(step in steps)

  @staticmethod
  def _get_param(check, steps, step, param, action=None):
    """Returns the value of the given step's cmd-line parameter."""
    check(step in steps)
    parser = argparse.ArgumentParser()
    # TODO(machenbach): Add test case for this branch or delete code.
    if action:  # pragma: no cover
      parser.add_argument(param, dest='param', action=action)
    else:
      parser.add_argument(param, dest='param')
    options, _ = parser.parse_known_args(steps[step].cmd)
    check(options)
    return options.param

  @staticmethod
  def _check_param_equals(check, steps, step, param, value):
    action = 'store_true' if value in [True, False] else None
    check(value == V8TestApi._get_param(check, steps, step, param, action))

  @staticmethod
  def _check_in_param(check, steps, step, param, value):
    check(value in V8TestApi._get_param(check, steps, step, param))

  def check_param_equals(self, step, param, value):
    """Check if a step has a particular parameter matching a given value."""
    return self.post_process(
        V8TestApi._check_param_equals, step, param, value)

  def check_in_param(self, step, param, value):
    """Check if a given value is a substring of a step's parameter."""
    return self.post_process(V8TestApi._check_in_param, step, param, value)

  def check_in_any_arg(self, step, value):
    """Check if a given value is a substring of any argument in a step."""
    def check_any(check, steps, step, value):
      if self._check_step(check, steps, step):
        check(any(value in arg for arg in steps[step].cmd))
    return self.post_process(check_any, step, value)

  def check_not_in_any_arg(self, step, value):
    """Check that a given value is not a substring of any argument in a step.

    This is the opposite of the method above.
    """
    def check_any(check, steps, step, value):
      if self._check_step(check, steps, step):
        check(not any(value in arg for arg in steps[step].cmd))
    return self.post_process(check_any, step, value)
