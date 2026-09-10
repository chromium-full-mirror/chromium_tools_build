#!/usr/bin/env python3

# Copyright 2013 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Aggregates Jacoco coverage files to produce output."""

from __future__ import annotations

import argparse
import sys
from xml.dom import minidom

# Conforms to JaCoCo coverage counter types:
# https://www.jacoco.org/jacoco/trunk/doc/counters.html
JAVA_COVERAGE_METRICS = [
  'instruction',
  'branch',
  'line',
  'complexity',
  'method',
  'class',
]


def _add_missing_nodes_to_main(main_dict, auxiliary_dict, root_node, attribute):
  # Adds any node in the auxiliary_dict not in main_dict, to the main_dict.
  for key in auxiliary_dict:
    node_attribute = auxiliary_dict[key].getAttribute(attribute)
    if node_attribute not in main_dict:
      added_node = root_node.appendChild(auxiliary_dict[key])
      main_dict[node_attribute] = added_node


def _create_attribute_to_object_dict(element_list, attrib):
  return {e.getAttribute(attrib): e for e in element_list}


def _create_counter_map(counter_list):
  """Creates a map of counter types to the counter nodes.

  Args:
    counter_list: A list of counters
  Returns:
    A map of counter types to counter nodes.
  """
  counter_map = {
    counter.getAttribute('type').lower(): counter for counter in counter_list
  }

  return counter_map


def _create_total_dicts():
  total_dicts = {}
  for metric in JAVA_COVERAGE_METRICS:
    new_dict = {'covered': 0, 'missed': 0}
    total_dicts[metric] = new_dict

  return total_dicts


def _get_covered_and_missed_from_counter(counter):
  return (counter.getAttribute('covered'), counter.getAttribute('missed'))


def _get_dict_for_each_element(
  main_node, auxiliary_node, children_tag, attribute_tag
):
  # Returns dictionaries mapping the attribute tag to the node's children.
  main_children = main_node.getElementsByTagName(children_tag)
  auxiliary_children = auxiliary_node.getElementsByTagName(children_tag)
  main_mapping_dict = _create_attribute_to_object_dict(
    main_children, attribute_tag
  )
  auxiliary_mapping_dict = _create_attribute_to_object_dict(
    auxiliary_children, attribute_tag
  )
  return (main_mapping_dict, auxiliary_mapping_dict)


def _get_counters_list(root_node):
  return [node for node in root_node.childNodes if node.tagName == 'counter']


def _get_counter_totals_for_tag_name(root_node, tag_name):
  # Gets a diciontary of the totals of the counters in the children's counters.
  total_dicts = _create_total_dicts()
  # Cannot just use getElementByTagName as that will go more than one layer
  # deep. Want to avoid double counting the counters.
  nodes = [node for node in root_node.childNodes if node.tagName == tag_name]
  for node in nodes:
    for counter in _get_counters_list(node):
      covered_lines, missed_lines = _get_covered_and_missed_from_counter(
        counter
      )
      counter_type = counter.getAttribute('type').lower()
      total_dicts[counter_type]['covered'] += int(covered_lines)
      total_dicts[counter_type]['missed'] += int(missed_lines)

  return total_dicts


def _set_higher_counter_in_main(main_counter, auxiliary_counter):
  # Ideally would use min/max on covered and missed, but want to make sure
  # to use the variables from the same counter.
  if int(main_counter.getAttribute('covered')) >= int(
    auxiliary_counter.getAttribute('covered')
  ):
    chosen_counter = main_counter
  else:
    chosen_counter = auxiliary_counter

  covered, missed = _get_covered_and_missed_from_counter(chosen_counter)
  main_counter.setAttribute('covered', covered)
  main_counter.setAttribute('missed', missed)


def _set_higher_method_coverage_in_main(
  main_method_dict, auxiliary_method_dict
):
  for method_key in main_method_dict:
    main_method = main_method_dict[method_key]
    if method_key not in auxiliary_method_dict:
      continue

    main_counter_map = _create_counter_map(
      main_method.getElementsByTagName('counter')
    )
    auxiliary_counter_map = _create_counter_map(
      auxiliary_method_dict[method_key].getElementsByTagName('counter')
    )
    for metric in main_counter_map:
      if metric in auxiliary_counter_map:
        _set_higher_counter_in_main(
          main_counter_map[metric], auxiliary_counter_map[metric]
        )


