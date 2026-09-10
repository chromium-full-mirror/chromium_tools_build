# Copyright 2022 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import io
import json
import unittest

from dataclasses import dataclass, field
from typing import Dict, Optional, Sequence
from unittest.mock import patch, mock_open

from libs.test_binary import utils, create_test_binary_from_jsonish
from libs.test_binary import create_test_binary_from_task_request
from libs.test_binary.base_test_binary import (
  BaseTestBinary,
  TestBinaryWithBatchMixin,
  TestBinaryWithParallelMixin,
)
from libs.test_binary.gtest_test_binary import GTestTestBinary
from libs.test_binary.blink_web_tests_binary import BlinkWebTestsBinary
from testdata import get_test_data


@dataclass
class MockTaskSlice:
  command: Sequence[str] = ()
  relative_cwd: str = ''
  env_vars: Dict[str, str] = field(default_factory=dict)
  dimensions: Dict[str, str] = field(default_factory=dict)

  # TODO - add better support for this later, if needed
  cas_input_root: Optional[Dict] = None

  @classmethod
  def from_jsonish(cls, task_slice: Dict) -> MockTaskSlice:
    p = task_slice['properties']
    return cls(
      command=p.get('command', []),
      relative_cwd=p.get('relative_cwd', ''),
      env_vars={pair['key']: pair['value'] for pair in p.get('env', [])},
      dimensions={
        pair['key']: pair['value'] for pair in p.get('dimensions', [])
      },
      cas_input_root=p.get('cas_input_root', None),
    )


@dataclass
class MockTaskRequest:
  tags: Sequence[str] = ()
  task_slices: Sequence[MockTaskSlice] = ()

  def __getitem__(self, idx: int) -> MockTaskSlice:
    return self.task_slices[idx]

  def __len__(self) -> int:
    return len(self.task_slices)

  @classmethod
  def from_testdata(cls, datafile: str) -> MockTaskRequest:
    data = json.loads(get_test_data(datafile))
    return cls(
      tags=data.get('tags', ()),
      task_slices=[
        MockTaskSlice.from_jsonish(slc) for slc in data['task_slices']
      ],
    )


class TestBinaryUtilsTest(unittest.TestCase):
  def test_strip_command_switches(self):
    self.assertEqual(
      utils.strip_command_switches(['--foo', '1', '2', '3', '4'], {'foo': 0}),
      ['1', '2', '3', '4'],
    )
    self.assertEqual(
      utils.strip_command_switches(['--foo', '1', '2', '3', '4'], {'foo': 2}),
      ['3', '4'],
    )
    self.assertEqual(
      utils.strip_command_switches(
        ['--foo', '1', '--bar', '3', '4'], {'foo': 2}
      ),
      ['--bar', '3', '4'],
    )
    self.assertEqual(
      utils.strip_command_switches(
        ['--foo', '1', '--bar', '3', '4'], {'foo': 2, 'bar': 1}
      ),
      ['4'],
    )
    # --switch=value shouldn't take multiple values.
    self.assertEqual(
      utils.strip_command_switches(
        ['--foo=1', '--foo=2', '1', '2', '3', '4'], {'foo': 2}
      ),
      ['1', '2', '3', '4'],
    )
    # I don't know if -foo= 1 2 is valid, but I would expect following behavior.
    self.assertEqual(
      utils.strip_command_switches(['--foo=', '1', '2', '3', '4'], {'foo': 2}),
      ['3', '4'],
    )

  @patch('subprocess.Popen')
  @patch('logging.info')
  @patch('os.environ', new={'foo': 'bar'})
  @patch('os.chdir')
  def test_run_cmd(self, mock_chdir, mock_logging, mock_popen):
    mock_popen.return_value.wait.return_value = 0
    cmd = ['./exec', '--args']
    utils.run_cmd(cmd, env={}, cwd='out\\Release_x64')
    mock_logging.assert_called_once_with(
      'Running %r in %r with %r', cmd, 'out\\Release_x64', {}
    )
    mock_popen.assert_called_once_with(cmd, env={})
    self.assertEqual(mock_chdir.call_count, 2)

  @patch('subprocess.Popen')
  @patch('logging.info')
  @patch('os.environ', new={'ISOLATED_OUTDIR': '/isolated_out_dir'})
  @patch('os.chdir')
  def test_run_cmd_with_isolated_outdir(
    self, mock_chdir, mock_logging, mock_popen
  ):
    mock_popen.return_value.wait.return_value = 0
    cmd = ['./exec', '--args', r'--output=${ISOLATED_OUTDIR}']
    utils.run_cmd(cmd, env={}, cwd='out\\Release_x64')
    mock_logging.assert_called_once_with(
      'Running %r in %r with %r',
      ['./exec', '--args', '--output=/isolated_out_dir'],
      'out\\Release_x64',
      {},
    )
    mock_popen.assert_called_once_with(
      ['./exec', '--args', '--output=/isolated_out_dir'], env={}
    )

  @patch('subprocess.Popen')
  @patch('logging.info')
  @patch('os.environ', new={'foo': 'bar'})
  @patch('os.chdir')
  def test_run_cmd_with_failure(self, mock_chdir, mock_logging, mock_popen):
    mock_popen.return_value.__enter__.return_value.wait.return_value = 1
    cmd = ['./exec', '--args']
    ret = utils.run_cmd(cmd, env={}, cwd='out\\Release_x64')
    mock_popen.assert_called_once_with(cmd, env={})
    self.assertEqual(ret, 1)
    self.assertEqual(mock_chdir.call_count, 2)


