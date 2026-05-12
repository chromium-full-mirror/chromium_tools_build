# Copyright 2014 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Classes for running different kinds of tests.

This module contains two main class hierarchies: test specs and tests.
Test specs are immutable objects that define the details of a specific
test and can be used to create the test object, which actually knows how
to execute a test. Test objects can also be decorated with test
wrappers, which can modify the execution of the test.

The class `AbstractTestSpec` is the root of the class hierarchy for test
specs and test wrapper specs. It defines the single method `get_test`
which is how the test or wrapped test is obtained from the spec.

All test spec types inherit from `TestSpec`. `TestSpec` implements the
`get_test` method in terms of the `test_class` property, which concrete
subclasses must override to return the class of the test type. All test
wrapper types inherit from `TestWrapperSpec`.`TestWrapperSpec`
implements the `get_test` method in terms of the `test_wrapper_class`
property, which concrete subclasses must override to return the class of
the test wrapper type.

The class `AbstractTest` is the root of the class hierarchy for tests
and test wrappers. All test types inherit from `Test` and all test
wrapper types inherit from `TestWrapper`, which are both abstract base
classes. Each concrete test type or test wrapper type has an associated
spec type that contains the input details for the test or test wrapper.
"""

from __future__ import annotations

import abc
import attr
from collections.abc import Iterable, Set
import contextlib
from enum import StrEnum
import hashlib
import itertools
import inspect
import re
import struct
import urllib

from recipe_engine import step_data
from recipe_engine.config_types import Path

from .resultdb import ResultDB

from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb2
from PB.go.chromium.org.luci.resultdb.proto.v1 import (test_result as
                                                       test_result_pb2)

from RECIPE_MODULES.build import chromium_swarming
from RECIPE_MODULES.build.attr_utils import (attrib, attrs, command_args, enum,
                                             mapping, sequence)
from RECIPE_MODULES.build.chromium_utr.instruction import get_utr_instruction
from RECIPE_MODULES.build.skylab.test_runner import TestRunner
from RECIPE_MODULES.build.test_utils import util

# Pylint doesn't understand an abstract class hierarchy where a subclass will
# override some of the abstract methods of its base and remain abstract itself.
# The actual implementation of abstract base classes ensures that everything is
# overridden when attempting to instantiate a class, so trying to make this
# pylint clean isn't worth the effort.
# pylint: disable=abstract-method

RESULTS_URL = 'https://chromeperf.appspot.com'

# When we retry failing tests, we try to choose a high repeat count so that
# flaky tests will produce both failures and successes. The tradeoff is with
# total run time, which we want to keep low.
REPEAT_COUNT_FOR_FAILING_TESTS = 10

# To guarantee a deterministic failure, we would extend the retry time when
# running failed tests on CI retry shards.
RETRY_LIMIT_FOR_CI_RETRY_SHARDS = 5

# Pinned version of
# https://chromium.googlesource.com/infra/infra/+/main/go/src/infra/cmd/mac_toolchain
MAC_TOOLCHAIN_PACKAGE = 'infra/tools/mac_toolchain/${platform}'
MAC_TOOLCHAIN_VERSION = (
    'git_revision:b0c0a706097c27444dbe3f84e5553f1aaa77c1a6')
MAC_TOOLCHAIN_ROOT = '.'

ALLOWED_RESULT_HANDLER_NAMES = ('default', 'layout tests', 'fake')

# Matches the name of the new invocation that gets printed to stderr when
# calling `rdb stream -new`.
RDB_INVOCATION_NAME_RE = re.compile(r'rdb-stream: included "(\S+)" in "\S+"')

INCLUDE_CI_FOOTER = 'Include-Ci-Only-Tests'

INVALID_SUITE_STATUS = 'Invalid'
INCOMPLETE_SUITE_STATUS = 'Incomplete'
FAILURE_SUITE_STATUS = 'Failure'
SUCCESS_SUITE_STATUS = 'Success'


def _merge_arg(args, flag, value):
  args = [a for a in args if not a.startswith(flag)]
  if value is not None:
    return args + ['%s=%s' % (flag, str(value))]
  return args + [flag]


@attrs()
class TestOptionFlags:
  """Flags for supporting TestOptions features.

  For each of the options in TestOptions, the different test types have
  varying support and will require different arguments to be set. This
  type abstracts out those details and provides a mechanism for adding
  the appropriate flags to arguments when supported.
  """

  # Flag argument used to specify test filters
  filter_flag = attrib(str, default='')
  # The delimiter to use between values when specifying test filters
  filter_delimiter = attrib(str, default='')
  # Flag argument used to define how many times to repeat tests
  repeat_flag = attrib(str, default='')
  # Flag argument used to define the upper limit of retries.
  retry_limit_flag = attrib(str, default='')
  # Flag argument used to run disabled tests.
  run_disabled_flag = attrib(str, default='')
  # Flag argument used to set how many tests run in a given shard
  batch_limit_flag = attrib(str, default='')

  @classmethod
  def create(cls, **kwargs):
    filter_flag = kwargs.get('filter_flag')
    filter_delimiter = kwargs.get('filter_delimiter')
    if filter_flag and not filter_delimiter:
      raise ValueError("'filter_delimiter' must be set if 'filter_flag' is")
    return cls(**kwargs)


_DEFAULT_OPTION_FLAGS = TestOptionFlags.create()
_GTEST_OPTION_FLAGS = TestOptionFlags.create(
    filter_flag='--gtest_filter',
    filter_delimiter=':',
    repeat_flag='--gtest_repeat',
    retry_limit_flag='--test-launcher-retry-limit',
    run_disabled_flag='--gtest_also_run_disabled_tests',
    batch_limit_flag='--test-launcher-batch-limit',
)
_ISOLATED_SCRIPT_OPTION_FLAGS = TestOptionFlags.create(
    filter_flag='--isolated-script-test-filter',
    filter_delimiter='::',
    repeat_flag='--isolated-script-test-repeat',
    retry_limit_flag='--isolated-script-test-launcher-retry-limit',
)
# webkit_layout_tests were renamed to blink_web_tests, which only supports
# gtest style arguments. See crbug/831345 and crrev/c/1006067 for details.
# batch limit was never supported for webkit_layout_tests, so we'll exclude
# override of that variable.
_BLINK_WEB_TESTS_OPTION_FLAGS = TestOptionFlags.create(
    filter_flag='--gtest_filter',
    filter_delimiter=':',
    repeat_flag='--gtest_repeat',
    retry_limit_flag='--test-launcher-retry-limit',
    run_disabled_flag='--gtest_also_run_disabled_tests',
)
_ANGLE_UNITTESTS_OPTION_FLAGS = TestOptionFlags.create(
    filter_flag='--gtest_filter',
    filter_delimiter=':',
    repeat_flag='--gtest_repeat',
    retry_limit_flag='--flaky-retries',
    run_disabled_flag='--gtest_also_run_disabled_tests',
)


@attrs()
class TestOptions:
  """Test-type agnostic configuration of test running options."""

  # How many times to run each test
  repeat_count = attrib(int, default=None)
  # A list of tests to restrict execution
  test_filter = attrib(sequence[str], default=())
  # Whether to run tests that have been disabled.
  run_disabled = attrib(bool, default=False)
  # How many times to retry tests until getting a pass
  retry_limit = attrib(int, default=None)
  # Whether to run all tests independently, with no state leaked between them.
  # This can significantly increase the time it takes to run tests.
  force_independent_tests = attrib(bool, default=False)

  @classmethod
  def create(cls, **kwargs):
    return cls(**kwargs)

  def for_running(self, suffix, tests_to_retry):
    """Gets options for running for a given suffix and tests to retry.

    When retrying tests without patch, we want to run the tests a fixed
    number of times, regardless of whether they succeed, to see if they
    flakily fail. Some recipes specify an explicit repeat_count -- for
    those, we don't override their desired behavior.

    Args:
      suffix: A string suffix.
      tests_to_retry: A container of tests to retry. An empty container
        indicates that it is not a retry and all tests should be run.
    """
    # If there are too many tests, avoid setting a repeat count since that can
    # cause timeouts. tests_to_retry can be None to indicate that all tests
    # should be run. It can also rarely be the empty list, which is caused by an
    # infra failure even though results are valid and all tests passed.
    # https://crbug.com/910706.
    if not tests_to_retry or len(tests_to_retry) > 100:
      return self

    if self.repeat_count is None and suffix == 'without patch':
      return attr.evolve(
          self,
          repeat_count=REPEAT_COUNT_FOR_FAILING_TESTS,
          # If we're repeating the tests 10 times, then we want to set
          # retry_limit=0. The default retry_limit of 3 means that failing tests
          # will be retried 40 times, which is not our intention.
          retry_limit=0,
          # Since we're retrying a small number of tests, force them to be
          # independent. This increases run time but produces more reliable
          # results.
          force_independent_tests=True,
      )

    # Allow more retries for CI shard retries.
    if suffix == 'retry shards':
      return attr.evolve(self, retry_limit=RETRY_LIMIT_FOR_CI_RETRY_SHARDS)

    return self

  def add_args(self, args, flags):
    """Add arguments to the command line corresponding to the options.

    Args:
      args: A sequence of strings containing the command-line.
      flags: The TestOptionFlags instance containing the supported flags
        for the test.

    Returns:
      args: A list of strings containing the command-line. For any
      enabled options, if there is a supporting flag, the command-line
      will be modified to add the flag or replace it if it was already
      present.
    """
    args = list(args)

    if self.test_filter and flags.filter_flag:
      args = _merge_arg(args, flags.filter_flag,
                        flags.filter_delimiter.join(self.test_filter))

    if self.repeat_count and self.repeat_count > 1 and flags.repeat_flag:
      args = _merge_arg(args, flags.repeat_flag, self.repeat_count)

    if self.retry_limit is not None and flags.retry_limit_flag:
      args = _merge_arg(args, flags.retry_limit_flag, self.retry_limit)

    if self.run_disabled and flags.run_disabled_flag:
      args = _merge_arg(args, flags.run_disabled_flag, None)

    if self.force_independent_tests and flags.batch_limit_flag:
      args = _merge_arg(args, flags.batch_limit_flag, 1)

    return args


def _add_suffix(step_name, suffix):
  if not suffix:
    return step_name
  return '{} ({})'.format(step_name, suffix)


def _present_info_messages(presentation, test, messages):
  messages = list(messages)
  if test.is_rts:
    messages.append('Ran tests selected by RTS.')
  if test.spec.description:
    messages.append(test.spec.description)
  messages.append(presentation.step_text)
  presentation.step_text = '\n'.join(messages)


class AbstractTestSpec(abc.ABC):
  """Abstract base class for specs for tests and wrapped tests."""

  @abc.abstractmethod
  def get_test(self, chromium_tests_api):
    """Get a test instance described by the spec.

    Returns:
      An instance of either a `Test` subclass or an instance of a
      `TestWrapper` subclass.
    """
    raise NotImplementedError()  # pragma: no cover


class TestLocality(StrEnum):
  LOCAL = 'local'
  SWARMING = 'swarming'
  SKYLAB = 'skylab'


class AbstractTest(abc.ABC):
  """Abstract base class for tests and wrapped tests."""

  @property
  @abc.abstractmethod
  def name(self) -> str:
    """The name of the test's step without a phase suffix.

    Additional suffixes may be present (e.g. os and GPU for swarming
    tests).
    """
    raise NotImplementedError()  # pragma: no cover

  @property
  @abc.abstractmethod
  def spec(self) -> AbstractTestSpec:
    """The spec for the test."""
    raise NotImplementedError()  # pragma: no cover

  @spec.setter
  @abc.abstractmethod
  def spec(self, value: AbstractTestSpec) -> None:
    """The spec for the test."""
    raise NotImplementedError()  # pragma: no cover

  @property
  def is_enabled(self) -> bool:
    """Whether the test is enabled or not.

    Tests that are not enabled should still support having pre_run and
    run called and can produce empty steps to provide information in the
    build, but users should not call methods dealing with results on
    tests that are not enabled.
    """
    raise NotImplementedError()  # pragma: no cover

  @property
  @abc.abstractmethod
  def is_experimental(self) -> bool:
    """Whether the test is experimental or not.

    Failures in experimental tests should not fail the builds.
    """
    raise NotImplementedError()  # pragma: no cover

  @property
  @abc.abstractmethod
  def is_ci_only(self) -> bool:
    """Whether the test is ci_only or not.

    If failures are present in ci_only tests, additional information will be
    added to the build's summary to indicate how to run them on the try builder.
    """
    raise NotImplementedError()  # pragma: no cover

  @property
  @abc.abstractmethod
  def canonical_name(self) -> str:
    """Canonical name of the test, no suffix attached."""
    raise NotImplementedError()  # pragma: no cover

  @abc.abstractmethod
  def step_name(self, suffix: str) -> str:
    """Helper to uniformly combine tests's name with a suffix.

    Note this step_name is not necessarily the same as the step_name in actual
    builds, since there could be post-processing on the step_name by other
    apis, like swarming (see api.chromium_swarming.get_step_name()).
    """
    raise NotImplementedError()  # pragma: no cover

  @property
  @abc.abstractmethod
  def target_name(self) -> str:
    raise NotImplementedError()  # pragma: no cover

  @abc.abstractmethod
  def compile_targets(self) -> Iterable[str]:
    """the compile targets needed by this test."""
    raise NotImplementedError()  # pragma: no cover

  @property
  @abc.abstractmethod
  def uses_local_devices(self) -> bool:
    raise NotImplementedError()  # pragma: no cover

  @property
  @abc.abstractmethod
  def isolate_target(self) -> str | None:
    """The name of the isolate to create for the test."""
    raise NotImplementedError()  # pragma: no cover

  @property
  def uses_isolate(self) -> bool:
    """Returns true if the test is run via an isolate.

    This does not need to be overridden in any subclasses. Overriding
    isolate_target to return a non-false value will cause the test to
    report that it uses isolate.
    """
    return bool(self.isolate_target)

  @property
  @abc.abstractmethod
  def locality(self) -> TestLocality:
    """Where the test is executed."""
    raise NotImplementedError()  # pragma: no cover

  @property
  def runs_locally(self) -> bool:
    """Whether the test runs locally."""
    return self.locality == TestLocality.LOCAL

  @property
  def runs_on_swarming(self) -> bool:
    """Whether or not the test runs on swarming."""
    return self.locality == TestLocality.SWARMING

  @property
  def runs_on_skylab(self) -> bool:
    """Whether the test runs on skylab."""
    return self.locality == TestLocality.SKYLAB

  @property
  @abc.abstractmethod
  def supports_rts(self) -> bool:
    """Determine whether the test supports RTS.

    Regression Test Selection (RTS) is a mode of operation where a subset of the
    tests are run. This should be checked before trying to set is_rts to enable
    RTS.
    """
    raise NotImplementedError()  # pragma: no cover

  @property
  @abc.abstractmethod
  def is_rts(self) -> bool:
    """Determine whether the test is currently running with RTS.

    Regression Test Selection (RTS) is a mode of operation where a subset of the
    tests are run. This property determines whether this mode is enabled or not.
    """
    raise NotImplementedError()  # pragma: no cover

  @is_rts.setter
  @abc.abstractmethod
  def is_rts(self, value: bool) -> None:
    """Set whether the test is currently running with RTS.

    Regression Test Selection (RTS) is a mode of operation where a subset of the
    tests are run. This property will enable running only the tests selected by
    RTS.
    """
    raise NotImplementedError()  # pragma: no cover

  @property
  @abc.abstractmethod
  def test_id_prefix(self) -> str:
    """Prefix of test_id in ResultDB. e.g.

    "ninja://chrome/test:telemetry_gpu_integration_test/trace_test/"
    """
    raise NotImplementedError()  # pragma: no cover

  @property
  @abc.abstractmethod
  def option_flags(self) -> TestOptionFlags:
    """Get the flags that the test uses for TestOptions."""
    raise NotImplementedError()  # pragma: no cover

  @property
  @abc.abstractmethod
  def test_options(self) -> TestOptions:
    """Get the test options that will be used when running the test."""
    raise NotImplementedError()  # pragma: no cover

  @property
  @abc.abstractmethod
  def retry_only_failed_tests(self) -> bool:
    """Whether to retry only the failed tests, with patch."""
    raise NotImplementedError()  # pragma: no cover

  @test_options.setter
  @abc.abstractmethod
  def test_options(self, value: TestOptions) -> None:
    """Set the test options that will be used when running the test."""
    raise NotImplementedError()  # pragma: no cover

  @abc.abstractmethod
  def pre_run(self,
              suffix: str,
              include_utr_instruction: bool = False,
              is_ci_only: bool = False) -> None:
    """Steps to execute before running the test."""
    raise NotImplementedError()  # pragma: no cover

  @abc.abstractmethod
  def run(
      self,
      checkout_dir: Path,
      source_dir: Path,
      build_dir: Path,
      suffix: str,
      info_messages: Iterable[str] = (),
  ) -> None:
    """Run the test."""
    raise NotImplementedError()  # pragma: no cover

  @abc.abstractmethod
  def get_invocation_names(self, suffix: str) -> Iterable[str]:
    """Returns the invocation names tracking the test's results in RDB."""
    raise NotImplementedError()  # pragma: no cover

  @abc.abstractmethod
  def failure_on_exit(self, suffix: str) -> bool:
    """Returns True if the test (or any of its shards) exited non-zero.

    Used to determine the result of the test in the absence of anything
    uploaded to RDB.
    """
    raise NotImplementedError()  # pragma: no cover

  @abc.abstractmethod
  def did_complete(self, suffix: str) -> bool:
    """Returns True if the test had a chance to run to completion.

    False implies invalid results (see below).
    """
    raise NotImplementedError()  # pragma: no cover

  @abc.abstractmethod
  def has_valid_results(self, suffix: str) -> bool:
    """Returns True if results (failures) are valid.

    If False, this indicates the test failed but also failed to report any
    results in machine-readable format.

    With did_complete() above and deterministic_failures() below, this
    makes it possible to distinguish between the following cases:
    a) Test ran, and exited zero,
    b) Test ran, exited non-zero, and reported failures.
    c) Test ran, exited non-zero, but did not report any failures:
    d) Test was not able to run.

    Both b) and c) are often due to the code-under-test. d) is often due to an
    infrastructure failure.
    """
    raise NotImplementedError()  # pragma: no cover

  @abc.abstractmethod
  def get_rdb_results(self, suffix: str) -> util.RDBPerSuiteResults:
    raise NotImplementedError()  # pragma: no cover

  @abc.abstractmethod
  def update_rdb_results(
      self,
      suffix: str,
      results: util.RDBPerSuiteResults,
  ) -> None:
    raise NotImplementedError()  # pragma: no cover

  @abc.abstractmethod
  def deterministic_failures(self, suffix: str) -> Set[str]:
    raise NotImplementedError()  # pragma: no cover

  @abc.abstractmethod
  def notrun_failures(self, suffix: str) -> Set[str]:
    """Returns tests that had status NOTRUN/UNKNOWN.

    FindIt has special logic for handling for tests with status NOTRUN/UNKNOWN.
    This method returns test for which every test run had a result of either
    NOTRUN or UNKNOWN.

    Returns:
      not_run_tests: A set of strings. Only valid if valid_results is True.
    """
    raise NotImplementedError()  # pragma: no cover

  @property
  @abc.abstractmethod
  def known_luci_analysis_flaky_failures(self) -> Set[str]:
    raise NotImplementedError()  # pragma: no cover

  @abc.abstractmethod
  def add_known_luci_analysis_flaky_failures(
      self,
      test_names: Iterable[str],
  ) -> None:
    """Add known flaky failures on ToT."""
    raise NotImplementedError()  # pragma: no cover

  @property
  @abc.abstractmethod
  def weak_luci_analysis_flaky_failures(self) -> Set[str]:
    raise NotImplementedError()  # pragma: no cover

  @abc.abstractmethod
  def add_weak_luci_analysis_flaky_failure(self, test_name: str) -> None:
    """Add known weak flaky failures."""
    raise NotImplementedError()  # pragma: no cover

  @property
  @abc.abstractmethod
  def check_flakiness_for_new_tests(self) -> bool:
    """Whether to check flakiness for new tests in try jobs."""
    raise NotImplementedError()  # pragma: no cover

  def failures_including_retry(
      self,
      suffix: str,
  ) -> tuple[bool, Set[str] | None]:
    """Returns test failures after retries.

    This method only considers tests to be failures if every test run fails,
    if the test runner retried tests, they're still considered successes as long
    as they didn't cause step failures.

    It also considers retried shards and the known flaky tests on tip of tree
    when determining if a test failed, which is to say that a test is determined
    as a failure if and only if it succeeded neither original run or retry and
    is NOT known to be flaky on tip of tree.

    Returns: A tuple (valid_results, failures).
      valid_results: A Boolean indicating whether results are valid.
      failures: A set of strings. Only valid if valid_results is True.
    """
    original_run_valid = self.has_valid_results(suffix)
    if original_run_valid:
      failures = self.deterministic_failures(suffix)
    retry_suffix = 'retry shards'
    if suffix:
      retry_suffix = ' '.join([retry_suffix, suffix])
    retry_shards_valid = self.has_valid_results(retry_suffix)
    if retry_shards_valid:
      retry_shards_failures = self.deterministic_failures(retry_suffix)

    if original_run_valid and retry_shards_valid:
      # Returning retry_shards_failures ensures that if there were passed
      # tests in the original run that failed in the retry shards, those
      # failures are exposed in the build.
      return True, (
          set(retry_shards_failures) - self.known_luci_analysis_flaky_failures)

    if original_run_valid:
      return True, set(failures) - self.known_luci_analysis_flaky_failures

    if retry_shards_valid:
      return True, set(
          retry_shards_failures) - self.known_luci_analysis_flaky_failures

    return False, None

  def with_patch_failures_including_retry(self) -> tuple[bool, Set[str] | None]:
    return self.failures_including_retry('with patch')

  # TODO(crbug.com/1040596): Remove this method and update callers to use
  # |deterministic_failures('with patch')| once the bug is fixed.
  #
  # Currently, the sematics of this method is only a subset of
  # |deterministic_failures('with patch')| due to that it's missing tests that
  # failed "with patch", but passed in "retry shards with patch".
  def has_failures_to_summarize(self) -> bool:
    _, failures = self.failures_including_retry('with patch')
    return bool(failures or self.known_luci_analysis_flaky_failures)

  def without_patch_failures_to_ignore(self) -> tuple[bool, Set[str]]:
    """Returns test failures that should be ignored.

    Tests that fail in 'without patch' should be ignored, since they're failing
    without the CL patched in. If a test is flaky, it is treated as a failing
    test.

    Returns: A tuple (valid_results, failures_to_ignore).
      valid_results: A Boolean indicating whether failures_to_ignore is valid.
      failures_to_ignore: A set of strings. Only valid if valid_results is True.
    """
    results = self.get_rdb_results('without patch')
    if not self.has_valid_results('without patch') or not results:
      return (False, None)

    ignored_failures = set()
    for test in results.all_tests:
      for i, status in enumerate(test.statuses):
        expected = test.expectednesses[i]
        if status != test_result_pb2.PASS and not expected:
          ignored_failures.add(test.test_name)
          break

    return (True, ignored_failures)

  def get_status(self, suffix: str) -> str:
    """Returns the status of the test for the given suffix

    Determines whether the test suite has succeeded, failed, or has invalid
    results for the provided suffix, checking exonerations for without
    patch and retrying shards

    Args:
      suffix: String suffix representing the phase of the build used to
      determine which phases can be used to exonerate the suite. Expected to
      be 'with patch' to allow 'without patch' to exonerate

    Returns: A string designating the suite's current status
    """
    if not self.did_complete(suffix):
      return INCOMPLETE_SUITE_STATUS
    if suffix == 'with patch':
      valid, test_failures = self.with_patch_failures_including_retry()
      if not valid:
        return INVALID_SUITE_STATUS
      if not test_failures:
        return SUCCESS_SUITE_STATUS
      # Check if the without patch exonerates this suite
      valid, without_patch_failures = self.deterministic_without_patch_failures(
      )
      if valid and not without_patch_failures:
        return SUCCESS_SUITE_STATUS
      return FAILURE_SUITE_STATUS
    valid, test_failures = self.failures_including_retry(suffix)
    if not valid:
      return INVALID_SUITE_STATUS
    if test_failures:
      return FAILURE_SUITE_STATUS
    return SUCCESS_SUITE_STATUS

  def deterministic_without_patch_failures(
      self) -> tuple[bool, Set[str] | None]:
    # Check if the suite succeeded in without patch
    valid_results, ignored_failures = self.without_patch_failures_to_ignore()
    if not valid_results:
      return False, None

    valid_results, test_failures = self.with_patch_failures_including_retry()
    assert valid_results, (
        "If there were no valid results, then there was no "
        "point in running 'without patch'. This is a recipe bug.")

    # The FAILURE and NOTRUN test statuses are both considered deterministic
    # failures. But some suites can have trouble during later phases, causing
    # some tests that exited with FAILURE in the 'with patch' phase to exit
    # with NOTRUN in the 'without patch' phase. So when a 'without patch' test
    # fails with a different status, don't ignore it.
    if ignored_failures:
      with_patch_notruns = self.notrun_failures('with patch')
      without_patch_notruns = self.notrun_failures('without patch')
      for ignored_failure in ignored_failures.copy():
        if ((ignored_failure in with_patch_notruns) !=
            (ignored_failure in without_patch_notruns)):
          ignored_failures.remove(ignored_failure)
    # Remove the tests that failed wo patch
    return True, test_failures - ignored_failures


