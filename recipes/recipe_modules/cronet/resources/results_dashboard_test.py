#!/usr/bin/env vpython
# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Test cases for results_dashboard."""

import httplib2
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
import urllib
import zlib

import mock

import results_dashboard


class FakeDateTime(object):
  # pylint: disable=R0201
  def utctimetuple(self):
    return time.struct_time((2013, 8, 1, 0, 0, 0, 3, 217, 0))

  @classmethod
  def utcnow(cls):
    return cls()


class ResultsDashboardFormatTest(unittest.TestCase):
  """Tests related to functions which convert data format."""

  def setUp(self):
    super(ResultsDashboardFormatTest, self).setUp()
    self.maxDiff = None
    os.environ['BUILDBOT_BUILDBOTURL'] = (
        'http://build.chromium.org/p/my.master/')

  def test_MakeDashboardJsonV1(self):
    self.internal_Test_MakeDashboardJsonV1()

  def test_MakeDashboardJsonV1WithDisabledBenchmark(self):
    self.internal_Test_MakeDashboardJsonV1(enabled=False)

  def internal_Test_MakeDashboardJsonV1(self, enabled=True):
    with mock.patch('results_dashboard._GetTimestamp') as getTS:
      getTS.side_effect = [307226, 307226]

      v1json = results_dashboard.MakeDashboardJsonV1(
          {
              'some_json': 'from_telemetry',
              'enabled': enabled
          }, {
              'rev': 'f46bf3c',
              'git_revision': 'f46bf3c',
              'v8_rev': '73a34f',
              'commit_pos': 307226
          }, 'foo_test', 'my-bot', 'Builder', '10', {
              'a_annotation': 'xyz',
              'r_my_rev': '789abc01'
          }, True, 'ChromiumPerf')
      self.assertEqual(
          {
              'master': 'ChromiumPerf',
              'bot': 'my-bot',
              'chart_data': {
                  'some_json': 'from_telemetry',
                  'enabled': enabled
              },
              'is_ref': True,
              'test_suite_name': 'foo_test',
              'point_id': 307226,
              'supplemental': {
                  'annotation':
                      'xyz',
                  'a_stdio_uri': ('[Buildbot stdio](http://build.chromium.org/p'
                                  '/my.master/builders/Builder/builds/10/steps/'
                                  'foo_test/logs/stdio)')
              },
              'versions': {
                  'v8_rev': '73a34f',
                  'chromium': 'f46bf3c',
                  'my_rev': '789abc01'
              }
          }, v1json)

  @mock.patch('subprocess.call')
  def test_MakeHistogramSetWithDiagnostics_CallsAddReservedDiagnostics(
      self, call):
    with tempfile.NamedTemporaryFile(suffix='.json', prefix='test') as f:
      f.write(json.dumps({'histogram': 'data'}))
      f.flush()

      def _mock_call(args):
        self.assertEqual(args, [
            sys.executable,
            '/path/to/chromium/src/third_party/catapult/tracing/bin/'
            'add_reserved_diagnostics', '--benchmarks', 'foo.test', '--bots',
            'bot', '--builds', '1', '--masters', 'ChromiumPerf',
            '--is_reference_build', '', '--log_urls_k', 'Buildbot stdio',
            '--log_urls_v', 'http://build.chromium.org/p/my.master/'
            'builders/builder/builds/1/steps/foo.test/logs/stdio', f.name
        ])

      call.side_effect = _mock_call

      results_dashboard.MakeHistogramSetWithDiagnostics(f.name,
                                                        '/path/to/chromium',
                                                        'foo.test', 'bot',
                                                        'builder', 1, {}, False,
                                                        'ChromiumPerf')

  def test_MakeListOfPoints_MinimalCase(self):
    """A very simple test of a call to MakeListOfPoints."""

    actual_points = results_dashboard.MakeListOfPoints(
        {'bar': {
            'traces': {
                'baz': ["100.0", "5.0"]
            },
            'rev': '307226',
        }}, 'my-bot', 'foo_test', 'Builder', 10, {}, 'MyMaster')
    expected_points = [{
        'master': 'MyMaster',
        'bot': 'my-bot',
        'test': 'foo_test/bar/baz',
        'revision': 307226,
        'value': '100.0',
        'error': '5.0',
        'supplemental_columns': {
            'r_commit_pos':
                307226,
            'a_stdio_uri': ('[Buildbot stdio](http://build.chromium.org/p'
                            '/my.master/builders/Builder/builds/10/steps/'
                            'foo_test/logs/stdio)')
        },
    }]
    self.assertEqual(expected_points, actual_points)

  def test_MakeListOfPoints_RevisionsDict(self):
    """A very simple test of a call to MakeListOfPoints."""

    actual_points = results_dashboard.MakeListOfPoints(
        {'bar': {
            'traces': {
                'baz': ["100.0", "5.0"]
            },
        }},
        'my-bot',
        'foo_test',
        'Builder',
        10, {},
        revisions_dict={'rev': '377777'},
        perf_dashboard_machine_group='MyMaster')
    expected_points = [{
        'master': 'MyMaster',
        'bot': 'my-bot',
        'test': 'foo_test/bar/baz',
        'revision': 377777,
        'value': '100.0',
        'error': '5.0',
        'supplemental_columns': {
            'r_commit_pos':
                377777,
            'a_stdio_uri': ('[Buildbot stdio](http://build.chromium.org/p'
                            '/my.master/builders/Builder/builds/10/steps/'
                            'foo_test/logs/stdio)')
        },
    }]
    self.assertEqual(expected_points, actual_points)

  def test_MakeListOfPoints_GeneralCase(self):
    """A test of making a list of points, including all optional data."""

    actual_points = results_dashboard.MakeListOfPoints(
        {
            'bar': {
                'traces': {
                    'bar': ['100.0', '5.0'],
                    'bar_ref': ['98.5', '5.0'],
                },
                'rev': '12345',
                'git_revision': '46790669f8a2ecd7249ab92418260316b1c60dbf',
                'v8_rev': 'undefined',
                'units': 'KB',
            },
            'x': {
                'traces': {
                    'y': [10.0, 0],
                },
                'important': ['y'],
                'rev': '23456',
                'git_revision': '46790669f8a2ecd7249ab92418260316b1c60dbf',
                'v8_rev': '2345',
                'units': 'count',
            },
        },
        'my-bot',
        'foo_test',
        'Builder',
        10,
        {
            'r_bar': '89abcdef',
            # The supplemental columns here are included in all points.
        },
        'MyMaster')
    expected_points = [
        {
            'master': 'MyMaster',
            'bot': 'my-bot',
            'test': 'foo_test/bar',  # Note that trace name is omitted.
            'revision': 12345,
            'value': '100.0',
            'error': '5.0',
            'units': 'KB',
            'supplemental_columns': {
                'r_bar':
                    '89abcdef',
                'r_chromium':
                    '46790669f8a2ecd7249ab92418260316b1c60dbf',
                'a_stdio_uri': ('[Buildbot stdio](http://build.chromium.org/p'
                                '/my.master/builders/Builder/builds/10/steps/'
                                'foo_test/logs/stdio)')
                # Note that v8 rev is not included since it was 'undefined'.
            },
        },
        {
            'master': 'MyMaster',
            'bot': 'my-bot',
            'test': 'foo_test/bar/ref',  # Note the change in trace name.
            'revision': 12345,
            'value': '98.5',
            'error': '5.0',
            'units': 'KB',
            'supplemental_columns': {
                'r_bar':
                    '89abcdef',
                'r_chromium':
                    '46790669f8a2ecd7249ab92418260316b1c60dbf',
                'a_stdio_uri': ('[Buildbot stdio](http://build.chromium.org/p'
                                '/my.master/builders/Builder/builds/10/steps/'
                                'foo_test/logs/stdio)')
            },
        },
        {
            'master': 'MyMaster',
            'bot': 'my-bot',
            'test': 'foo_test/x/y',
            'revision': 23456,
            'value': 10.0,
            'error': 0,
            'units': 'count',
            'important': True,
            'supplemental_columns': {
                'r_v8_rev':
                    '2345',
                'r_bar':
                    '89abcdef',
                'r_chromium':
                    '46790669f8a2ecd7249ab92418260316b1c60dbf',
                'a_stdio_uri': ('[Buildbot stdio](http://build.chromium.org/p'
                                '/my.master/builders/Builder/builds/10/steps/'
                                'foo_test/logs/stdio)')
            },
        },
    ]
    self.assertEqual(expected_points, actual_points)

  @mock.patch('datetime.datetime', new=FakeDateTime)
  def test_MakeListOfPoints_TimestampUsedWhenRevisionIsNaN(self):
    """Tests sending data with a git hash as "revision"."""
    actual_points = results_dashboard.MakeListOfPoints(
        {
            'bar': {
                'traces': {
                    'baz': ["100.0", "5.0"]
                },
                'rev': '2eca27b067e3e57c70e40b8b95d0030c5d7c1a7f',
            }
        }, 'my-bot', 'foo_test', 'Builder', 10, {}, 'ChromiumPerf')
    expected_points = [{
        'master': 'ChromiumPerf',
        'bot': 'my-bot',
        'test': 'foo_test/bar/baz',
        # Corresponding timestamp for the fake datetime is used.
        'revision': 1375315200,
        'value': '100.0',
        'error': '5.0',
        'supplemental_columns': {
            'r_chromium':
                '2eca27b067e3e57c70e40b8b95d0030c5d7c1a7f',
            'a_stdio_uri': ('[Buildbot stdio](http://build.chromium.org/p'
                            '/my.master/builders/Builder/builds/10/steps/'
                            'foo_test/logs/stdio)')
        },
    }]
    self.assertEqual(expected_points, actual_points)

  @mock.patch('datetime.datetime', new=FakeDateTime)
  def test_GetStdioUri(self):
    expected_supplemental_column = {
        'a_stdio_uri': ('[Buildbot stdio](http://build.chromium.org/p'
                        '/my.master/builders/Builder/builds/10/steps/'
                        'foo_test/logs/stdio)')
    }
    stdio_uri_column = results_dashboard._GetStdioUriColumn(
        'foo_test', 'Builder', 10)
    self.assertEqual(expected_supplemental_column, stdio_uri_column)