class TestBinaryFactoryTest(unittest.TestCase):
  def setUp(self):
    self.maxDiff = None

  def test_from_jsonish_no_class_name(self):
    jsonish = json.loads(get_test_data('gtest_test_binary.json'))
    jsonish.pop('class_name')
    with self.assertRaises(ValueError):
      create_test_binary_from_jsonish(jsonish)

  def test_from_jsonish_unknown_class_name(self):
    jsonish = json.loads(get_test_data('gtest_test_binary.json'))
    jsonish['class_name'] = 'UnknownTestBinary'
    with self.assertRaises(ValueError):
      create_test_binary_from_jsonish(jsonish)

  def test_to_jsonish_should_support_with_methods(self):
    jsonish = json.loads(get_test_data('gtest_test_binary_with_overrides.json'))
    test_binary = create_test_binary_from_jsonish(jsonish)
    test_binary = (
      test_binary.with_tests(
        ["MockUnitTests.CrashTest", "MockUnitTests.PassTest"]
      )
      .with_repeat(3)
      .with_single_batch()
      .with_parallel_jobs(5)
    )
    to_jsonish = test_binary.to_jsonish()
    test_binary_from_jsonish = create_test_binary_from_jsonish(to_jsonish)
    self.assertEqual(test_binary_from_jsonish.tests, test_binary.tests)
    self.assertEqual(test_binary_from_jsonish.repeat, test_binary.repeat)
    self.assertEqual(
      test_binary_from_jsonish.single_batch, test_binary.single_batch
    )
    self.assertEqual(
      test_binary_from_jsonish.parallel_jobs, test_binary.parallel_jobs
    )

  def test_from_jsonish(self):
    jsonish = json.loads(get_test_data('gtest_test_binary.json'))
    test_binary = create_test_binary_from_jsonish(jsonish)
    to_jsonish = test_binary.to_jsonish()
    self.assertEqual(to_jsonish, jsonish)

  def test_from_jsonish_blink_web_tests(self):
    jsonish = json.loads(get_test_data('blink_web_tests_binary.json'))
    test_binary = create_test_binary_from_jsonish(jsonish)
    to_jsonish = test_binary.to_jsonish()
    self.assertEqual(to_jsonish, jsonish)

  def test_from_jsonish_with_overrides(self):
    jsonish = json.loads(get_test_data('gtest_test_binary_with_overrides.json'))
    test_binary = create_test_binary_from_jsonish(jsonish)
    to_jsonish = test_binary.to_jsonish()
    self.assertEqual(to_jsonish, jsonish)

  def test_from_task_request_empty(self):
    tr = MockTaskRequest()
    with self.assertRaisesRegex(ValueError, "No TaskSlice"):
      create_test_binary_from_task_request(tr)

  def test_from_task_request_no_test_suite(self):
    tr = MockTaskRequest(
      tags=['test_suite:chrome_all_tast_tests'],
      task_slices=[
        MockTaskSlice(),
      ],
    )
    with self.assertRaisesRegex(NotImplementedError, "Not Supported"):
      create_test_binary_from_task_request(tr)

  def test_from_task_request_completely_unsupported(self):
    tr = MockTaskRequest(
      task_slices=[
        MockTaskSlice(
          command=['something.exe', 'else'],
        ),
      ]
    )
    with self.assertRaisesRegex(NotImplementedError, "Not Supported"):
      create_test_binary_from_task_request(tr)

  def test_from_task_request_result_adapter(self):
    binary = create_test_binary_from_task_request(
      MockTaskRequest.from_testdata('gtest_task_request.json')
    )
    self.assertIsInstance(binary, GTestTestBinary)

  def test_from_task_request_test_launcher_summary_output(self):
    tr = MockTaskRequest(
      tags=['builder:somebuilder'],
      task_slices=[
        MockTaskSlice(
          command=[
            'gtest',
            '--test-launcher-summary-output',
            'outfile',
            'somescript.py',
          ],
        ),
      ],
    )
    binary = create_test_binary_from_task_request(tr)
    self.assertIsInstance(binary, GTestTestBinary)

  def test_from_task_request_blink(self):
    tr = MockTaskRequest.from_testdata('blink_web_tests_task_request.json')
    binary = create_test_binary_from_task_request(tr)
    self.assertIsInstance(binary, BlinkWebTestsBinary)


