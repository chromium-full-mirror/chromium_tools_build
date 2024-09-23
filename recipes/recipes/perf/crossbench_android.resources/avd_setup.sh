#!/bin/bash
# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
#
# Script to ensure the avd is set up for testing.
#
# Usage: ./avd_setup.sh <path-to-adb-executable>

set -x
set -e

# Monitor but it appears the device is always ready within a couple seconds.
readonly WAIT_ITERS=60

# Validate a single config is returning the desired value.
function validate_config() {
    local adb=${1}
    local config=${2}
    local exp_return_value=${3}
    local ready=1
    # Wait for avd to reach home screen
    echo "Checking ${config} is returning ${exp_return_value}."
    for((i=0; i<${WAIT_ITERS}; i++)); do
        out=$(${adb} shell "getprop ${config}" | tr -d '[:space:]')
        if [[ "${out}" = "${exp_return_value}" ]]; then
            echo "${config} is ready."
            ready=0
            break
        else
            echo "Config ${config} is not ready."
            echo "output: ${out}"
            sleep 1
        fi
    done
    if [[ ${ready} -eq 1 ]]; then
        echo "${config} was not ready in time."
        return 1
    fi
    return 0
}

# Validate an array of configs is returning their desired values.
function validate_all_configs() {
    local adb=$1; shift
    local config_array=("$@")
    for config in "${config_array[@]}"; do
        validate_config "${adb}" "${config}" "1"
        if [[ $? -ne 0 ]]; then
            echo "Config ${config} not ready."
            exit 1
        fi
    done
}

function print_device_list() {
    local adb=${1}
    local devices_output=$(${adb} devices)
    echo ""
    echo "${devices_output}"
    echo ""
}

# path to the adb executable
adb=${1}
which ${adb}
if [[ $? -eq 1 ]]; then
    echo "Unable to locate adb on path."
fi
# properties to validate
declare -a configs_to_validate=("sys.boot_completed" "dev.bootcomplete")
print_device_list "${adb}"

echo "Validating that emulator is booted."
validate_all_configs "${adb}" "${configs_to_validate[@]}"

# Ensure developer mode is enabled
${adb} shell settings put global development_settings_enabled 1

echo "Validating that emulator is booted."
validate_all_configs "${adb}" "${configs_to_validate[@]}"

print_device_list "${adb}"

# clear exit signal for the LUCI ci.
echo "Emulator ready."
exit 0
