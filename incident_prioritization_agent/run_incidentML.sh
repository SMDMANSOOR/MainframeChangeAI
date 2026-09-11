#!/bin/bash
# run_incidentML.sh - the ONE script for this folder.
#
# Does everything in order:
#   0. Verifies all required files are present and correctly sized
#   1. Installs dependencies (requests, scikit-learn)
#   2. Runs the offline validation test (no ServiceNow needed) as a smoke test
#   3. Fetches live incidents from your ServiceNow instance
#   4. Clusters + ranks them (ensemble: rule-based + ML text clustering)
#   5. Opens the HTML report automatically
#
# Written to work with macOS's default bash (3.2) - no associative arrays.
#
# Run from inside the incident_prioritization_agent folder:
#   cd $MFHOME/incident_prioritization_agent
#   chmod +x run_incidentML.sh
#   ./run_incidentML.sh
#
# WARNING: SN_PASSWORD_HARDCODED below is a convenience for repeated local
# runs. Never leave a real password in it any longer than one working
# session, and never commit this file to git with it filled in.

SN_PASSWORD_HARDCODED="cIE!7^boxML2"

set -e

# ---------------------------------------------------------------------------
# Step 0: verify required files
# ---------------------------------------------------------------------------
echo "=== Step 0: verifying required files ==="

FILES="prioritize.py requirements.txt README.md ml_clustering.py fetch_incidents.py test_with_synthetic_data.py agent2_payload.json"
SIZES="21991 22 19974 2977 3373 2177 154547"

set -- $FILES
FILE_LIST=("$@")
set -- $SIZES
SIZE_LIST=("$@")

ALL_OK=1
i=0
while [ $i -lt ${#FILE_LIST[@]} ]; do
  f="${FILE_LIST[$i]}"
  expected="${SIZE_LIST[$i]}"
  if [ ! -f "$f" ]; then
    echo "MISSING  $f"
    ALL_OK=0
  else
    actual=$(wc -c < "$f" | tr -d ' ')
    if [ "$actual" = "$expected" ]; then
      echo "OK       $f ($actual bytes)"
    else
      echo "MISMATCH $f - expected $expected bytes, found $actual bytes (stale download?)"
      ALL_OK=0
    fi
  fi
  i=$((i + 1))
done

echo ""
if [ "$ALL_OK" -eq 0 ]; then
  echo "One or more files are missing or don't match. Re-download the"
  echo "flagged file(s) from the chat, move them into this folder, and"
  echo "re-run this script before continuing."
  exit 1
fi
echo "All required files verified."
echo ""

# ---------------------------------------------------------------------------
# Step 1: dependencies
# ---------------------------------------------------------------------------
echo "=== Step 1: installing dependencies ==="
pip3 install -r requirements.txt --quiet
echo "Done."
echo ""

# ---------------------------------------------------------------------------
# Step 2: offline smoke test (no ServiceNow connection needed)
# ---------------------------------------------------------------------------
echo "=== Step 2: offline validation (smoke test, no live connection) ==="
python3 test_with_synthetic_data.py
echo ""
read -p "Offline test passed above? Continue to live ServiceNow fetch? [y/N]: " PROCEED
if [ "$PROCEED" != "y" ] && [ "$PROCEED" != "Y" ]; then
  echo "Stopped before touching ServiceNow. Re-run when ready."
  exit 0
fi
echo ""

# ---------------------------------------------------------------------------
# Step 3: credentials + live fetch
# ---------------------------------------------------------------------------
echo "=== Step 3: fetching live incidents from ServiceNow ==="
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
python3 fetch_incidents.py
echo ""

# ---------------------------------------------------------------------------
# Step 4: cluster + rank
# ---------------------------------------------------------------------------
echo "=== Step 4: clustering and ranking (ensemble: rules + ML) ==="
python3 prioritize.py --input incidents_raw.json --top 5
echo ""

# ---------------------------------------------------------------------------
# Step 5: open report
# ---------------------------------------------------------------------------
echo "=== Done ==="
echo "results.json         - full cluster data"
echo "phase1_summary.html  - visual report"
if command -v open >/dev/null 2>&1; then
  open phase1_summary.html
fi