class BaseTestBinaryTest(unittest.TestCase):
  def setUp(self):
    # Note: BaseTestBinary can not created via create_test_binary_from_jsonish
    jsonish = json.loads(get_test_data('gtest_test_binary.json'))
    self.test_binary = BaseTestBinary.from_jsonish(jsonish)

    # @patch('tempfile.NamedTemporaryFile') for all tests
    tmp_patcher = patch('tempfile.NamedTemporaryFile')
    self.addClassCleanup(tmp_patcher.stop)
    mock_NamedTemporaryFile = tmp_patcher.start()

    def new_NamedTemporaryFile(
      mode='w+b',
      buffering=-1,
      encoding=None,
      newline=None,
      suffix=None,
      prefix=None,
      dir=None,  # pylint: disable=redefined-builtin
      delete=True,
      *,
      errors=None,
    ):
      fp = io.StringIO()
      fp.name = "/{0}/{1}mock-temp-{2}{3}".format(
        dir or 'mock-tmp',
        prefix or '',
        mock_NamedTemporaryFile.call_count,
        suffix or '',
      )
      return fp

    mock_NamedTemporaryFile.side_effect = new_NamedTemporaryFile

  def test_strip_for_bots(self):
    test_binary = self.test_binary.strip_for_bots()
    self.assertEqual(
      test_binary.command,
      [
        "vpython3",
        "../../testing/test_env.py",
        "./base_unittests.exe",
        "--test-launcher-bot-mode",
        "--asan=0",
        "--lsan=0",
        "--msan=0",
        "--tsan=0",
        "--cfi-diag=0",
        "--test-launcher-summary-output=${ISOLATED_OUTDIR}/output.json",
      ],
    )
    self.assertNotIn('LLVM_PROFILE_FILE', test_binary.env_vars)

  def test_strip_for_bots_should_return_a_copy(self):
    new_test_binary = self.test_binary.strip_for_bots()
    self.assertNotIn('a', new_test_binary.env_vars)
    new_test_binary.env_vars['a'] = 'b'
    self.assertIn('a', new_test_binary.env_vars)
    self.assertNotIn('a', self.test_binary.env_vars)
    self.assertIsNot(new_test_binary.command, self.test_binary.command)
    self.assertIsNot(new_test_binary.env_vars, self.test_binary.env_vars)
    self.assertIsNot(new_test_binary.dimensions, self.test_binary.dimensions)

  def test_strip_for_bots_should_raise_exceptions(self):
    test_binary = BaseTestBinary(['rdb', '--abc', './test'])
    with self.assertRaisesRegex(ValueError, 'Unsupported command wrapper:'):
      test_binary.strip_for_bots()

    test_binary = BaseTestBinary(['rdb', '--'])
    with self.assertRaisesRegex(ValueError, 'Empty command after strip:'):
      test_binary.strip_for_bots()

  def test_with_methods(self):
    tests = ['abc.aaa', 'cba.bbb']
    ret = self.test_binary.with_tests(tests)
    self.assertIsNot(ret, self.test_binary)
    self.assertIsNot(ret.tests, tests)
    self.assertEqual(ret.tests, tests)

    ret = self.test_binary.with_repeat(3)
    self.assertIsNot(ret, self.test_binary)
    self.assertEqual(ret.repeat, 3)

  def test_not_implemented(self):
    with self.assertRaisesRegex(
      NotImplementedError, 'Method should be implemented in sub-classes'
    ):
      self.test_binary._get_command()

    with self.assertRaisesRegex(
      NotImplementedError, 'RESULT_SUMMARY_CLS should be set in sub-classes'
    ):
      self.test_binary.run()

  @patch.object(utils, 'run_cmd')
  @patch(
    'builtins.open',
    new=mock_open(read_data=get_test_data('gtest_good_output.json')),
  )
  @patch('os.unlink')
  @patch.object(BaseTestBinary, '_get_command')
  @patch.object(BaseTestBinary, 'RESULT_SUMMARY_CLS')
  def test_run_tests(
    self, mock_result_cls, mock_get_command, mock_unlink, mock_run_cmd
  ):
    mock_get_command.return_value = ['return', 'command']
    test_binary = self.test_binary.strip_for_bots()
    test_binary.with_tests(['MockUnitTests.CrashTest'] * 2).run()
    mock_result_cls.from_output_json.assert_called()
    mock_get_command.assert_called_once_with(None, '/mock-tmp/mock-temp-1.json')
    mock_run_cmd.assert_called_once_with(
      ['return', 'command'], cwd=test_binary.cwd
    )
    mock_unlink.assert_called()

  @patch.object(utils, 'run_cmd')
  @patch(
    'builtins.open',
    new=mock_open(read_data=get_test_data('gtest_good_output.json')),
  )
  @patch('os.unlink')
  @patch.object(BaseTestBinary, '_get_command')
  @patch.object(BaseTestBinary, 'RESULT_SUMMARY_CLS')
  def test_run_tests_with_multiple_tests(
    self, mock_result_cls, mock_get_command, mock_unlink, mock_run_cmd
  ):
    mock_get_command.return_value = ['return', 'command']
    test_binary = self.test_binary.strip_for_bots()
    test_binary.with_tests(['MockUnitTests.CrashTest'] * 20).run()
    mock_result_cls.from_output_json.assert_called()
    mock_get_command.assert_called_once_with(
      '/mock-tmp/mock-temp-1.filter', '/mock-tmp/mock-temp-2.json'
    )
    mock_run_cmd.assert_called_once_with(
      ['return', 'command'], cwd=test_binary.cwd
    )
    mock_unlink.assert_called()

  @patch.object(BaseTestBinary, '_get_command')
  def test_readable_command(self, mock_get_command):
    mock_get_command.return_value = ['return', 'command']
    self.maxDiff = None
    test_binary = self.test_binary.strip_for_bots()
    readable_info = test_binary.with_tests(
      ['MockUnitTests.CrashTest']
    ).readable_command()
    mock_get_command.assert_called_with(None)
    self.assertEqual(readable_info, 'return command')

    readable_info = test_binary.with_tests(
      ['MockUnitTests.CrashTest'] * 20
    ).readable_command()
    mock_get_command.assert_called_with('tests.filter')
    self.assertEqual(
      readable_info,
      (
        "cat <<'__TESTS_FILTER_EOF__' > tests.filter\n"
        + ('\n'.join(['MockUnitTests.CrashTest'] * 20))
        + '\n__TESTS_FILTER_EOF__\nreturn command'
      ),
    )

    with self.assertRaisesRegex(
      Exception,
      'Tests list contains delimiter __TESTS_FILTER_EOF__, cannot safely '
      'generate readable command',
    ):
      test_binary.with_tests(
        ['MockUnitTests.CrashTest', '__TESTS_FILTER_EOF__'] * 10
      ).readable_command()

  @patch.object(BaseTestBinary, '_get_command')
  def test_as_command(self, mock_get_command):
    mock_get_command.return_value = ['return', 'command']
    self.maxDiff = None
    test_binary = self.test_binary.strip_for_bots()
    command = test_binary.with_tests(['MockUnitTests.CrashTest']).as_command(
      '${ISOLATED_OUTDIR}/output-$N$.json'
    )
    mock_get_command.assert_called_once_with(
      output_json='${ISOLATED_OUTDIR}/output-$N$.json'
    )
    self.assertEqual(command, ['return', 'command'])

    with self.assertRaisesRegex(
      Exception, 'Too many tests, filter file not supported in as_command'
    ):
      test_binary.with_tests(['MockUnitTests.CrashTest'] * 20).as_command()