@attrs()
class TestSpec(AbstractTestSpec):
  """Abstract base class for specs for tests.

  Attributes:
    * name - The displayed name of the test.
    * target_name - The ninja build target for the test, a key in
      one of the gn_isolate_map.pyl files being used, e.g. "browser_tests".
    * full_test_target - A fully qualified Ninja target, e.g.
      "//chrome/test:browser_tests".
    * waterfall_builder_group - The matching waterfall builder group.
      This value would be the builder group of the mirrored builder for
      a try builder.
    * waterfall_buildername - The matching waterfall builder name. This
      value would be the name of the mirrored builder for a try builder.
    * resultdb - The ResultDB integration configuration. If
      `resultdb.enable` is not True, then ResultDB integration is
      disabled.
    * test_id_prefix: A prefix to be added to the test Id for the test
      e.g.
      "ninja://chrome/test:telemetry_gpu_integration_test/trace_test/".
    * retry_only_failed_tests: Whether to retry only the failed tests, with
      patch. The alternative is the status quo of retrying the entire shard.
  """

  _name = attrib(str)
  target_name = attrib(str)
  description = attrib(str, default=None)
  full_test_target = attrib(str, default=None)
  waterfall_builder_group = attrib(str, default=None)
  waterfall_buildername = attrib(str, default=None)
  resultdb = attrib(ResultDB, default=ResultDB.create())
  # TODO(crbug/1106965): remove test_id_prefix, if deriver gets turned down.
  test_id_prefix = attrib(str, default=None)
  check_flakiness_for_new_tests = attrib(bool, default=True)
  results_handler_name = attrib(str, default=None)
  retry_only_failed_tests = attrib(bool, default=True)

  @property
  def name(self):
    """The name of the step without a phase suffix.

    Additional suffixes may be present (e.g. os and GPU for swarming
    tests).
    """
    return self._name

  @property
  def canonical_name(self):
    """Canonical name of the test, no suffix attached."""
    return self._name

  @classmethod
  def create(cls, name, **kwargs):
    """Create a TestSpec.

    Arguments:
      * name - The name of the test. The returned spec will have this
        value for name.
      * kwargs - Additional keyword arguments that will be used to
        initialize the attributes of the returned spec. If the
        `target_name` keyword is not set, the `target_name` attribute of
        the returned spec have the value of `name`.
    """
    kwargs['target_name'] = kwargs.get('target_name') or name
    return cls(name=name, **kwargs)

  @property
  @abc.abstractmethod
  def test_class(self):
    """The test class associated with the spec."""
    raise NotImplementedError()  # pragma: no cover

  def get_test(self, chromium_tests_api):
    """Get the test described by the spec."""
    return self.test_class(self, chromium_tests_api)


