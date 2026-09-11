# Incident Prioritization Agent (Pipeline Phase 1)

Connects to the ServiceNow instance holding the GENAPP incidents, clusters
them by recurring root cause, scores each cluster, and surfaces the top N
(default 5) — the "Incident Discovery Agent" phase from the 9-phase pipeline
diagram (Incidents Ingested / Clusters Formed / Top Program / Priority
Score / Risk Score).

This is a **separate subfolder** from `../servicenow/` on purpose: that
folder is the data-generation and initial-load side of the project, this
one is the consuming/analysis side. They share the same synthetic dataset
(`agent2_payload.json`, copied in here) so the clustering logic can be
validated offline before pointing it at live data.

---

## How it works

### 1. `fetch_incidents.py` — pulls data from ServiceNow
Connects via the Table API (same credential pattern as `../servicenow/`'s
loader scripts — env vars, never hardcoded except temporarily and locally),
paginates through all incidents, and keeps only the ones tagged as GENAPP
by filtering for the `"Technical signal (Agent 1 extract)"` marker that
`create_servicenow_incidents.py` writes into every incident's description.
This is how the pipeline knows to ignore the PDI's unrelated pre-existing
demo incidents (SAP, HR, etc.) without needing a custom field. Writes
`incidents_raw.json`.

### 2. `prioritize.py` — clustering + scoring
**Clustering approach:** rather than a black-box embedding model, this
extracts the structured technical signature every GENAPP incident already
carries — `Program`, `Transaction`, `Abend code`, `SQLCODE`, `Return code` —
either as parsed text (from the description, for real ServiceNow records)
or as exact JSON fields (for the synthetic payload). Incidents sharing an
identical signature are the same recurring failure. Anything with no
structured signal at all falls back to a normalized short-description
match. This is simpler and more explainable than text-embedding clustering,
and — because it uses the same signal Agent 1 already wrote into every
incident — it's very accurate on this dataset (see validation below).

**Scoring** — each cluster gets a composite `priority_score` (0–1):
```
priority_score = 0.35 × Frequency + 0.30 × Severity + 0.20 × Escalation + 0.15 × Recency
```
`risk_score_pct` is the same number shown as a percentage. Weights are
constants at the top of `prioritize.py` (`WEIGHTS` dict) — tune them there
if your organization wants to weight, say, severity higher than frequency.
The ranking itself is just: compute this score for every cluster, sort
descending, take the top N.

