# bob4z_top5incidents_depednency_agent

Closes the loop between the ServiceNow incident-prioritization work and
the actual GENAPP codebase: reads the top-ranked recurring incident
clusters, finds the program(s) behind each one, asks Bob IDE how many
dependencies each program has in the real codebase, and recommends the
incident with the **smallest dependency footprint** as the lowest-risk
one to fix first — which isn't necessarily the #1 highest
business-priority incident.

---

## Why per-program, not summed per incident

An earlier version summed all programs in a multi-program cluster (e.g.
`LGICDB01 / LGICUS01` → combined total). Real usage showed this gives a
misleading answer: `LGICDB01` alone had only 1 dependency, but because it
shares a cluster with `LGICUS01` (2 dependencies), the combined total (3)
made a completely different single-program incident look "easier" even
though `LGICDB01` itself was genuinely the lowest-risk individual program
across the whole top 5. Fixed by ranking every program independently — no
combining — so the chart directly answers "which single program is
safest to touch," and leaves it to you to judge whether an incident's
other implicated programs also need changes.

## Why this matters

The ServiceNow ranking (`incident_prioritization_agent/prioritize.py`)
scores incidents by recurrence, severity, escalation, and recency — it
has no idea how risky or far-reaching a code change would actually be. A
program with 60 dependencies is a much bigger, riskier fix than one with
5, even if the first one's incident cluster is more urgent from a pure
ops standpoint. This script surfaces that tradeoff explicitly rather than
leaving it implicit.

## Prerequisites

- `incident_prioritization_agent` has already run and produced a real
  `results.json` (from live ServiceNow data or the synthetic dataset)
- `bob_agent/setup_workspace.sh` has already run against `cics-genapp` —
  specifically, the workspace needs to have been **scanned** (Step 4),
  since Bob's dependency-analysis tooling relies on that scan's index
- Bob IDE is open with the `cics-genapp` workspace. **The script now
  pauses at the start and explicitly asks you to switch to Z Architect
  mode** before any queries begin — dependency analysis relies on the
  same IDE-only tooling as the Data Dictionary step, so this is enforced
  as a checkpoint, not just assumed.

## Running it

```bash
cd $MFHOME/bob4z_top5incidents_depednency_agent
chmod +x analyze_incident_dependencies.sh
./analyze_incident_dependencies.sh <path-to-cics-genapp> [path-to-results.json] [top N]
```
Actually run it **from inside** the `cics-genapp` workspace folder so the
Bob-activation logic works the same way as `bob_agent`'s scripts:
```bash
cd $MFHOME/bob_agent/cics-genapp
$MFHOME/bob4z_top5incidents_depednency_agent/analyze_incident_dependencies.sh
```

Optional arguments (both have defaults):
```bash
./analyze_incident_dependencies.sh [path-to-results.json] [top N]
```
Defaults: `$MFHOME/incident_prioritization_agent/results.json`, top 5.

## What happens

1. **Reads and prints the top N incident clusters** from `results.json`,
   including the **actual member incident numbers** for each — not just
   the summary stats:
```
#1  Program(s): LGICDB01 / LGICUS01
     Priority score: 0.87   Occurrences: 30
     Incident numbers: INC0010087,INC0010088,INC0010089,...
```
2. **Splits compound entries** — a cluster like `LGICDB01 / LGICUS01`
   is two separate programs, queried individually
3. **Explains the dependency-check methodology explicitly** before
   querying anything, so it's not left implicit:
```
=== How dependencies are checked ===
For each unique program listed above, this asks Bob directly in chat:
  "Show the dependencies for program <PROGRAM> within the local workspace
   situated at .bobz/local-settings.json"
This relies on Bob's dependency-analysis tooling (the same
get_program_dependencies capability used elsewhere in Bob IDE), which
in turn relies on the workspace having already been scanned (Step 4 of
setup_workspace.sh) - without that scan, Bob has no index to answer
this from.
```

**Why the path is spelled out explicitly:** in real testing, asking Bob
generically ("within the local workspace") led it to search a default
database path, find nothing, and offer to re-scan the whole workspace —
even though a local database already existed at a different location
(`.bobz/local-settings.json`). Pointing the prompt directly at that file
avoids the whole back-and-forth.
4. **Asks Bob once per unique program** (skips repeats across clusters),
   labeling each query with which incident rank/numbers it belongs to
5. **You type back the number** Bob reports — the script can't read
   Bob's chat output itself, so this has to come from you
6. **Prints a sorted chart, one row per program** (not aggregated per
   incident — a real run showed why that matters, see below), and an
   explicit recommendation:

```
=== Sorted chart: dependencies per program (lowest first) ===

#   Program      Deps   Chart                            From Incident Priority
1   LGICDB01     1      ##########                       #1        0.87
2   LGACVS01     1      ##########                       #2        0.65
3   LGICUS01     2      ####################             #1        0.87
4   LGUPDB01     2      ####################             #3        0.6
5   LGUPOL01     2      ####################             #4        0.56
6   LGTESTC1     3      ##############################   #5        0.51

RECOMMENDATION: LGICDB01 has the FEWEST dependencies
(1) among all programs across the top 5 incidents -
lowest individual blast radius to fix. It belongs to incident rank
#1 (priority score 0.87), incidents:
INC0010087,INC0010088,...

Note: this is NOT the same as which whole INCIDENT is easiest overall -
a multi-program incident may still need other programs touched too.
This chart ranks individual programs, not combined cluster totals.

```

## Same focus caveat as `bob_agent`'s scripts

This reuses the exact same `type_into_bob` mechanism as
`setup_workspace.sh` — Bob IDE gets activated by app name before each
keystroke, but keyboard focus *within* the app isn't guaranteed to land
back on the chat text field (e.g. if focus was left on a previous
response or button). The script prints "Click directly into Bob's chat
input box now" before every query with a short pause — do that each time,
don't assume the previous click carries over.

## Every auto-type is now verified with you, not assumed

I have no way to see your screen, so I can't actually confirm text landed
in Bob's chat input rather than some other window or an open file. After
every `type_into_bob` call, the script now asks directly:
```
Did that text actually appear in Bob's chat input box (not some other
window/file)? [y/N]:
```
- **`y`** → moves straight on
- **`N`** → prints instructions to undo (Cmd+Z, in case it typed into a
  file), click directly into Bob's chat input yourself, and re-paste the
  exact same prompt (repeated on screen so you don't have to scroll back)

This applies to every prompt this script sends — the dependency query for
each program — not just the first one.

## A real bug I hit and fixed while building this

The first version silently skipped programs and produced garbled
dependency numbers, caused by a classic bash pitfall: redirecting a
`while read` loop's input from a file (`done < file`) redirects the
**entire loop's** standard input from that file — including any nested
interactive `read -p` calls inside the loop body, which then silently
consumed leftover lines from the file instead of your actual typed
answers. Fixed by redirecting the file to file descriptor 3 instead of
stdin (`done 3< file`, `read ... <&3`), leaving stdin free for the
interactive prompts. Verified with a full simulated run (6 programs
across 5 clusters, correct per-cluster sums, correct recommendation)
before shipping.

## If something doesn't work

- **"Can't find results.json"** → pass the path explicitly:
  `./analyze_incident_dependencies.sh /full/path/to/results.json`
- **Script says it couldn't find Bob IDE in `/Applications`** → same fix
  as `bob_agent`: run `ls /Applications | grep -i bob`, find the real
  name, add it to `BOB_IDE_CANDIDATES` near the top of the script
- **A dependency count looks wrong** → double-check you typed back the
  number Bob actually reported for *that specific* program, not a
  leftover number from the previous query