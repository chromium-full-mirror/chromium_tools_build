# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from google.protobuf import json_format
from google.protobuf import struct_pb2
from google.protobuf import timestamp_pb2
from recipe_engine import recipe_test_api

from PB.go.chromium.org.luci.buildbucket.proto import build as build_pb2
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb


class ChromiumRtsApi(recipe_test_api.RecipeTestApi):

  def override_reused_compilator_steps(self,
                                       comp_build_id=1234,
                                       sub_build_status=common_pb.SUCCESS,
                                       sub_build_summary='',
                                       empty_props=False,
                                       tests=None,
                                       affected_files=None,
                                       skipping_coverage=None,
                                       empty_gitiles_commit=False):
    output_json_obj = self.get_compilator_output_props(
        comp_build_id=comp_build_id,
        empty_props=empty_props,
        tests=tests,
        affected_files=affected_files,
        skipping_coverage=skipping_coverage)

    sub_build = build_pb2.Build(
        id=54321,
        status=sub_build_status,
        summary_markdown=sub_build_summary,
        start_time=timestamp_pb2.Timestamp(seconds=1562475245),
        output=dict(
            gitiles_commit=dict(
                host="chromium.googlesource.com",
                project="chromium/src",
                id="cd7164f91fe44b4ec2304df88aa01da1ec930dd2",
                ref="refs/heads/main",
                position=1069217,
            ) if not empty_gitiles_commit else None,
            properties=json_format.Parse(
                self.m.json.dumps(output_json_obj), struct_pb2.Struct())))
    unused_compilator = build_pb2.Build(
        id=54322,
        status=sub_build_status,
        summary_markdown=sub_build_summary,
        start_time=timestamp_pb2.Timestamp(seconds=1562475246),
        output=dict(
            gitiles_commit=dict(
                host="chromium.googlesource.com",
                project="chromium/src",
                id="cd7164f91fe44b4ec2304df88aa01da1ec930dd2",
                ref="refs/heads/main",
                position=1069217,
            ) if not empty_gitiles_commit else None,
            properties=json_format.Parse(
                self.m.json.dumps(output_json_obj), struct_pb2.Struct())))
    return self.m.buildbucket.simulated_search_results(
        [sub_build, unused_compilator], step_name='get compilator build')

  def get_compilator_output_props(self,
                                  comp_build_id=1234,
                                  empty_props=False,
                                  is_swarming_phase=True,
                                  with_patch=True,
                                  tests=None,
                                  affected_files=None,
                                  skipping_coverage=None):
    tests = tests or ['browser_tests']
    output_json_obj = {}
    if is_swarming_phase:
      if not empty_props:
        output_json_obj = {
            'bot_update_manifest': {
                'src': {
                    'repository':
                        'https://chromium.googlesource.com/chromium/src.git',
                    'revision':
                        '568396b93b850dc7c3dcf85fbdd67e396c82611b'
                },
                'src/chrome/test/data/perf/canvas_bench': {
                    'repository':
                        'https://chromium.googlesource.com/chromium/canvas_bench.git',
                    'revision':
                        'a7b40ea5ae0239517d78845a5fc9b12976bfc732'
                },
                'src/chrome/test/data/perf/frame_rate/content': {
                    'repository':
                        'https://chromium.googlesource.com/chromium/frame_rate/content.git',
                    'revision':
                        'c10272c88463efeef6bb19c9ec07c42bc8fe22b9'
                },
                'src/v8': {
                    'repository': 'https://chromium.googlesource.com/v8/v8.git',
                    'revision': '79aec72034961c94e53dfd6d27bc818b502fa41f'
                },
            },
            "gitiles_commit": {
                "commitHash": "b740e9b724f3756c4ea0825eebf299e0a685225e",
                "host": "chromium.googlesource.com",
                "position": "1138017",
                "project": "chromium/src",
                "ref": "refs/heads/main"
            }
        }
        if with_patch:
          if not affected_files:
            affected_files = [
                'src/ash/root_window_controller.cc',
                'src/ash/root_window_controller.h',
                'src/deleted_file.cc',
            ]

          output_json_obj.update({
              'affected_files': {
                  'first_100': affected_files,
                  'total_count': 3
              },
          })
    return output_json_obj