class Test(AbstractTest):
  """
  Base class for a test suite that can be run locally or remotely.

  Tests consist of three components:
    * configuration
    * logic for how to run the test
    * results

  The logic for how to run a test should only depend on the configuration, and
  not on global state. For example, once a SwarmingTest has been configured,
  calling pre_run() or run() should work regardless of context.

  The only exception is for local tests that depend on file-system state. We do
  not want to add the complexity of a file-system cache to this class, so
  changes to the file-system can affect behavior of the test.

  As part of this contract, methods that access configuration or results should
  never take an "api" parameter; the configuration and results should be
  self-contained. Likewise, the logic for running tests must take an "api"
  parameter to access relevant recipe modules, but should not look up state from
  those modules; the state should already be stored in the configuration.
  """

  def __init__(self, spec, chromium_tests_api):
    super().__init__()

    self._spec = spec
    self._chromium_tests_api = chromium_tests_api

    self._test_options = TestOptions.create()

    # Set of test names that are either flaky or deterministically failing on
    # ToT, according to luci analysis criteria.
    # TODO (crbug/1314194): Update name to something else since
    # this also includes tests failing on ToT
    self._known_luci_analysis_flaky_failures = set()

    # Set of test names that barely meet luci analysis flaky criteria. These can
    # trigger a retry of the shard to avoid data cannibalization
    self._weak_luci_analysis_flaky_failures = set()

    # Used to track results of tests as reported by RDB. Separate from
    # _deterministic_failures above as that is populated by parsing the tests'
    # JSON results, while this field is populated entirely by RDB's API. Also
    # keyed via suffix like _deterministic_failures above.
    self._rdb_results = {}

    # Maps suffix to wheter or not the test exited non-zero. In conjunction with
    # _rdb_results above, can safely handle any type of test failure without
    # inspecting JSON.
    self._failure_on_exit_suffix_map = {}

    # Marks the test as using RTS. When enabled this suite will only run the
    # tests chosen by RTS.
    self._is_rts = False

    # Include the UTR instructions in the reproduction instruction to run this
    # test
    self._include_utr_instruction = False

  @property
  def spec(self) -> AbstractTestSpec:
    return self._spec

  @spec.setter
  def spec(self, value: AbstractTestSpec) -> None:
    self._spec = value

  @property
  def is_enabled(self) -> bool:
    return True

  @property
  def is_experimental(self) -> bool:
    return False

  @property
  def is_ci_only(self) -> bool:
    return False

  @property
  def option_flags(self) -> TestOptionFlags:
    return _DEFAULT_OPTION_FLAGS

  @property
  def test_options(self) -> TestOptions:
    return self._test_options

  @test_options.setter
  def test_options(self, value: TestOptions) -> None:
    self._test_options = value

  @property
  def name(self) -> str:
    return self.spec.name

  @property
  def canonical_name(self) -> str:
    return self.spec.canonical_name

  @property
  def target_name(self) -> str:
    return self.spec.target_name

  @property
  def check_flakiness_for_new_tests(self) -> bool:
    """Whether to check flakiness for new tests in try jobs.

    Default True unless specified in test spec json files.
    """
    return self.spec.check_flakiness_for_new_tests

  @property
  def test_id_prefix(self) -> str:
    return self.spec.test_id_prefix

  @property
  def isolate_target(self) -> str | None:
    """Returns isolate target name.

    Test types that use isolate should override this to return the
    appropriate isolate target.
    """
    return None

  @property
  def supports_rts(self) -> bool:
    """Determine whether the test supports RTS.

    Test types that support RTS should override this.
    """
    return False

  @property
  def is_rts(self) -> bool:
    return self._is_rts

  @is_rts.setter
  def is_rts(self, value: bool) -> None:
    if value:
      assert self.supports_rts
    self._is_rts = value

  @property
  def retry_only_failed_tests(self) -> bool:
    return self.spec.retry_only_failed_tests

  @property
  def api(self):
    """Returns the chromium_tests RecipeApi object associated with the test."""
    return self._chromium_tests_api

  def get_rdb_results(self, suffix: str) -> util.RDBPerSuiteResults:
    return self._rdb_results.get(suffix)

  def update_rdb_results(
      self,
      suffix: str,
      results: util.RDBPerSuiteResults,
  ) -> None:
    self._rdb_results[suffix] = results

  @property
  def known_luci_analysis_flaky_failures(self) -> Set[str]:
    return self._known_luci_analysis_flaky_failures

  @property
  def weak_luci_analysis_flaky_failures(self) -> Set[str]:
    return self._weak_luci_analysis_flaky_failures

  def add_known_luci_analysis_flaky_failures(
      self,
      test_names: Iterable[str],
  ) -> None:
    self._known_luci_analysis_flaky_failures.update(test_names)

  def add_weak_luci_analysis_flaky_failure(self, test_name: str) -> None:
    self._weak_luci_analysis_flaky_failures.add(test_name)

  def _update_failure_on_exit(self, suffix, failure_on_exit):
    self._failure_on_exit_suffix_map[suffix] = failure_on_exit
    rdb_results = self._rdb_results.get(suffix)
    if rdb_results:
      self._rdb_results[suffix] = rdb_results.with_failure_on_exit(
          failure_on_exit)

  def failure_on_exit(self, suffix: str) -> bool:
    return self._failure_on_exit_suffix_map.get(suffix, True)

  def did_complete(self, suffix: str) -> bool:
    return True

  def has_valid_results(self, suffix: str) -> bool:
    if suffix not in self._rdb_results:
      return False

    return not self._rdb_results[suffix].invalid

  def deterministic_failures(self, suffix: str) -> Set[str]:
    failure_msg = (
        'There is no data for the test run suffix ({0}). This should never '
        'happen as all calls to deterministic_failures() should first check '
        'that the data exists.'.format(suffix))
    assert suffix in self._rdb_results, failure_msg
    return {
        t.test_name for t in self._rdb_results[suffix].unexpected_failing_tests
    }

  def notrun_failures(self, suffix: str) -> Set[str]:
    assert self.has_valid_results(suffix), (
        'notrun_failures must only be called when the test run is known to '
        'have valid results.')
    return set(
        t.test_name for t in self._rdb_results[suffix].unexpected_skipped_tests)

  @property
  def uses_local_devices(self) -> bool:
    return False

  def step_name(self, suffix: str) -> str:
    step_name = _add_suffix(self.name, suffix)
    return step_name

  def _tests_to_retry(self, suffix: str) -> Set[str] | None:
    """Computes the tests to run on an invocation of the test suite.

    Args:
      suffix: A unique identifier for this test suite invocation. Only supported
          suffix will get a list of filtered tests. Others will return None
          meaning all tests should be run.

    Returns:
      A list of tests to retry. Returning None means all tests should be run.
    """
    if suffix == 'retry shards':
      valid_results, failures = self.failures_including_retry('')
      # Invalid results should be treated as if every test failed.
      return failures if valid_results else None

    if (suffix == 'without patch' or
        (suffix == 'retry shards with patch' and self.retry_only_failed_tests)):
      valid_results, failures = self.with_patch_failures_including_retry()
      # Invalid results should be treated as if every test failed.
      return failures if valid_results else None

    # If we don't recognize the step, then return None. This makes it easy for
    # bugs to slip through, but this matches the previous behavior. Importantly,
    # all the tests fail to pass a suffix.
    return None

  def _present_rdb_results(self,
                           step_result,
                           rdb_results,
                           as_nested_step=False):
    """Add a summary of test failures tracked in RDB to the given step_result.

    This duplicates info present in the "Test Results" tab in the new Milo UI.
    TODO(crbug.com/1245085): Remove this if/when all users have migrated to
    the new UI.

    Args:
      step_result: Active step to display test info on.
      rdb_results: A util.RDBPerSuiteResults instance containing the suite's
          results.
      as_nested_step: True if we need to treat step_result as a nested step
          for display purposes.
    """
    if not rdb_results or not rdb_results.unexpected_failing_tests:
      return

    _, failures_text = self.api.m.test_utils.limit_failures(
        sorted([t.test_name for t in rdb_results.unexpected_failing_tests]))
    display_text = self.api.m.presentation_utils.format_step_text(
        [['deterministic failures [caused step to fail]:', failures_text]])
    if as_nested_step:
      step_result.step_text += display_text
    else:
      step_result.presentation.step_text += display_text

  def _instructions_tag_for_suffix(self, instruction_type: str,
                                   suffix: str) -> str:
    identifiers = [self.name, instruction_type, suffix]
    return 'tag_' + str(
        hashlib.sha1(''.join(
            str(hashlib.sha1(identifier.encode('utf-8')).hexdigest())
            for identifier in identifiers
            if identifier).encode('utf-8')).hexdigest())


class AbstractSwarmingTest(AbstractTest):
  """Interface for tests that run on swarming."""

  @property
  @abc.abstractmethod
  def raw_cmd(self) -> Iterable[str]:
    raise NotImplementedError()  # pragma: no cover

  @raw_cmd.setter
  @abc.abstractmethod
  def raw_cmd(self, value: Iterable[str]) -> None:
    raise NotImplementedError()  # pragma: no cover

  @property
  @abc.abstractmethod
  def rts_raw_cmd(self) -> Iterable[str]:
    raise NotImplementedError()  # pragma: no cover

  @rts_raw_cmd.setter
  @abc.abstractmethod
  def rts_raw_cmd(self, value: Iterable[str]) -> None:
    raise NotImplementedError()  # pragma: no cover

  @property
  @abc.abstractmethod
  def relative_cwd(self) -> str:
    raise NotImplementedError()  # pragma: no cover

  @relative_cwd.setter
  @abc.abstractmethod
  def relative_cwd(self, value: str) -> None:
    raise NotImplementedError()  # pragma: no cover

  @property
  @abc.abstractmethod
  def isolate_profile_data(self) -> bool:
    raise NotImplementedError()  # pragma: no cover

  @property
  @abc.abstractmethod
  def shards(self) -> int:
    raise NotImplementedError()  # pragma: no cover

  @abc.abstractmethod
  def get_task(self, suffix: str) -> chromium_swarming.SwarmingTask:
    raise NotImplementedError()  # pragma: no cover

  @abc.abstractmethod
  def _create_task(
      self,
      suffix: str,
      cas_input_root: str,
      include_utr_instruction: bool,
      is_ci_only: bool,
  ) -> chromium_swarming.SwarmingTask:
    """Creates a swarming task. Must be overridden in subclasses.

    Args:
      suffix: Suffix added to the test name.
      cas_input_root: Hash or digest of the isolated test to be run.
      is_ci_only: Whether the test is marked as ci_only.

    Returns:
      A SwarmingTask object.
    """
    raise NotImplementedError()  # pragma: no cover


class AbstractSkylabTest(AbstractTest):
  """Interface for tests that run on skylab."""

  @property
  @abc.abstractmethod
  def is_GPU_test(self) -> bool:
    raise NotImplementedError()  # pragma: no cover

  @property
  @abc.abstractmethod
  def ctp_build_ids(self) -> dict[str, int]:
    raise NotImplementedError()  # pragma: no cover

  @property
  @abc.abstractmethod
  def test_runner_builds(self) -> dict[str, TestRunner]:
    raise NotImplementedError()  # pragma: no cover

  @property
  @abc.abstractmethod
  def exe_rel_path(self) -> str:
    raise NotImplementedError()  # pragma: no cover

  @exe_rel_path.setter
  @abc.abstractmethod
  def exe_rel_path(self, value: str) -> None:
    raise NotImplementedError()  # pragma: no cover

  @property
  @abc.abstractmethod
  def lacros_gcs_path(self) -> str:
    raise NotImplementedError()  # pragma: no cover

  @lacros_gcs_path.setter
  @abc.abstractmethod
  def lacros_gcs_path(self, value: str) -> None:
    raise NotImplementedError()  # pragma: no cover

  @property
  @abc.abstractmethod
  def build_output_dir(self) -> str:
    raise NotImplementedError()  # pragma: no cover

  @build_output_dir.setter
  @abc.abstractmethod
  def build_output_dir(self, value: str) -> None:
    raise NotImplementedError()  # pragma: no cover

@attrs()
class TestWrapperSpec(AbstractTestSpec):
  """Abstract base class for specs for test wrappers.

  Attributes:
    * test_spec - The spec for the wrapped test.
  """

  _test_spec = attrib(AbstractTestSpec)

  @classmethod
  def create(cls, test_spec, **kwargs):
    """Create a TestWrapperSpec.

    Arguments:
      * test_spec - The spec for the wrapped test.
      * kwargs - Additional keyword arguments that will be used to
        initialize the attributes of the returned spec.
    """
    return cls(test_spec, **kwargs)

  def get_test(self, chromium_tests_api):
    """Get the test described by the spec."""
    return self.test_wrapper_class(self,
                                   self._test_spec.get_test(chromium_tests_api),
                                   chromium_tests_api)

  @property
  @abc.abstractmethod
  def test_wrapper_class(self):
    """The test wrapper class associated with the spec."""
    raise NotImplementedError()  # pragma: no cover


class _TestDelegateAbstractMeta(abc.ABCMeta):
  """A metaclass that delegates abstract methods to a wrapped test.

  When a new class is created by this metaclass, any abstractmethod
  defined in the new class' bases will be overridden with a method that
  will call the method with the same name on the instance's _test
  attribute. Properties will be similarly overriden to get/set the
  attribute of the same name from the instance's test attribute.
  """

  def __new__(mcs, class_name, bases, namespace, /, **kwargs):
    for base in bases:
      for name, value in inspect.getmembers(base, mcs._is_abstractmethod):
        if name not in namespace:
          if isinstance(value, property):
            delegate = mcs._test_wrapper_delegate_property(name, value)
          else:
            delegate = mcs._test_wrapper_delegate_method(name)
          namespace[name] = delegate
    return super().__new__(mcs, class_name, bases, namespace, **kwargs)

  @staticmethod
  def _is_abstractmethod(obj):
    # How abc.ABC determines if a method is abstract, see
    # https://docs.python.org/3.8/library/abc.html#abc.abstractmethod
    return getattr(obj, '__isabstractmethod__', False)

  @staticmethod
  def _test_wrapper_delegate_method(name):

    def wrapped(self, *args, **kwargs):
      return getattr(self._test, name)(*args, **kwargs)

    return wrapped

  @classmethod
  def _test_wrapper_delegate_property(mcs, name, prop):
    fget = prop.fget
    if mcs._is_abstractmethod(fget):

      def fget(self):
        return getattr(self._test, name)

    fset = prop.fset
    if mcs._is_abstractmethod(fset):

      def fset(self, value):
        return setattr(self._test, name, value)

    return property(fget, fset)


class TestWrapper(
    AbstractSwarmingTest,
    AbstractSkylabTest,
    AbstractTest,
    # This handles delegating abstract methods in the base classes to _test
    metaclass=_TestDelegateAbstractMeta,
):
  """A base class for wrapping Tests to modify behavior.

  All abstract methods in base classes are automatically overriden to
  defer to the wrapped test. Subclasses are free to override the
  behavior for these methods.

  TestWrapper implements the interface for swarming tests and skylab
  tests because arbitrary tests can be wrapped, but the corresponding
  methods should only be called if the wrapped test is of the
  corresponding type.
  """

  def __init__(self, spec, test, chromium_tests_api):
    super().__init__()
    self._wrapper_spec = spec
    self._test = test
    self._chromium_tests_api = chromium_tests_api

  @property
  def api(self):
    """Returns the chromium_tests RecipeApi object associated with the test."""
    return self._chromium_tests_api

  @property
  def _disabled_message(self) -> str:
    """A message that explains why this test is disabled.

    An empty message indicates that the test is not disabled.
    TestWrappers that disable the test should override this to return a
    non-empty value.
    """
    return ''

  @property
  def _info_message(self) -> str:
    """Optional info to display in the step for the test.

    This will be called to provide additional information for enabled
    tests. This will not be called if disabled_message is non-empty.
    """
    return ''

  @property
  def is_enabled(self):
    return not self._disabled_message and self._test.is_enabled

  def pre_run(self,
              suffix: str,
              include_utr_instruction: bool = False,
              is_ci_only: bool = False) -> None:
    if not self._disabled_message:
      return self._test.pre_run(suffix, include_utr_instruction, is_ci_only)

  def run(
      self,
      checkout_dir: Path,
      source_dir: Path,
      build_dir: Path,
      suffix: str,
      info_messages: Iterable[str] = (),
  ) -> None:
    # Don't call methods on self that take the suffix, if the subclass performs
    # suffix modification then the suffix passed in should already be modified
    if self._disabled_message:
      info_messages = itertools.chain([self._disabled_message], info_messages)
      self.api.m.step.empty(
          self._test.step_name(suffix), step_text='\n'.join(info_messages))
      return

    if self._info_message:
      info_messages = itertools.chain([self._info_message], info_messages)
    return self._test.run(checkout_dir, source_dir, build_dir, suffix,
                          info_messages)


class CiOnlyTestSpec(TestWrapperSpec):
  """A spec for a test that is marked ci_only."""

  @property
  def test_wrapper_class(self):
    """The test wrapper class associated with the spec."""
    return CiOnlyTest


