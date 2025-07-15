#!/usr/bin/env python3
# Copyright 2025 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Test cases for the decision graph invoker script.
"""

import unittest
import tempfile
import os
import json
import shutil
import sys
from unittest.mock import patch, mock_open, MagicMock

# Add the resources directory to the Python path to import the module under test
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'resources'))
import decisiongraph_invoker as dgi
import requests


class TestDecisionGraphInvoker(unittest.TestCase):
  """Test cases for decision graph invoker functionality."""

  def setUp(self):
    self.temp_dir = tempfile.mkdtemp()
    self.test_data = {
        "build_id": "12345",
        "change": 123456,
        "patchset": 1,
        "builder": "linux-rel",
        "api_key": "test_api_key_123"
    }

    self.api_key_file = os.path.join(self.temp_dir, 'api_key.txt')
    self.filter_dir = os.path.join(self.temp_dir, 'filters')

    os.makedirs(self.filter_dir)
    with open(self.api_key_file, 'w', encoding="utf-8") as f:
      f.write(self.test_data['api_key'])

    self.api_url_with_key = f"{dgi.API_URL}?key={self.test_data['api_key']}"

  def tearDown(self):
    shutil.rmtree(self.temp_dir)

  def _create_filter_file(self, test_suite, content=""):
    filter_file = os.path.join(self.filter_dir, f'{test_suite}.filter')
    with open(filter_file, 'w', encoding="utf-8") as f:
      f.write(content)
    return filter_file

  def test_read_api_key_success(self):
    api_key = dgi.read_api_key(self.api_key_file)
    self.assertEqual(api_key, self.test_data['api_key'])

  @patch('sys.exit')
  @patch('builtins.print')
  def test_read_api_key_file_not_found(self, mock_print, mock_exit):
    non_existent_file = os.path.join(self.temp_dir, 'non_existent.txt')
    dgi.read_api_key(non_existent_file)
    mock_print.assert_any_call(
        f"Error: API key file not found at {non_existent_file}")
    mock_exit.assert_called_once_with(1)

  @patch('sys.exit')
  @patch('builtins.print')
  def test_read_api_key_permission_error(self, mock_print, mock_exit):
    with patch('builtins.open', mock_open()) as mock_file:
      mock_file.side_effect = PermissionError("Permission denied")
      dgi.read_api_key('some_file.txt')
      mock_exit.assert_called_once_with(1)

  @patch('builtins.print')
  @patch('requests.post')
  def test_fetch_api_data_success(self, mock_post, mock_print):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"status": "success"}
    mock_response.text = '{"status": "success"}'
    mock_response.raise_for_status.return_value = None

    mock_post.return_value = mock_response

    response = dgi.fetch_api_data(self.api_url_with_key, {"test": "data"})
    self.assertEqual(response, {"status": "success"})
    mock_print.assert_any_call('{"status": "success"}')
    mock_print.assert_any_call(200)

  @patch('builtins.print')
  @patch('requests.post')
  def test_fetch_api_data_failure_http_error(self, mock_post, mock_print):
    mock_response = MagicMock()
    mock_response.status_code = 500
    mock_response.text = "Internal Server Error"
    mock_response.raise_for_status.side_effect = (
        requests.exceptions.HTTPError("500 Server Error"))

    mock_post.return_value = mock_response

    response = dgi.fetch_api_data(self.api_url_with_key, {"test": "data"})
    self.assertIsNone(response)
    mock_print.assert_any_call("An error occurred: 500 Server Error")
    mock_print.assert_any_call("Internal Server Error")
    mock_print.assert_any_call(500)

  @patch('builtins.print')
  @patch('requests.post')
  def test_fetch_api_data_failure_request_exception(self, mock_post,
                                                    mock_print):
    mock_post.side_effect = requests.exceptions.ConnectionError(
        "Max retries exceeded")

    response = dgi.fetch_api_data(self.api_url_with_key, {"test": "data"})
    self.assertIsNone(response)
    mock_print.assert_called_with("An error occurred: Max retries exceeded")

  @patch('builtins.print')
  def test_overwrite_filter_file_success(self, mock_print):
    """Test successful filter file overwrite."""
    test_suite = 'browser_tests'
    self._create_filter_file(test_suite)

    tests_to_skip = ['Test1.Method1', 'Test2.Method2', 'Test3.Method3']

    result = dgi.overwrite_filter_file(self.filter_dir, test_suite,
                                       tests_to_skip)

    self.assertTrue(result)
    mock_print.assert_any_call(
        f"Successfully wrote {len(tests_to_skip)} tests to {os.path.join(self.filter_dir, f'{test_suite}.filter')}"
    )

    # Verify file contents
    filter_file = os.path.join(self.filter_dir, f'{test_suite}.filter')
    with open(filter_file, 'r', encoding="utf-8") as f:
      content = f.read()

    expected_content = '-Test1.Method1*\n-Test2.Method2*\n-Test3.Method3*\n'
    self.assertEqual(content, expected_content)

  @patch('builtins.print')
  def test_overwrite_filter_file_empty_list(self, mock_print):
    test_suite = 'empty_tests'
    self._create_filter_file(test_suite)

    result = dgi.overwrite_filter_file(self.filter_dir, test_suite, [])

    self.assertTrue(result)
    mock_print.assert_any_call(
        f"Successfully wrote 0 tests to {os.path.join(self.filter_dir, f'{test_suite}.filter')}"
    )

    # Verify file is empty
    filter_file = os.path.join(self.filter_dir, f'{test_suite}.filter')
    with open(filter_file, 'r', encoding="utf-8") as f:
      content = f.read()

    self.assertEqual(content, '')

  @patch('builtins.print')
  def test_overwrite_filter_file_not_found(self, mock_print):
    test_suite = 'browser_tests'
    tests_to_skip = ['Test1.Method1']

    result = dgi.overwrite_filter_file(self.filter_dir, test_suite,
                                       tests_to_skip)

    self.assertFalse(result)
    filter_file = os.path.join(self.filter_dir, f'{test_suite}.filter')
    mock_print.assert_any_call(f"Error: Filter file not found at {filter_file}")

  @patch('builtins.print')
  def test_overwrite_filter_file_permission_error(self, mock_print):
    test_suite = 'browser_tests'
    self._create_filter_file(test_suite)

    filter_file = os.path.join(self.filter_dir, f'{test_suite}.filter')

    # Mock the file open for writing to raise PermissionError
    original_open = open

    def side_effect_open(file_path, mode='r', *args, **kwargs):  # pylint: disable=keyword-arg-before-vararg
      if mode == 'w' and file_path == filter_file:
        raise PermissionError("Permission denied")
      return original_open(file_path, mode, *args, **kwargs)

    with patch('builtins.open', side_effect=side_effect_open):
      result = dgi.overwrite_filter_file(self.filter_dir, test_suite, ['Test1'])

      self.assertFalse(result)
      mock_print.assert_any_call(
          f"An error occurred while writing to {filter_file}: Permission denied"
      )


if __name__ == '__main__':
  unittest.main(verbosity=2)
