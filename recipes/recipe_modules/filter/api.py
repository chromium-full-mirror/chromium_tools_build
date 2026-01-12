# Copyright 2014 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from collections.abc import Callable, Collection, Iterable, Mapping
import functools
import itertools
import posixpath
import re
import typing

from recipe_engine import config_types
from recipe_engine import recipe_api
from recipe_engine import step_data

from RECIPE_MODULES.build import chromium_types

_AnalyzeInput: typing.TypeAlias = dict[str, Collection[str]]
_AnalyzeOutput: typing.TypeAlias = dict[str, typing.Any]
# _AnalyzeOutput is the test data value
_Analyzer: typing.TypeAlias = Callable[[_AnalyzeInput, _AnalyzeOutput],
                                       step_data.StepData]


class ResultsCallback(typing.Protocol):
  """A callback to report the results of an analyze operation."""

  def __call__(
      self,
      reason: str,
      compile_targets: Iterable[str],
      test_targets: Iterable[str],
      /,
      *,
      skip_reasons: Iterable[str] = (),
  ) -> None:
    """Execute the callback to report the results.

    Args:
      reason: The reason that the provided results were computed.
      compile_targets: The affected compile targets.
      test_targets: The affected test targets.
      skip_reasons: If provided, any reasons why analysis was skipped.
    """


def _noop_results_callback(
    reason: str,
    compile_targets: Iterable[str],
    test_targets: Iterable[str],
    /,
    *,
    skip_reasons: Iterable[str] = (),
) -> None:
  pass



