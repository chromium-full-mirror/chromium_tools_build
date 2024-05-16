# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from abc import ABC, abstractmethod


class TestRunPhase(ABC):

  @abstractmethod
  def trigger(self, runner):
    pass  # pragma: no cover

  @abstractmethod
  def process_results(self, runner):
    pass  # pragma: no cover


class FirstRunPhase(TestRunPhase):

  def trigger(self, runner):
    runner.trigger()

  def process_results(self, runner):
    runner.process_results()


class ExonerationPhase(TestRunPhase):

  def __init__(self, test_names):
    self.test_names = test_names

  def trigger(self, runner):
    runner.trigger_exoneration(self.test_names)

  def process_results(self, runner):
    runner.process_exoneration_results(self.test_names)