def _update_all_nodes_in_main_to_higher_coverage(
  main_package_dict, auxiliary_package_dict
):
  # Go to every (package, class, method) in main_package_dict,
  # compare it against corresponding entity in auxiliary_package_dict
  # and in place update the matching entity in main_package_dict with the
  # higher coverage counter.
  for key in main_package_dict:
    main_package = main_package_dict[key]
    if key not in auxiliary_package_dict:
      continue
    auxiliary_package = auxiliary_package_dict[key]
    main_class_dict, auxiliary_class_dict = _get_dict_for_each_element(
      main_package, auxiliary_package, 'class', 'name'
    )
    _add_missing_nodes_to_main(
      main_class_dict, auxiliary_class_dict, main_package, 'name'
    )
    for class_key in main_class_dict:
      main_class = main_class_dict[class_key]
      if class_key not in auxiliary_class_dict:
        continue
      auxiliary_class = auxiliary_class_dict[class_key]
      main_method_dict, auxiliary_method_dict = _get_dict_for_each_element(
        main_class, auxiliary_class, 'method', 'line'
      )
      _add_missing_nodes_to_main(
        main_method_dict, auxiliary_method_dict, main_class, 'line'
      )
      # Rewrite the values in method coverage based on which is higher.
      # Then update the counter at the class level.
      _set_higher_method_coverage_in_main(
        main_method_dict, auxiliary_method_dict
      )
      _update_children_counters(main_class, 'method')

    _update_package_source_files_in_main(main_package, auxiliary_package)
    _update_children_counters(main_package, 'sourcefile')


def _update_children_counters(root_node, tag_name):
  # Updates the children (not deeper, ie grandchildren) of the node. This is to
  # avoid double counting counters (as a class's total counter is the summation
  # of the method counters.)
  counters = _get_counters_list(root_node)
  total_dicts = _get_counter_totals_for_tag_name(root_node, tag_name)
  _update_counters_from_total(counters, total_dicts)


def _update_counters_from_total(counter_nodes, total_dicts):
  for counter in counter_nodes:
    counter_type = counter.getAttribute('type').lower()
    counter.setAttribute('covered', str(total_dicts[counter_type]['covered']))
    counter.setAttribute('missed', str(total_dicts[counter_type]['missed']))


def _update_line_code_coverage_nodes_in_main(
  main_dict, auxiliary_dict, main_source_node, auxiliary_source_node
):
  # Gets the nodes that are in the auxiliary_tree and not in the main_tree.
  # If the node exists in both trees, choose the one that higher
  # covered instructions (ci).
  instruction_list = ['ci', 'cb', 'mi', 'mb']
  total_dict = {inst: 0 for inst in instruction_list}

  # Add any nodes that are in auxiliary, that aren't in dict. This adds the
  # entry to main_dict.
  _add_missing_nodes_to_main(main_dict, auxiliary_dict, main_source_node, 'nr')

  # Check all the lines that are the same. Set the main_node to have the
  # fields that are higher.
  for key in main_dict:
    main_line = main_dict[key]
    if key not in auxiliary_dict:
      continue

    auxiliary_line = auxiliary_dict[key]
    auxiliary_line_ci = int(auxiliary_line.getAttribute('ci'))
    main_line_ci = int(main_line.getAttribute('ci'))
    # We'll take all the data from the auxiliary line if ci is better.
    # This is to ensure the cb, mi, mb data also matches up.
    if main_line_ci < auxiliary_line_ci:
      for inst in instruction_list:
        main_line.setAttribute(inst, auxiliary_line.getAttribute(inst))

  # Sum up all the coverage numbers.
  for key in main_dict:
    main_line = main_dict[key]
    for inst in instruction_list:
      total_dict[inst] += int(main_line.getAttribute(inst))

  _update_source_file_counters_in_main(
    main_source_node, auxiliary_source_node, total_dict
  )


def _update_package_source_files_in_main(main_package, auxiliary_package):
  # Adds any source files in the auxiliary_tree that are not in the main_tree.
  # One the source file that are the same, combine the source files
  # based on "nr"(line number)
  main_sources = main_package.getElementsByTagName('sourcefile')
  auxiliary_sources = auxiliary_package.getElementsByTagName('sourcefile')

  main_name_to_sources_dict = _create_attribute_to_object_dict(
    main_sources, 'name'
  )
  auxiliary_name_to_sources_dict = _create_attribute_to_object_dict(
    auxiliary_sources, 'name'
  )

  # Adds any source files that are in the auxiliary package that
  # are not in the main package.
  _add_missing_nodes_to_main(
    main_name_to_sources_dict,
    auxiliary_name_to_sources_dict,
    main_package,
    'name',
  )

  for key in main_name_to_sources_dict:
    main_source_node = main_name_to_sources_dict[key]
    auxiliary_source_node = auxiliary_name_to_sources_dict[key]
    main_line_dict = _create_attribute_to_object_dict(
      main_source_node.getElementsByTagName('line'), 'nr'
    )
    auxiliary_line_dict = _create_attribute_to_object_dict(
      auxiliary_source_node.getElementsByTagName('line'), 'nr'
    )
    # Takes all the "lines" in the source file, then compares them and chooses
    # the "line" that has higher coverage.
    _update_line_code_coverage_nodes_in_main(
      main_line_dict,
      auxiliary_line_dict,
      main_source_node,
      auxiliary_source_node,
    )


