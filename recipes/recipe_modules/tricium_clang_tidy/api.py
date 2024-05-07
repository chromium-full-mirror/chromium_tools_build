# Copyright 2020 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""
Recipe module to encapsulate the logic of calling clang-tidy on a list
of affected files, gather warnings, and post via Tricium.
"""

import collections

from recipe_engine.recipe_api import RecipeApi

from . import _clang_tidy_path


def _is_clang_diagnostic(check_name):
  """Returns if `check_name` is a clang diagnostic instead of a tidy nit."""
  return check_name.startswith('clang-diagnostic-')


_TidyDiagnosticID = collections.namedtuple(
    '_TidyDiagnosticID', ['message', 'line_number', 'check_name'])


class _SourceFileComments:

  def __init__(self):
    self._source_comments = []
    # _source_comments which have notes that map them back to macros. We have
    # to group these, since a single CL might have multiple diagnostics that
    # ultimately point to this same macro.
    self._macro_comments = collections.defaultdict(set)
    self._build_failed = False
    self._tidy_failed = False
    self._tidy_timed_out = False

  def note_tidy_timed_out(self):
    self._tidy_timed_out = True

  def note_build_failed(self):
    self._build_failed = True

  def note_tidy_failed(self):
    self._tidy_failed = True

  def add_macro_expanded_tidy_diagnostic(self, message, line_number, check_name,
                                         suggestions, file_of_expansion,
                                         line_of_expansion):
    key = _TidyDiagnosticID(message, line_number, check_name)
    # FIXME(gbiv): Macro suggestions may be tricky. Don't mind them for now.
    _ = suggestions
    self._macro_comments[key].add((file_of_expansion, line_of_expansion))

  def add_tidy_diagnostic(self, message, line_number, check_name, suggestions):
    # Clang diagnostics should be reported through presubmits, rather than
    # Tricium. One diagnostic can cause a cascade of others, and that's a bad
    # UX.
    assert not _is_clang_diagnostic(check_name), check_name
    self._source_comments.append((_TidyDiagnosticID(message, line_number,
                                                    check_name), suggestions))

  def __iter__(self):
    """Yields comments as (category, message, line_num, suggestions) tuples."""
    category = 'ClangTidy'

    if self._tidy_timed_out:
      message = ('warning: clang-tidy timed out on this file; issuing '
                 'diagnostics is impossible.')
      yield category, message, 0, ()

    def fix_message(message, check_name):
      if '-' in check_name:
        check_category, check_name = check_name.split('-', 1)
        url_path = '%s/%s' % (check_category, check_name)
      else:
        url_path = check_name
      return (message + ' (https://clang.llvm.org/extra/clang-tidy/checks/'
              '%s.html)' % url_path)

    if self._build_failed:
      failure_suffix = ('\n\n(Note: building this file or its dependencies '
                        'failed; this diagnostic might be incorrect as a '
                        'result.)')
    elif self._tidy_failed:
      failure_suffix = ('\n\n(Note: running clang-tidy on this file failed; '
                        'this diagnostic might be incorrect as a result.)')
    else:
      failure_suffix = ''

    for (message, line_number,
         check_name), suggestions in self._source_comments:
      subcategory = '/'.join([category, check_name])
      message = fix_message(message, check_name) + failure_suffix
      yield subcategory, message, line_number, suggestions

    macro_comments = sorted(self._macro_comments.items())
    for (message, line_number, check_name), expansions in macro_comments:
      assert len(expansions) > 0, message

      message = fix_message(message, check_name)
      expansions = sorted(expansions)
      expansion_file, expansion_line = expansions[0]
      suffix = '\n\nExpanded from %s:%d' % (expansion_file, expansion_line)

      # Listing one expansion location should be enough for now.
      if len(expansions) == 1:
        suffix += '.'
      elif len(expansions) == 2:
        suffix += ', and 1 other place.'
      else:
        suffix += ', and %d other places.' % (len(expansions) - 1)

      subcategory = '/'.join([category, check_name])
      yield subcategory, message + suffix + failure_suffix, line_number, ()


def _fix_win_file_path(file_path):
  return file_path.replace('\\', '/')


def _parse_tidy_diagnostic(diagnostic, diagnostic_name, is_windows):
  """Parses a single clang-tidy diagnostic into tricium-amenable messages."""
  if is_windows:
    fix_file_path = _fix_win_file_path
  else:
    fix_file_path = lambda x: x

  base_message = diagnostic['message']
  base_line_number = diagnostic['line_number']
  base_file_path = fix_file_path(diagnostic['file_path'])

  yield (
      base_message,
      base_line_number,
      base_file_path,
      diagnostic['expansion_locs'],
  )

  # bugprone-use-after-move can be caused either by the addition of a use after
  # a move, or by the addition of a move before a use. We want to warn about
  # both cases, since CLs can easily introduce either.
  if diagnostic_name != 'bugprone-use-after-move':
    return

  for note in diagnostic.get('notes', ()):
    if note['message'] != 'move occurred here':
      continue

    message = 'A `move` operation occurred here, which caused %r at %s:%d' % (
        base_message,
        base_file_path,
        base_line_number,
    )
    yield message, note['line_number'], fix_file_path(
        note['file_path']), note['expansion_locs']


class TriciumClangTidyApi(RecipeApi):

  def lint_source_files(self,
                        output_dir,
                        file_paths,
                        is_windows=False):
    """Runs clang-tidy on provided source files in file_paths, then writes
    warnings to Tricium.

    file_paths is an interable of Path, only files that exist and have C/C++
    extensions will be linted.

    is_windows is a boolean; if true, we'll expect build commands to use
    clang-cl, and use windows-compatible linting.
    """
    src_file_suffixes = {'.cc', '.cpp', '.cxx', '.c', '.h', '.hpp'}
    affected = [
        f for f in file_paths
        # Check for removed files.
        if self.m.path.exists(f) and
        # Check for non-C/C++ files.
        self.m.path.splitext(f)[1] in src_file_suffixes
    ]

    if not affected:
      # No files affect, just write and be done.
      self.m.tricium.write_comments()
      return

    with self.m.step.nest('clang-tidy'):
      with self.m.step.nest('generate-warnings'):
        per_file_comments = self._generate_clang_tidy_comments(
            output_dir, affected, is_windows)

      for file_path, comments in per_file_comments.items():
        for category, message, line_number, suggestions in comments:
          # Clang-tidy only gives us one file offset, so we use line
          # comments.
          self.m.tricium.add_comment(
              category,
              message,
              file_path,
              start_line=line_number,
              suggestions=suggestions)

    self.m.tricium.write_comments()

  def _generate_clang_tidy_comments(self, output_dir, file_paths, is_windows):
    clang_tidy_location = self.m.context.cwd.joinpath(*_clang_tidy_path)
    per_file_comments = collections.defaultdict(_SourceFileComments)

    warnings_file = self.m.path.cleanup_dir / 'clang_tidy_complaints.yaml'

    tricium_clang_tidy_command = [
        'vpython3',
        self.resource('tricium_clang_tidy_script.py'),
        '--out_dir=%s' % output_dir,
        '--findings_file=%s' % warnings_file,
        '--clang_tidy_binary=%s' % clang_tidy_location,
        '--base_path=%s' % self.m.context.cwd,
        '--ninja_jobs=%s' % self.m.reclient.jobs,
        '--verbose',
    ]

    # Speficy the path to gn under buldtools explicitly.
    gn_subdir = {
        'linux': 'linux64',
        'mac': 'mac',
        'win': 'win',
    }[self.m.platform.name]
    gn_path = self.m.context.cwd.joinpath('buildtools', gn_subdir, 'gn')
    tricium_clang_tidy_command.append('--gn=' + str(gn_path))

    if is_windows:
      tricium_clang_tidy_command.append('--windows')

    tricium_clang_tidy_command.append('--')
    tricium_clang_tidy_command += file_paths

    if is_windows:
      fix_file_path = _fix_win_file_path
    else:
      fix_file_path = lambda x: x

    autoninja_dir = self.m.path.checkout_dir.joinpath('third_party',
                                                      'depot_tools')
    autoninja_path = {'PATH': [autoninja_dir]}
    with self.m.context(env_suffixes=autoninja_path):
      self._build_with_reclient('tricium_clang_tidy_script.py',
                                tricium_clang_tidy_command)

    # Please see tricium_clang_tidy_script.py for full docs on what this
    # contains.
    clang_tidy_output = self.m.file.read_json('read tidy output', warnings_file)

    if clang_tidy_output.get('failed_src_files') or clang_tidy_output.get(
        'failed_tidy_files') or clang_tidy_output.get('timed_out_src_files'):
      self.m.step.active_result.presentation.status = 'WARNING'

    for file_path in clang_tidy_output.get('failed_src_files', ()):
      per_file_comments[fix_file_path(file_path)].note_build_failed()

    for file_path in clang_tidy_output.get('failed_tidy_files', ()):
      per_file_comments[fix_file_path(file_path)].note_tidy_failed()

    for file_path in clang_tidy_output.get('timed_out_src_files', ()):
      per_file_comments[fix_file_path(file_path)].note_tidy_timed_out()

    for diagnostic in clang_tidy_output.get('diagnostics', ()):
      file_path = fix_file_path(diagnostic['file_path'])
      assert file_path, ("Empty paths should've been filtered "
                         "by tricium_clang_tidy: %s" % diagnostic)

      diag_name = diagnostic['diag_name']
      if _is_clang_diagnostic(diag_name):
        continue

      tidy_replacements = diagnostic['replacements']
      if tidy_replacements:
        suggestions = [{
            'replacements': [{
                'path': file_path,
                'replacement': x['new_text'],
                'start_line': x['start_line'],
                'end_line': x['end_line'],
                'start_char': x['start_char'],
                'end_char': x['end_char'],
            } for x in tidy_replacements],
        }]
      else:
        suggestions = ()

      # One clang-tidy diagnostic can turn into multiple Tricium comments when
      # there's action at a distance (e.g., we report use-after-move both where
      # the use occurs, and where each move that could end in badness occurs).
      # crbug.com/1157503
      #
      # Because all messages have the same diagnostic, we attach the same set
      # of replacements to each message.
      for message, line, file_path, raw_expansions in _parse_tidy_diagnostic(
          diagnostic, diag_name, is_windows=is_windows):
        if raw_expansions:
          # Expansions are emitted by clang-tidy (thus tricium_clang_tidy) such
          # that item [i] "invokes" the expansion of [i+1]. So the last item in
          # this list should tell us where the original macro definition is.
          e = raw_expansions[-1]
          # FIXME(gbiv): In general, notes should be handled here, as well. It's
          # unclear how to do so cleanly.
          fp = fix_file_path(e['file_path'])
          per_file_comments[fp].add_macro_expanded_tidy_diagnostic(
              message, e['line_number'], diag_name, suggestions, file_path,
              line)
        else:
          per_file_comments[file_path].add_tidy_diagnostic(
              message, line, diag_name, suggestions)

    return per_file_comments

  def _build_with_reclient(self, step_name, cmd):
    ninja_command = ""
    with self.m.reclient.process(step_name, ninja_command) as p:
      step_result = self.m.step(step_name, cmd)
      p.build_exit_status = step_result.retcode
