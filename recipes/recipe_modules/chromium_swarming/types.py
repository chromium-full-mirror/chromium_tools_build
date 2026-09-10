# Copyright 2020 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine.config_types import Path

from RECIPE_MODULES.build.attr_utils import attrib, attrs, command_args


@attrs()
class TriggerScript:
  """A type describing a custom script for triggering swarming tasks.

  Attributes:
    * script: A path to a script to call which will use custom logic to
      trigger appropriate swarming jobs, using swarming.py. Required.
    * args: An optional list of additional arguments to pass to the
      script.

  The script will receive the exact same arguments that are normally
  passed to calls to `swarming.py trigger`, along with any arguments
  provided in the `args` entry.

  The script is required to output a json file to the location provided
  by the `--dump-json` argument. This json file should describe the
  swarming tasks it launched, as well as some information about the
  request, which is used when swarming collects the tasks.

  If the script launches multiple swarming shards, it needs to pass the
  appropriate environment variables to each shard (this is normally done
  by `swarming.py trigger`). Specifically, each shard should receive
  GTEST_SHARD_INDEX`, which is its shard index, and
  `GTEST_TOTAL_SHARDS`, which is the total number of shards. This can be
  done by passing `--env GTEST_SHARD_INDEX [NUM]` and `--env
  GTEST_SHARD_SHARDS [NUM]` when calling `swarming.py trigger`.
  """

  script = attrib(Path)
  args = attrib(command_args, default=())
  # TODO(gbeaty) What does this field mean? Add documentation to class docstring
  requires_simultaneous_shard_dispatch = attrib(bool, default=False)

  @classmethod
  def create(cls, **kwargs):
    """Create a TriggerScript with attributes set according to kwargs."""
    return cls(**kwargs)


@attrs()
class MergeScript:
  """A type describing a custom script for merging swarming task output.

  Attributes:
    * script: A path to a script to call to post process and merge the
      collected outputs from the tasks. Required.
    * args: An optional list of additional arguments to pass to the
      script.

  The script will be called with the following arguments:
  * `-o` (for output) and the path to write the merged results to.
  * The arguments in the `args` attribute.
  * An arbitrary number of paths to the result files to merge.

  The merged results should be in the JSON Results File Format
  (https://www.chromium.org/developers/the-json-test-results-format) and
  may optionally contain a top level "links" field that may contain a
  dict mapping link text to URLs, for a set of links that will be
  included in the build step UI.
  """

  script = attrib(Path)
  args = attrib(command_args, default=())

  @classmethod
  def create(cls, **kwargs):
    """Create a MergeScript with attributes set according to kwargs."""
    return cls(**kwargs)


@attrs()
class CipdPackage:
  """A type describing a CIPD package to be required by a swarming task.

  Attributes:
    * name - The name of the CIPD package.
    * version - The version of the CIPD package.
    * root - The path relative to the swarming task directory to install
      the package.
  """

  name = attrib(str)
  version = attrib(str)
  root = attrib(str)

  @classmethod
  def create(cls, **kwargs):
    """Create a CipdPackage with attributes set according to kwargs."""
    return cls(**kwargs)