def _update_source_file_counters_in_main(
  main_source_node, auxiliary_source_node, total_dict
):
  # Update the counter nodes of the source file.
  main_counter_dict = _create_attribute_to_object_dict(
    main_source_node.getElementsByTagName('counter'), 'type'
  )
  auxiliary_counter_dict = _create_attribute_to_object_dict(
    auxiliary_source_node.getElementsByTagName('counter'), 'type'
  )
  for inst in main_counter_dict:
    main_counter = main_counter_dict[inst]
    if inst == 'INSTRUCTION':
      main_counter.setAttribute('covered', str(total_dict['ci']))
      main_counter.setAttribute('missed', str(total_dict['mi']))
    elif inst == 'BRANCH':
      main_counter.setAttribute('covered', str(total_dict['cb']))
      main_counter.setAttribute('missed', str(total_dict['mb']))
    else:
      covered_val = max(
        int(main_counter.getAttribute('covered')),
        int(auxiliary_counter_dict[inst].getAttribute('covered')),
      )
      missed_val = min(
        int(main_counter.getAttribute('missed')),
        int(auxiliary_counter_dict[inst].getAttribute('missed')),
      )
      main_counter.setAttribute('covered', str(covered_val))
      main_counter.setAttribute('missed', str(missed_val))


def combine_xml_files(combined_file_path, main_file_path, auxiliary_file_path):
  """Combines two xml jacoco report files into one.

  Expected input is two jacoco coverage report xml files.
  The report is composed of a tree of nodes, the root node is the "report" node
  which contains counters and packages.
  -The package nodes contain class nodes, sourcefile nodes, and counters.
  -The class nodes contain the class's name, method nodes, and counters
  -The sourcefile nodes contain the file name, line nodes, and counters
  -The method nodes contain the method's name and counters.
  -The line nodes contain 4 attributes,
    nr=line number corresponding to the physical line in the sourcefile.
    mi=missed instructions for the line
    ci=covered instructiosn for the line
    mb=missed branch for the line
    cb=covered branch for the line

  The counters contain an instruction type as found in JAVA_COVERAGE_METRICS
  and the total number of "covered" or "missed" items on that metric in that
  node. So a class node's counters contain the sum of counters in the methods.
  The package node's counters contain the sum total of the counters in the
  sourcefile nodes. The sourcefile nodes contain the sum total of the counters
  in the line node.

  The code walks down to each method in the main report and finds
  the matching method in the auxiliary report. It then chooses to use the
  coverage data from whichever node is higher. This is not perfect as main
  report can have 10 covered and 10 missed, and auxiliary can have 15 covered
  and 5 missed, so maybe it should be 20 covered and 0 missed if the coverage
  overlaps properly. If a method/class/package is in the auxiliary report and
  not in the main report, it adds it into the main_report.

  The code then walks down the sourcefiles and adds to the main report any
  lines that are in the auxiliary report and not already present. It then
  compares every line in the main report to the auxiliary report and chooses to
  use the line that has higher ci. It then calculates a new sum for the counters
  in sourcefile and packages and reports (not classes and methods).

  Args:
    combined_file_path: The write path of combined report.
    main_file_path: The location of the main coverage report.
    auxiliary_file_path: The location of the auxiliary coverage report.

  Returns:
    Results are written to combined_file_path
  """
  main_tree = minidom.parse(main_file_path)
  auxiliary_tree = minidom.parse(auxiliary_file_path)
  main_report_node = main_tree.getElementsByTagName('report')[0]
  main_packages = main_tree.getElementsByTagName('package')
  auxiliary_packages = auxiliary_tree.getElementsByTagName('package')
  main_name_to_package_dict = _create_attribute_to_object_dict(
    main_packages, 'name'
  )
  auxiliary_name_to_package_dict = _create_attribute_to_object_dict(
    auxiliary_packages, 'name'
  )
  _update_all_nodes_in_main_to_higher_coverage(
    main_name_to_package_dict, auxiliary_name_to_package_dict
  )
  _add_missing_nodes_to_main(
    main_name_to_package_dict,
    auxiliary_name_to_package_dict,
    main_report_node,
    'name',
  )

  # Only updates one layer of counters, ie doesn't do grandchildren.
  _update_children_counters(main_report_node, 'package')

  with open(combined_file_path, 'w') as xmlfile:
    main_tree.writexml(xmlfile)


def main():
  parser = argparse.ArgumentParser()
  parser.add_argument(
    '--main-xml', required=True, help='Path to the main xml coverage report.'
  )
  parser.add_argument(
    '--auxiliary-xml',
    required=True,
    help='Path to the auxiliary xml coverage report.',
  )
  parser.add_argument(
    '--combined-xml',
    required=True,
    help='Destination path to write the combined xml coverage report.',
  )
  args = parser.parse_args()
  combine_xml_files(args.combined_xml, args.device_xml, args.host_xml)


if __name__ == '__main__':
  sys.exit(main())
