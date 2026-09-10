# Copyright 2014 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import itertools

from recipe_engine import recipe_test_api

from PB.go.chromium.org.luci.buildbucket.proto import build as build_pb2
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb
from google.protobuf import json_format
from google.protobuf import struct_pb2


class V8BuiltinsPgoTestApi(recipe_test_api.RecipeTestApi):
  def mock_compilation(self, versions, archs, compilator_properties=None):
    if compilator_properties is None:
      compilator_properties = {
        'swarm_hashes': {'d8_pgo': 'fa3e4a54'},
        'parent_test_spec': {
          'tests': [['pgo_instrumentation']],
          'swarming_dimensions': {'os': 'Ubuntu 31.41'},
          'swarming_task_attrs': {
            'expiration': 21600,
            'hard_timeout': 7200,
          },
        },
      }

    sub_build = build_pb2.Build(
      id=54321,
      status=common_pb.SUCCESS,
      summary_markdown='All good!',
      output=dict(
        properties=json_format.Parse(
          self.m.json.dumps({'compilator_properties': compilator_properties}),
          struct_pb2.Struct(),
        )
      ),
    )

    return [
      self.step_data(
        f'collect compilation isolates.{version} {arch}.compilator steps',
        self.m.step.sub_build(sub_build),
      )
      for version, arch in itertools.product(versions, archs)
    ]

  def mock_git_tags(self, tags):
    response = ''
    for revision, tag in tags:
      response += f'{revision}\t{tag}\n'

    return self.override_step_data(
      'init trackers for candidate versions.git ls-remote',
      self.m.raw_io.stream_output_text(response, stream='stdout'),
    )

  def mock_block_file(self, content, report_exceptions=False):
    mocks = [
      self.override_step_data(
        'init trackers for candidate versions.gsutil cat',
        self.m.raw_io.stream_output(content, stream='stdout'),
      )
    ]

    if report_exceptions:
      mocks.append(
        self.override_step_data(
          'report exceptions.gsutil cat',
          self.m.raw_io.stream_output(content, stream='stdout'),
        )
      )

    return mocks

  def mock_profiles(self, versions, archs, content=None):
    mock_content = content or (
      'block_hint,CallUndefinedReceiver1ExtraWideHandler,19,20,0\n'
      'builtin_count,ShiftLeftWideHandler,0\n'
      'block_count,MulSmiExtraWideHandler,34,0\n'
      'builtin_hash,RecordWriteSaveFP,234037449\n'
    )
    return [
      self.step_data(
        f'validate profiles.read profile for {version} {arch}',
        self.m.file.read_text(mock_content),
      )
      for version, arch in itertools.product(versions, archs)
    ]

  def blocked_version_line(self, version, failures_hours_ago=None):
    if failures_hours_ago is None:
      failures_hours_ago = [0] * 4

    failures = self.m.json.dumps(
      [
        {"build": "url1", "time": 1700000000 - 3600 * hours}
        for hours in failures_hours_ago
      ]
    )
    justification = '{"failures": ' + failures + '}'

    return f'{version} {justification}'