class TestBinaryMixinTest(unittest.TestCase):
  def test_TestBinaryWithBatchMixin(self):

    # pylint: disable-next=abstract-method
    class NewTestBinary(TestBinaryWithBatchMixin, BaseTestBinary):
      pass

    test_binary = NewTestBinary(['abc'])
    self.assertEqual(test_binary.command, ['abc'])
    self.assertTrue(hasattr(test_binary, 'with_single_batch'))
    ret = test_binary.with_single_batch()
    self.assertTrue(ret.single_batch)

  def test_TestBinaryWithParallelMixin(self):

    # pylint: disable-next=abstract-method
    class NewTestBinary(TestBinaryWithParallelMixin, BaseTestBinary):
      pass

    test_binary = NewTestBinary(['abc'])
    self.assertEqual(test_binary.command, ['abc'])
    self.assertTrue(hasattr(test_binary, 'with_parallel_jobs'))
    ret = test_binary.with_parallel_jobs(3)
    self.assertEqual(ret.parallel_jobs, 3)

  def test_with_options_from_other(self):

    # pylint: disable-next=abstract-method
    class NewTestBinary(
      TestBinaryWithBatchMixin, TestBinaryWithParallelMixin, BaseTestBinary
    ):
      pass

    test_binary = NewTestBinary(['abc'])
    test_binary = (
      test_binary.with_tests(['a', 'b'])  # go/pyformat-break  #
      .with_repeat(10)  #
      .with_single_batch()  #
      .with_parallel_jobs(10)
    )
    new_test_binary = NewTestBinary(['abc'])
    new_test_binary = new_test_binary.with_options_from_other(test_binary)
    self.assertDictEqual(test_binary.to_jsonish(), new_test_binary.to_jsonish())