#### Factor 1 — Frequency (weight 0.35, the largest)
```
frequency_score = (incidents in this cluster) ÷ (incidents in the largest cluster)
```
How dominant this recurring pattern is *relative to the worst offender* in
the current dataset — not an absolute count. The largest cluster always
scores a perfect 1.00 (it's being compared to itself); every other cluster
is scaled proportionally down from there. This is the heaviest-weighted
factor because the whole point of this agent is finding incidents that
keep recurring — 30 occurrences of the same failure is a much stronger
signal of a systemic, unfixed problem than a handful of occurrences, even
if those few happen to be marked more severe individually.

Worked example from an actual live run (largest cluster = 30 incidents):
| Cluster | Occurrences | Frequency score |
|---|---|---|
| LGICDB01/LGICUS01 | 30 | 30÷30 = **1.00** |
| LGACVS01 | 15 | 15÷30 = **0.50** |
| LGUPDB01 | 12 | 12÷30 = **0.40** |
| LGUPOL01 | 10 | 10÷30 = **0.33** |
| LGTESTC1 | 8 | 8÷30 = **0.27** |

Note this is purely count-based — it doesn't care *when* those occurrences
happened (that's Recency) or how severe each one was (that's Severity).

#### Factor 2 — Severity (weight 0.30)
```
severity_score = average over the cluster's incidents of:
                  ((6 - average(priority, urgency, impact)) ÷ 5)
```
ServiceNow's priority/urgency/impact scale runs 1 (highest) to 5 (lowest),
so this formula inverts it: a P1/U1/I1 incident scores close to **1.00**, a
P5/U5/I5 incident scores close to **0.20**. Each incident's own
priority/urgency/impact are averaged together first, then that result is
averaged again across every incident in the cluster — so a cluster where
severity crept up over time (like a pattern that escalates from P3 to P1 as
it keeps recurring) still only gets a moderate overall severity score,
since it's averaging across the *whole history*, not just the worst single
occurrence. This is intentional: one severe outlier shouldn't fully
overshadow a cluster's real long-run severity profile, but a cluster that
is consistently severe throughout will score correspondingly high.

#### Factor 3 — Escalation (weight 0.20)
```
escalation_score = min(1.0, (total keyword hits across the cluster) ÷ (2 × cluster size))
```
Counts how often language signaling "this is a known, worsening, or
unresolved problem" shows up across the cluster's descriptions and close
notes: `recurring`, `duplicate`, `escalat...`, `blocker`, `blocking`,
`problem candidate`, `cab`, `workaround only`, `no fix`, `not yet`, `still
unresolved`, `reopen`, `same as`, `again`, `repeat`, `flagged by`, `uat
blocker`. The `÷ (2 × cluster size)` normalizes for cluster size (bigger
clusters naturally have more total text to search), and the result is
capped at 1.00. This is what lets a pattern's own resolution history — ops
literally writing "same as last week, bounced the region again" over and
over — count as evidence toward prioritizing it, separately from raw
frequency.

#### Factor 4 — Recency (weight 0.15, the smallest)
```
recency_score = e^(-age_in_days ÷ 30)
```
Exponential decay based on how many days old the cluster's *most recent*
occurrence is. An incident that opened today scores **1.00**; one whose
most recent occurrence was 30 days ago scores roughly **0.37**; 90 days ago
scores roughly **0.05**. This is the lightest-weighted factor deliberately
— recency matters (a cluster that's stopped recurring is less urgent than
one still actively happening), but it shouldn't be able to single-handedly
outrank a high-frequency, high-severity pattern just because it happened
to log one incident yesterday.

#### Putting it together
No single factor decides the ranking alone — a cluster has to combine
**high recurrence, real severity, and visible escalation signals** to rank
at the top; a cluster that's merely recent, or merely severe-but-rare,
won't outrank one that's been recurring for months with worsening priority
and explicit "still unresolved" language in its close notes. That
combination is what the `phase1_summary.html` report visualizes with the
four colored bars per row, plus a plain-English "Driven by..." sentence
summarizing which factors pushed that cluster to its rank.

Outputs:
- `results.json` — every cluster, full metrics (including each factor's raw
  score before weighting), member incident numbers
- `phase1_summary.html` — a dashboard-style report matching the pipeline
  diagram's visual language (dark theme, metric cards, ranked table with
  per-factor bars)

### 3. `test_with_synthetic_data.py` — offline validation
Runs the identical clustering/scoring code from `prioritize.py` against
`agent2_payload.json`, which carries ground-truth `u_pattern_id` labels, and
checks that the #1-ranked cluster is actually PTN-005 (the flagship SSC1
"No data was returned" pattern) with high cluster purity. **Run this any
time you change `WEIGHTS` or the signature-extraction logic**, before
trusting a change against live data. Current result: top cluster =
`LGICDB01 / LGICUS01`, 30 incidents, priority_score 0.87, **100% purity**
(every member is genuinely PTN-005, nothing else mixed in).

### 4. `run_pipeline.sh` — end-to-end wrapper
`fetch_incidents.py` → `prioritize.py` in one command, with the same
hidden-password-prompt safety pattern as `../servicenow/run_import.sh`.

---

## Running it

**Offline validation (no ServiceNow connection needed):**
```bash
pip3 install -r requirements.txt
python3 test_with_synthetic_data.py
```

**Against your live instance:**
```bash
export SN_INSTANCE="https://dev324854.service-now.com"
export SN_USER="admin"
export SN_PASSWORD="your-current-password"
./run_pipeline.sh
```
Then open `phase1_summary.html` in a browser.

---

## Files

| File | Purpose |
|---|---|
| `fetch_incidents.py` | Pulls GENAPP incidents from ServiceNow → `incidents_raw.json` |
| `prioritize.py` | Clusters + scores → `results.json` + `phase1_summary.html`. Also the importable module `test_with_synthetic_data.py` uses. |
| `test_with_synthetic_data.py` | Offline sanity check against known ground truth — run this first |
| `run_pipeline.sh` | Wrapper: fetch, then prioritize, in one command |
| `requirements.txt` | Just `requests` — no ML libraries needed given the signature-based clustering approach |
| `agent2_payload.json` | Local copy of the synthetic dataset, used only by the offline validation |
| `incidents_raw.json`, `results.json`, `phase1_summary.html` | Generated output — gitignored, since these reflect live instance data |

## Before committing to git
Same rule as `../servicenow/`: check `run_pipeline.sh` for a live password
before `git add`:
```bash
grep -n "SN_PASSWORD_HARDCODED" run_pipeline.sh
```
Should show only `SN_PASSWORD_HARDCODED=""`.