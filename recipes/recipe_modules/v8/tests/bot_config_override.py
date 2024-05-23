# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine.engine_types import freeze
from recipe_engine.post_process import DropExpectation, LogEquals

DEPS = [
    'depot_tools/gitiles',
    'recipe_engine/buildbucket',
    'recipe_engine/json',
    'v8',
]

DEFAULT_BOT_CONFIG = freeze({'dims': {'os': 'Ubuntu-20', 'cpu': 'x64'}})


def RunSteps(api):
  api.v8.apply_bot_config(DEFAULT_BOT_CONFIG, revision='deadbeef')


def GenTests(api):

  def test(name, override_content, bot_config_expectation):
    expected = api.json.dumps(bot_config_expectation)
    step_overrides = []

    if override_content is not None:
      step_overrides.append(
          api.override_step_data(
              'Retrieve bot_config.fetch deadbeef:infra/builder_properties.pyl',
              api.gitiles.make_encoded_file(override_content),
          ))

    return api.test(
        name,
        api.buildbucket.ci_build(builder=f'Builder {name}'),
        *step_overrides,
        api.post_process(LogEquals, 'Retrieve bot_config', 'merged bot_config',
                         expected),
        api.post_process(DropExpectation),
    )

  yield test(
      name='no-override-file',
      override_content=None,
      bot_config_expectation=DEFAULT_BOT_CONFIG,
  )

  yield test(
      name='no-override-builder',
      override_content="{'Builder other-name': {'dims': {'os': 'Mac'}}}",
      bot_config_expectation=DEFAULT_BOT_CONFIG,
  )

  yield test(
      name='no-override-empty-dict',
      override_content="{'Builder no-override-empty-dict': {'dims': {}}}",
      bot_config_expectation=DEFAULT_BOT_CONFIG,
  )

  yield test(
      name='update-entry',
      override_content="{'Builder update-entry': {'dims': {'os': 'Mac',},},}",
      bot_config_expectation={'dims': {
          'os': 'Mac',
          'cpu': 'x64'
      }},
  )

  yield test(
      name='substituted-dict',
      override_content="{'Builder substituted-dict': {'dims': None}}",
      bot_config_expectation={'dims': None},
  )
