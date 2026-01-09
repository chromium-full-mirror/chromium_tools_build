#!/usr/bin/env python3
# Copyright 2024 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Removes the current LUCI_CONTEXT and replaces it with a new one for a cmd.

This can be useful if you need to detach the result_sink aspect of a
LUCI_CONTEXT for a single sub-command.
"""

from __future__ import annotations

import os
import sys

os.environ.pop('LUCI_CONTEXT', None)
# We prefer the new LUCI_CONTEXT to only have the userinfo.email scope, since
# the UTR guarantees the user is already authenticated with that scope.
# Otherwise the user may have to re-login to use the default scopes.
args = [
    'luci-auth',
    'context',
    '-scopes',
    'https://www.googleapis.com/auth/userinfo.email',
    '--',
] + sys.argv[1:]
os.execvp(args[0], args)
