# Copyright (c) 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import collections
import collections.abc
import random
import re

from google.protobuf import timestamp_pb2
from recipe_engine import recipe_api
from RECIPE_MODULES.build.chromium_tests import steps
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb2
from PB.go.chromium.org.luci.analysis.proto.v1 import common as common_weetbix_pb2
from PB.go.chromium.org.luci.analysis.proto.v1 import predicate as predicate_pb2
from PB.recipe_engine import result as result_pb2

from . import utils

# A regular expression for file paths indicating that change in the matched file
# might introduce new tests.
# TODO(crbug.com/1204163): Extend for new tests from DEPS and build file changes
# with '^(.+(BUILD\.gn|DEPS|\.gni)|src/chromeos/CHROMEOS_LKGM|.+[T|t]est.*)$'.
_FILE_PATH_ADDING_TESTS_PATTERN = '^(?!testing/buildbot).+[T|t]est.*$'


class FlakinessApi(recipe_api.RecipeApi):
  """A module for new test identification on try builds."""

  def __init__(self, properties, *args, **kwargs):
    super().__init__(*args, **kwargs)
    self._check_for_flakiness = properties.check_for_flakiness
    self._check_for_flakiness_with_resultdb = properties.check_for_flakiness_with_resultdb
    # Input to cross reference step in "verify_new_tests" might be too large
    # and cause step failure, when there are too many new tests to verify
    # (caused by stale history JSON file, or a config roll adding new test
    # targets or variant configs just landed). This constant limits the number
    # of test variants passed to GetTestResultHistory RPC. The number is chosen
    # because it's about the max possible number of test variants naturally
    # added in a builder before a new test history JSON is generated.
    # Context: crbug.com/1294973
    self._max_test_variants_to_cross_reference = 1000
    # This is to limit the final new test variants that's endorsed.
    self._max_test_targets = properties.max_test_targets or 40
    self._repeat_count = properties.repeat_count or 20
    # The module will shard test reruns for swarming tests so that test
    # in each shard is shorter than this length.
    self._MAX_SHARD_TIME_MINUTES = 20
    # The max limit of test results in a test obejct, so that all results are
    # fetched for current build. If result count is larger, only unexpected
    # results are fetched. This means new tests in large suites are not
    # identified, e.g. blink_web_test.
    self.PER_TEST_OBJECT_RESULT_LIMIT = 50000
    self.COMMIT_FOOTER_KEY = 'Validate-Test-Flakiness'
    self.IDENTIFY_STEP_NAME = 'searching_for_new_tests'
    self.RUN_TEST_STEP_NAME = 'test new tests for flakiness'
    self.CALCULATE_FLAKE_RATE_STEP_NAME = 'calculate flake rates'

  @property
  def test_suffix(self):
    return self._suffix_by_shard_index(0)

  def _suffix_by_shard_index(self, index):
    """Suffix used for the input shard index when step.Test is sharded."""
    return 'check flakiness shard #%d' % index

  @property
  def check_for_flakiness(self):
    """Boolean to determine whether flakiness logic should be run for trybots.

    This needs to be enabled in order for the coordinating function of
    this module to execute.

    Returns:
        A boolean of whether the build is identifying new tests.
    """
    return self._check_for_flakiness or self._check_for_flakiness_with_resultdb

  @property
  def gs_bucket(self):
    return 'flake_endorser'

  def gs_source_template(self, experimental=False):
    """Provides template for generator recipe

    Project, bucket and builder information are queried for in Buildbucket,
    and this provides the template used.

    Expected gs_source format:
    * {project}/{bucket}/{builder}/{build_number}

    Args:
      * experimental: (bool) flag for experimental runs, appends experimental
        to the path.

    Return:
      * (str) template
    """
    base = '{}/{}/{}/{}/'
    if experimental:
      base = 'experimental/' + base
    return base

  def builder_gs_path(self, builder, build_number=None, experimental=False):
    """Generates the GS source

    Args:
      * builder: Buildbucket's BuilderID.
      * builder_name: (str) optional arg for setting builder_name. this defaults
        to 'builder' from builder_id.
      * build_number: (int) build number to append to gs_path. defaults to
        'latest' if not set.
      * experimental: (bool) will append prefix path 'experimental/' if True.
    """
    # a list of builder names are queried for by the pre-computing builder, and
    # requires a mechanism to set this value for the correct upload path.
    bucket = self.m.led.shadowed_bucket or builder.bucket
    return self.gs_source_template(experimental=experimental).format(
        builder.project,
        bucket,
        builder.builder,
        str(build_number) if build_number else 'latest',
    ) + '{}.json.tar.gz'.format(builder.builder)

  def is_test_file_present(self, affected_files):
    """Checks the list of affected files and ensures there's a test file.

    This is used to determine whether the flakiness workflow should run.

    Args:
      * affected_files: (list) of files associated with the given change. see
                        self.m.chromium_checkout.get_files_affected_by_patch.

    Returns:
      (bool) whether a test file is present.
    """
    pattern = re.compile(_FILE_PATH_ADDING_TESTS_PATTERN)
    return any(pattern.match(file_path) for file_path in affected_files)

  def fetch_precomputed_test_data(self):
    """Fetch the precomputed JSON file from GS

    Returns:
      dict JSON file of precomputed data
    """
    builder = self.m.buildbucket.build.builder
    source = self.builder_gs_path(
        builder, experimental=self.m.runtime.is_experimental)
    local_dest = self.m.path.mkstemp()
    try:
      self.m.gsutil.download(self.gs_bucket, source, local_dest)
    except recipe_api.StepFailure:
      # File may not exist, in which the data has not been precomputed.
      return self.m.json.loads('{}')

    # The output dir must not exist for untar.
    output_dir = self.m.path.cleanup_dir / 'flake_endorser'
    self.m.tar.untar('unpack {}'.format(source), local_dest, output_dir)

    bucket = self.m.led.shadowed_bucket or builder.bucket
    return self.m.file.read_json(
        'process precomputed test history',
        output_dir.joinpath(builder.project, bucket,
                            '{}.json'.format(builder.builder)),
        test_data=[{
            'test_id':
                ('ninja://ios/chrome/test/earl_grey2:ios_chrome_bookmarks_'
                 'eg2tests_module/TestSuite.test_a'),
            'variant_hash': 'some_hash',
        }],
        # We turn off logging for the JSON as some of the files are pretty
        # large, and logging significantly affects the runtime in these cases.
        include_log=False)

  def process_precomputed_test_data(self, test_data):
    """Process the precomputed test data into TestDefinition objects.

    Args:
      * test_data: (dict) JSON of the test data. Supported keys in the test
        data JSON include:
        - test_id: (str, required) ResultDB's test_id.
        - variant_hash: (str) ResultDB's variant_hash, a hash of the variants.

    Returns:
      set of TestDefinition objects
    """
    tests = set()
    for test_entry in test_data:
      tests.add(
          utils.TestDefinition(
              test_entry['test_id'],
              variant_hash=test_entry.get('variant_hash', None)))
    return tests

  def verify_new_tests(
      self,
      prelim_tests: set[utils.TestDefinition],
      builder: str,
  ) -> set[utils.TestDefinition]:
    """Verify the newly identified tests are new by cross-checking ResultDB.

    Queries ResultDB for the instances of the given test_ids on the builder for
    the past hours and iterates through response to eliminate any false
    positives from the preliminary new test list.

    Args:
      prelim_tests: The set of TestDefinition objects identified as potential
        new tests to be cross-referenced with ResultDB. This set will be
        modified by this method to contain only tests not found in ResultDB
        existing tests.
      builder: The name of the builder to query test results for.

    Returns:
      The set of TestDefinition objects for the tests that are actually new.
    """

    def _ensure_new(test_id):
      now = int(self.m.time.time())
      # The searched time is the start time of presubmit CV run or try build
      # including the test. The earliest searched time is 28 hours
      # because data shows 99.9% builds in CV are created within
      # 25 hours before CV end time, plus a 3 hour history JSON generation
      # frequency.
      earliest = now - 3600 * 28
      search_range = common_weetbix_pb2.TimeRange(
          earliest=timestamp_pb2.Timestamp(seconds=earliest),
          latest=timestamp_pb2.Timestamp(seconds=now))
      # TODO(crbug.com/1366463): Add default test data to make creating
      # integration tests easier.
      # The default query size is 1000. This is sufficient for the query so
      # page token is not used.
      verdicts, _ = self.m.luci_analysis.query_test_history(
          test_id,
          sub_realm='try',
          variant_predicate=predicate_pb2.VariantPredicate(
              contains={'def': {
                  'builder': builder,
              }}),
          submitted_filter=common_weetbix_pb2.ONLY_SUBMITTED,
          partition_time_range=search_range)

      for test_verdict in verdicts:
        test = utils.TestDefinition(
            test_id=test_verdict.test_id,
            variant_hash=test_verdict.variant_hash,
        )
        # If a test has already been run, ie/ through chained CLs, the
        # test history RPC call should return it as part of the verdict
        # and will be removed from the set of preliminary tests.
        if test in prelim_tests:
          prelim_tests.remove(test)

    futures = []
    for test_id in list(set(t.test_id for t in prelim_tests)):
      futures.append(self.m.futures.spawn(_ensure_new, test_id))

    for f in futures:
      f.result()

    return prelim_tests

  def trim_new_tests(
      self,
      new_tests: collections.abc.Sequence[utils.TestDefinition],
      limit: int,
      step_name: str | None = None,
  ) -> collections.abc.Collection[utils.TestDefinition]:
    """trim_new_tests will return a subset of new_tests according to the limit

    Our infrastructure won't allow us to test for flakiness for every single
    new test detected. To prevent us from overloading the infrastructure, this
    method will take a random subset according to the size limit provided.
    A step is generated to make note of the random subset taken alongside logs
    to indicate what the new test looks like.
    """
    if not new_tests or len(new_tests) <= limit:
      return new_tests

    # There are more new tests detected than what we're permitting, so we're
    # taking a random subset for the specific step.
    res = random.sample(new_tests, limit)
    step_name = step_name or 'randomly sampling {} tests '.format(limit)

    log_text = [
        ('The system only permits a total of {} new tests to prevent overloading '
         'CQ.'.format(limit)),
        'The following are the randomly selected subset that will be tested:\n',
    ]

    log_text += '\n'.join([
        'test_id: {}, variant_hash: {}, duration_milliseconds: {}'.format(
            t.test_id, t.variant_hash, t.duration_milliseconds) for t in res
    ])

    self.m.step.empty(
        step_name,
        step_text='too many new tests detected.',
        log_text=log_text,
        log_name='new_tests')

    return res

  def identify_new_tests(
      self,
      test_objects: collections.abc.Iterable[steps.Test],
  ) -> set[utils.TestDefinition]:
    """Coordinating method for identifying new tests on the current build.

    This method queries ResultDB for the historical tests run on the specified
    most recent builds on the current builder. This test list is compared with
    the tests running on the current build to identify and return new tests
    from the current CL.

    Args:
      test_objects: The step.Test objects with RDB results for current build.

    Returns:
        A set of TestDefinition objects for newly-added tests.
    """
    with self.m.step.nest(self.IDENTIFY_STEP_NAME) as p:
      builder_name = self.m.buildbucket.builder_name

      try:
        precomputed_json = self.fetch_precomputed_test_data()
      # We return an empty set for errors where we don't want to fail the build
      # while aborting the current workflow.
      # Note: InfraFailure is a subclass of StepFailure
      except recipe_api.InfraFailure:
        p.status = self.m.step.INFRA_FAILURE
        p.step_text = ('Failed to parse the precomputed test history. '
                       'Aborting the flakiness check.')
        return set()

      if not precomputed_json:
        p.status = self.m.step.EXCEPTION
        p.step_text = ('The current try builder may not have test data '
                       'precomputed.')
        return set()

      # Historical tests are a set of TestDefinition objects with just
      # test_id and variant_hash from the precomputed JSON files.
      historical_tests = self.process_precomputed_test_data(precomputed_json)
      p.logs['historical_tests'] = utils.set_to_string(historical_tests)

      # For logging purpose only.
      skipped_test_suites = set([])

      preliminary_new_tests = set([])
      # Stores stats of new tests. This is for early failing if any new test
      # is aready flaky before we trigger new test reruns.
      non_experimental_new_test_stats = {}
      experimental_new_test_stats = {}

      # For logging purpose only.
      current_tests_log = []
      for test_object in test_objects:
        if not test_object.check_flakiness_for_new_tests:
          skipped_test_suites.add(test_object.canonical_name)
          continue
        for suffix in ['with patch', 'retry shards with patch']:
          rdb_suite_result = test_object.get_rdb_results(suffix)
          if not rdb_suite_result:
            continue
          variant_hash = rdb_suite_result.variant_hash
          step_name = '%s (%s)' % (test_object.name, suffix)
          for individual_test in rdb_suite_result.all_tests:
            # Use 0 as duration if the info doesn't exist.
            duration_milliseconds = individual_test.duration_milliseconds or 0

            test_id = individual_test.test_id
            test_definition = utils.TestDefinition(
                test_id,
                test_name=individual_test.test_name,
                duration_milliseconds=duration_milliseconds,
                test_object=test_object,
                variant_hash=variant_hash,
                file_path=individual_test.test_metadata_file_name)
            current_tests_log.append('%s_%s' % (test_id, variant_hash))

            if not test_definition in historical_tests:
              preliminary_new_tests.add(test_definition)
              test_stats = (
                  experimental_new_test_stats if test_object.is_experimental
                  else non_experimental_new_test_stats)
              self._add_test_to_stats(individual_test, step_name, variant_hash,
                                      test_stats)

      p.logs['current_build_tests'] = current_tests_log
      if skipped_test_suites:
        p.logs['skipped_test_suites'] = '\n'.join(sorted(skipped_test_suites))

      p.logs['preliminary_tests'] = utils.set_to_string(preliminary_new_tests)

      if not preliminary_new_tests:
        return set()

      # Trim once before verify_new_tests to avoid input too large for RDB RPC.
      preliminary_new_tests = set(
          self.trim_new_tests(
              list(preliminary_new_tests),
              self._max_test_variants_to_cross_reference))

      # Cross-referencing the potential new tests with ResultDB to ensure they
      # are not present in existing builds.
      try:
        new_tests = self.verify_new_tests(
            prelim_tests=preliminary_new_tests,
            builder=builder_name)
      except recipe_api.StepFailure:
        p.status = self.m.step.INFRA_FAILURE
        p.step_text = ('Failed to verify if new tests exist. '
                       'Aborting flakiness check.')
        return set()

      p.logs['new_tests'] = ('new tests: \n\n{}'.format('\n'.join([
          'test_id: {}, variant_hash: {}, duration_milliseconds: {}'.format(
              t.test_id, t.variant_hash, t.duration_milliseconds)
          for t in new_tests
      ])))

    # At this time, test stats contains info in the initial
    # |preliminary_new_tests|. Filter to keep only new tests after trimming and
    # verification.
    new_test_filter = lambda item: item[0] in new_tests
    non_experimental_new_test_stats = dict(
        filter(new_test_filter, non_experimental_new_test_stats.items()))
    experimental_new_test_stats = dict(
        filter(new_test_filter, experimental_new_test_stats.items()))

    if self._calculate_flakiness_and_summary(
        non_experimental_new_test_stats,
        experimental_new_test_stats,
        present_summary_in_step=True):
      # Fail the build if there are already flaky new tests in "with patch" or
      # "retry shards with patch" steps, so we don't need to trigger new "check
      # flakiness" steps.
      self.m.step.empty(
          'New tests are found flaky in with patch test runs.',
          status=self.m.step.FAILURE,
          step_text=('New test are flaky in "with patch" or'
                     '"retry shards with patch" test steps.'
                     'See %s step for details.' %
                     self.CALCULATE_FLAKE_RATE_STEP_NAME))

    return new_tests

  def identify_new_test_variants(self) -> set[tuple[str, str]]:
    """Utilize ResultDB to determine if tests are new

    Query for new test variants through ResultDB QueryNewTestVariants RPC.
    A baseline is a set of test variants, usually identified by the buildbucket
    bucket and builder name. ResultDB tracks test variants run against baselines
    when changes are submitted.

    QueryNewTestVariants calculates new test variants through the difference
    between the test variants in the current run and the set of test variants
    for the baseline.

    New builders (and thus new baselines) will not be ready for querying
    if the builder has not run any tests for 72 hours.
    """
    curr_inv = self.m.resultdb.current_invocation
    builder = self.m.buildbucket.build.builder
    bucket = self.m.led.shadowed_bucket or builder.bucket

    baseline = 'projects/{}/baselines/{}:{}'.format(
        builder.project,
        bucket,
        builder.builder,
    )

    step_name = '{} with ResultDB'.format(self.IDENTIFY_STEP_NAME)
    with self.m.step.nest(step_name) as p:
      # Note that this RPC limits the number of new tests detected to 10,000.
      resp = self.m.resultdb.query_new_test_variants(curr_inv, baseline)

      if not resp.is_baseline_ready:
        # baseline is not ready, which means we cannot calculate for new tests.
        self.m.step.empty('Baseline is not yet ready to calculate new tests')
        return set()

      resp_new_tests = resp.new_test_variants
      if not resp_new_tests:
        self.m.step.empty('No new tests detected')
        return set()

      # Add all new tests into a set as tuples that we can check against while
      # we loop the test objects to find the correct ones.
      new_tests_identified = set()
      for new_test in resp_new_tests:
        new_tests_identified.add((new_test.test_id, new_test.variant_hash))

      p.logs['new_tests'] = [
          'test_id: {}, variant_hash: {}'.format(t[0], t[1])
          for t in new_tests_identified
      ]

    return new_tests_identified

  def _get_legacy_filter_from_test_id(self, test_id):
    """
    Translates a ResultDB v2 test_id to a legacy test filter string
    compatible with --isolated-script-test-filter.
    Ref: go/chrome-test-id-v2
    """
    if not test_id:
      return None

    if not test_id.startswith('://'):
      return None

    # New v2 format
    test_id_pattern = re.compile(r"://(?P<module_name>[^!]+)!"
                                 r"(?P<scheme>[^:]+):"
                                 r"(?P<coarse>[^:]*):"
                                 r"(?P<fine>[^#]+)#"
                                 r"(?P<case>.+)")
    match = test_id_pattern.match(test_id)
    if not match:
      # Pattern didn't match, might be a different v2 structure or malformed.
      return None

    components = match.groupdict()
    scheme = components['scheme']
    coarse = components['coarse']
    fine = components['fine']
    case = components['case']

    # Decode case name per go/chrome-test-id-v2 "Note about encoding"
    # Unescape ':' and '\' in the case name.
    case = case.replace('\\:', ':').replace('\\\\', '\\')

    if scheme == 'pyunit':
      # Example: ://chrome/test/chromedriver\:chromedriver_py_tests!pyunit:__main__:ChromeDriverW3cTest#testSendKeysLongStringNotCorrupted
      # Expected filter: __main__.ChromeDriverW3cTest.testSendKeysLongStringNotCorrupted
      return f"{coarse}.{fine}.{case}"
    # TODO(crbug.com/456432041): Add in other schemes besides pyunit.

    # Fallback for unhandled schemes:
    # Let the caller use test.test_name
    return None

  def _map_test_object(
      self,
      test_objects: collections.abc.Iterable[steps.Test],
      new_test_tuples: collections.abc.Collection[tuple[str, str]],
  ) -> dict[steps.Test, tuple[str, str]]:
    """_map_test_object formats tests objects to test filters and durations.

    One test object may be host to many new tests (ie/ if test_suite =
    browser_tests, there may be several test_ids as part of that test_suite
    that are actually new). So, we map test object to a test definition
    and create a list of tests on the fly.

    A dict of test object to a tuple of (test filter, duration) is returned
    """
    # new_tests is a dict of Test object to list of test names (test filter).
    # If exists, the TestDefinition's filter list should be updated to store
    # the list of tests.
    def join_tuple(t) -> str:
      return ' '.join(t)

    new_tests = {}
    not_found = []
    with self.m.step.nest('mapping new tests to test objects') as p:
      p.logs['new_test_tuples'] = '\n'.join(map(join_tuple, new_test_tuples))
      for test_obj in test_objects:
        if not test_obj.check_flakiness_for_new_tests:
          continue
        # There are two types of suffixes: with patch, retry with patch.
        # We're not determining new tests anymore - the new ones are already
        # defined for us by ResultDB. Whether it's retried or not, we just need
        # to find the corresponding test objects, so we'll use with patch.
        suffix = 'with patch'
        test_suite_results = test_obj.get_rdb_results(suffix)
        vh = test_suite_results.variant_hash

        for test in test_suite_results.all_tests:
          if (test.test_id, vh) in new_test_tuples:
            # Test object in list of new tests already, so update the filter.
            # Otherwise create a new one.
            test_filter, duration_milliseconds = new_tests.setdefault(
                test_obj, ([], 0))

            legacy_filter = self._get_legacy_filter_from_test_id(test.test_id)
            if legacy_filter:
              test_filter.append(legacy_filter)
            else:
              test_filter.append(test.test_name)

            # duration_milliseconds can default to 0 for our calculations because
            # it's only calculated if duration values are reported to ResultDB.
            test_duration_ms = test.duration_milliseconds or 0
            duration_milliseconds += test_duration_ms
            new_tests[test_obj] = (test_filter, duration_milliseconds)
          else:
            not_found.append((test.test_id, vh))
      if not_found:
        p.logs['not_found'] = '\n'.join(map(join_tuple, not_found))

    return new_tests

  def check_test_files(
      self,
      new_tests: collections.abc.Iterable[utils.TestDefinition],
      affected_files: list[str],
  ) -> list[utils.TestDefinition]:
    """Determines whether the correct test files are being modified by the patch

    This is used to determine whether the flakiness workflow should run.
    TestResults from ResultDB can specify a file_path (or a path to its
    associated test file), relative to the project (chromium/src). This check
    ensures that new tests that define a file path have that file being
    modified as part of this change.

    If a test does not define a path, it will by default be added to the list.

    Args:
      new_tests: Test objects that are deemed new, meaning that they have not
        been run in the past.
      affected_files: The files affected by the change under test. see
        self.m.chromium_checkout.get_files_affected_by_patch.

    Returns:
      (list) list of new tests that have a file being modified from the patchset
      or don't have a file_path defined.
    """
    excluded_tests = []
    filtered_tests = []
    for t in new_tests:
      # all file paths defined through ResultDB's TestMetadata are relative,
      # meaning they start with //.
      # `git diff to analyze patch`
      # or chromium_checkout.get_files_affected_by_patch() doesn't, so
      # it needs to be removed.
      if not t.file_path:
        # add to list of tests to test for by default. The next RPC call should
        # re-verify that this is indeed new.
        filtered_tests.append(t)
        continue

      fmt_path = t.file_path.strip('/')
      if fmt_path in affected_files:
        filtered_tests.append(t)
      else:
        excluded_tests.append(t)

    if excluded_tests:
      # logging purposes
      with self.m.step.nest('Skipped tests') as s:
        logs = [
            ('some tests have been skipped because the file path defined for the '
             'test is not being modified in this patchset.'),
            'files affected by this patchset',
        ]
        logs += affected_files
        logs.append('excluded tests:')
        for et in excluded_tests:
          logs.append(('test id %s variant_hash %s and path %s' %
                       (et.test_id, et.variant_hash, et.file_path)))
        s.logs['skipped tests'] = logs

        new_test_logs = []
        for nt in filtered_tests:
          new_test_logs.append(('test id %s variant_hash %s and path %s' %
                                (nt.test_id, nt.variant_hash, nt.file_path)))
        s.logs['new tests'] = new_test_logs

    return filtered_tests

  def _shard_runs(self, total_duration_milliseconds):
    """Calculates and shards endorser test runs considering test duration.

    Args:
      total_duration_milliseconds: Total duration in milliseconds for all tests
        to run once.

    Returns:
      A list of integers representing test runs in each shard.
    """
    if total_duration_milliseconds == 0:
      return [self._repeat_count]
    max_shard_time_milliseconds = self._MAX_SHARD_TIME_MINUTES * 60 * 1000
    # Max runs per shard confirming to |self._MAX_SHARD_TIME_MINUTES|. At least
    # 1 run per shard.
    runs_per_shard = int(
        max(max_shard_time_milliseconds / total_duration_milliseconds, 1))
    remaining = self._repeat_count
    shards = []
    while remaining > 0:
      shards.append(min(remaining, runs_per_shard))
      remaining = remaining - runs_per_shard
    return shards

  def find_tests_for_flakiness(
      self,
      test_objects: collections.abc.Iterable[steps.Test],
      affected_files: list[str] | None = None,
  ) -> collections.abc.Mapping[str, list[steps.Test]]:
    """Searches for new tests in a given change

    This method coordinates the workflow for searching and identifying new
    tests. Test history for a CQ builder should be pre-generated, where the
    test history is based on submitted test data. New tests are re-verified
    through the (LUCI Test) Test History RPC call, and we only focus on
    submitted test histories. This means that new tests that are merged but
    not yet included in the generated test history are removed.

    Test history generated through http://shortn/_BTd3kCb4cx.

    There are a few restrictions in place that will skip this (and thus) the
    flakiness checks:
    1) Only new tests in new test files are checked. Thus, a new test file
       must be included as part of the change.
    2) This logic will not be triggered if "check_for_flakiness" recipe_module
       property is not enabled.
    3) If 'Validate-Test-Flakiness:skip' commit footer is set, this logic will
       be skipped.

    Args:
      test_objects: list of step.Test objects
      affected_files: The files affected by the change under test. see
        self.m.chromium_checkout.get_files_affected_by_patch.

    Returns:
      A mapping from test suffixes to lists of steps.Test objects.
    """
    # Do not run anything if both properties are not set.
    if not (self.check_for_flakiness or
            self._check_for_flakiness_with_resultdb):
      return {}

    # Check if there are endorser footers to parse
    commit_footer_values = [
        val.lower()
        for val in self.m.tryserver.get_footer(self.COMMIT_FOOTER_KEY)
    ]
    if 'skip' in commit_footer_values:
      # No action for endorsing logic
      self.m.step(
          'skipping flaky test check since commit footer '
          '\'Validate-Test-Flakiness: Skip\' was detected.',
          cmd=None)
      return {}

    # TODO (crbug/1456545) - With ResultDB, the new test detection system is
    # more accurate as we don't rely on a cron-based system to compute the
    # test history. The ResultDB solution also allows us to identify new tests
    # for the larger test suites (wpt, etc.) that we previously were not able to
    # check.
    #
    # Thus, we may want to consider removing this check so that tests being
    # introduced through testing/buildbot or other means (ie/ disabled
    # annotations) are tested for flakiness. ResultDB's mechanism to track test
    # history for a baseline only requires it to be from a submitted build, so
    # tests introduced through infra configuration changes would become a part
    # of the history anyways.
    #
    # Note that even though we remove this check, we'd still have the upper
    # bound limit (at the time of writing, 40) through the random sampling to
    # avoid overloading CQ.
    affected_files = (
        affected_files or
        self.m.chromium_checkout.get_files_affected_by_patch())

    if not self.is_test_file_present(affected_files=affected_files):
      self.m.step.empty('no test files were detected with this change.')
      return {}

    # This is a map of test object to a tuple of ([test_names], total_duration).
    filter_and_time_by_test_object = {}

    # This is a dict of test suffix to list of test objects. In this case,
    # "check flakiness shard #X" to a test object, where X defaults to 0.
    test_objects_by_suffix = collections.defaultdict(list)

    ### ResultDB-Based Identification ###
    if self._check_for_flakiness_with_resultdb:
      # new tests tuples, in format (test_id, variant_hash)
      # terminate early if there's nothing
      new_test_tuples = self.identify_new_test_variants()
      if not new_test_tuples:
        return test_objects_by_suffix

      # TODO (crbug/1456545) - remove this comment when
      # "check_flakiness_for_new_tests" on test objects has been deprecated.
      # It was used previously to skip large test suites, but this limitation
      # is resolved with ResultDB, so this workflow disregards that check.

      # This is effectively trim_new_tests() minus the logging specific to
      # using TestDefinition object.
      if len(new_test_tuples) > self._max_test_targets:
        # random.sample can only be used correctly with Sequence types - set is
        # not a Sequence.
        if isinstance(new_test_tuples, set):
          new_test_tuples = sorted(new_test_tuples)
        new_test_tuples = random.sample(new_test_tuples, self._max_test_targets)
        self.m.step.empty(
            'randomly sampling {} tests'.format(self._max_test_targets),
            step_text='too many new tests detected',
            log_text=[
                'test_id: {} variant_hash: {}'.format(t[0], t[1])
                for t in new_test_tuples
            ],
            log_name='new_tests')

      # test object to list of test names
      filter_and_time_by_test_object = self._map_test_object(
          test_objects, new_test_tuples)
    ### Original Cron-History-Based Workflow ###
    # TODO (crbug/1456545) - this workflow and methods specific to this workflow
    # should deprecate once all CQ builders migrate to the workflow above.
    else:
      new_tests = self.identify_new_tests(test_objects)
      new_tests = self.trim_new_tests(list(new_tests), self._max_test_targets)
      new_tests = self.check_test_files(new_tests, affected_files)

      s = self.m.step('match single new tests with test suites', cmd=None)

      # This operation is O(len(test_obj) * len(new_tests)) because parsing
      # test_id is only intended for LUCI UI grouping, see
      # http://shortn/_StMScXolrz. max_test_targets will also bind the number of
      # iterations here.
      #
      # We loop the test objects and check all new tests to see if the test_id
      # start similarly.
      for test in test_objects:
        total_duration_ms = 0
        test_filter = []
        for new_test in new_tests:
          if new_test.test_object == test:
            test_filter.append(new_test)
            total_duration_ms += (new_test.duration_milliseconds or 0)

        if test_filter:
          log_lines = [
              'test_id: {}, variant_hash: {}, duration_milliseconds: {}'.format(
                  t.test_id, t.variant_hash, t.duration_milliseconds)
              for t in test_filter
          ]
          log_lines.append('total_duration_milliseconds: %d' %
                           total_duration_ms)
          s.presentation.logs['new tests to run in %s' %
                              test.canonical_name] = '\n'.join(log_lines)

          # Rework test filter into the required format
          test_filter = [new_test.test_name for new_test in test_filter]
          filter_and_time_by_test_object[test] = (test_filter,
                                                  total_duration_ms)

    # For each new test update all test filters to repeat and rerun 20 times.
    for test, metadata in filter_and_time_by_test_object.items():
      test_filter = metadata[0]
      if isinstance(test.spec, steps.ScriptTestSpec):
        test_objects_by_suffix[self.test_suffix].append(
            utils.apply_script_test_filter(test, test_filter,
                                           self._repeat_count))
      elif isinstance(test.spec, steps.SwarmingTestSpec):
        # For Swarming test objects, tests are sharded by duration, if the total
        # duration exceeds 20 minutes.
        total_duration_ms = metadata[1]
        shards = self._shard_runs(total_duration_ms)
        for index, shard_runs in enumerate(shards):
          test_objects_by_suffix[self._suffix_by_shard_index(index)].append(
              utils.apply_swarming_shard_test_filter(test, test_filter,
                                                     shard_runs))
      else:
        test_objects_by_suffix[self.test_suffix].append(
            utils.apply_default_test_filter(test, test_filter,
                                            self._repeat_count))
    return test_objects_by_suffix

  def _add_test_to_stats(self, test, step_name, variant_hash, stats):
    """Adds a test info to stats.

    Args:
      test: (RDBPerIndividualTestResults defined in test_utils module) test info
        to add.
      step_name: (str) Step name for the test.
      variant_hash: (str) Variant hash for the test,
      stats: (dict) A dictionary with (test_id, variant_hash) as keys and
        (test name, list of suites step names with failures,
        count of unexpected unpassed runs, count of all runs) as values.
    """
    test_name = test.test_name
    # Key is a tuple of (test_id, variant_hash)
    key = (test.test_id, variant_hash)
    total = test.total_test_count()
    unexpected_unpassed = test.unexpected_unpassed_count()
    # Value fields are: (test name, list of suite step names with failures,
    # count of unexpected unpassed runs, count of all runs)
    info = stats.get(key, ('', [], 0, 0))
    if unexpected_unpassed > 0:
      info[1].append(step_name)
    stats[key] = (test_name, info[1], info[2] + unexpected_unpassed,
                  info[3] + total)

  def _flakiness_summary_markdown(self, test_stats):
    """Creates a summary markdown using flakiness run results.

    Args:
      test_stats: A dictionary mapping from (test id, variant hash) tuple to
        a tuple of (test name, list of test step names with failed runs,
        count of unexpected runs, count of total runs).
        Only tests with unexpected results are included.

        E.g.
        {
          (test_id_1, variant_hash_1): ('test_name_1',
                             ['test_suite_1 (check flakiness, 0)']),
                             1,
                             20),
          (test_id_2, variant_hash_1): ('test_name_2',
                             ['test_suite_1 (check flakiness, 0)']),
                             1,
                             20),
          (test_id_3, variant_hash_2): ('test_name_3',
                             [
                               'test_suite_1 (check flakiness, 0)',
                               'test_suite_1 (check flakiness, 1)'
                             ]
                             2,
                             20
                             )
        }

    Returns:
      A list of line strs in markdown format presenting run stats of suites and
      flaky tests.
    """
    lines = []
    for test, stats in test_stats.items():
      variant_hash = test[1]
      test_name = stats[0]
      infra_steps = stats[1]
      failures = stats[2]
      total = stats[3]
      lines.append('Test: **{}**, variant hash: {}, # of failures: {}, '
                   'total # of runs: {}. See failed runs in:'.format(
                       test_name, variant_hash, failures, total))

      lines.extend(['- %s' % step for step in infra_steps])
    return lines

  def _calculate_flakiness_and_summary(self,
                                       flaky_non_experimental_test_stats,
                                       flaky_experimental_test_stats,
                                       present_summary_in_step=False):
    """Calculates and returns summary if non experimental tests have flakiness.

    Args:
      flaky_non_experimental_test_stats: A dictionary mapping from
        (test id, variant hash) tuple to a tuple of (test name, list of
        test step names with failed runs, count of unexpected runs,
        count of total runs), for tests in non experiental suites.
      flaky_experimental_test_stats: A dictionary mapping from
        (test id, variant hash) tuple to a tuple of (test name, list of
        test step names with failed runs, count of unexpected runs,
        count of total runs), for tests in experiental suites.

    Returns:
      A summary markdown str as failure message if there are flakiness.
      None if no flakiness.
    """
    with self.m.step.nest(self.CALCULATE_FLAKE_RATE_STEP_NAME) as p:
      p.step_text = (
          'Tests that have exceeded the tolerated flake rate most likely '
          'indicate flakiness. See logs for details of the flaky test '
          'and the flake rate.\n')

      # Keep only test variants with unexpected results.
      test_stats_filter = lambda item: item[1][2] > 0
      flaky_non_experimental_test_stats = dict(
          filter(test_stats_filter, flaky_non_experimental_test_stats.items()))
      flaky_experimental_test_stats = dict(
          filter(test_stats_filter, flaky_experimental_test_stats.items()))

      non_experimental_summary_lines = []
      if flaky_non_experimental_test_stats:
        non_experimental_summary_lines.append(
            'Flaky new test(s) in non-experimental suites (fatal):')
        non_experimental_summary_lines.extend(
            self._flakiness_summary_markdown(flaky_non_experimental_test_stats))

      experimental_summary_lines = []
      if flaky_experimental_test_stats:
        experimental_summary_lines.append(
            'Flaky new test(s) in experimental suites (non-fatal):')
        experimental_summary_lines.extend(
            self._flakiness_summary_markdown(flaky_experimental_test_stats))

      if non_experimental_summary_lines or experimental_summary_lines:
        p.logs['flaky tests'] = ('\n'.join(non_experimental_summary_lines +
                                           experimental_summary_lines))

        if non_experimental_summary_lines:
          p.status = self.m.step.FAILURE
          summary_lines = [
              'Some new test(s) added from your CL appear '
              'to be flaky. Please check "%s" step for test identification and '
              'test steps for test run details.' % self.IDENTIFY_STEP_NAME
          ]
          summary_lines.extend(non_experimental_summary_lines)
          summary_markdown = '\n\n'.join(summary_lines)[:3500]
          summary_markdown += (
              '\n\nSee full logs in "flaky tests" under %s step.' %
              self.CALCULATE_FLAKE_RATE_STEP_NAME)

          if present_summary_in_step:
            p.step_text += summary_markdown
          return summary_markdown
        # When there is non fatal flakiness, let users know why the build
        # doesn't fail.
        p.step_text += ('\nFlaky new tests in logs are non fatal because they '
                        'come from experimental suites.')

    return None

  def check_run_results(self, suffix_suites):
    """Calculates and presents flakiness info using test results from input.

    Args:
      suffix_suites: A mapping from test suffixes to lists of steps.Test
        objects with valid results for the suffix.

    Returns:
      A RawResult object with the status of the build and failure message if
      there are flakiness. None if no flakiness.
    """
    # In this step, flaky tests in experimental suites (test objects) are non
    # fatal, otherwise they are fatal and will fail the build.
    flaky_non_experimental_test_stats = {}
    flaky_experimental_test_stats = {}
    # A list of test step names without results (invalid).
    empty_result_steps = []
    invalid_suites = []
    flaky_suites = set()
    for suffix, test_objects in suffix_suites.items():
      for t in test_objects:
        flaky_test_stats = (
            flaky_experimental_test_stats
            if t.is_experimental else flaky_non_experimental_test_stats)
        rdb_results = t.get_rdb_results(suffix)
        step_name = '%s (%s)' % (t.name, suffix)
        if not rdb_results.all_tests:
          invalid_suites.append(t)
          empty_result_steps.append(step_name)
          continue
        for test in rdb_results.all_tests:
          if test.unexpected_unpassed_count() > 0:
            flaky_suites.add(t)
          self._add_test_to_stats(test, step_name, rdb_results.variant_hash,
                                  flaky_test_stats)

    self._record_suite_flakiness(list(flaky_suites), invalid_suites)

    if empty_result_steps:
      summary_lines = [
          ('%s steps in %s didn\'t produce test results.' %
           (', '.join(empty_result_steps), self.RUN_TEST_STEP_NAME))
      ]
      return result_pb2.RawResult(
          summary_markdown='\n\n'.join(summary_lines),
          status=common_pb2.FAILURE)

    summary_markdown = self._calculate_flakiness_and_summary(
        flaky_non_experimental_test_stats, flaky_experimental_test_stats)
    if summary_markdown:
      return result_pb2.RawResult(
          summary_markdown=summary_markdown, status=common_pb2.FAILURE)

    return None

  def _record_suite_flakiness(self, flaky_tests, invalid_suites):
    step_result = self.m.step.empty('record suite flakiness')
    step_result.presentation.properties['flake_endorser_rejections'] = {
        'flaky_suites': [test.name for test in flaky_tests],
        'invalid_suites': [test.name for test in invalid_suites],
    }
