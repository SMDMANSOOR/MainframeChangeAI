# GENAPP Synthetic Incident Dataset — ServiceNow Load Kit

This folder contains a synthetic set of 100 CICS-GENAPP incidents (75 recurring
across 5 root-cause patterns + 25 standalone one-offs), built to feed an
Incident Discovery → Incident Prioritization agent pipeline, and the tooling
used to load them into a ServiceNow PDI for testing.

All 100 incidents have already been created in the target instance as of
2026-09-11 (see `incidents_created.json` for the mapping from synthetic
numbers to real INC numbers).

---

## Key ServiceNow links

| Link | What it's for |
|---|---|
| `https://developer.servicenow.com/dev.do#!/manage-instance` | Developer portal control panel for the PDI — shows instance status (Online/Offline), the admin username/password, current release version, and lets you reset the instance or its password. Go here first if login stops working or the instance needs waking up. |
| `https://dev324854.service-now.com/now/nav/ui/classic/params/target/incident_list.do%3Fsysparm_query%3Dsys_created_onONToday%40javascript%3Ags.beginningOfToday()%40javascript%3Ags.endOfToday()` | Direct link to the Incident list, pre-filtered to **records created today**. Used during setup to isolate and clean up partial/failed test batches without touching the pre-existing seed incidents (which are dated back in May). Safe to bookmark for any future re-runs. |

---

## Files in this folder

