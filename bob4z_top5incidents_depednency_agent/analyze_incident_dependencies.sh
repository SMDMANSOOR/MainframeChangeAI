#!/bin/bash
# Reads the top-ranked recurring incident clusters from the
# incident_prioritization_agent's results.json, finds the program(s)
# behind each one, asks Bob (via chat, using its dependency-analysis
# tooling - available now that the workspace has been scanned) how many
# dependencies each program has, and recommends the incident with the
# SMALLEST dependency footprint as the lowest-risk one to fix first -
# not necessarily the highest business-priority one.
#
# Run this from inside the cics-genapp workspace, with Bob IDE already
# open (per bob_agent/setup_workspace.sh) - it types prompts into Bob the
# same way that script does.
#
# Usage:
#   ./analyze_incident_dependencies.sh [path-to-results.json] [top N]
# Defaults: $MFHOME/incident_prioritization_agent/results.json, top 5

set -e

# Fall back to the known path if $MFHOME isn't set in this shell (e.g. a
# terminal window opened before ~/.zshrc ran it). Uses the real env var
# if it IS set, so this stays correct if you ever move the project.
MFHOME="${MFHOME:-/Users/syedmohammadmansoor/Downloads/MainframeChangeAI}"

RESULTS_JSON="${1:-$MFHOME/incident_prioritization_agent/results.json}"
TOP_N="${2:-5}"
FOUND_APP=""

if [ ! -f "$RESULTS_JSON" ]; then
  echo "Can't find results.json at: $RESULTS_JSON"
  echo "Usage: $0 [path-to-results.json] [top N, default 5]"
  exit 1
fi

# ---------------------------------------------------------------------------
# Same type_into_bob mechanism as setup_workspace.sh: activates Bob IDE by
# name, then types text into whatever now has focus. Always requires a
# manual click into Bob's chat input first (printed instruction below) -
# same focus caveat documented in setup_workspace.sh.
# ---------------------------------------------------------------------------
type_into_bob() {
  local text="$1"
  local press_enter="${2:-true}"
  echo "--- Prompt (typing this into Bob now) ---"
  echo "$text"
  echo "------------------------------------------"
  if [ -n "$FOUND_APP" ]; then
    echo "Bringing '$FOUND_APP' to the front..."
  else
    echo "WARNING: no app name known to activate - typing into whatever is"
    echo "currently frontmost. Click into Bob's chat input now if needed."
  fi
  if ! osascript <<APPLESCRIPT
on run
  $( [ -n "$FOUND_APP" ] && echo "tell application \"$FOUND_APP\" to activate" )
  delay 1.2
  set theText to "$(printf '%s' "$text" | sed 's/\\/\\\\/g; s/"/\\"/g')"
  set AppleScript's text item delimiters to linefeed
  set theLines to text items of theText
  set numLines to count of theLines
  tell application "System Events"
    repeat with i from 1 to numLines
      keystroke (item i of theLines)
      if i < numLines then
        key code 36 using {shift down}
      end if
    end repeat
    $( [ "$press_enter" = "true" ] && echo "key code 36" )
  end tell
end run
APPLESCRIPT
  then
    echo ""
    echo "Auto-type failed outright (likely missing Accessibility permission)."
    echo "Type/paste this manually into Bob's chat input box now:"
    echo "$text"
    read -p "Press Enter once you've entered it manually in Bob's chat: " _
    return
  fi

  # Verify with you, rather than assume it actually landed in Bob's chat
  # input - I have no way to see the screen myself to confirm this.
  echo ""
  read -p "Did that text actually appear in Bob's chat input box (not some other window/file)? [y/N]: " LANDED_OK
  if [ "$LANDED_OK" != "y" ] && [ "$LANDED_OK" != "Y" ]; then
    echo ""
    echo "It went somewhere else. If it typed into a file or the wrong"
    echo "field, undo/clear that first (Cmd+Z), then click directly into"
    echo "Bob's chat input box yourself and type/paste this:"
    echo "$text"
    read -p "Press Enter once it's correctly entered in Bob's chat: " _
  fi
}

# ---------------------------------------------------------------------------
# Find Bob IDE (same candidate list as setup_workspace.sh)
# ---------------------------------------------------------------------------
BOB_IDE_CANDIDATES=("IBM Bob - Insiders" "IBM Bob Insiders" "Bob IDE" "IBM Bob" "IBM Bob IDE" "Bob")
for name in "${BOB_IDE_CANDIDATES[@]}"; do
  if [ -d "/Applications/${name}.app" ]; then
    FOUND_APP="$name"
    break
  fi
done
if [ -n "$FOUND_APP" ]; then
  echo "Found Bob IDE: ${FOUND_APP}.app"
else
  echo "Could not find Bob IDE in /Applications - will type into whatever"
  echo "app is currently frontmost. Make sure Bob IDE is already open."
fi
echo ""