class CiOnlyTest(TestWrapper):
  """A test wrapper that runs the wrapped test that is marked ci_only."""

  def __init__(self, spec, test, chromium_tests_api):
    super().__init__(spec, test, chromium_tests_api)
    self._disabled = self._compute_disabled()

  def pre_run(self,
              suffix: str,
              include_utr_instruction: bool = False,
              is_ci_only: bool = False) -> None:
    if not self._disabled_message:
      self._test.pre_run(suffix, include_utr_instruction, is_ci_only=True)

  @property
  def is_ci_only(self) -> bool:
    return True

  @property
  def check_flakiness_for_new_tests(self) -> bool:
    # There isn't sufficient result history data for tests that are normally
    # CI-only.
    return False

  def _compute_disabled(self) -> bool:
    if not self.api.m.tryserver.is_tryserver:
      return False

    if (self.api.m.cv.active and
        self.api.m.cv.run_mode in self.api.MEGA_CQ_MODE_NAMES):
      return False

    enabled_tests_by_builder = self.api.get_footer_enabled_ci_only_tests()

    def test_is_enabled_for_builder(builder):
      tests_for_builder = enabled_tests_by_builder.get(builder, set())
      return ('*' in tests_for_builder or
              self.canonical_name in tests_for_builder)

    if test_is_enabled_for_builder('*'):
      return False

    if test_is_enabled_for_builder(self._builder_id):
      return False

    return True

  @property
  def _builder_id(self) -> str:
    spec = self._test.spec
    return f'{spec.waterfall_builder_group}:{spec.waterfall_buildername}'

  @property
  def _footer_to_enable(self) -> str:
    return f'{INCLUDE_CI_FOOTER}: {self._builder_id}|{self.canonical_name}'

  @property
  def _disabled_message(self):
    return (("This test is not being run because it is marked 'ci_only'."
             f" Add '{self._footer_to_enable}' to CL footers to override.")
            if self._disabled else '')

  @property
  def _info_message(self):
    if (self.api.m.cv.active and
        self.api.m.cv.run_mode in self.api.MEGA_CQ_MODE_NAMES):
      return 'This test is being run on Mega CQ runs'

    if self.api.m.tryserver.is_tryserver:
      return ('This test is being run due to the'
              f' {INCLUDE_CI_FOOTER} gerrit footer')
    return ('This test will not be run on try builders by default, add the '
            f'following CL footer to override: `{self._footer_to_enable}`\n')


class SuccessReuseTestSpec(TestWrapperSpec):
  """A spec for a test that is being skipped."""

  @property
  def test_wrapper_class(self):
    """The test wrapper class associated with the spec."""
    return SuccessReuseTest


class SuccessReuseTest(TestWrapper):
  """A test wrapper that provides steps for a skipped test."""

  @property
  def _disabled_message(self):
    return ("This test is not being run because it has passed in the last 24 "
            "hours with the equivalent patchset")


@attrs()
class ExperimentalTestSpec(TestWrapperSpec):
  """A spec for a test to be executed at some percentage."""

  experiment_percentage = attrib(int)

  @classmethod
  def create(cls, test_spec, experiment_percentage):  # pylint: disable=line-too-long,arguments-differ
    """Create an ExperimentalTestSpec.

    Arguments:
      * test_spec - The spec of the wrapped test.
      * experiment_percentage - The percentage chance that the test will be
        executed.
    """
    experiment_percentage = max(0, min(100, experiment_percentage))
    return super().create(
        test_spec, experiment_percentage=experiment_percentage)

  @property
  def test_wrapper_class(self):
    """The test wrapper class associated with the spec."""
    return ExperimentalTest


class ExperimentalTest(TestWrapper):
  """A test wrapper that runs the wrapped test on an experimental test.

  Experimental tests:
    - can run at <= 100%, depending on the experiment_percentage.
    - will not cause the build to fail.
  """

  def __init__(self, spec, test, chromium_tests_api):
    super().__init__(spec, test, chromium_tests_api)
    self._is_in_experiment = self._calculate_is_in_experiment()

  def _calculate_is_in_experiment(self):
    # Arbitrarily determine whether to run the test based on its experiment
    # key. Tests with the same experiment key should always either be in the
    # experiment or not; i.e., given the same key, this should always either
    # return True or False, but not both.
    #
    # The experiment key is either:
    #   - builder name + patchset + name of the test, for trybots
    #   - builder name + build number + name of the test, for CI bots
    #
    # These keys:
    #   - ensure that the same experiment configuration is always used for
    #     a given patchset
    #   - allow independent variation of experiments on the same test
    #     across different builders
    #   - allow independent variation of experiments on different tests
    #     across a single build
    #
    # The overall algorithm is copied from the CQ's implementation of
    # experimental builders, albeit with different experiment keys.
    criteria = [
        self.api.m.buildbucket.builder_name,
        (self.api.m.tryserver.gerrit_change and
         self.api.m.tryserver.gerrit_change.change) or
        self.api.m.buildbucket.build.number or '0',
        self.name,
    ]

    digest = hashlib.sha1(''.join(
        str(c) for c in criteria).encode('utf-8')).digest()
    short = struct.unpack_from('<H', digest)[0]
    return self._wrapper_spec.experiment_percentage * 0xffff >= short * 100

  def _experimental_suffix(self, suffix):
    if not suffix:
      return 'experimental'
    return '%s, experimental' % (suffix)

  def _actually_has_valid_results(self, suffix):
    """Check if the underlying test produced valid results.

    The ExperimentalTest reports that it always has valid results, so
    various result methods (failures, notrun_failures, etc.) will be
    called. If the underlying test does not have valid results, then
    calling the superclass version of the method would violate the
    contract, so this method indicates if calling the superclass version
    should be safe.
    """
    return super().has_valid_results(self._experimental_suffix(suffix))

  @property
  def _disabled_message(self):
    if self._is_in_experiment:
      return ''
    return 'This test was not selected for its experiment in this build'

  @property
  def _info_message(self):
    return ('This is an experimental test that was selected for this build,'
            ' failures will not cause build failures')

  @property
  def is_experimental(self) -> bool:
    return True

  def step_name(self, suffix: str) -> str:
    return self._test.step_name(self._experimental_suffix(suffix))

  #override
  def pre_run(self,
              suffix: str,
              include_utr_instruction: bool = False,
              is_ci_only: bool = False) -> None:
    try:
      return super().pre_run(
          self._experimental_suffix(suffix),
          include_utr_instruction,
          is_ci_only=is_ci_only)
    except self.api.m.step.StepFailure:
      pass

  #override
  def run(
      self,
      checkout_dir: Path,
      source_dir: Path,
      build_dir: Path,
      suffix: str,
      info_messages: Iterable[str] = (),
  ) -> None:
    try:
      return super().run(
          checkout_dir,
          source_dir,
          build_dir,
          self._experimental_suffix(suffix),
          info_messages,
      )
    except self.api.m.step.StepFailure as e:
      return e.result

  def get_task(self, suffix: str) -> chromium_swarming.SwarmingTask:
    return self._test.get_task(self._experimental_suffix(suffix))

  #override
  def has_valid_results(self, suffix: str) -> bool:
    # Call the wrapped test's implementation in case it has side effects, but
    # ignore the result.
    super().has_valid_results(self._experimental_suffix(suffix))
    return True

  #override
  def failure_on_exit(self, suffix: str) -> bool:
    # Call the wrapped test's implementation in case it has side effects, but
    # ignore the result.
    super().failure_on_exit(self._experimental_suffix(suffix))
    return False

  #override
  def deterministic_failures(self, suffix: str) -> Set[str]:
    if self._actually_has_valid_results(suffix):
      # Call the wrapped test's implementation in case it has side effects,
      # but ignore the result.
      super().deterministic_failures(self._experimental_suffix(suffix))
    return []

  #override
  def notrun_failures(
      self,
      suffix: str,
  ) -> Set[str]:  # pragma: no cover
    if self._actually_has_valid_results(suffix):
      # Call the wrapped test's implementation in case it has side effects,
      # but ignore the result.
      super().notrun_failures(self._experimental_suffix(suffix))
    return set()

  def get_invocation_names(self, suffix: str) -> Iterable[str]:
    return super().get_invocation_names(self._experimental_suffix(suffix))

  def get_rdb_results(self, suffix: str) -> util.RDBPerSuiteResults:
    return super().get_rdb_results(self._experimental_suffix(suffix))

  def update_rdb_results(
      self,
      suffix: str,
      results: util.RDBPerSuiteResults,
  ) -> None:
    return super().update_rdb_results(
        self._experimental_suffix(suffix), results)


class LocalTest(Test):
  """Abstract class for local tests.

  This class contains logic related to running tests locally, namely for local
  RDB invocations. All of which is intended to be shared with any subclasses.
  """

  def __init__(self, spec, chromium_tests_api):
    super().__init__(spec, chromium_tests_api)
    self._suffix_to_invocation_names = {}

  @property
  def locality(self) -> TestLocality:
    return TestLocality.LOCAL

  def pre_run(self,
              suffix: str,
              include_utr_instruction: bool = False,
              is_ci_only: bool = False) -> None:
    del suffix, is_ci_only

  def get_invocation_names(self, suffix: str) -> Iterable[str]:
    inv = self._suffix_to_invocation_names.get(suffix)
    return [inv] if inv else []

  def _prep_local_rdb(self,
                      source_dir: Path,
                      *,
                      temp=None,
                      include_artifacts=True):
    """Returns a ResultDB instance suitable for local test runs.

    Main difference between remote swarming runs and local test runs (ie:
    ScriptTests and LocalIsolatedScriptTests) is the location of a temp
    result file and the location of the result_adapter binary.

    Args:
      source_dir: The path to the top-level repo.
      temp: Path to temp file to store results.
      include_artifacts: If True, add the parent dir of temp as an artifact dir.
    """
    temp = temp or self.api.m.path.mkstemp()
    artifact_dir = self.api.m.path.dirname(temp) if include_artifacts else ''
    base_tags = None
    if (self.api.m.chromium.c and self.api.m.chromium.c.TARGET_PLATFORM):
      base_tags = (('target_platform', self.api.m.chromium.c.TARGET_PLATFORM),)
    resultdb = attr.evolve(
        self.spec.resultdb,
        artifact_directory=artifact_dir,
        base_tags=base_tags,
        base_variant=dict(
            self.spec.resultdb.base_variant or {},
            test_suite=self.canonical_name),
        result_adapter_path=str(source_dir / 'tools/resultdb/result_adapter'),
        result_file=self.api.m.path.abspath(temp),
        # Give each local test suite its own invocation to make it easier to
        # fetch results.
        include=True)
    return resultdb

  def _update_inv_name_from_stderr(self, stderr, suffix):
    """Scans the given stderr for a local test for the test's invocation name.

    And updates self._suffix_to_invocation_names with the name.

    Args:
      stderr: stderr from the step_data.StepData obj returned from the step
          wrapped with `rdb stream -new ...`.
      suffix: String suffix representing the phase of the build.
    """
    # TODO(crbug.com/1227180): Specify our own custom invocation name rather
    # than parsing stderr.
    match = RDB_INVOCATION_NAME_RE.search(stderr)
    if match:
      inv_name = match.group(1)
      self._suffix_to_invocation_names[suffix] = inv_name


@attrs()
class ScriptTestSpec(TestSpec):
  """A spec for a test that runs a script.

  Attributes:
    * script - The filename of a script to run. The script must be
      located within the //testing/scripts directory of the checkout.
    * compile_targets - The compile targets that need to be built to run
      the script.
    * script_args - Arguments to be passed to the script.
  """

  script = attrib(str)
  compile_targets = attrib(sequence[str])
  script_args = attrib(command_args, default=())

  @property
  def test_class(self):
    """The test class associated with the spec."""
    return ScriptTest


class ScriptTest(LocalTest):
  """
  Test which uses logic from script inside chromium repo.

  This makes it possible to keep the logic src-side as opposed
  to the build repo most Chromium developers are unfamiliar with.

  Another advantage is being to test changes to these scripts
  on trybots.

  All new tests are strongly encouraged to use this infrastructure.
  """

  def compile_targets(self) -> Iterable[str]:
    return self.spec.compile_targets

  def run(
      self,
      checkout_dir: Path,
      source_dir: Path,
      build_dir: Path,
      suffix: str,
      info_messages: Iterable[str] = (),
  ) -> None:
    del checkout_dir

    run_args = []

    tests_to_retry = self._tests_to_retry(suffix)
    if tests_to_retry:
      run_args.extend(
          ['--filter-file',
           self.api.m.json.input(sorted(tests_to_retry))])

    resultdb = self._prep_local_rdb(source_dir)

    step_test_data = lambda: (
        self.api.m.json.test_api.output({
            'valid': True,
            'failures': []
        }) + self.api.m.raw_io.test_api.stream_output_text(
            'rdb-stream: included "invocations/test-name" in '
            '"invocations/build-inv"', 'stderr'))

    script_args = []
    if self.spec.script_args:
      script_args = ['--args', self.api.m.json.input(self.spec.script_args)]

    # Enforce that all scripts are in the specified directory for
    # consistency.
    common_args, paths, properties = (
        self.api.m.chromium_tests.get_common_args_for_scripts(
            source_dir, build_dir))
    cmd = ([
        'vpython3',
        (source_dir / 'testing/scripts' /
         self.api.m.path.basename(self.spec.script))
    ] + common_args + script_args +
           ['run', '--output', self.api.m.json.output()] + run_args)
    step_name = self.step_name(suffix)
    if resultdb:
      cmd = resultdb.wrap(self.api.m, cmd, step_name=step_name)
    result = self.api.m.step(
        step_name,
        cmd=cmd,
        raise_on_failure=False,
        stderr=self.api.m.raw_io.output_text(
            add_output_log=True, name='stderr'),
        step_test_data=step_test_data)
    result.presentation.logs['paths.json'] = str(paths)
    result.presentation.logs['properties.json'] = str(properties)

    status = result.presentation.status

    failures = None
    if result.json.output:
      failures = result.json.output.get('failures')
    if failures is None:
      self.api.m.step.empty(
          '%s with suffix %s had an invalid result' % (self.name, suffix),
          status=self.api.m.step.FAILURE,
          step_text=(
              'The recipe expected the result to contain the key \'failures\'.'
              ' Contents are:\n%s' %
              self.api.m.json.dumps(result.json.output, indent=2)))

    self._update_failure_on_exit(suffix, result.retcode != 0)

    _, failures = self.api.m.test_utils.limit_failures(failures)
    result.presentation.step_text += (
        self.api.m.presentation_utils.format_step_text([['failures:',
                                                         failures]]))

    self._update_inv_name_from_stderr(result.stderr, suffix)

    _present_info_messages(result.presentation, self, info_messages)

    self.api.m.step.raise_on_failure(result, status)


@attrs()
class LocalGTestTestSpec(TestSpec):
  """A spec for a test that runs a gtest-based test locally.

  Attributes:
    * args - Arguments to be passed to the test.
    * use_xvfb - Whether to use the X virtual frame buffer. Only has an
      effect on Linux. Mostly harmless to set this, except on GPU
      builders.
  """

  args = attrib(command_args, default=())
  use_xvfb = attrib(bool, default=True)

  @property
  def test_class(self):
    """The test class associated with the spec."""
    return LocalGTestTest