| File | Type | Purpose |
|---|---|---|
| `agent2_payload.json` | Data | The full 100-incident dataset as structured JSON — the canonical source of truth. Every script and CSV in this folder is generated from this file. Includes the technical signal (program, abend/SQLCODE, transaction) and ground-truth pattern labels (`u_pattern_id`, `u_occurrence_number`, `u_related_incidents`) used to validate — not train — the prioritization agent. |
| `GENAPP_synthetic_incidents.xlsx` | Data (human-readable) | The same 100 incidents as a formatted workbook: an **Incidents** tab (color-coded by pattern), a **Pattern Key** tab (the answer key for validating clustering — don't feed this to Agent 2), and a **Data Dictionary** tab explaining every column. |
| `create_servicenow_incidents.py` | Loader script | The script actually used to load all 100 incidents via ServiceNow's Table API (and attach evidence screenshots via the Attachment API). Also queries the instance's real `close_code` choice list before creating anything, since this instance rejects an unrecognized choice value silently. This is the file `run_import.sh` calls. |
| `run_import.sh` | Wrapper script | Convenience wrapper: installs the `requests` dependency, prompts for instance URL/username/password (or reads a hardcoded password if you've filled in `SN_PASSWORD_HARDCODED` at the top — **leave that blank when not actively running it**), runs a single-record diagnostic test first, and only proceeds to the full 100-record load after you confirm the test succeeded. |
| `test_single_incident.py` | Diagnostic script | Creates exactly one throwaway test incident with verbose diagnostic output (HTTP status, response headers, full response body, and a plain-English diagnosis of common failure causes). Called automatically by `run_import.sh`'s Step 1 — used standalone if you ever need to re-diagnose an auth or Data Policy issue without touching the real 100 records. |
| `incidents_created.json` | Output / local record | Written automatically after a successful `create_servicenow_incidents.py` run. Maps each synthetic `number` to the **real** ServiceNow-assigned INC number and `sys_id`. Useful as a receipt of what was created, and as a lookup if you need to go clean up or reference specific records later. Contains internal `sys_id`s from your instance — keep this **out of git** (already excluded via `.gitignore`). |
| `recurring_incidents_dump.csv` | Alternate load path | The 75 recurring incidents only, using **plain out-of-box ServiceNow fields** (no custom columns) — formatted for the Import Set wizard (System Import Sets → Load Data) as a no-scripting alternative to the Python loader. Not needed if you've already loaded everything via `create_servicenow_incidents.py`. |
| `standalone_incidents_dump.csv` | Alternate load path | Same idea, the 25 one-off incidents only. |
| `all_incidents_dump.csv` | Alternate load path | Same idea, all 100 combined into a single file. |

---

---

## What's actually inside each file

### `agent2_payload.json`
Top-level structure:
```json
{
  "source": "Agent 1 - Incident Discovery Agent",
  "target": "Agent 2 - Incident Prioritization Agent",
  "generated_at": "...",
  "application": "CICS-GENAPP",
  "record_count": 100,
  "note": "...",
  "incidents": [ {...}, {...}, ... ]   // 100 records
}
```
Each of the 100 records in `incidents[]` has these fields:

| Field | Contents |
|---|---|
| `number` | Synthetic placeholder INC number (e.g. `INC0010042`) — **not** the real ServiceNow number; see `incidents_created.json` for that mapping |
| `opened_at`, `resolved_at` | Timestamps spread across the last ~95 days |
| `state` | `New` / `In Progress` / `Resolved` |
| `short_description`, `description` | Written with deliberately inconsistent analyst phrasing; `description` also contains a "Business impact" note and, for recurring records, a "Duplicate/related incidents" list |
| `cmdb_ci` | Always `CICS-GENAPP` |
| `category`, `subcategory` | e.g. Software / VSAM, Software / Database, Performance / Batch |
| `u_transaction_id`, `u_program_name`, `u_job_name` | The GENAPP transaction (SSC1/SSP1-4), COBOL program, and batch job involved |
| `u_abend_code`, `u_sqlcode`, `u_return_code` | The technical failure signal (e.g. `LGV0`, `-911`, `98`) |
| `priority`, `urgency`, `impact` | 1 (highest) – 5 (lowest); escalate over time for the PTN-005 pattern |
| `assignment_group` | e.g. CICS Systems Programming, Db2 DBA, z/OS Batch Ops, Mainframe Application Support |
| `close_notes` | Resolution text — repeats "recycled/bounced/duplicate" language across a pattern's early incidents, then a real root-cause writeup on the closing one |
| `u_pattern_id` | `PTN-001` through `PTN-005`, or `ONE-OFF` — **ground truth only**, not meant to be fed into the prioritization model |
| `u_is_permanent_fix` | `"true"` on the incident that actually fixed the pattern, `"false"` on earlier reopens |
| `u_attachment` | Filename of the evidence screenshot for that record, where applicable |
| `u_occurrence_number` | 1, 2, 3... position of this incident within its recurring thread |
| `u_related_incidents` | Comma-separated list of the prior synthetic INC numbers in the same thread |

### `GENAPP_synthetic_incidents.xlsx`
Three sheets:
- **Incidents** — all 100 records as a table, color-coded by `u_pattern_id`, with autofilter enabled
- **Pattern Key (validation)** — one row per pattern (PTN-001...PTN-005, ONE-OFF) summarizing root cause, incident count, program/transaction, and resolution type — the answer key for checking Agent 2's clustering
- **Data Dictionary** — explains every column and how it maps to ServiceNow fields

### `recurring_incidents_dump.csv` / `standalone_incidents_dump.csv` / `all_incidents_dump.csv`
Same 13 columns in all three (75 / 25 / 100 rows respectively) — **plain OOB ServiceNow fields only**, no custom columns:
`number, short_description, description, category, subcategory, priority, urgency, impact, state, assignment_group, opened_at, resolved_at, close_notes`
The technical signal that lives in `u_*` fields elsewhere is folded into the `description` text here instead, under a "Technical signal" heading.

### `create_servicenow_incidents.py`
Reads `agent2_payload.json`, then for each record:
1. Queries `sys_choice` once at the start to discover this instance's real `close_code` values (rather than guessing labels)
2. Builds a Table API POST body, mapping priority/urgency/impact/state and folding the technical signal into `description`
3. Sets `close_code` on any Resolved/Closed record, choosing "permanent fix" vs. "workaround" wording based on `u_is_permanent_fix`
4. POSTs to `/api/now/table/incident`; if the response ID references a screenshot, uploads it via `/api/now/attachment/file`
5. On a Resolution-code-related failure even after the choice-discovery fix, retries once as "In Progress" rather than dropping the record
6. Writes `incidents_created.json` at the end mapping synthetic → real numbers/sys_ids

### `run_import.sh`
Bash wrapper: installs `requests`, collects instance URL/username/password (prompted, or from `SN_PASSWORD_HARDCODED` if filled in), runs `test_single_incident.py` as a pre-flight check, asks for `y/N` confirmation, then runs `create_servicenow_incidents.py` for the full batch.

### `test_single_incident.py`
Standalone diagnostic: creates one throwaway incident and prints the full HTTP status, response headers, and response body, plus a plain-English diagnosis for 401 (auth/Basic-Auth-restriction issues) vs. 403 (permissions/Data Policy issues) vs. other errors.

### `incidents_created.json`
A flat JSON array, one entry per successfully created incident:
```json
[
  {"synthetic_number": "INC0010042", "sys_id": "...", "real_number": "INC0010104"},
  ...
]
```

## Files that can be safely deleted

Nothing here is broken or wrong, but a few things are now redundant given you
loaded everything through the Python script rather than the Import Set
wizard:

- **`recurring_incidents_dump.csv`, `standalone_incidents_dump.csv`,
  `all_incidents_dump.csv`** — these were the no-code alternative (Import Set
  wizard) to `create_servicenow_incidents.py`. Since the Python route is what
  actually got used, these three CSVs aren't doing anything for you anymore.
  Safe to delete, or keep them around in case you ever want to demo the
  Import Set wizard path instead.
- **`incidents_created.json`** — keep it locally as a record of what was
  created (useful if you ever need to look up a `sys_id`), but don't commit
  it to git or share it externally; it references real records in your
  instance.

Nothing else in the folder is unwanted — `agent2_payload.json` and
`GENAPP_synthetic_incidents.xlsx` are the source data, and the three `.py`
files plus `run_import.sh` are the actual working toolchain.

## What's missing (not wrong, just not present)

- **`attachments/` folder** — was never populated locally, which is why every
  load run logged `attachment file not found`. All 100 incidents were still
  created successfully; they just don't have the two real screenshots
  (`SSC1_cust_inquiry_no_data_returned.png`,
  `BOB_issue1_analysis_rca_evidence.png`) attached. Add this folder with
  those two files if you want the RCA evidence actually attached — ask for a
  small standalone script to attach them retroactively using
  `incidents_created.json`, without re-creating any incidents.

## Before pushing to GitHub

Run this from the parent folder first, every time, before committing:
```bash
grep -rn "SN_PASSWORD_HARDCODED" .
```
It should only ever show `SN_PASSWORD_HARDCODED=""`. If it shows anything
else, blank it out before `git add`.
