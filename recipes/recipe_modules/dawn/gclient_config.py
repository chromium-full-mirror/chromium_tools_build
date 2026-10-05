# Copyright 2025 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from RECIPE_MODULES.depot_tools.gclient import CONFIG_CTX


@CONFIG_CTX(includes=['dawn'])
def dawn_android(c):
  c.target_os.add('android')


@CONFIG_CTX(includes=['dawn'])
def dawn_node(c):
  for soln in c.solutions:
    if soln.name == 'dawn':
      soln.custom_vars['dawn_node'] = True
      break


@CONFIG_CTX(includes=['dawn'])
def dawn_wasm(c):
  for soln in c.solutions:
    if soln.name == 'dawn':
      soln.custom_vars['dawn_wasm'] = True
      break


@CONFIG_CTX(includes=['dawn'])
def checkout_litert_lm(c):
  for soln in c.solutions:
    if soln.name == 'dawn':
      soln.custom_vars['checkout_litert_lm'] = True
      break


@CONFIG_CTX(includes=['dawn'])
def checkout_v8(c):
  for soln in c.solutions:
    if soln.name == 'dawn':
      soln.custom_vars['checkout_v8'] = True
      break