class ResultsDashboardSendDataTest(unittest.TestCase):
  """Tests related to sending requests and saving data from failed requests."""

  def setUp(self):
    super(ResultsDashboardSendDataTest, self).setUp()
    self.build_dir = tempfile.mkdtemp()
    os.makedirs(os.path.join(self.build_dir, results_dashboard.CACHE_DIR))
    self.cache_file_name = os.path.join(self.build_dir,
                                        results_dashboard.CACHE_DIR,
                                        results_dashboard.CACHE_FILENAME)

  def tearDown(self):
    shutil.rmtree(self.build_dir)

  def _TestSendJsonResults(self,
                           new_data,
                           expected_json,
                           errors,
                           status_codes,
                           expected_result,
                           oauth_token=''):
    """Test one call of SendResults with the given set of arguments.

    This method will fail a test case if the JSON that gets sent and the
    errors that are raised when results_dashboard.SendResults is called
    don't match the expected json and errors.

    Args:
      new_data: The new (not cached) data to send.
      expected_json_sent: A list of JSON string expected to be sent.
      errors: A list of corresponding errors expected to be received.
      expected_result: Expected return value of SendResults.
      oauth_token: An oauth token to be used for upload.
    """
    idx = [0]  # ref

    def _fake_httplib2_req(url, data, token):
      i = idx[0]
      idx[0] += 1
      exp_data, err = expected_json[i], errors[i],
      urllib.urlencode({'data': json.dumps(exp_data)})
      self.assertEqual(url, 'https://x.com/add_point')
      self.assertEqual(data, urllib.urlencode({'data': json.dumps(exp_data)}))
      self.assertEqual(token, oauth_token)

      if err:
        raise err

      return httplib2.Response({'status': status_codes[i], 'reason': 'foo'}), ''

    with mock.patch(
        'results_dashboard._Httplib2PostRequest',
        side_effect=_fake_httplib2_req):
      result = results_dashboard.SendResults(
          new_data, 'https://x.com', self.build_dir, oauth_token=oauth_token)
      self.assertEqual(expected_result, result)

  def test_Json_500_Fatal(self):
    """500 responses are fatal."""
    # First, some data is sent but it fails for some reason.
    self._TestSendJsonResults(
        {
            'sample': 1,
            'master': 'm',
            'bot': 'b',
            'chart_data': {
                'benchmark_name': 'b'
            },
            'point_id': 1234
        }, [{
            'sample': 1,
            'bot': 'b',
            'master': 'm',
            'chart_data': {
                'benchmark_name': 'b'
            },
            'point_id': 1234
        }], [None], [500],
        False,
        oauth_token='fake')

  def test_Json_403_Retried(self):
    """After failing once, the same JSON is sent the next time."""
    # First, some data is sent but it fails for some reason.
    self._TestSendJsonResults(
        {
            'sample': 1,
            'master': 'm',
            'bot': 'b',
            'chart_data': {
                'benchmark_name': 'b'
            },
            'point_id': 1234
        }, [{
            'sample': 1,
            'bot': 'b',
            'master': 'm',
            'chart_data': {
                'benchmark_name': 'b'
            },
            'point_id': 1234
        }], [None], [403],
        True,
        oauth_token='fake')

    # The next time, the old data is sent with the new data.
    self._TestSendJsonResults(
        {
            'sample': 2,
            'master': 'm2',
            'bot': 'b2',
            'chart_data': {
                'benchmark_name': 'b'
            },
            'point_id': 1234
        }, [{
            'sample': 1,
            'bot': 'b',
            'master': 'm',
            'chart_data': {
                'benchmark_name': 'b'
            },
            'point_id': 1234
        }, {
            'sample': 2,
            'bot': 'b2',
            'master': 'm2',
            'chart_data': {
                'benchmark_name': 'b'
            },
            'point_id': 1234
        }], [None, None], [200, 200],
        True,
        oauth_token='fake')

  def test_Json_UnexpectedException_Fatal(self):
    """Unexpected exceptions are fatal."""
    # First, some data is sent but it fails for some reason.
    self._TestSendJsonResults(
        {
            'sample': 1,
            'master': 'm',
            'bot': 'b',
            'chart_data': {
                'benchmark_name': 'b'
            },
            'point_id': 1234
        }, [{
            'sample': 1,
            'bot': 'b',
            'master': 'm',
            'chart_data': {
                'benchmark_name': 'b'
            },
            'point_id': 1234
        }], [ValueError('foo')], [200],
        False,
        oauth_token='fake')

  def test_Json_UnexpectedException_Fatal(self):
    """Unexpected exceptions are fatal."""
    # First, some data is sent but it fails for some reason.
    self._TestSendJsonResults(
        {
            'sample': 1,
            'master': 'm',
            'bot': 'b',
            'chart_data': {
                'benchmark_name': 'b'
            },
            'point_id': 1234
        }, [{
            'sample': 1,
            'bot': 'b',
            'master': 'm',
            'chart_data': {
                'benchmark_name': 'b'
            },
            'point_id': 1234
        }], [ValueError('foo')], [200],
        False,
        oauth_token='fake')

  def test_Json_TransientFailure_Retried(self):
    """After failing once, the same JSON is sent the next time."""
    # First, some data is sent but it fails for some reason.
    self._TestSendJsonResults(
        {
            'sample': 1,
            'master': 'm',
            'bot': 'b',
            'chart_data': {
                'benchmark_name': 'b'
            },
            'point_id': 1234
        }, [{
            'sample': 1,
            'bot': 'b',
            'master': 'm',
            'chart_data': {
                'benchmark_name': 'b'
            },
            'point_id': 1234
        }], [httplib2.HttpLib2Error('some reason')], [200],
        True,
        oauth_token='fake')

    # The next time, the old data is sent with the new data.
    self._TestSendJsonResults(
        {
            'sample': 2,
            'master': 'm2',
            'bot': 'b2',
            'chart_data': {
                'benchmark_name': 'b'
            },
            'point_id': 1234
        }, [{
            'sample': 1,
            'bot': 'b',
            'master': 'm',
            'chart_data': {
                'benchmark_name': 'b'
            },
            'point_id': 1234
        }, {
            'sample': 2,
            'bot': 'b2',
            'master': 'm2',
            'chart_data': {
                'benchmark_name': 'b'
            },
            'point_id': 1234
        }], [None, None], [200, 200],
        True,
        oauth_token='fake')

  def _TestSendHistogramResults(self,
                                new_data,
                                expected_data,
                                errors,
                                status_codes,
                                expected_result,
                                send_as_histograms=False,
                                oauth_token=''):
    """Test one call of SendResults with the given set of arguments.

    This method will fail a test case if the JSON that gets sent and the
    errors that are raised when results_dashboard.SendResults is called
    don't match the expected json and errors.

    Args:
      new_data: The new (not cached) data to send.
      expected_data: A list of data expected to be sent.
      errors: A list of corresponding errors expected to be received.
      status_codes: A list of corresponding status_codes for responses.
      expected_result: Expected return value of SendResults.
      send_as_histograms: True if result is to be sent to /add_histograms.
      oauth_token: An oauth token to be used for upload.
    """
    idx = [0]

    def _fake_httplib2_req(url, data, token):
      i = idx[0]
      idx[0] += 1
      exp_data, err = expected_data[i], errors[i],
      self.assertEqual(url, 'https://fake.dashboard/add_histograms')
      self.assertEqual(data, zlib.compress(json.dumps(exp_data)))
      self.assertEqual(token, oauth_token)

      if err:
        raise err

      return httplib2.Response({'status': status_codes[i], 'reason': 'foo'}), ''

    with mock.patch(
        'results_dashboard._Httplib2PostRequest',
        side_effect=_fake_httplib2_req):
      result = results_dashboard.SendResults(
          new_data,
          'https://fake.dashboard',
          self.build_dir,
          send_as_histograms=send_as_histograms,
          oauth_token=oauth_token)
      self.assertEqual(expected_result, result)

  def test_Histogram_500_Fatal(self):
    """500 responses are fatal."""
    # First, some data is sent but it fails for some reason.
    self._TestSendHistogramResults([{
        'histogram': 'data1'
    }], [[{
        'histogram': 'data1'
    }]], [None], [500],
                                   False,
                                   send_as_histograms=True,
                                   oauth_token='fake')

  def test_Histogram_403_Retried(self):
    """After failing once, the same JSON is sent the next time."""
    # First, some data is sent but it fails for some reason.
    self._TestSendHistogramResults([{
        'histogram': 'data1'
    }], [[{
        'histogram': 'data1'
    }]], [None], [403],
                                   True,
                                   send_as_histograms=True,
                                   oauth_token='fake')

    # The next time, the old data is sent with the new data.
    self._TestSendHistogramResults([{
        'histogram': 'data2'
    }], [[{
        'histogram': 'data1'
    }], [{
        'histogram': 'data2'
    }]], [None, None], [200, 200],
                                   True,
                                   send_as_histograms=True,
                                   oauth_token='fake')

  def test_Histogram_UnexpectedException_Fatal(self):
    """Unexpected exceptions are fatal."""
    # First, some data is sent but it fails for some reason.
    self._TestSendHistogramResults([{
        'histogram': 'data1'
    }], [[{
        'histogram': 'data1'
    }]], [ValueError('foo')], [200],
                                   False,
                                   send_as_histograms=True,
                                   oauth_token='fake')

  def test_Histogram_UnexpectedException_Fatal(self):
    """Unexpected exceptions are fatal."""
    # First, some data is sent but it fails for some reason.
    self._TestSendHistogramResults([{
        'histogram': 'data1'
    }], [[{
        'histogram': 'data1'
    }]], [ValueError('foo')], [200],
                                   False,
                                   send_as_histograms=True,
                                   oauth_token='fake')

  def test_Histogram_TransientFailure_Retried(self):
    """After failing once, the same JSON is sent the next time."""
    # First, some data is sent but it fails for some reason.
    self._TestSendHistogramResults([{
        'histogram': 'data1'
    }], [[{
        'histogram': 'data1'
    }]], [httplib2.HttpLib2Error('some reason')], [200],
                                   True,
                                   send_as_histograms=True,
                                   oauth_token='fake')

    # The next time, the old data is sent with the new data.
    self._TestSendHistogramResults([{
        'histogram': 'data2'
    }], [[{
        'histogram': 'data1'
    }], [{
        'histogram': 'data2'
    }]], [None, None], [200, 200],
                                   True,
                                   send_as_histograms=True,
                                   oauth_token='fake')


class ResultsDashboardTest(unittest.TestCase):
  """Tests for other functions in results_dashboard."""

  # Testing private method.
  # pylint: disable=W0212
  def test_DashboardUrl_WithData(self):
    self.assertEqual(
        ('https://chromeperf.appspot.com/report'
         '?masters=MyMaster&bots=b&tests=sunspider&rev=1234'),
        results_dashboard._DashboardUrl('https://chromeperf.appspot.com', [{
            'master': 'MyMaster',
            'bot': 'b',
            'test': 'sunspider/Total',
            'revision': 1234,
            'value': 10,
        }]))

  def test_DashboardUrl_UnexpectedData(self):
    self.assertIsNone(results_dashboard._DashboardUrl('', {}))


if __name__ == '__main__':
  unittest.main()