class LocalGTestTest(LocalTest):


  @property
  def option_flags(self) -> TestOptionFlags:
    return _GTEST_OPTION_FLAGS

  @property
  def uses_local_devices(self) -> bool:
    return True

  def compile_targets(self) -> Iterable[str]:
    return [self.target_name]

  def run(
      self,
      checkout_dir: Path,
      source_dir: Path,
      build_dir: Path,
      suffix: str,
      info_messages: Iterable[str] = (),
  ) -> None:
    tests_to_retry = self._tests_to_retry(suffix)
    # pylint apparently gets confused by a property in a base class where the
    # setter is overridden
    test_options = self.test_options.for_running(suffix, tests_to_retry)  # pylint: disable=no-member
    args = test_options.add_args(self.spec.args, self.option_flags)

    if tests_to_retry:
      args = _merge_arg(args, '--gtest_filter', ':'.join(tests_to_retry))

    resultdb = self._prep_local_rdb(source_dir, include_artifacts=False)
    gtest_results_file = self.api.m.json.output(
        add_json_log=False, leak_to=resultdb.result_file)

    step_test_data = lambda: (
        self.api.m.test_utils.test_api.canned_gtest_output(True) + self.api.m.
        raw_io.test_api.stream_output_text(
            'rdb-stream: included "invocations/some-inv-name" in '
            '"invocations/parent-inv-name"', 'stderr'))

    kwargs = {
        'name': self.step_name(suffix),
        'args': args,
        'step_test_data': step_test_data,
        'resultdb': resultdb,
        'parse_gtest_output': True,
    }
    kwargs['xvfb'] = self.spec.use_xvfb
    kwargs['test_type'] = self.name
    kwargs['test_launcher_summary_output'] = gtest_results_file

    step_result = self.api.m.chromium.runtest(
        checkout_dir,
        build_dir,
        self.target_name,
        builder_group=self.spec.waterfall_builder_group,
        stderr=self.api.m.raw_io.output_text(
            add_output_log=True, name='stderr'),
        raise_on_failure=False,
        **kwargs)

    status = step_result.presentation.status

    # TODO(kbr): add functionality to generate_gtest to be able to force running
    # these local gtests via isolate from the src-side JSON files.
    # crbug.com/584469
    self._update_failure_on_exit(suffix, step_result.retcode != 0)

    self._update_inv_name_from_stderr(step_result.stderr, suffix)

    _present_info_messages(step_result.presentation, self, info_messages)

    self.api.m.step.raise_on_failure(step_result, status)


def _clean_step_name(step_name, suffix):
  """
  Based on
  https://crrev.com/48baea8de14f5a17aef2edd7d0b8c00d7bbf7909/go/src/infra/appengine/test-results/frontend/builders.go#260
  Some tests add 'suffixes' in addition to the regular suffix, in order to
  distinguish different runs of the same test suite on different hardware. We
  don't want this to happen for layout test result uploads, since we have no
  easy way to discover this new name. So, we normalize the step name before
  uploading results.
  """
  if ' ' in step_name:
    step_name = step_name.split(' ')[0]

  return _add_suffix(step_name, suffix)


def _archive_layout_test_results(api,
                                 step_name,
                                 step_suffix=None,
                                 swarm_task_ids=None):
  # LayoutTest's special archive and upload results
  results_dir = api.path.start_dir / 'layout-test-results'

  buildername = api.buildbucket.builder_full_name
  buildnumber = api.buildbucket.build.number

  gcs_bucket = 'chromium-layout-test-archives'
  cmd = [
      'python3',
      api.chromium_tests.resource('archive_layout_test_results.py'),
      '--results-dir',
      results_dir,
      '--build-number',
      buildnumber,
      '--builder-name',
      buildername,
      '--gs-bucket',
      f'gs://{gcs_bucket}',
  ]
  if swarm_task_ids:
    cmd.extend(['--task-ids', ','.join(swarm_task_ids)])

  # TODO: The naming of the archive step is clunky, but the step should
  # really be triggered src-side as part of the post-collect merge and
  # upload, and so this should go away when we make that change.
  step_name = _clean_step_name(step_name, step_suffix)
  cmd += ['--step-name', step_name]
  archive_step_name = 'archive results for ' + step_name

  cmd += ['--bot-utils-gsutil-py-path', api.depot_tools.gsutil_py_path]
  archive_result = api.step(archive_step_name, cmd)

  # TODO(tansell): Move this to render_results function
  sanitized_buildername = re.sub('[ .()]', '_', buildername)

  # Also link the new version of results.html which would fetch test results
  # from result DB. It will have parameters in following format:
  # ?json=<full_results_jsonp.js>
  base = f"https://{gcs_bucket}.storage.googleapis.com/results.html"
  path_full_results_jsonp = "%s/%s/%s/full_results_jsonp.js" % (
      sanitized_buildername, buildnumber, urllib.parse.quote(step_name))
  web_test_results = f"{base}?json={path_full_results_jsonp}"
  archive_result.presentation.links['web_test_results'] = web_test_results
  return web_test_results


@attrs()
class SwarmingTestSpec(TestSpec):
  """Spec for a test that runs via swarming.

  Attributes:
    * cipd_packages - The CIPD packages to be loaded for the test's
      swarming tasks.
    * containment_type - The type of containment to use for the test's
      swarming tasks. See `swarming.py trigger --help` for more info.
    * dimensions - Requested dimensions of the test. The keys are
      dimension names. The values are the value of the dimensions or
      None to clear a dimension.
    * expiration - The expiration timeout in seconds of the test's
      swarming tasks.
    * optional_dimensions - Optional dimensions that create additional
      fallback task sices. The keys are cumulative expiration times for
      the additional slices mapping to dicts of the same form as the
      `dimensions` attribute. Additional task slices will be created for
      each item, in order of the expiration time for the item using the
      dimensions specified in the value. The final slice will set the
      dimensions according to the `dimensions` attribute.
    * extra_suffix - An additional suffix applied to the test's step
      name.
    * hard_timeout - The execution timeout in seconds of the test's
      swarming tasks.
    * io_timeout - The maximum amount of time in seconds swarming will
      allow the task to be silent (no stdout or stderr).
    * trigger_script - An optional script used for triggering the test's
      swarming tasks.
    * merge - An optional script used for merging results between the
      test's swarming tasks.
    * args - Arguments to be passed to the test.
    * isolate_profile_data - Whether to isolate profile data during task
      execution.
    * named_caches - Named caches to mount for the test's swarming
      tasks. The keys are the named of the cache and the values are the
      path relative to the swarming task's root directory where the
      cache should be mounted.
    * shards - The number of shards to trigger.
    * server - The Swarming server to run the test on. If not provided, the
      server the current task is running on will be used.
    * service_account - The service account to run the test's swarming
      tasks as.
    * idempotent - Whether to mark the test's swarming tasks as
      idempotent. If not provided, the default logic used by the
      `chromium_swarming` recipe module will be used.
    * realm - The realm to run the Swarming task in.
  """
  # pylint: disable=abstract-method

  cipd_packages = attrib(sequence[chromium_swarming.CipdPackage], default=())
  containment_type = attrib(str, default=None)
  dimensions = attrib(mapping[str, ...], default={})
  expiration = attrib(int, default=None)
  optional_dimensions = attrib(mapping[int, mapping[str, ...]], default={})
  extra_suffix = attrib(str, default=None)
  hard_timeout = attrib(int, default=None)
  io_timeout = attrib(int, default=None)
  trigger_script = attrib(chromium_swarming.TriggerScript, default=None)
  merge = attrib(chromium_swarming.MergeScript, default=None)
  args = attrib(command_args, default=())
  isolate_profile_data = attrib(bool, False)
  named_caches = attrib(mapping[str, str], default={})
  shards = attrib(int, default=1)
  server = attrib(str, default=None)
  service_account = attrib(str, default=None)
  idempotent = attrib(bool, default=None)
  realm = attrib(str, default=None)

  @classmethod
  def create(cls, name, **kwargs):
    """Create a SwarmingTestSpec.

    Arguments:
      * name - The name of the test.
      * kwargs - Additional keyword arguments that will be used to
        initialize the attributes of the returned spec. If the keyword
        `extra_suffix` is not set, a value will be computed if the
        `'gpu'` dimension is specified or if the `'os'` dimension is
        `'Android'` and the `'device_type'` dimension is set.
    """
    dimensions = kwargs.get('dimensions', {})
    extra_suffix = kwargs.pop('extra_suffix', None)
    if extra_suffix is None:
      if dimensions.get('gpu'):
        extra_suffix = cls._get_gpu_suffix(dimensions)
      elif dimensions.get('os') == 'Android' and dimensions.get('device_type'):
        extra_suffix = cls._get_android_suffix(dimensions)
    return super().create(name, extra_suffix=extra_suffix, **kwargs)

  @property
  def name(self):
    if self.extra_suffix:
      return '%s %s' % (self._name, self.extra_suffix)
    return self._name

  def with_shards(self, shards):
    return attr.evolve(self, shards=int(shards))

  @staticmethod
  def _get_gpu_suffix(dimensions):
    all_gpu_dimensions = dimensions.get('gpu', '').split('|')
    all_gpu_identifiers = []

    for gpu_dimension in all_gpu_dimensions:
      # Expected format if present is vendor:model-driver
      split_gpu_dimension = gpu_dimension.split(':')
      gpu_vendor_id = split_gpu_dimension[0].lower()
      vendor_ids = {
          '8086': 'Intel',
          '10de': 'NVIDIA',
          '1002': 'AMD',
          'none': 'SwiftShader',  # explicit 'none' means requesting SwS
      }
      gpu_model_id = ''
      if len(split_gpu_dimension) > 1:
        gpu_model_id = split_gpu_dimension[1].split('-')[0]
      if gpu_vendor_id in vendor_ids:
        if gpu_model_id:
          all_gpu_identifiers.append('%s 0x%s' %
                                     (vendor_ids[gpu_vendor_id], gpu_model_id))
        else:
          all_gpu_identifiers.append(vendor_ids[gpu_vendor_id])
      else:
        if gpu_model_id:
          all_gpu_identifiers.append('(%s:%s)' % (gpu_vendor_id, gpu_model_id))
        else:
          all_gpu_identifiers.append('(%s)' % gpu_vendor_id)
    gpu_identifier = '/'.join(all_gpu_identifiers)

    os = dimensions.get('os', '')
    if os.lower().startswith('mac'):
      if dimensions.get('hidpi', '') == '1':
        os_name = 'Mac Retina'
      else:
        os_name = 'Mac'
    elif os.lower().startswith('windows'):
      os_name = 'Windows'
    else:
      # TODO(crbug/1018836): Use distro specific name instead of Linux.
      os_name = 'Linux'

    return 'on %s GPU on %s' % (gpu_identifier, os_name)

  @staticmethod
  def _get_android_suffix(dimensions):
    device_codenames = {
        'angler': 'Nexus 6P',
        'athene': 'Moto G4',
        'blueline': 'Pixel 3',
        'bullhead': 'Nexus 5X',
        'cheetah': 'Pixel 7 Pro',
        'crosshatch': 'Pixel 3 XL',
        'dragon': 'Pixel C',
        'flame': 'Pixel 4',
        'flo': 'Nexus 7 [2013]',
        'flounder': 'Nexus 9',
        'foster': 'NVIDIA Shield',
        'fugu': 'Nexus Player',
        'goyawifi': 'Galaxy Tab 3',
        'grouper': 'Nexus 7 [2012]',
        'hammerhead': 'Nexus 5',
        'herolte': 'Galaxy S7 [Global]',
        'heroqlteatt': 'Galaxy S7 [AT&T]',
        'j5xnlte': 'Galaxy J5',
        'm0': 'Galaxy S3',
        'mako': 'Nexus 4',
        'manta': 'Nexus 10',
        'marlin': 'Pixel 1 XL',
        'oriole': 'Pixel 6',
        'panther': 'Pixel 7',
        'raven': 'Pixel 6 Pro',
        'redfin': 'Pixel 5',
        'sailfish': 'Pixel 1',
        'sargo': 'Pixel 3a',
        'shamu': 'Nexus 6',
        'shiba': 'Pixel 8',
        'sprout': 'Android One',
        'sunfish': 'Pixel 4a',
        'taimen': 'Pixel 2 XL',
        'tangorpro': 'Pixel Tablet',
        'walleye': 'Pixel 2',
        'zerofltetmo': 'Galaxy S6',
    }
    targetted_device = dimensions['device_type']
    product_name = device_codenames.get(targetted_device, targetted_device)
    return 'on Android device %s' % product_name


