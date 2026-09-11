#!/bin/bash
# Run this from the servicenow folder: ./run_import.sh
# It installs the dependency, prompts you for the instance/user/password
# (password input is hidden, and nothing is written to disk unless you
# fill in SN_PASSWORD_HARDCODED below), then runs the loader script.

# --- OPTIONAL: fill this in yourself to skip the password prompt ---
# WARNING: this stores your password in plaintext in this file. Only do
# this on a machine you're not sharing, and don't commit/zip/email this
# file anywhere with the password still in it. Leave blank ("") to keep
# being prompted securely instead.
SN_PASSWORD_HARDCODED="cIE!7^boxML2"
# ---------------------------------------------------------------------

set -e

echo "Installing dependency..."
pip3 install requests --quiet

read -p "ServiceNow instance URL [https://dev324854.service-now.com]: " SN_INSTANCE_INPUT
export SN_INSTANCE="${SN_INSTANCE_INPUT:-https://dev324854.service-now.com}"

read -p "ServiceNow username [admin]: " SN_USER_INPUT
export SN_USER="${SN_USER_INPUT:-admin}"

if [ -n "$SN_PASSWORD_HARDCODED" ]; then
  echo "Using password from SN_PASSWORD_HARDCODED (skipping prompt)."
  export SN_PASSWORD="$SN_PASSWORD_HARDCODED"
else
  read -s -p "ServiceNow password (hidden): " SN_PASSWORD
  echo ""
  if [ -z "$SN_PASSWORD" ]; then
    echo "No password entered - aborting. Re-run and type your current admin password."
    exit 1
  fi
  export SN_PASSWORD
fi

echo ""
echo "Running against: $SN_INSTANCE as $SN_USER"
echo ""

echo "Step 1: testing with a single diagnostic incident first..."
echo ""
set +e
python3 test_single_incident.py
TEST_EXIT=$?
set -e

if [ $TEST_EXIT -ne 0 ]; then
  echo ""
  echo "Single-record test failed to run - aborting before touching the other 99."
  exit 1
fi

echo ""
read -p "Did the test above say SUCCESS? Proceed with the remaining 99 records? [y/N]: " PROCEED
if [ "$PROCEED" != "y" ] && [ "$PROCEED" != "Y" ]; then
  echo "Stopped. Fix the issue above and re-run ./run_import.sh when ready."
  exit 0
fi

echo ""
echo "Step 2: running the full 100-record import..."
echo ""
python3 create_servicenow_incidents.py