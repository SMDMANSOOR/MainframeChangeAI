#!/bin/bash
# One-shot setup + run for the Incident Prioritization Agent (Phase 1).
#
# Run this FROM INSIDE the incident_prioritization_agent folder, after
# fetch_incidents.py and prioritize.py are sitting next to it - this script
# checks for them first and tells you exactly what's missing rather than
# failing partway through, since that's what kept happening with the
# ServiceNow loader scripts earlier.
#
# WARNING: this is a live ServiceNow instance. Don't leave a real password
# in SN_PASSWORD_HARDCODED below any longer than one run.

SN_PASSWORD_HARDCODED="cIE!7^boxML2"

set -e

echo "=== Pre-flight check ==="
MISSING=0
for f in fetch_incidents.py prioritize.py requirements.txt; do
  if [ ! -f "$f" ]; then
    echo "MISSING: $f is not in this folder."
    MISSING=1
  fi
done
if [ $MISSING -eq 1 ]; then
  echo ""
  echo "Download the missing file(s) from the chat and place them in this"
  echo "same folder before re-running this script."
  exit 1
fi
echo "All required files present."
echo ""

echo "=== Installing dependencies ==="
pip3 install -r requirements.txt --quiet
echo ""

echo "=== Credentials ==="
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
    echo "No password entered - aborting."
    exit 1
  fi
  export SN_PASSWORD
fi

echo ""
echo "=== Step 1: fetching incidents from $SN_INSTANCE ==="
python3 fetch_incidents.py

echo ""
echo "=== Step 2: clustering and ranking ==="
python3 prioritize.py --input incidents_raw.json --top 5

echo ""
echo "=== Done ==="
echo "results.json      - full cluster data"
echo "phase1_summary.html - visual report"
echo ""

# Auto-open the report on macOS; harmless no-op elsewhere.
if command -v open >/dev/null 2>&1; then
  open phase1_summary.html
fi