class SwarmingTest(Test, AbstractSwarmingTest):
  # Some suffixes should have marginally higher priority. See crbug.com/937151.
  SUFFIXES_TO_INCREASE_PRIORITY = set(
      ['without patch', 'retry shards with patch'])
  # The flake endorser triggers test "shards" as different test suffixes.
  # For example, there could be an android_browsertests (check flakiness
  # shard #0) and android_browsertests (check flakiness shard #1). Since the
  # shard # can vary, we need to check if 'check flakiness' is in the test
  # suffix being triggered.
  # Why these shards need higher priority: crbug.com/1366122
  CHECK_FLAKINESS_SUFFIX = 'check flakiness'

  def __init__(self, spec, chromium_tests_api):
    super().__init__(spec, chromium_tests_api)

    self._tasks = {}
    self._raw_cmd = []
    self._rts_raw_cmd = []
    self._relative_cwd = None

  def _dispatches_to_windows(self):
    if self.spec.dimensions:
      os = self.spec.dimensions.get('os', '')
      return os.startswith('Windows')
    return False

  @property
  def locality(self) -> TestLocality:
    return TestLocality.SWARMING

  @property
  def isolate_target(self) -> str:
    return self.target_name

  @property
  def isolate_profile_data(self) -> bool:
    return self.spec.isolate_profile_data

  @property
  def raw_cmd(self) -> Iterable[str]:
    return self._raw_cmd

  @raw_cmd.setter
  def raw_cmd(self, value: Iterable[str]) -> None:
    self._raw_cmd = value

  @property
  def rts_raw_cmd(self) -> Iterable[str]:
    return self._rts_raw_cmd

  @rts_raw_cmd.setter
  def rts_raw_cmd(self, value: Iterable[str]) -> None:
    self._rts_raw_cmd = value

  @property
  def relative_cwd(self) -> str:
    return self._relative_cwd

  @relative_cwd.setter
  def relative_cwd(self, value: str) -> None:
    self._relative_cwd = value

  @property
  def shards(self) -> int:
    return self.spec.shards

  @property
  def supports_rts(self) -> bool:
    return bool(self.rts_raw_cmd)

  def _add_instructions(self, suffix: str, include_utr_instruction: bool):
    """Gets the reproduction instructions to be attached to the invocation"""

    # Attempt to find the last compile for the local step dependency
    local_dependency = self.api.m.repro_instructions.get_dependency(r'compile')

    task = self._tasks[suffix]
    prebuilt_task_id = task.trigger_output.get('tasks',
                                               []).get(0,
                                                       {}).get('task_id', None)
    cas_digest = self.api.m.isolate.isolated_tests.get(self.isolate_target)
    remote_instruction = None
    remote_dependency = None
    builder = self.api.m.properties.get('orchestrator', {}).get(
        'builder_name', self.api.m.buildbucket.build.builder.builder)

    def _create_prebuilt_instruction(extra_args: Iterable[str]):
      prebuilt_instruction = []
      if include_utr_instruction:
        prebuilt_instruction.append('**Re-trigger in swarming:**')
        prebuilt_instruction.append(
            get_utr_instruction(
                'test',
                self.api.m.buildbucket.build.builder.project,
                self.api.m.led.shadowed_bucket or
                self.api.m.buildbucket.build.builder.bucket,
                builder,
                [self.name],
                utr_flags=['--reuse-task', prebuilt_task_id],
                extra_args=extra_args,
            ))
        prebuilt_instruction.append(
            '*Note: additional args can be used by extending this command*')
        prebuilt_instruction.append('')
      if cas_digest:
        prebuilt_instruction.append('**Download test binary:**')
        prebuilt_instruction.append('```./tools/luci-go/cas download '
                                    f'-cas-instance {self.api.m.cas.instance} '
                                    f'-digest {cas_digest} -dir tmp```')
        prebuilt_instruction.append('Run `./tools/luci-go/cas login` if needed')
        prebuilt_instruction.append(
            '*See the local instructions tab and run from the tmp dir*')
      return '<br/>'.join(prebuilt_instruction)

    if include_utr_instruction:
      remote_dependency = self.api.m.repro_instructions.get_dependency(
          r'bot_update')
      remote_instruction = get_utr_instruction(
          'compile-and-test',
          self.api.m.buildbucket.build.builder.project,
          self.api.m.led.shadowed_bucket or
          self.api.m.buildbucket.build.builder.bucket,
          builder,
          [self.name],
      )
    self.api.m.repro_instructions.create_step_instruction(
        self._instructions_tag_for_suffix('step', suffix),
        f'{self.name} instructions',
        local_content=task.get_local_instruction(),
        remote_content=remote_instruction,
        local_dependency=local_dependency,
        remote_dependency=remote_dependency,
        prebuilt_content=_create_prebuilt_instruction(None),
    )

    test_invocations = [
        inv if '/' not in inv else inv.split('/')[1]
        for inv in task.get_invocation_names()
    ]

    if test_invocations:
      # Escaping the brackets makes the placeholder a constant string of
      # {{test.tags.test_name}} which will be replaced in milo with the
      # actual test name
      filter_arg = f'{self.option_flags.filter_flag}={{{{test.tags.test_name}}}}'
      if include_utr_instruction:
        remote_instruction = get_utr_instruction(
            'compile-and-test',
            self.api.m.buildbucket.build.builder.project,
            self.api.m.led.shadowed_bucket or
            self.api.m.buildbucket.build.builder.bucket,
            self.api.m.buildbucket.build.builder.builder.replace(
                '-compilator', ''), [self.name],
            extra_args=['--', filter_arg])
        self.api.m.repro_instructions.create_test_result_instruction(
            self._instructions_tag_for_suffix('test', suffix),
            f'{self.name} instructions',
            test_invocations,
            local_content=task.get_local_instruction(extra_args=[filter_arg]),
            remote_content=remote_instruction,
            prebuilt_content=_create_prebuilt_instruction(['--', filter_arg]),
            local_dependency=local_dependency,
            remote_dependency=remote_dependency,
        )

  def did_complete(self, suffix) -> bool:
    return suffix in self._tasks and not self._tasks[
        suffix].has_incomplete_shards

  def has_valid_results(self, suffix: str) -> bool:
    if not super().has_valid_results(suffix):
      return False

    rdb_results = self.get_rdb_results(suffix)
    task = self.get_task(suffix)

    if not rdb_results or not task:
      return False

    num_failed_shards = len(task.failed_shards)

    rdb_invocations_with_fails = {
        test.invocation_id for test in rdb_results.unexpected_failing_tests
    }

    # This check ensure that all shards from the task that marked as a failure
    # have corresponding tests in RDB. This will ensure that when a shard fails
    # and reports no results to RDB, the specific shard will be marked as invalid,
    # labeling the whole step as invalid.
    return num_failed_shards == len(rdb_invocations_with_fails)

  @abc.abstractmethod
  def _create_task(
      self,
      suffix: str,
      cas_input_root: str,
      include_utr_instruction: bool,
      is_ci_only: bool,
  ) -> chromium_swarming.SwarmingTask:
    """Creates a swarming task. Must be overridden in subclasses.

    Args:
      suffix: Suffix added to the test name.
      cas_input_root: Hash or digest of the isolated test to be run.
      is_ci_only: Whether the test is marked as ci_only.

    Returns:
      A SwarmingTask object.
    """
    raise NotImplementedError()  # pragma: no cover

  def _handle_results(
      self,
      suffix: str,
      step_result: step_data.StepData,
  ) -> None:
    """Handle step results from collecting the swarming tasks.

    Swarming tests that require additional handling after collecting the
    tasks should override this.
    """
    del suffix, step_result

  def _shards_to_retry_with(self, original_num_shards, num_tests_to_retry,
                            test_options):
    """Calculates the number of shards to run when retrying this test.

    Args:
      original_num_shards: The number of shards used to run the test when it
                           first ran.
      num_tests_to_retry: The number of tests we're trying to retry.
      test_options: The TestOptions for running for a given retry.

    Returns:
      The number of shards to use when retrying tests that failed.

    Note that this assumes this test has run 'with patch', and knows how many
    tests ran in that case. It doesn't make sense to ask how this test should
    run when retried, if it hasn't run already.
    """
    if original_num_shards <= 1:
      return original_num_shards

    total_tests_ran = max(
        result.total_tests_ran for result in self._rdb_results.values())
    assert total_tests_ran, (
        "We cannot compute the total number of tests to re-run if no tests "
        "were run 'with patch'. Expected the results tracker to contain key "
        "'total_tests_ran', but it didn't")

    # We want to approximately match the previous shard load. Using only one
    # shard can lead to a single shard running many more tests than it
    # normally does. As the number of tests to retry approaches the total
    # number of total tests ran, we get closer to running with the same number
    # of shards as we originally were triggered with.
    # Note that this technically breaks when we're running a tryjob on a CL
    # which changes the number of tests to be run.
    # Clamp to be 1 < value < original_num_shards, so that we don't trigger too
    # many shards, or 0 shards.
    #
    # Since we repeat failing tests REPEAT_COUNT_FOR_FAILING_TESTS times, we
    # artificially inflate the number of shards by that factor, since we expect
    # tests to take that much longer to run.
    #
    # We never allow more than num_test_to_retry shards, since that would leave
    # shards doing nothing.
    return int(
        min(
            max(
                original_num_shards * (test_options.repeat_count or 1) *
                (float(num_tests_to_retry) / total_tests_ran), 1),
            original_num_shards,
            num_tests_to_retry,
        ))

  def _apply_swarming_task_config(self, task, suffix, filter_flag,
                                  filter_delimiter, extra_args, is_ci_only):
    """Applies shared configuration for swarming tasks.
    """
    add_one_test_shard_enabled = False
    shards = self.spec.shards
    # When this experiment is enabled, we want to trigger suites with one
    # additional shard so that we can go back and query for test overhead
    # estimations.
    # See go/nplus1shardsproposal
    # For now, only add a shard if the suite already runs with multiple shards
    # Although rare, some suites may be swarmed but unable to work properly
    # with more than one shard.
    buildbucket_experiments = self.api.m.buildbucket.build.input.experiments
    add_one_test_shard_enabled = (
        'chromium.add_one_test_shard' in buildbucket_experiments and
        suffix in ['with patch', 'retry shards with patch'] and shards > 1)
    if add_one_test_shard_enabled:
      shards += 1

    tests_to_retry = self._tests_to_retry(suffix)
    if tests_to_retry:
      # The filter list is eventually passed to the binary over the command
      # line.  On Windows, the command line max char limit is 8191 characters.
      # On other OSes, the max char limit is over 100,000 characters. We avoid
      # sending the filter list if we're close to the limit -- this causes all
      # tests to be run.
      char_limit = 6000 if self._dispatches_to_windows() else 90000
      expected_filter_length = (
          sum(len(x) for x in tests_to_retry) +
          len(tests_to_retry) * len(filter_delimiter))
      if expected_filter_length >= char_limit:
        tests_to_retry = None

    test_options = self.test_options.for_running(suffix, tests_to_retry)
    args = test_options.add_args(extra_args, self.option_flags)
    if tests_to_retry:
      test_list = filter_delimiter.join(tests_to_retry)
      # Append filter with individual tests to retry
      args = _merge_arg(args, filter_flag, test_list)
      shards = self._shards_to_retry_with(shards, len(tests_to_retry),
                                          test_options)

    task.extra_args.extend(args)
    task.shards = shards

    task_request = task.request
    task_slice = task_request[0]

    merge = self.spec.merge
    using_pgo = self.api.m.chromium_tests.m.pgo.using_pgo
    if self.api.m.pinlist.upload_pinlist:
      merge = self.api.m.pinlist.shard_merge()
    elif self.isolate_profile_data or using_pgo:
      # Targets built with 'use_clang_coverage' or 'use_clang_profiling' (also
      # set by chrome_pgo_phase=1) will look at this environment variable to
      # determine where to write the profile dumps. The %Nm syntax is understood
      # by this instrumentation, see:
      #   https://clang.llvm.org/docs/SourceBasedCodeCoverage.html#id4
      llvm_profile_file = '${ISOLATED_OUTDIR}/profraw/'

      # Enable the continuous mode for coverage builds only, not PGO. Coverage
      # builds have isolate_profile_data set and using_pgo *not* set.
      #
      # TODO(crbug.com/41493392): reenable the continuous mode on Windows once
      # test timeout issues are resolved.
      if not using_pgo and not self._dispatches_to_windows():
        # Enable the continuous mode, which is set by adding %c to
        # LLVM_PROFILE_FILE, for coverage builds. The continuous mode causes the
        # instrumentation to update counters in real time instead of flushing
        # them to disk at process shutdown, which recovers coverage data for
        # sandboxed processes and processes that exit abnormally (e.g. death
        # tests). See https://crbug.com/1468343.
        llvm_profile_file = llvm_profile_file + 'default-%2m%c.profraw'
      else:
        llvm_profile_file = llvm_profile_file + 'default-%2m.profraw'
      env_vars = {
          'LLVM_PROFILE_FILE': llvm_profile_file,
      }

      # crbug.com/1124774 - For PGO, we're increasing the shutdown timeout to
      # 300 seconds to allow sufficient time for all processes to finish writing
      # profiles.
      if using_pgo:
        env_vars['CHROME_SHUTDOWN_TIMEOUT'] = '300'
        if self.api.m.chromium.c.TARGET_PLATFORM == 'android':
          env_vars['CHROME_PGO_PROFILING'] = '1'

      task_slice = task_slice.with_env_vars(**env_vars)

      sparse = True
      skip_validation = False

      # code coverage runs llvm-profdata merge with --sparse. PGO does not.
      if using_pgo:
        sparse = False
        skip_validation = True
      # TODO(crbug.com/1076055) - Refactor this to the profiles recipe_module
      # Wrap the merge script specific to the test type (i.e. gtest vs isolated
      # script tests) in a wrapper that knows how to merge coverage/pgo profile
      # data. If the test object does not specify a merge script, use the one
      # defined by the swarming task in the chromium_swarm module. (The default
      # behavior for non-coverage/non-profile tests).
      merge = self.api.m.code_coverage.shard_merge(
          self.step_name(suffix),
          self.target_name,
          skip_validation=skip_validation,
          sparse=sparse,
          additional_merge=self.spec.merge or task.merge)

    if suffix.startswith('retry shards'):
      task_slice = task_slice.with_idempotent(False)
    elif self.spec.idempotent is not None:
      task_slice = task_slice.with_idempotent(self.spec.idempotent)

    # task.shard_indices dictates how many shards will be triggered
    # CI builders retry invalid shards with the suffix "retry shards", instead
    # of "retry shards with patch" so check for both options.
    # We will re-calculate the number of shards for tests_to_retry and retry the
    # same failed shard indices otherwise.
    if not tests_to_retry and suffix in [
        'retry shards', 'retry shards with patch'
    ]:
      if suffix == 'retry shards':
        # CI builders use the default '' suffix when calling run_tests()
        # in test_utils/api
        task_suffix = ''
      else:
        task_suffix = 'with patch'
      task.task_to_retry = self._tasks[task_suffix]
      assert task.task_to_retry, (
          '\'retry_shards_with_patch\' expects that the \'with patch\' phase '
          'has already run, but it apparently hasn\'t.')
      task.shard_indices = task.task_to_retry.failed_shards
      # Test suite failure is determined by merging and examining the JSON
      # output from the shards. Failed shards are determined by looking at the
      # swarming output [retcode !=0 or state != 'SUCCESS']. It is possible that
      # these are not in sync. This will cause no tasks to be dispatched for
      # 'retry shards with patch'. This error has graceful recovery: 'retry
      # shards with patch' will simply reuse results from 'with patch'.
      # Regardless, we want to emit a failing step so that the error is not
      # overlooked.
      if len(task.shard_indices) == 0:  # pragma: no cover
        self.api.m.step.empty(
            'missing failed shards',
            status=self.api.m.step.FAILURE,
            step_text=(
                "Retry shards with patch is being run on {},"
                " which has no failed shards."
                " This usually happens because of a test runner bug."
                " The test runner reports test failures, but had exit_code 0."
                .format(self.step_name(suffix='with patch'))))
    else:
      task.shard_indices = range(task.shards)

    task.build_properties = self.api.m.chromium.build_properties
    task.containment_type = self.spec.containment_type
    if merge:
      task.merge = merge

    task.trigger_script = self.spec.trigger_script

    ensure_file = task_slice.cipd_ensure_file
    for package in self.spec.cipd_packages:
      ensure_file.add_package(package.name, package.version, package.root)

    task_slice = (task_slice.with_cipd_ensure_file(ensure_file))

    task.named_caches.update(self.spec.named_caches)

    if (suffix in self.SUFFIXES_TO_INCREASE_PRIORITY or
        self.CHECK_FLAKINESS_SUFFIX in suffix):
      task_request = task_request.with_priority(task_request.priority - 1)

    if self.spec.expiration:
      task_slice = task_slice.with_expiration_secs(self.spec.expiration)

    if self.spec.hard_timeout:
      task_slice = task_slice.with_execution_timeout_secs(
          self.spec.hard_timeout)

    if self.spec.io_timeout:
      task_slice = task_slice.with_io_timeout_secs(self.spec.io_timeout)

    task_dimensions = task_slice.dimensions
    # Add custom dimensions.
    task_dimensions.update(self.spec.dimensions)
    task_slice = task_slice.with_dimensions(**task_dimensions)

    # Add optional dimensions.
    task.optional_dimensions = self.spec.optional_dimensions

    # Add tags.
    tags = {
        'ninja_target': [self.spec.full_test_target or ''],
        # TODO(crbug/1106965): remove test_id_prefix from tags, if deriver
        # gets turned down.
        'test_id_prefix': [self.test_id_prefix or ''],
        'test_suite': [self.canonical_name],
        'waterfall_builder_group': [self.spec.waterfall_builder_group or ''],
        'waterfall_buildername': [self.spec.waterfall_buildername or ''],
        'test_phase': [suffix or ''],
    }
    if add_one_test_shard_enabled:
      tags.update({
          'experimental_shard_count': [str(shards)],
          'normally_assigned_shard_count': [str(shards - 1)],
      })

    if is_ci_only and self.api.m.tryserver.is_tryserver:
      tags['include_ci_only_tests'] = ['true']

    if self.spec.server:
      task.server = self.spec.server

    if self.spec.realm:
      task_request = task_request.with_realm(self.spec.realm)

    task.request = (
        task_request.with_slice(0, task_slice).with_name(
            self.step_name(suffix)).with_service_account(
                self.spec.service_account or '').with_tags(tags))
    return task

  def get_task(self, suffix: str) -> chromium_swarming.SwarmingTask:
    return self._tasks.get(suffix)

  def get_invocation_names(self, suffix: str) -> Iterable[str]:
    task = self.get_task(suffix)
    if task:
      return task.get_invocation_names()
    return []

  def pre_run(self,
              suffix: str,
              include_utr_instruction: bool = False,
              is_ci_only: bool = False) -> None:
    """Launches the test on Swarming."""
    assert suffix not in self._tasks, ('Test %s was already triggered' %
                                       self.step_name(suffix))

    task_input = self.api.m.isolate.isolated_tests.get(self.isolate_target)
    if not task_input:
      return self.api.m.step.empty(
          '[error] %s' % self.step_name(suffix),
          status=self.api.m.step.INFRA_FAILURE,
          step_text=('*.isolated file for target %s is missing' %
                     self.isolate_target))

    # Create task.
    self.api.m.chromium_turboci.create_test_check(self.step_name(suffix))
    self._tasks[suffix] = self._create_task(suffix, task_input,
                                            include_utr_instruction, is_ci_only)

    # Export TARGET_PLATFORM to resultdb tags
    resultdb = self.spec.resultdb
    base_tags = resultdb.base_tags or tuple()
    if (self.api.m.chromium.c and self.api.m.chromium.c.TARGET_PLATFORM):
      resultdb = attr.evolve(
          resultdb,
          base_tags=base_tags +
          (('target_platform', self.api.m.chromium.c.TARGET_PLATFORM),))

    self.api.m.chromium_swarming.trigger_task(
        self._tasks[suffix], resultdb=resultdb)
    self.api.m.chromium_turboci.set_test_check_planned(self.step_name(suffix))

    # Add instructions now that we have invocations
    self._add_instructions(suffix, include_utr_instruction)

  def run(
      self,
      checkout_dir: Path,
      source_dir: Path,
      build_dir: Path,
      suffix: str,
      info_messages: Iterable[str] = (),
  ) -> None:
    """Waits for launched test to finish and collects the results."""
    # There's no guarantee that a checkout exists for a swarming test, so
    # checkout_dir and source_dir shouldn't be used
    del checkout_dir, source_dir, build_dir

    step_result, _ = (
        self.api.m.chromium_swarming.collect_task(
            self._tasks[suffix], raise_on_failure=False))

    metadata = self._step_metadata(suffix)
    metadata['full_step_name'] = '.'.join(step_result.name_tokens)
    step_result.presentation.logs['step_metadata'] = (self.api.m.json.dumps(
        metadata, indent=2, sort_keys=True)).splitlines()

    failure_on_exit = (
        bool(self._tasks[suffix].failed_shards) or step_result.retcode != 0)
    self._update_failure_on_exit(suffix, failure_on_exit)

    info_message_list = list(info_messages)
    if suffix == 'retry shards with patch' and self.retry_only_failed_tests:
      info_message_list.append(
          'Ran only previously failing tests, instead of the entire shard. '
          'This is enabled on a per suite and per builder basis.\n')
    _present_info_messages(step_result.presentation, self, info_message_list)

    self._present_rdb_results(step_result, self._rdb_results.get(suffix))
    self.api.m.chromium_turboci.finalize_test_check(
        self.step_name(suffix), not failure_on_exit,
        step_result.presentation.step_text)

    self._handle_results(suffix, step_result)

  def _step_metadata(self, suffix):
    data = {
        'waterfall_builder_group': self.spec.waterfall_builder_group,
        'waterfall_buildername': self.spec.waterfall_buildername,
        'canonical_step_name': self.canonical_name,
        'isolate_target_name': self.isolate_target,
    }
    if suffix is not None:
      data['patched'] = suffix in ('with patch', 'retry shards with patch')
      data['dimensions'] = self._tasks[suffix].request[0].dimensions
      data['swarm_task_ids'] = self._tasks[suffix].get_task_ids()
    return data

