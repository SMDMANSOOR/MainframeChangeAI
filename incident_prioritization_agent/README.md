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
**Clustering approach — an ensemble of rules and ML, not either alone:**
by default (`--clustering ensemble`) this uses two clustering methods
together, each handling the case it's actually good at:

1. **Structured signature matching (rule-based).** Extracts `Program`,
   `Transaction`, `Abend code`, `SQLCODE`, `Return code` — either parsed from
   the description text (real ServiceNow records) or exact JSON fields (the
   synthetic payload) — and groups incidents that share an identical
   signature. Fast, fully explainable, and very accurate whenever that
   structured signal exists, because it's the same signal Agent 1 already
   wrote into every incident.

2. **TF-IDF + Agglomerative text clustering (genuine unsupervised ML,
   `ml_clustering.py`).** Used specifically for the incidents the rule-based
   method *can't* handle — the ones with no structured technical fields at
   all (e.g. a network or monitoring incident where program/transaction were
   left blank). Rather than lumping every such incident into one vague
   catch-all bucket, or requiring exact-text matches, this vectorizes each
   incident's text with TF-IDF and clusters by cosine distance using
   `AgglomerativeClustering(distance_threshold=..., n_clusters=None)` — the
   `n_clusters=None` + `distance_threshold` combination means it does **not**
   need to be told how many clusters exist up front (unlike KMeans), which
   matters because the real number of distinct recurring problems in an
   incident stream is unknown and changes over time.

   *Why this model and not a bigger one:* no labeled training data exists
   for "which incidents are really the same root cause" (a supervised model
   isn't appropriate here), and a large pretrained embedding model would add
   an external dependency/download for no real benefit on short, templated
   incident text like this. TF-IDF's explainability is also a genuine
   advantage — you can inspect exactly which shared vocabulary drove any
   given cluster.

   *Honest limitation:* `distance_threshold` (default 0.6, in
   `ml_clustering.py`) is a hyperparameter chosen empirically against this
   dataset's one-off incidents, not derived from a validation set — it may
   need retuning against a larger or differently-worded real incident corpus.

Other modes, selectable via `--clustering`:
- `signature` — rule-based only, no ML at all
- `tfidf` — ML text clustering on *every* incident, ignoring structured
  fields entirely (useful for comparing against the ensemble/signature
  results, or for datasets where the structured fields aren't reliable)
- `ensemble` (default) — the combination described above

Each cluster's `cluster_method` field in `results.json` (and the colored
badge on `phase1_summary.html`) records whether it came from the exact
signature match or from ML text clustering, so you can always see which
mechanism actually grouped any given cluster.

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
time you change `WEIGHTS`, the signature-extraction logic, or
`ml_clustering.py`'s `distance_threshold`**, before trusting a change
against live data. Current result (ensemble mode, the default): 30 clusters
formed, 3 of them via ML text clustering (the incidents with no structured
technical fields — each correctly kept as its own distinct one-off, not
merged together or lost in a catch-all bucket) and 27 via exact signature
match. Top cluster = `LGICDB01 / LGICUS01`, 30 incidents, priority_score
0.87, **100% purity** (every member is genuinely PTN-005, nothing else
mixed in) — identical ranking to the pure rule-based (`signature`) mode on
this dataset, which is expected: the ML component's job is to catch what
the rules *miss*, not to outrank them where they already work.

### 4. `run_incidentML.sh` — the one script that does everything
This is the single canonical entry point for this folder — it replaces
what used to be three separate scripts (`run_pipeline.sh`,
`setup_and_run.sh`, `verify_and_test.sh`), which are no longer needed and
have been removed to avoid confusion about which one to run. It does, in
order:

1. **Verifies all 7 required files** are present and exactly the right byte
   size (catches a missing or stale/partial download immediately, with a
   clear message naming the exact file — rather than letting it fail
   confusingly several steps later). Written in plain POSIX-ish bash
   (no associative arrays), since macOS ships bash 3.2 by default which
   doesn't support them.
2. **Installs dependencies** (`requests`, `scikit-learn`) via
   `pip3 install -r requirements.txt`.
3. **Runs the offline validation smoke test** (`test_with_synthetic_data.py`)
   — no ServiceNow connection needed for this step — and pauses with a
   `[y/N]` prompt so you can confirm it passed before the script touches
   your live instance.
4. **Prompts for `SN_INSTANCE`/`SN_USER`/`SN_PASSWORD`** (hidden input,
   nothing written to disk) and runs `fetch_incidents.py` to pull live
   incidents from ServiceNow into `incidents_raw.json`.
5. **Runs `prioritize.py`** (ensemble clustering, top 5) against that live
   data → `results.json` + `phase1_summary.html`.
6. **Auto-opens** `phase1_summary.html` in your browser when done.

Same optional `SN_PASSWORD_HARDCODED=""` slot near the top as the
`servicenow/` folder's scripts, for skipping the password prompt on
repeated local runs — same warning applies: fill it in locally only, never
commit it, blank it again when you're done for the session.

---

## Rule-based vs. ML: what each one actually contributes

It's a fair question to ask directly, because on this dataset the two
methods **agree on every top-5 ranking** — so it's worth being precise
about where the ML component earns its keep and where it doesn't, rather
than overselling it.

| | Rule-based (signature matching) | ML (TF-IDF + Agglomerative clustering) |
|---|---|---|
| **What it needs** | A structured `Program`/`Transaction`/`Abend code`/`SQLCODE`/`Return code` block in the incident | Nothing structured — works from free text alone |
| **How it decides "same failure"** | Exact match on that structured tuple | Cosine similarity of TF-IDF vectors over the incident's text |
| **Explainability** | Perfect — you can point at the exact fields that matched | Good — you can inspect the shared vocabulary driving a cluster, but it's a similarity threshold, not an exact rule |
| **Fails when...** | The structured fields are missing or weren't captured | Never structurally fails, but a badly-chosen `distance_threshold` could over- or under-merge |
| **On this dataset, handles** | 27 of 30 clusters (all incidents with structured signal) | 3 of 30 clusters (the incidents with none) |

### Why the top 5 look identical either way
Every one of your top 5 clusters has 8-30 occurrences, and every member of
those clusters carries the full structured signal (because
`create_servicenow_incidents.py` writes it into every description). Rule
matching handles all of them perfectly on its own — the ML component never
even gets involved with these incidents, because `build_groups()` only
routes an incident to ML clustering when its structured signature comes
back completely empty. A method that was never asked to touch the top 5
can't be expected to change them.

### Where the ML component's benefit actually is
Not in re-ranking the winners — in **not losing or misgrouping the
incidents the rules can't see**. Verified from your own live run's
`results.json`:
```
ml_assisted_clusters: 3
 - CICS transaction dump dataset full  -> INC0010137
 - GENARENEW batch delayed a full day  -> INC0010128
 - DBB pipeline failed once            -> INC0010140
```
These 3 incidents have no `Program`/`Abend code`/etc. at all (real-world
equivalent: a network blip, a scheduling conflict, a credential-expiry
failure — none of which map cleanly to a COBOL program). Without the ML
step, `build_groups()`'s only fallback would be an **exact match on
normalized short description** — meaning two incidents describing the same
underlying problem in even slightly different wording (a very realistic
scenario for free-text incident reports) would incorrectly end up as
separate clusters, silently undercounting a real recurring pattern's
frequency. TF-IDF + cosine similarity catches near-duplicate wording that
an exact string match would miss.

### Where this would actually start to matter more
This dataset is close to a best case for the rule-based method (nearly
every incident has clean structured fields, by design). On a real
production ServiceNow instance where incident descriptions are typed by
many different people with inconsistent detail, the *proportion* of
incidents lacking a clean structured signature would likely be much higher
than 3% — and that's exactly the regime where the ML component's
contribution to cluster completeness (not ranking) becomes visible in the
top-level metrics too, not just in `results.json`'s fine print.



**Everything, start to finish, in one command:**
```bash
cd $MFHOME/incident_prioritization_agent
chmod +x run_incidentML.sh
./run_incidentML.sh
```
Answer the prompts as they come: `[y/N]` to proceed past the offline
smoke test, then instance URL / username / password for the live fetch
(Enter, Enter, then type the password — hidden, no characters shown).

**Just the offline validation, no live connection** (e.g. after changing
`WEIGHTS` or `ml_clustering.py`'s `distance_threshold`):
```bash
python3 test_with_synthetic_data.py
```

**Re-scoring already-fetched data with a different clustering mode**
(skips the live fetch entirely, since `incidents_raw.json` already exists
from a prior run):
```bash
python3 prioritize.py --input incidents_raw.json --clustering signature   # rules only
python3 prioritize.py --input incidents_raw.json --clustering tfidf       # ML only
python3 prioritize.py --input incidents_raw.json --clustering ensemble    # default, both
```

---

## Files

| File | Purpose |
|---|---|
| `run_incidentML.sh` | **The script to run.** Verifies files → installs deps → offline smoke test → live fetch → cluster/rank → opens report |
| `fetch_incidents.py` | Pulls GENAPP incidents from ServiceNow → `incidents_raw.json`. Called by `run_incidentML.sh`; can also be run standalone. |
| `prioritize.py` | Clusters + scores → `results.json` + `phase1_summary.html`. Called by `run_incidentML.sh`; also the importable module `test_with_synthetic_data.py` uses directly. |
| `ml_clustering.py` | The ML component — TF-IDF + Agglomerative clustering, used by `prioritize.py`'s `tfidf` and `ensemble` modes |
| `test_with_synthetic_data.py` | Offline sanity check against known ground truth. Run standalone any time, or via `run_incidentML.sh`'s Step 2 |
| `requirements.txt` | `requests` + `scikit-learn` (the latter only used by `ml_clustering.py`) |
| `agent2_payload.json` | Local copy of the synthetic dataset, used only by the offline validation |
| `incidents_raw.json`, `results.json`, `phase1_summary.html` | Generated output — gitignored, since these reflect live instance data. Not present until `run_incidentML.sh` (or `fetch_incidents.py`/`prioritize.py` directly) has been run at least once. |
| `.gitignore` | Excludes the generated-output files above, plus `__pycache__/` and `.DS_Store` |

## Before committing to git
Same rule as `../servicenow/`: check `run_incidentML.sh` for a live
password before `git add`:
```bash
grep -n "SN_PASSWORD_HARDCODED" run_incidentML.sh
```
Should show only `SN_PASSWORD_HARDCODED=""`.

---

## Troubleshooting (issues already hit and resolved once)

**`Input file not found: incidents_raw.json`** — this file only exists
after `fetch_incidents.py` has successfully run at least once (it's your
live ServiceNow data, not something that ships with the repo). Run
`./run_incidentML.sh` (it fetches automatically), or `python3
fetch_incidents.py` directly if you just need to re-fetch.

**`MFHOME: No such file or directory` / commands using `$MFHOME` fail with
a path starting at `/`** — the terminal window doesn't have `$MFHOME`
loaded, usually because it was opened before `~/.zshrc` was set up, or is a
tab that didn't inherit the environment. Run `source ~/.zshrc` in that
window, then `echo $MFHOME` to confirm it's set. New terminal windows
opened after the initial setup should have it automatically.

**`syntax error: invalid arithmetic operator` in a shell script** — macOS
ships bash 3.2 (from 2007) by default, which doesn't support associative
arrays (`declare -A`). `run_incidentML.sh` is written without them
specifically to avoid this; if you ever modify it, avoid `declare -A`.

**A script behaves like it's using old logic after being "fixed"** — almost
always a stale or duplicate download. Browsers sometimes save a repeat
download as `filename (1).py` instead of overwriting, or leave it in
`~/Downloads/` instead of the target folder. Check exact byte sizes with
`wc -c <file>` against the table in the Files section above, don't trust
Finder's rounded KB display, and check `ls -lat ~/Downloads | head -5` to
see what actually just downloaded and where.