class FilterApi(recipe_api.RecipeApi):

  def _load_analyze_config(
      self,
      config_path: config_types.Path,
  ) -> dict[str, dict[str, list[str]]]:
    """Load the given analyze config.

    Analyze config files are expected to be JSONs in the following format:

    ```
      {
        "<analyze-config-name>": {
          "exclusions": ["<regex-exclusion>", ...],
        },
        ...
      }
    ```

    e.g.

    ```
      {
        "base": {
          "exclusions": [
            "base-regex-exclusion-1",
            "base-regex-exclusion-2",
            "base-regex-exclusion-.*",
            ...
          ]
        },
        "chromium": {
          "exclusions": [
            "chromium-regex-exclusion-1",
            "chromium-regex-exclusion-2",
            "chromium-regex-exclusion-.*",
            ...
          ]
        },
        ...
      }
    ```

    Regex patterns are expected to be posix-style paths.

    Files changed by a patch will be matched against all regex patterns in all
    applicable analyze configs.

    An analyze config file is *required* to have an analyze config named
    "base". Some clients may require additional analyze configs (e.g.
    chromium_tests requires "chromium" in addition to "base").
    """
    config = self.m.file.read_json(
        'read filter exclusion spec',
        config_path,
        test_data={'base': {
            'exclusions': []
        }},
    )
    self.m.step.active_result.presentation.step_text = 'path: %r' % config_path
    return config

  def _get_path_matchers(
      self,
      additional_names: Collection[str],
      config_path: config_types.Path,
  ) -> tuple[Collection[re.Pattern], Collection[re.Pattern]]:
    """Get the regular expressions for excluding and ignoring paths.

    Args:
      additional_names: The names of top-level keys in the config file
        to consider in addition to 'base'.
      config_file_name: The config file to look up exclusions in.

    Returns:
      A 2-tuple where each element is a list of regexes that can be used
      to match against paths. The first element is a list of patterns
      matching paths to exclude and the second element is a list of
      patterns matching paths to ignore.
    """
    config = self._load_analyze_config(config_path)

    names = ['base']
    names.extend(additional_names or [])

    exclusions = []
    ignores = []

    for name in names:
      config_for_name = config.get(name)
      if config_for_name is None:
        continue
      exclusions.extend(config_for_name.get('exclusions', []))
      ignores.extend(config_for_name.get('ignores', []))

    def to_regexes(patterns):
      return [re.compile(p) for p in patterns]

    return to_regexes(exclusions), to_regexes(ignores)

  @staticmethod
  def _find_matching_pattern(
      path: str,
      regexes: Collection[re.Pattern],
  ) -> re.Pattern | None:
    """Returns the pattern string that matches a path (if any)."""
    for regex in regexes:
      match = regex.fullmatch(path)
      if match:
        return regex
    return None

  def _convert_path_to_posix(self, path):
    components = []
    while path:
      path, tail = self.m.path.split(path)
      if tail:
        components = [tail] + components
    if components:
      return posixpath.join(components[0], *components[1:])
    return None

  def _run_mb_analyze(
      self,
      source_dir: config_types.Path,
      build_dir: config_types.Path,
      analyze_input: _AnalyzeInput,
      test_analyze_output: _AnalyzeOutput,
      *,
      builder_id: chromium_types.BuilderId | None,
      mb_path: config_types.Path | None,
      mb_config_path: config_types.Path | None,
      phase: str | None,
  ) -> step_data.StepData:
    env = {}

    # Ensure that mb runs in a clean environment.
    if self.m.chromium.c.env.FORCE_MAC_TOOLCHAIN:
      env['FORCE_MAC_TOOLCHAIN'] = self.m.chromium.c.env.FORCE_MAC_TOOLCHAIN

    with self.m.context(env=env):
      builder_id = builder_id or self.m.chromium.get_builder_id()
      return self.m.chromium.mb_analyze(
          source_dir,
          build_dir,
          builder_id,
          analyze_input,
          mb_path=mb_path,
          mb_config_path=mb_config_path,
          phase=phase,
          test_analyze_output=test_analyze_output)

  def _run_chromium_gyp_analyze(
      self,
      source_dir: config_types.Path,
      analyze_input: _AnalyzeInput,
      test_analyze_output: _AnalyzeOutput,
  ) -> step_data.StepData:
    # If building for CrOS, execute through the "chrome_sdk" wrapper. This
    # will override GYP environment variables, so we'll refrain from defining
    # them to avoid confusing output.
    with self.m.context(env=self.m.chromium.c.gyp_env.as_jsonish()):
      return self.m.step(
          'analyze', [
              'python3',
              source_dir / 'build/gyp_chromium',
              '--analyzer',
              self.m.json.input(analyze_input),
              self.m.json.output(),
          ],
          step_test_data=lambda: self.m.json.test_api.output(test_analyze_output
                                                            ))

  def _determine_affected_targets(
      self,
      results_callback: ResultsCallback,
      paths: Collection[str],
      test_targets: Collection[str],
      additional_compile_targets: Collection[str],
      exclusions: Mapping[re.Pattern, str],
      ignores: Collection[re.Pattern],
      analyzer: _Analyzer,
  ) -> tuple[Collection[str], Collection[str]]:
    for path in paths:
      if self._find_matching_pattern(path, ignores):
        continue
      matched_pattern = self._find_matching_pattern(path, exclusions)
      if matched_pattern:
        self.m.step.empty(
            'analyze',
            step_text='Analyze disabled: matched exclusion',
            log_name='excluded_files',
            log_text=f'{path} (regex = \'{matched_pattern.pattern}\'), '
            f'exclusion source: {exclusions[matched_pattern]}')
        all_targets = set(test_targets) | set(additional_compile_targets)

        all_targets = sorted(all_targets)
        test_targets = sorted(test_targets)

        results_callback(
            'skipping analysis',
            all_targets,
            test_targets,
            skip_reasons=[(f'matched exclusion: {path}'
                           f' matched regex "{matched_pattern.pattern}"'
                           f' from {exclusions[matched_pattern]}')])

        return test_targets, all_targets

    analyze_input = {
        'files': paths,
        'test_targets': test_targets,
        'additional_compile_targets': additional_compile_targets,
    }
    test_analyze_output = {
        'status': 'Found dependency',
        'test_targets': test_targets,
        'compile_targets': additional_compile_targets,
    }
    step_result = analyzer(analyze_input, test_analyze_output)

    if 'error' in step_result.json.output:
      step_result.presentation.step_text = (
          'Error: ' + step_result.json.output['error'])
      step_result.presentation.status = self.m.step.FAILURE
      raise self.m.step.StepFailure('Error: ' +
                                    step_result.json.output['error'])

    if 'invalid_targets' in step_result.json.output:
      raise self.m.step.StepFailure(
          'Error, following targets were not found: ' +
          ', '.join(step_result.json.output['invalid_targets']))

    if (step_result.json.output['status'] in ('Found dependency',
                                              'Found dependency (all)')):
      test_targets = sorted(step_result.json.output['test_targets'])
      compile_targets = step_result.json.output['compile_targets']

      # TODO(dpranke) crbug.com/557505 - we need to not prune meta
      # targets that are part of 'test_targets', because otherwise
      # we might not actually build all of the binaries needed for
      # a given test, even if they aren't affected by the patch.
      # Until the GYP code is updated, we will merge the returned
      # test_targets into compile_targets to be safe.
      compile_targets = sorted(set(test_targets + compile_targets))

      results_callback('analysis complete', compile_targets, test_targets)

      return test_targets, compile_targets

    step_result.presentation.step_text = 'No compile necessary'
    step_result.presentation.properties['no_compile'] = sorted(
        itertools.chain(test_targets, additional_compile_targets))

    results_callback('analysis complete, found no dependencies', [], [])

    return [], []

  def analyze(
      self,
      source_dir: config_types.Path,
      build_dir: config_types.Path,
      affected_files: Collection[str],
      test_targets: Collection[str] | None,
      additional_compile_targets: Collection[str] | None,
      *,
      config_path: config_types.Path | None = None,
      additional_names: Collection[str] | None = None,
      additional_exclusions: Mapping[str, str] | None = None,
      builder_id: chromium_types.BuilderId | None = None,
      mb_path: config_types.Path | None = None,
      mb_config_path: config_types.Path | None = None,
      phase: str | None = None,
      results_callback: ResultsCallback | None = None,
  ) -> tuple[Collection[str], Collection[str]]:
    """Runs "analyze" step to determine targets affected by the patch.

    The config file identified by |config_file_name| will be read to get
    configs containing exclusions and ignores to use. The file must
    contain the json representation of a dict of the following form:

    ```
    {
      "<config-name>": {
        "exclusions": ["<exclusion-regex>", ...],
        "ignores": ["<ignore-regex>", ...]
      },
      ...
    }
    ```

    The config named 'base' will always be used if present, with
    additional configs being used depending on the value of
    |additional_names|. If any file paths are matched by selected
    exclusions and not matched by any selected ignores, then all
    provided targets will be considered affected.

    An analysis script will be called out to that takes as input the
    affected file paths, the test targets to analyze and any additional
    targets to analyze. If the chromium module's project generator tool
    is configured to 'mb', then mb will be used for analysis, otherwise
    the gyp_chromium script located within the build directory of the
    checkout will be used.

    Args:
      build_dir: The path to the build output directory.
      affected_files: Collection of files affected by the current patch.
        Paths should only use forward slashes ("/") on all platforms.
      test_targets: The possible set of executables that are desired to
        run.
      additional_compile_targets: Any targets to compile in addition to
        the test_targets.
      config_file_name: The config file containing the exclusions and
        ignores to use. The file should contain the json representation
        of a dict, where the keys are config names used to group related
        exclusions and ignores. The value for each key is a dict with
        the optional keys 'exclusions' and 'ignores', where the
        corresponding values are lists of regex patterns that will be
        matched against file paths relative to the source root. The
        'base' config will always be used.
      additional_names: Config names to look up exclusions and ignores,
        see |config_file_name|. If not provided, ['chromium'] will be
        used.
      additional_exclusions: A mapping of additinal exclusion path strings
        to their sources. The string paths are to be compiled into regex
        and then added to the exclusions.
      builder_id: The ID of the builder with the config to run MB
        against.
      mb_path: The path to the source directory containing the mb.py
        script.
      mb_config_path: The path to the MB config file.
      phase: String to distinguish the phase of a builder.
      results_callback: Callback that will be called with the results of
        the analyze operation.

    Returns:
      A 2-element tuple: * The collection of provided test targets that
      are affected * The collection of all provided targets that are
      affected
    """
    if additional_names is None:
      additional_names = ['chromium']

    if config_path is None:
      config_path = self.m.chromium.analyze_config_path(source_dir)
      assert config_path, (
          'either config_path must be passed in'
          ' or the chromium config must set analyze_config_path')

    exclusions_from_config, ignores = self._get_path_matchers(
        additional_names, config_path)
    exclusions = {
        exclusion: 'analyze config file' for exclusion in exclusions_from_config
    }
    if additional_exclusions:
      for path, source in additional_exclusions.items():
        exclusions[re.compile(re.escape(path))] = source

    # TODO(gbeaty) Check if this is necessary, the documentation for the
    # parameter indicates that they should always have forward slashes
    paths = [
        p for p in (self._convert_path_to_posix(f) for f in affected_files) if p
    ]

    if self.m.chromium.c.project_generator.tool == 'mb':
      analyzer = functools.partial(
          self._run_mb_analyze,
          source_dir,
          build_dir,
          builder_id=builder_id,
          mb_path=mb_path,
          mb_config_path=mb_config_path,
          phase=phase,
      )
    else:
      analyzer = functools.partial(self._run_chromium_gyp_analyze, source_dir)

    test_targets = list(test_targets or [])
    additional_compile_targets = list(additional_compile_targets or [])
    results_callback = results_callback or _noop_results_callback

    analyze_test_targets, analyze_compile_targets = (
        self._determine_affected_targets(
            results_callback,
            paths,
            test_targets,
            additional_compile_targets,
            exclusions,
            ignores,
            analyzer,
        ))

    # Emit more detailed output useful for debugging.
    analyze_details = {
        'test targets': test_targets,
        'additional compile targets': additional_compile_targets,
        'affected test targets': analyze_test_targets,
        'affected compile targets': analyze_compile_targets,
    }
    details_json = self.m.json.dumps(analyze_details, indent=2, sort_keys=False)
    step_result = self.m.step.active_result
    step_result.presentation.logs['analyze_details'] = details_json.splitlines()

    return analyze_test_targets, analyze_compile_targets