@attrs()
class SwarmingGTestTestSpec(SwarmingTestSpec):
  """A spec for a test that runs a gtest-based test via swarming."""

  @property
  def test_class(self):
    """The test class associated with the spec."""
    return SwarmingGTestTest


class SwarmingGTestTest(SwarmingTest):

  @property
  def option_flags(self) -> TestOptionFlags:
    return _GTEST_OPTION_FLAGS

  def compile_targets(self) -> Iterable[str]:
    return [self.target_name]

  def _create_task(
      self,
      suffix: str,
      cas_input_root: str,
      include_utr_instruction: bool,
      is_ci_only: bool,
  ) -> chromium_swarming.SwarmingTask:
    json_override = None
    # TODO(crbug.com/1255217): Remove this android exception when logcats and
    # tombstones are in resultdb.
    if self.api.m.chromium.c.TARGET_PLATFORM != 'android':
      json_override = self.api.m.path.mkstemp()

    if self.is_rts:
      cmd = self.rts_raw_cmd
    else:
      cmd = self.raw_cmd
    # gtests only support 1 test-launcher-filter-file. Remove the filter file
    # arg from the raw command and combine it after the test spec is consumed
    cmd_filters = [arg for arg in cmd if '--test-launcher-filter-file=' in arg]
    for cmd_filter in cmd_filters:
      cmd.remove(cmd_filter)
    task = self.api.m.chromium_swarming.gtest_task(
        test_name=self.name,
        raw_cmd=cmd,
        relative_cwd=self.relative_cwd,
        cas_input_root=cas_input_root,
        collect_json_output_override=json_override,
        instructions_tag=self._instructions_tag_for_suffix('step', suffix),
        include_utr_instruction=include_utr_instruction)
    extra_args = list(self.spec.args) + cmd_filters
    merged_filter_file_arg = ';'.join(
        arg[len('--test-launcher-filter-file='):]
        for arg in extra_args
        if arg.startswith('--test-launcher-filter-file='))
    if merged_filter_file_arg:
      extra_args = _merge_arg(extra_args, '--test-launcher-filter-file',
                              merged_filter_file_arg)
    self._apply_swarming_task_config(task, suffix, '--gtest_filter', ':',
                                     extra_args, is_ci_only)
    return task


@attrs()
class LocalIsolatedScriptTestSpec(TestSpec):
  """Spec for a test that runs an isolated script locally.

  Attributes:
    * args - Arguments to be passed to the test.
    * results_handler_name - A name identifying the type of
      `ResultsHandler` that will be used for processing the test
      results:
      * 'default' - JSONResultsHandler
      * 'layout tests' - LayoutTestResultsHandler
      * 'fake' - FakeCustomResultsHandler
    * isolate_profile_data - Whether to isolate profile data during task
      execution.
  """

  args = attrib(command_args, default=())
  results_handler_name = attrib(
      enum(ALLOWED_RESULT_HANDLER_NAMES), default='default')
  isolate_profile_data = attrib(bool, False)

  @property
  def test_class(self):
    """The test class associated with the spec."""
    return LocalIsolatedScriptTest


class LocalIsolatedScriptTest(LocalTest):

  def __init__(self, spec, chromium_tests_api):
    super().__init__(spec, chromium_tests_api)
    self.raw_cmd = []
    self.relative_cwd = None

  @property
  def option_flags(self) -> TestOptionFlags:
    if 'blink_web_tests' in self.name:
      return _BLINK_WEB_TESTS_OPTION_FLAGS
    if 'angle_unittests' in self.name:
      return _ANGLE_UNITTESTS_OPTION_FLAGS
    return _ISOLATED_SCRIPT_OPTION_FLAGS

  @property
  def isolate_target(self) -> bool:
    return self.target_name

  def compile_targets(self) -> Iterable[str]:
    return [self.target_name]

  # TODO(nednguyen, kbr): figure out what to do with Android.
  # (crbug.com/533480)
  def run(
      self,
      checkout_dir: Path,
      source_dir: Path,
      build_dir: Path,
      suffix: str,
      info_messages: Iterable[str] = (),
  ) -> None:
    del checkout_dir, build_dir

    tests_to_retry = self._tests_to_retry(suffix)
    # pylint apparently gets confused by a property in a base class where the
    # setter is overridden
    test_options = self.test_options.for_running(suffix, tests_to_retry)  # pylint: disable=no-member
    pre_args = []
    if self.relative_cwd:
      pre_args += ['--relative-cwd', self.relative_cwd]

    cmd = list(self.raw_cmd)
    cmd.extend(self.spec.args)
    args = test_options.add_args(cmd, self.option_flags)
    # TODO(nednguyen, kbr): define contract with the wrapper script to rerun
    # a subset of the tests. (crbug.com/533481)

    temp = self.api.m.path.mkstemp()
    json_results_file = self.api.m.json.output(leak_to=temp)
    args.extend(['--isolated-script-test-output', json_results_file])

    step_test_data = lambda: (
        self.api.m.json.test_api.output({
            'valid': True,
            'failures': []
        }) + self.api.m.raw_io.test_api.stream_output_text(
            'rdb-stream: included "invocations/test-name" in '
            '"invocations/build-inv"', 'stderr'))

    kwargs = {}
    if self.spec.isolate_profile_data:
      kwargs.update({
          # Targets built with 'use_clang_coverage' will look at this
          # environment variable to determine where to write the profile dumps.
          # The %Nm syntax # is understood by this instrumentation, see:
          #   https://clang.llvm.org/docs/SourceBasedCodeCoverage.html#id4
          # The %c syntax enables the continuous mode, which updates counters
          # in real time instead of flusing to disk at process exit.
          # We use one profile only as this is meant for short, single-process
          # tests. Anything longer or more complex should be running on swarming
          # instead of locally.
          'env': {
              'LLVM_PROFILE_FILE':
                  '${ISOLATED_OUTDIR}/profraw/default-%1m%c.profraw',
          },
          # The results of the script will be isolated, and the .isolate will be
          # dumped to stdout.
          'stdout': self.api.m.raw_io.output_text(),
      })

    resultdb = self._prep_local_rdb(source_dir, temp=temp)

    step_result = self.api.m.isolate.run_isolated(
        self.step_name(suffix),
        self.api.m.isolate.isolated_tests[self.target_name],
        args,
        pre_args=pre_args,
        step_test_data=step_test_data,
        raise_on_failure=False,
        resultdb=resultdb if resultdb else None,
        stderr=self.api.m.raw_io.output_text(
            add_output_log=True, name='stderr'),
        **kwargs)

    status = step_result.presentation.status

    self._update_inv_name_from_stderr(step_result.stderr, suffix)
    self._update_failure_on_exit(suffix, step_result.retcode != 0)

    _present_info_messages(step_result.presentation, self, info_messages)

    self.api.m.step.raise_on_failure(step_result, status)


@attrs()
class SwarmingIsolatedScriptTestSpec(SwarmingTestSpec):
  """Spec for a test that runs an isolated script via swarming.

  Attributes:
    * results_handler_name - A name identifying the type of
      `ResultsHandler` that will be used for processing the test
      results:
      * 'default' - JSONResultsHandler
      * 'layout tests' - LayoutTestResultsHandler
      * 'fake' - FakeCustomResultsHandler
  """

  results_handler_name = attrib(
      enum(ALLOWED_RESULT_HANDLER_NAMES), default='default')

  @property
  def test_class(self):
    """The test class associated with the spec."""
    return SwarmingIsolatedScriptTest


class SwarmingIsolatedScriptTest(SwarmingTest):

  def compile_targets(self) -> Iterable[str]:
    return [self.target_name]

  @property
  def option_flags(self) -> TestOptionFlags:
    if 'blink_web_tests' in self.name:
      return _BLINK_WEB_TESTS_OPTION_FLAGS
    if 'angle_unittests' in self.name:
      return _ANGLE_UNITTESTS_OPTION_FLAGS
    return _ISOLATED_SCRIPT_OPTION_FLAGS

  def _create_task(
      self,
      suffix: str,
      cas_input_root: str,
      include_utr_instruction: bool,
      is_ci_only: bool,
  ) -> chromium_swarming.SwarmingTask:
    if self.is_rts:
      cmd = self.rts_raw_cmd
    else:
      cmd = self.raw_cmd

    task = self.api.m.chromium_swarming.isolated_script_task(
        raw_cmd=cmd,
        relative_cwd=self.relative_cwd,
        cas_input_root=cas_input_root,
        instructions_tag=self._instructions_tag_for_suffix('step', suffix),
        test_name=self.name,
        include_utr_instruction=include_utr_instruction)

    self._apply_swarming_task_config(task, suffix,
                                     '--isolated-script-test-filter', '::',
                                     self.spec.args, is_ci_only)
    return task

  def _handle_results(
      self,
      suffix: str,
      step_result: step_data.StepData,
  ) -> None:
    if self.spec.results_handler_name == 'layout tests':
      upload_step_name = step_result.name_tokens[-1]
      swarm_task_ids = self._tasks[suffix].get_task_ids()
      _archive_layout_test_results(
          self.api.m,
          upload_step_name,
          step_suffix=suffix,
          swarm_task_ids=swarm_task_ids)


@attrs()
class MockTestSpec(TestSpec):
  """Spec for a mock test.

  Attributes:
    * failures - The test cases to report as failures.
    * has_valid_results - Whether the test has valid results.
    * per_suffix_failures - A mapping of suffix to the test cases to
      report as failures for the suffix.
    * per_suffix_valid - A mapping of suffix to whether the test has
    * per_suffix_complete - A mapping of suffix to whether the test had
      complete shards..
    * invocation_names - Used as return value in |MockTest|'s
      |get_invocation_names| method.
    * retry_only_failed_tests - Whether to only retry failed tests, instead
      of the entire shard.
  """

  failures = attrib(sequence[str], default=())
  has_valid_results = attrib(bool, default=True)
  did_complete = attrib(bool, default=True)
  per_suffix_failures = attrib(mapping[str, sequence[str]], default={})
  per_suffix_valid = attrib(mapping[str, bool], default={})
  per_suffix_complete = attrib(mapping[str, bool], default={})
  runs_on_swarming = attrib(bool, default=False)
  shards = attrib(int, default=1)
  invocation_names = attrib(sequence[str], default=[])
  supports_rts = attrib(bool, default=False)
  option_flags = attrib(TestOptionFlags, default=_DEFAULT_OPTION_FLAGS)
  retry_only_failed_tests = attrib(bool, default=True)

  @property
  def test_class(self):
    """The test class associated with the spec."""
    return MockTest


class MockTask:

  def __init__(self, shards: int):
    self._shards = shards
    self.server = 'mock-swarming.appspot.com'

  def get_task_ids(self) -> Iterable[str]:
    return [f'fake-task-id-{id(self)}-{i}' for i in range(self._shards)]


