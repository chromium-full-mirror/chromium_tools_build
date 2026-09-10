# Copyright (c) 2020 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Presubmit for recipes for tools/build repo.

See http://dev.chromium.org/developers/how-tos/depottools/presubmit-scripts for
details on the presubmit API built into git cl.
"""

import re

PRESUBMIT_VERSION = '2.0.0'

USE_PYTHON3 = True

_DEPRECATED_PROPERTY_REGEX = re.compile(
  r'\.properties\.(generic|(git_)?scheduled|tryserver)'
)


def CheckNoBuildbotPropertiesMethods(input_api, output_api):

  def buildbot_properties_method_not_used(file_ext, line):
    violation = file_ext == 'py' and _DEPRECATED_PROPERTY_REGEX.search(line)
    return not violation

  def error_formatter(filename, line_number, line):
    return '* {}:{}\n{}'.format(filename, line_number, line)

  violations = input_api.canned_checks._FindNewViolationsOfRule(
    buildbot_properties_method_not_used,
    input_api,
    error_formatter=error_formatter,
  )

  if violations:
    message = [
      'Found new uses of deprecated properties test API methods',
      'See go/no-buildbot-properties for more information',
    ]
    return [output_api.PresubmitError('\n'.join(message + violations))]

  return []