# ---------------------------------------------------------------------------
# Step 1: pull the top N incident clusters out of results.json, including
# the actual member incident numbers, not just the summary stats
# ---------------------------------------------------------------------------
echo "=== Reading top $TOP_N incident clusters from $RESULTS_JSON ==="
python3 -c "
import json
d = json.load(open('$RESULTS_JSON'))
clusters = d['clusters'][:$TOP_N]
for i, c in enumerate(clusters, 1):
    members = ','.join(c.get('member_numbers', []))
    print(f\"{i}|{c['top_program']}|{c['priority_score']}|{c['incident_count']}|{members}\")
" > /tmp/top_incidents_$$.txt

echo ""
while IFS='|' read -r rank progs score count members; do
  echo "#$rank  Program(s): $progs"
  echo "     Priority score: $score   Occurrences: $count"
  echo "     Incident numbers: $members"
  echo ""
done < /tmp/top_incidents_$$.txt

# ---------------------------------------------------------------------------
# Step 2: for each unique program across the top N incidents, ask Bob for
# its dependency count within the local workspace
# ---------------------------------------------------------------------------
echo "=== Switch mode to 'Z Architect' in Bob's dropdown before continuing ==="
echo "Dependency analysis (get_program_dependencies) relies on the same"
echo "IDE-only tooling as the Data Dictionary step in setup_workspace.sh,"
echo "which only works in Z Architect mode."
read -p "Press Enter once Z Architect mode is selected: " _
echo ""

echo "=== How dependencies are checked ==="
echo "For each unique program listed above, this asks Bob directly in chat:"
echo "  \"Show the dependencies for program <PROGRAM> within the local workspace"
echo "   situated at .bobz/local-settings.json\""
echo "This relies on Bob's dependency-analysis tooling (the same"
echo "get_program_dependencies capability used elsewhere in Bob IDE), which"
echo "in turn relies on the workspace having already been scanned (Step 4 of"
echo "setup_workspace.sh) - without that scan, Bob has no index to answer"
echo "this from. The prompt explicitly points at .bobz/local-settings.json"
echo "since Bob otherwise searched a default path and reported no local"
echo "database found, even though one existed at a different location."
echo "I can't read Bob's chat response myself, so after each"
echo "query you'll read what Bob reports and type the dependency count back"
echo "to this script."
echo ""
echo "Click directly into Bob's chat input box now."
sleep 2
echo ""

> /tmp/dep_counts_$$.txt
while IFS='|' read -r rank progs score count members <&3; do
  # a cluster's top_program can be "LGICDB01 / LGICUS01" - split on " / "
  IFS='/' read -ra PROG_ARR <<< "$progs"
  for p in "${PROG_ARR[@]}"; do
    p_trim=$(printf '%s' "$p" | xargs)
    [ -z "$p_trim" ] && continue
    # skip if already queried this program (it may repeat across incidents)
    if grep -q "^$p_trim|" /tmp/dep_counts_$$.txt 2>/dev/null; then
      continue
    fi
    echo "--- Program: $p_trim (from incident rank #$rank: $members) ---"
    type_into_bob "Show the dependencies for program $p_trim within the local workspace situated at .bobz/local-settings.json"
    read -p "How many dependencies did Bob report for $p_trim? (a number): " DEP_COUNT
    DEP_COUNT=$(printf '%s' "$DEP_COUNT" | tr -cd '0-9')
    [ -z "$DEP_COUNT" ] && DEP_COUNT=0
    echo "$p_trim|$DEP_COUNT" >> /tmp/dep_counts_$$.txt
    echo ""
    echo "Click directly into Bob's chat input box now before the next query."
    sleep 2
  done
done 3< /tmp/top_incidents_$$.txt

# ---------------------------------------------------------------------------
# Step 3: one row per PROGRAM (not summed per incident), sorted ascending
# by dependency count, with a simple text bar chart
# ---------------------------------------------------------------------------
echo ""
echo "=== Sorted chart: dependencies per program (lowest first) ==="
echo ""
python3 -c "
top_lines = open('/tmp/top_incidents_$$.txt').read().strip().splitlines()
dep_lines = open('/tmp/dep_counts_$$.txt').read().strip().splitlines()

dep_map = {}
for line in dep_lines:
    prog, count = line.split('|')
    dep_map[prog] = int(count)

# One row per individual program - which incident rank(s)/priority it
# belongs to, no combining of multi-program clusters.
rows = []
for line in top_lines:
    parts = line.split('|')
    rank, progs, score, count = parts[0], parts[1], parts[2], parts[3]
    members = parts[4] if len(parts) > 4 else ''
    prog_list = [p.strip() for p in progs.split('/') if p.strip()]
    for p in prog_list:
        rows.append({
            'program': p, 'dependencies': dep_map.get(p, 0),
            'incident_rank': int(rank), 'priority_score': float(score),
            'incident_count': int(count), 'members': members,
        })

rows.sort(key=lambda r: r['dependencies'])

max_deps = max((r['dependencies'] for r in rows), default=1)
max_deps = max(max_deps, 1)
bar_width = 30

print(f\"{'#':<3} {'Program':<12} {'Deps':<6} {'Chart':<{bar_width+2}} {'From Incident':<9} {'Priority'}\")
for i, r in enumerate(rows, 1):
    filled = int((r['dependencies'] / max_deps) * bar_width) if max_deps else 0
    bar = '#' * max(filled, 1 if r['dependencies'] > 0 else 0)
    print(f\"{i:<3} {r['program']:<12} {r['dependencies']:<6} {bar:<{bar_width+2}} #{r['incident_rank']:<8} {r['priority_score']}\")

print()
lowest = rows[0]
print(f\"RECOMMENDATION: {lowest['program']} has the FEWEST dependencies\")
print(f\"({lowest['dependencies']}) among all programs across the top $TOP_N incidents -\")
print(f\"lowest individual blast radius to fix. It belongs to incident rank\")
print(f\"#{lowest['incident_rank']} (priority score {lowest['priority_score']}), incidents:\")
print(f\"{lowest['members']}\")
print()
print(f\"Note: this is NOT the same as which whole INCIDENT is easiest overall -\")
print(f\"a multi-program incident may still need other programs touched too.\")
print(f\"This chart ranks individual programs, not combined cluster totals.\")
"

rm -f /tmp/top_incidents_$$.txt /tmp/dep_counts_$$.txt