class MockTest(AbstractSwarmingTest, Test):
  """A Test solely intended to be used in recipe tests."""

  class ExitCodes:
    FAILURE = 1
    INFRA_FAILURE = 2

  def __init__(self, spec, chromium_tests_api):
    super().__init__(spec, chromium_tests_api)
    # We mutate the set of failures depending on the exit code of the test
    # steps, so get a mutable copy
    self._failures = list(spec.failures)
    # Tasks for if the test is mocking a swarming test
    self._tasks_by_suffix = {}
    self._raw_cmd = []
    self._rts_raw_cmd = []
    self._relative_cwd = None

  @property
  def option_flags(self) -> TestOptionFlags:
    return self.spec.option_flags

  @property
  def locality(self):
    return (TestLocality.SWARMING
            if self.spec.runs_on_swarming else TestLocality.LOCAL)

  @property
  def isolate_profile_data(self) -> bool:
    return False

  @property
  def raw_cmd(self) -> Iterable[str]:
    return self._raw_cmd

  @raw_cmd.setter
  def raw_cmd(self, value: Iterable[str]) -> None:
    self._raw_cmd = value

  @property
  def rts_raw_cmd(self) -> Iterable[str]:
    return self._rts_raw_cmd

  @rts_raw_cmd.setter
  def rts_raw_cmd(self, value: Iterable[str]) -> None:
    self._rts_raw_cmd = value

  @property
  def relative_cwd(self) -> str:
    return self._relative_cwd

  @relative_cwd.setter
  def relative_cwd(self, value: str) -> None:
    self._relative_cwd = value

  @property
  def shards(self):
    assert self.runs_on_swarming
    return self.spec.shards

  @property
  def retry_only_failed_tests(self) -> bool:
    return self.spec.retry_only_failed_tests

  def get_task(self, suffix):
    assert self.runs_on_swarming
    return self._tasks_by_suffix[suffix]

  def _create_task(
      self,
      suffix: str,
      cas_input_root: str,
      include_utr_instruction: bool,
      is_ci_only: bool,
  ) -> chromium_swarming.SwarmingTask:  # pragma: no cover
    return None  # pragma: no cover

  @contextlib.contextmanager
  def _mock_exit_codes(self):
    try:
      yield
    except self.api.m.step.StepFailure as f:
      if f.result.retcode == self.ExitCodes.INFRA_FAILURE:
        i = self.api.m.step.InfraFailure(f.name, result=f.result)
        i.result.presentation.status = self.api.m.step.EXCEPTION
        raise i from f
      self._failures.append('test_failure')
      raise

  def pre_run(self,
              suffix: str,
              include_utr_instruction: bool = False,
              is_ci_only: bool = False) -> None:
    del is_ci_only
    with self._mock_exit_codes():
      self.api.m.step('pre_run {}'.format(self.step_name(suffix)),
                      ['mock_test.pre_run'])
    if self.runs_on_swarming:
      self._tasks_by_suffix[suffix] = MockTask(self.shards)

  def run(
      self,
      checkout_dir: Path,
      source_dir: Path,
      build_dir: Path,
      suffix: str,
      info_messages: Iterable[str] = (),
  ) -> None:
    del checkout_dir, source_dir, build_dir

    with self._mock_exit_codes():
      step_result = self.api.m.step(self.step_name(suffix), ['mock_test'])

    _present_info_messages(step_result.presentation, self, info_messages)


  def has_valid_results(self, suffix: str) -> bool:
    if suffix in self.spec.per_suffix_valid:  # pragma: no cover
      return self.spec.per_suffix_valid[suffix]
    return self.spec.has_valid_results

  def did_complete(self, suffix: str) -> bool:
    if suffix in self.spec.per_suffix_complete:  # pragma: no cover
      return self.spec.per_suffix_complete[suffix]
    return self.spec.did_complete

  def deterministic_failures(self, suffix: str) -> Set[str]:
    if suffix in self.spec.per_suffix_failures:  # pragma: no cover
      return self.spec.per_suffix_failures[suffix]
    return set(self._failures)

  def compile_targets(self) -> Iterable[str]:  # pragma: no cover
    return []

  def get_invocation_names(self, suffix: str) -> Iterable[str]:
    return self.spec.invocation_names

  @property
  def supports_rts(self) -> bool:
    return self.spec.supports_rts


@attrs()
class SkylabTestSpec(TestSpec):
  """Spec for a suite that runs on CrOS Skylab."""
  # The CrOS board name, e.g. eve, kevin.
  cros_board = attrib(str)
  # Build target of the ChromeOS board. If unspecified, cros_board will be used.
  # BUild_target is used to look for LKGM image if needed.
  cros_build_target = attrib(str, default='')
  # The CrOS DUT model.
  cros_model = attrib(str, default='')
  # Use the LKGM version of CrOS image.
  # When this is set to true, cros_img must be empty.
  use_lkgm = attrib(bool, default=False)
  # The GS path presenting CrOS image to provision the DUT,
  # e.g. atlas-release/R88-13545.0.0
  cros_img = attrib(str, default='')
  # The optional GS bucket of CrOS image.
  bucket = attrib(str, default='')
  # The optional Public CTP Builder and luci bucket.
  # The public_builder and public_builder_bucket fields can be used when
  # default CTP builder is not sufficient/advised
  # (ex: chromium cq, satlab for partners).
  public_builder = attrib(str, default='')
  public_builder_bucket = attrib(str, default='')
  # The skylab device pool to run the test. By default the
  # quota pool, shared by all CrOS tests.
  dut_pool = attrib(str, default='')
  # The number of shards used to run the test.
  shards = attrib(int, default=1)
  # Maximum number to retry a failed shard.
  # When set to zero, retries continue infinitely until timeout.
  shard_level_retries_on_ctp = attrib(int, default=-1)
  # The timeout for the test in second. Default is one hour.
  timeout_sec = attrib(int, default=3600)
  # The runtime timeout sent to the test execution environment.
  max_run_sec = attrib(int, default=0)

  # Attributes for Tast First Class requests.
  # On ChromeOS, these parameters are not limited to tast first class but all
  # tests, so not using "tast" here.
  cros_test_tags = attrib(sequence[str], default=())
  cros_test_tags_exclude = attrib(sequence[str], default=())
  cros_test_names = attrib(sequence[str], default=())
  cros_test_names_exclude = attrib(sequence[str], default=())
  cros_test_names_from_file = attrib(sequence[str], default=())
  cros_test_names_exclude_from_file = attrib(sequence[str], default=())
  cros_test_max_in_shard = attrib(int, default=0)
  cros_ctp_suite_name = attrib(str, default='')

  # Generic arguments to pass to the test command run in skylab.
  test_args = attrib(command_args, default=())

  # The name of the autotest to be executed in Skylab.
  # This is tied to an autotest control file that contains setup
  # informations and runs the actual test. For tast test, an
  # autotest wrapper is required. e.g. tast.lacros
  autotest_name = attrib(str, default='')

  # Spec for the Multi-DUT tests.
  secondary_cros_board = attrib(str, default='')
  secondary_cros_img = attrib(str, default='')
  secondary_cros_build_target = attrib(str, default='')
  # Optional argument to control whether to provision browser files
  # through `secondary_lacros_gcs_path` in the `crosfleet` command.
  # If True, `skip` is put in `secondary_lacros_gcs_path`
  # in the position corresponding to a DUT.
  # e.g. [False,True] yield "skip,gs://path1"
  # If this argument is empty, by default browser files are sent
  # to all secondary DUTs.
  # The length has to match that of `secondary_cros_board`.
  should_provision_browser_files = attrib(sequence[bool], default=())

  # Spec for telemetry tests.
  benchmark = attrib(str, default='')
  story_filter = attrib(str, default='')
  results_label = attrib(str, default='')
  test_shard_map_filename = attrib(str, default='')

  # For GPU specific args.
  extra_browser_args = attrib(str, default='')

  # Strip ELF binary symbol before deploying Chrome to ChromeOS devices.
  # This option make deployed binary have similar size to the real release
  # build of ChromeOS. It won't impact any test results in theory.
  # Currently only enabling for disk_usage_tests (b/40671387) to track metrics
  # of more accurate disk usage of a ChromeOS with target Chrome browser.
  strip_chrome = attrib(bool, default=False)

  @property
  def test_class(self):
    return SkylabTest


class SkylabTest(AbstractSkylabTest, Test):

  def __init__(self, spec, chromium_tests_api):
    super().__init__(spec, chromium_tests_api)
    # Dict of a cros_test_platform, aka CTP, build ID keyed with
    # suffix. CTP build is the entrance of the CrOS hardware tests.
    # which kicks off test_runner builds for our test suite.
    # Each test suite invokes one CTP build, and the CTP build
    # initiates test_runner builds for each shard.
    self._ctp_build_ids = {}
    # Dict of skylab.TestRunner list with suffix as the key.
    # TestRunner represents the test execution in Skylab. For a suffix
    # given, there should be one test runner to represent each shard.
    self._test_runner_builds = {}

    # These fields represent the variables generated at the runtime.
    self._lacros_gcs_path = ''
    self._exe_rel_path = ''
    self._build_output_dir = ''
    self.telemetry_shard_index = None

  @property
  def locality(self) -> TestLocality:
    return TestLocality.SKYLAB

  @property
  def is_tag_criteria_test(self) -> bool:
    return bool(self.spec.cros_test_tags or self.spec.cros_test_tags_exclude or
                self.spec.cros_test_names or
                self.spec.cros_test_names_exclude or
                self.spec.cros_test_names_from_file or
                self.spec.cros_test_names_exclude_from_file)

  @property
  def is_GPU_test(self) -> bool:
    return self.spec.autotest_name == 'chromium_Graphics'

  @property
  def ctp_build_ids(self) -> dict[str, int]:
    return self._ctp_build_ids

  @property
  def test_runner_builds(self) -> dict[str, TestRunner]:
    return self._test_runner_builds

  def did_complete(self, suffix) -> bool:
    # Return false to trigger retry if any shard has infra failure.
    if not self.test_runner_builds.get(suffix):
      return False
    for t in self.test_runner_builds[suffix]:
      # If any attempt runner in the shard has a deterministic result,
      # consider the shard done.
      if not t.status in (common_pb2.FAILURE, common_pb2.SUCCESS):
        return False
    return True

  @property
  def exe_rel_path(self) -> str:
    return self._exe_rel_path

  @exe_rel_path.setter
  def exe_rel_path(self, value: str) -> None:
    self._exe_rel_path = value

  @property
  def lacros_gcs_path(self) -> str:
    return self._lacros_gcs_path

  @lacros_gcs_path.setter
  def lacros_gcs_path(self, value: str) -> None:
    self._lacros_gcs_path = value

  @property
  def build_output_dir(self) -> str:
    return self._build_output_dir

  @build_output_dir.setter
  def build_output_dir(self, value: str) -> None:
    self._build_output_dir = value

  def _raise_failed_nested_step(self, suffix, step, status, failure_msg):
    step.status = status
    step.step_text += failure_msg
    self._update_failure_on_exit(suffix, True)
    raise self.api.m.step.StepFailure(status)

  def get_invocation_names(self, suffix: str) -> Iterable[str]:
    if build_id := self.ctp_build_ids.get(suffix):
      return [f'invocations/build-{build_id}']
    return []

  def pre_run(self,
              suffix: str,
              include_utr_instruction: bool = False,
              is_ci_only: bool = False) -> None:
    del is_ci_only
    retry_shards = []
    runtime_excluded_tests = []
    runtime_override_tests = []
    runtime_no_retry = False
    if self.is_tag_criteria_test:
      if suffix == 'without patch':
        valid, failures = self.with_patch_failures_including_retry()
        runtime_no_retry = True
        assert valid, "Invalid with patch result should not trigger without patch"
        runtime_override_tests = [
            t.removeprefix(self.test_id_prefix or '') for t in failures
        ]
      rdb_results = self._rdb_results.get(
          self.api.m.test_utils.remove_retry_shards(suffix))
      if rdb_results:
        for individual_test in rdb_results.all_tests:
          if any(individual_test.expectednesses):
            runtime_excluded_tests.append(
                individual_test.test_name.removeprefix(self.test_id_prefix or
                                                       ''))
    else:
      for tr in self.test_runner_builds.get(
          self.api.m.test_utils.remove_retry_shards(suffix), []):
        # TODO(b/364830287): Change back to not status in [SUCCESS, FAILURE]
        if not tr.status in [common_pb2.SUCCESS] and tr.shard >= 0:
          retry_shards.append(tr.shard)
    self.api.m.skylab.schedule_suite(
        self,
        suffix,
        retry_shards=retry_shards,
        runtime_no_retry=runtime_no_retry,
        runtime_override_tests=runtime_override_tests,
        runtime_excluded_tests=runtime_excluded_tests)

    self._add_instructions(suffix, include_utr_instruction)

  def run(
      self,
      checkout_dir: Path,
      source_dir: Path,
      build_dir: Path,
      suffix: str,
      info_messages: Iterable[str] = (),
  ) -> None:
    # There's no guarantee that a checkout exists for a skylab test, so
    # checkout_dir and source_dir shouldn't be used
    del checkout_dir, source_dir, build_dir

    with self.api.m.step.nest(self.step_name(suffix)) as step:
      step.tags['resultdb.instruction.id'] = self._instructions_tag_for_suffix(
          'step', suffix)
      self.api.m.skylab.fetch_test_runners(self, suffix)

      _present_info_messages(step, self, info_messages)

      bb_url = 'https://ci.chromium.org/b/%d'
      rdb_results = self._rdb_results.get(suffix)
      if rdb_results.total_tests_ran:
        # If any test result was reported by RDB, the test run completed
        # its lifecycle as expected.
        self._update_failure_on_exit(suffix, False)
      else:
        if ctp_id := self.ctp_build_ids.get(suffix):
          step.links['CTP Build'] = bb_url % ctp_id

        self._raise_failed_nested_step(
            suffix, step, self.api.m.step.EXCEPTION,
            'Test did not run or failed to report to ResultDB.'
            'Check the CTP build for details.')

      if rdb_results.unexpected_failing_tests:
        step.status = self.api.m.step.FAILURE
      self._present_rdb_results(step, rdb_results, as_nested_step=True)

      # RDB may not collect all failures from test runners. E.g.
      # infra failure on one shard and did not upload its results
      # to RDB. So iterate all shards and raise an exception
      # if any shard did not reach a deterministic success or
      # failure.
      shard_steps = []
      test_runners = sorted(
          self.test_runner_builds.get(suffix, []), key=lambda x: x.shard)
      for tr in test_runners:
        with self.api.m.step.nest(
            f'shard: #{tr.shard}' if tr.shard >= 0 else f'shard: #{tr.name}',
            status='last') as shard_step:
          if tr.status == common_pb2.FAILURE:
            shard_step.status = self.api.m.step.FAILURE
          elif tr.status != common_pb2.SUCCESS:
            shard_step.status = self.api.m.step.EXCEPTION
          if tr.url:
            shard_step.links['test results'] = (f'{tr.url}/test-results')
          if tr.log_url:
            shard_step.links['debug log'] = tr.log_url
          shard_steps.append(shard_step)

      if any(not s.status in [self.api.m.step.SUCCESS, self.api.m.step.FAILURE]
             for s in shard_steps):
        self._raise_failed_nested_step(suffix, step, self.api.m.step.EXCEPTION,
                                       'Some shards were unsuccessful.')

  def _add_instructions(self, suffix: str, include_utr_instruction: bool):
    """Gets the reproduction instructions to be attached to the invocation"""

    if include_utr_instruction:
      remote_dependency = self.api.m.repro_instructions.get_dependency(
          r'bot_update')
      builder = self.api.m.properties.get('orchestrator', {}).get(
          'builder_name', self.api.m.buildbucket.build.builder.builder)
      remote_instruction = get_utr_instruction(
          'compile-and-test',
          self.api.m.buildbucket.build.builder.project,
          self.api.m.led.shadowed_bucket or
          self.api.m.buildbucket.build.builder.bucket,
          builder,
          [self.name],
      )
      self.api.m.repro_instructions.create_step_instruction(
          self._instructions_tag_for_suffix('step', suffix),
          f'{self.name} instructions',
          remote_content=remote_instruction,
          remote_dependency=remote_dependency,
      )

      test_invocations = [
          inv if '/' not in inv else inv.split('/')[1]
          for inv in self.get_invocation_names(suffix)
      ]

      if test_invocations:
        remote_instruction = get_utr_instruction(
            'compile-and-test',
            self.api.m.buildbucket.build.builder.project,
            self.api.m.led.shadowed_bucket or
            self.api.m.buildbucket.build.builder.bucket,
            builder,
            [self.name],
        )
        self.api.m.repro_instructions.create_test_result_instruction(
            self._instructions_tag_for_suffix('test', suffix),
            f'{self.name} instructions',
            test_invocations,
            remote_content=remote_instruction,
            remote_dependency=remote_dependency,
            recursive=True,
        )

  def compile_targets(self) -> Iterable[str]:
    t = [self.spec.target_name]
    return t
