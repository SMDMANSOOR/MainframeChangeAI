#!/usr/bin/env python3
"""
Phase 1 - Incident Discovery Agent: clustering + prioritization step.

Groups incidents into recurring-failure clusters and ranks them by a
composite priority score, so the top of the list is "recurring, severe,
escalating, and recent" - not just whatever was opened most recently.

Works against either:
  - incidents_raw.json  (real data fetched from ServiceNow by fetch_incidents.py)
  - agent2_payload.json (the synthetic dataset, for offline validation -
    see test_with_synthetic_data.py)

Usage:
    python3 prioritize.py --input incidents_raw.json --top 5
"""
import os
import re
import sys
import json
import math
import argparse
from datetime import datetime, timezone
from collections import defaultdict

from ml_clustering import tfidf_text_clusters

HERE = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------------------
# Signature extraction - how we detect "this is the same recurring failure"
# ---------------------------------------------------------------------------
# create_servicenow_incidents.py writes a labeled "Technical signal" block into
# every incident's description (Program:, Transaction:, Abend code:, SQLCODE:,
# Return code:). We parse that back out here. The synthetic payload
# (agent2_payload.json) carries the same information as plain JSON fields
# instead - we try that first since it's exact, and fall back to parsing the
# description text for real ServiceNow records.
LINE_PATTERNS = {
    "transaction": re.compile(r"^Transaction:\s*(.+)$", re.MULTILINE),
    "program": re.compile(r"^Program:\s*(.+)$", re.MULTILINE),
    "abend": re.compile(r"^Abend code:\s*(.+)$", re.MULTILINE),
    "sqlcode": re.compile(r"^SQLCODE:\s*(.+)$", re.MULTILINE),
    "return_code": re.compile(r"^Return code:\s*(.+)$", re.MULTILINE),
}

ESCALATION_KEYWORDS = [
    "recurring", "duplicate", "escalat", "blocker", "blocking", "problem candidate",
    "cab", "workaround only", "no fix", "not yet", "still unresolved", "reopen",
    "same as", "again", "repeat", "flagged by", "uat blocker",
]


def extract_signature(rec):
    """Return a tuple identifying the root cause this incident belongs to."""
    # Synthetic/offline path: exact structured fields already present.
    if any(k in rec for k in ("u_program_name", "u_abend_code", "u_sqlcode", "u_return_code")):
        program = (rec.get("u_program_name") or "").strip()
        transaction = (rec.get("u_transaction_id") or "").strip()
        abend = (rec.get("u_abend_code") or "").strip()
        sqlcode = (rec.get("u_sqlcode") or "").strip()
        return_code = (rec.get("u_return_code") or "").strip()
    else:
        desc = rec.get("description") or ""
        def grab(key):
            m = LINE_PATTERNS[key].search(desc)
            return m.group(1).strip() if m else ""
        transaction = grab("transaction")
        if transaction.lower() in ("n/a", ""):
            transaction = ""
        program = grab("program")
        if program.lower() in ("n/a", ""):
            program = ""
        abend = grab("abend")
        sqlcode = grab("sqlcode")
        return_code = grab("return_code")

    sig = (program, transaction, abend, sqlcode, return_code)
    if not any(sig):
        # No structured signal at all - fall back to a normalized short_description
        # so near-duplicate free-text complaints can still cluster together.
        norm = re.sub(r"[^a-z0-9 ]", "", (rec.get("short_description") or "").lower())
        norm = re.sub(r"\s+", " ", norm).strip()
        sig = ("(text)", norm, "", "", "")
    return sig


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------
PRIORITY_MAX = 5  # ServiceNow default scale, 1=highest..5=lowest

WEIGHTS = {
    "frequency": 0.35,
    "severity": 0.30,
    "escalation": 0.20,
    "recency": 0.15,
}


def to_int(v, default=3):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def parse_dt(s):
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def severity_score(rec):
    p = to_int(rec.get("priority"), 3)
    u = to_int(rec.get("urgency"), 3)
    i = to_int(rec.get("impact"), 3)
    avg = (p + u + i) / 3.0
    # invert: priority 1 (highest) -> 1.0, priority 5 (lowest) -> 0.2
    return max(0.0, min(1.0, (PRIORITY_MAX + 1 - avg) / PRIORITY_MAX))


def escalation_hits(text):
    text = (text or "").lower()
    return sum(1 for kw in ESCALATION_KEYWORDS if kw in text)


def build_groups(records, method="ensemble"):
    """
    Groups records into recurring-failure clusters using one of three methods:

      "signature" - pure rule-based: exact match on the structured
                    Program/Transaction/Abend/SQLCODE/Return-code fields.
                    Fast, fully explainable, but blind to incidents where
                    that structured signal wasn't captured.

      "tfidf"     - pure ML: TF-IDF + Agglomerative clustering (see
                    ml_clustering.py) on every incident's text, ignoring
                    structured fields entirely.

      "ensemble" (default) - best of both: use signature matching wherever
                    a real structured signature exists (trustworthy, exact),
                    and fall back to ML text clustering ONLY for the subset
                    of incidents with no structured signal at all - which is
                    exactly the case the rule-based approach can't handle on
                    its own. This is the mode prioritize.py runs by default.

    Returns: list of member-lists (each inner list = one cluster's incidents).
    """
    if method == "tfidf":
        texts = [(r.get("short_description", "") + " " + (r.get("description") or ""))
                 for r in records]
        labels = tfidf_text_clusters(texts)
        buckets = defaultdict(list)
        for rec, lbl in zip(records, labels):
            buckets[lbl].append(rec)
        return list(buckets.values())

    # "signature" and "ensemble" both start with rule-based grouping
    sig_groups = defaultdict(list)
    for rec in records:
        sig_groups[extract_signature(rec)].append(rec)

    if method == "signature":
        return list(sig_groups.values())

    # ensemble: pull out the no-structured-signal ("(text)") bucket(s) and
    # re-cluster just those with the ML model instead of a naive exact-text
    # match, so near-duplicate free-text complaints can still group together.
    final_groups = []
    text_fallback_records = []
    for sig, members in sig_groups.items():
        if sig[0] == "(text)":
            text_fallback_records.extend(members)
        else:
            final_groups.append(members)

    if text_fallback_records:
        texts = [(r.get("short_description", "") + " " + (r.get("description") or ""))
                 for r in text_fallback_records]
        labels = tfidf_text_clusters(texts)
        sub_buckets = defaultdict(list)
        for rec, lbl in zip(text_fallback_records, labels):
            sub_buckets[lbl].append(rec)
        final_groups.extend(sub_buckets.values())

    return final_groups


def describe_group(members):
    """
    Best-effort human-readable description of a cluster for reporting:
    if every member shares an identical structured signature, use it
    directly (this is a pure rule-based cluster). Otherwise (members were
    grouped by ML text similarity instead), fall back to the first member's
    short description and flag the cluster's origin accordingly.
    """
    first_sig = extract_signature(members[0])
    homogeneous = all(extract_signature(m) == first_sig for m in members)

    if homogeneous and first_sig[0] != "(text)":
        program, transaction, abend, sqlcode, return_code = first_sig
        top_program = program if program else (transaction or members[0].get("short_description", "")[:40])
        return (
            {"program": program, "transaction": transaction, "abend": abend,
             "sqlcode": sqlcode, "return_code": return_code},
            top_program,
            "signature",
        )
    else:
        top_program = members[0].get("short_description", "")[:40]
        return (
            {"program": "", "transaction": "", "abend": "", "sqlcode": "", "return_code": ""},
            top_program,
            "ml-text-similarity",
        )


def cluster_and_score(records, now=None, method="ensemble"):
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    groups = build_groups(records, method=method)

    max_freq = max((len(g) for g in groups), default=1)

    results = []
    for members in groups:
        freq = len(members)
        freq_score = freq / max_freq if max_freq else 0.0

        sev_scores = [severity_score(m) for m in members]
        sev_score = sum(sev_scores) / len(sev_scores)

        esc_total = sum(escalation_hits(m.get("description", "") + " " + m.get("close_notes", ""))
                         for m in members)
        esc_score = min(1.0, esc_total / (freq * 2))  # normalize, cap at 1.0

        dates = [parse_dt(m.get("opened_at")) for m in members]
        dates = [d for d in dates if d]
        if dates:
            most_recent = max(dates)
            age_days = max(0.0, (now - most_recent).total_seconds() / 86400)
            recency_score = math.exp(-age_days / 30.0)  # 30-day half-life-ish decay
        else:
            recency_score = 0.3

        composite = (
            WEIGHTS["frequency"] * freq_score
            + WEIGHTS["severity"] * sev_score
            + WEIGHTS["escalation"] * esc_score
            + WEIGHTS["recency"] * recency_score
        )
        composite = max(0.0, min(1.0, composite))

        signature, top_program, cluster_method = describe_group(members)

        results.append({
            "signature": signature,
            "cluster_method": cluster_method,
            "top_program": top_program,
            "incident_count": freq,
            "priority_score": round(composite, 2),
            "risk_score_pct": round(composite * 100, 1),
            "frequency_score": round(freq_score, 2),
            "severity_score": round(sev_score, 2),
            "escalation_score": round(esc_score, 2),
            "recency_score": round(recency_score, 2),
            "member_numbers": [m.get("number") for m in members],
            "sample_short_description": members[0].get("short_description", ""),
            "is_recurring": freq > 1,
        })

    results.sort(key=lambda r: r["priority_score"], reverse=True)
    return results


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------
def factor_bar(value, color):
    """Tiny inline bar chart cell for a 0-1 factor score."""
    pct = max(0, min(100, round(value * 100)))
    return (f'<div class="barwrap"><div class="bar" style="width:{pct}%;background:{color}"></div>'
            f'<span class="barval">{value:.2f}</span></div>')


def why_sentence(c):
    """Plain-English summary of which factors drove this cluster's rank."""
    parts = []
    if c["frequency_score"] >= 0.7:
        parts.append("very high recurrence")
    elif c["frequency_score"] >= 0.4:
        parts.append("moderate recurrence")
    else:
        parts.append("low recurrence")
    if c["severity_score"] >= 0.7:
        parts.append("high priority/urgency/impact")
    elif c["severity_score"] >= 0.4:
        parts.append("moderate severity")
    else:
        parts.append("low severity")
    if c["escalation_score"] >= 0.7:
        parts.append("strong escalation language in notes")
    if c["recency_score"] >= 0.5:
        parts.append("recently active")
    return "Driven by " + ", ".join(parts) + "."


def write_html_report(records, clusters, top_n, out_path, clustering_method="ensemble"):
    recurring = [c for c in clusters if c["is_recurring"]]
    top = clusters[:top_n]
    top_cluster = clusters[0] if clusters else None

    method_labels = {"signature": "Signature (rule-based)", "tfidf": "TF-IDF (ML)",
                      "ensemble": "Ensemble (rule-based + ML)"}

    rows = ""
    for rank, c in enumerate(top, start=1):
        badge_color = "#60a5fa" if c["cluster_method"] == "signature" else "#c084fc"
        badge_text = "signature match" if c["cluster_method"] == "signature" else "ML text clustering"
        rows += f"""
        <tr>
          <td class="rank">#{rank}</td>
          <td>{c['top_program']}<br><span class="badge" style="background:{badge_color}22;color:{badge_color};border:1px solid {badge_color}55;">{badge_text}</span></td>
          <td>{c['incident_count']}</td>
          <td>{c['priority_score']:.2f}</td>
          <td>{c['risk_score_pct']:.1f}%</td>
          <td>{factor_bar(c['frequency_score'], '#60a5fa')}</td>
          <td>{factor_bar(c['severity_score'], '#f87171')}</td>
          <td>{factor_bar(c['escalation_score'], '#fbbf24')}</td>
          <td>{factor_bar(c['recency_score'], '#4ade80')}</td>
          <td class="small">{', '.join(c['member_numbers'][:6])}{'...' if len(c['member_numbers']) > 6 else ''}</td>
          <td class="small">{c['sample_short_description']}<br><span class="why">{why_sentence(c)}</span></td>
        </tr>"""

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Phase 1 - Incident Discovery Summary</title>
<style>
  body {{ background:#0f172a; color:#e2e8f0; font-family: -apple-system, Segoe UI, Arial, sans-serif; padding: 32px; }}
  h1 {{ font-size: 22px; margin-bottom: 4px; }}
  .subtitle {{ color:#94a3b8; margin-bottom: 24px; }}
  .metrics {{ display:flex; gap:16px; flex-wrap:wrap; margin-bottom: 32px; }}
  .metric {{ background:#1e293b; border:1px solid #334155; border-radius:10px; padding:16px 20px; min-width:150px; }}
  .metric .label {{ color:#94a3b8; font-size:12px; text-transform:uppercase; letter-spacing:0.05em; }}
  .metric .value {{ font-size:26px; font-weight:700; color:#60a5fa; margin-top:4px; }}
  .metric.risk .value {{ color:#fbbf24; }}
  table {{ width:100%; border-collapse:collapse; background:#1e293b; border-radius:10px; overflow:hidden; }}
  th {{ text-align:left; background:#334155; padding:10px 12px; font-size:11px; text-transform:uppercase; color:#94a3b8; }}
  td {{ padding:10px 12px; border-top:1px solid #334155; font-size:14px; vertical-align:top; }}
  td.rank {{ font-weight:700; color:#4ade80; }}
  td.small {{ font-size:12px; color:#94a3b8; max-width:220px; }}
  .why {{ color:#64748b; font-style:italic; }}
  .barwrap {{ position:relative; width:70px; height:14px; background:#0f172a; border-radius:4px; overflow:hidden; }}
  .bar {{ height:100%; border-radius:4px; }}
  .barval {{ position:absolute; top:-1px; left:6px; font-size:10px; color:#0f172a; font-weight:700; mix-blend-mode:difference; color:#e2e8f0; }}
  .legend {{ display:grid; grid-template-columns:repeat(auto-fit, minmax(220px, 1fr)); gap:12px; margin:16px 0 20px; }}
  .legend-item {{ background:#1e293b; border:1px solid #334155; border-radius:8px; padding:10px 12px; }}
  .legend-item > span:first-child {{ font-size:13px; color:#e2e8f0; }}
  .legend-desc {{ display:block; font-size:11px; color:#94a3b8; margin-top:4px; line-height:1.4; }}
  .hint {{ margin-top:10px; color:#64748b; font-size:11px; font-style:italic; }}
  .legend span.dot {{ display:inline-block; width:10px; height:10px; border-radius:2px; margin-right:6px; }}
  .footer {{ margin-top:24px; color:#64748b; font-size:12px; }}
  .badge {{ display:inline-block; font-size:10px; padding:2px 6px; border-radius:4px; margin-top:4px; }}
</style></head>
<body>
  <h1>Phase 1 &mdash; Incident Discovery Agent</h1>
  <div class="subtitle">Feed: ServiceNow &middot; Clustering method: {method_labels.get(clustering_method, clustering_method)} &middot; Generated {datetime.now(timezone.utc).replace(tzinfo=None).strftime('%Y-%m-%d %H:%M UTC')}</div>

  <div class="metrics">
    <div class="metric"><div class="label">Incidents Ingested</div><div class="value">{len(records)}</div></div>
    <div class="metric"><div class="label">Clusters Formed</div><div class="value">{len(clusters)}</div></div>
    <div class="metric"><div class="label">Recurring Clusters</div><div class="value">{len(recurring)}</div></div>
    <div class="metric"><div class="label">Top Program</div><div class="value">{top_cluster['top_program'] if top_cluster else '-'}</div></div>
    <div class="metric"><div class="label">Priority Score</div><div class="value">{top_cluster['priority_score']:.2f} / 1.00</div></div>
    <div class="metric risk"><div class="label">Risk Score</div><div class="value">{top_cluster['risk_score_pct']:.0f}%</div></div>
  </div>

  <div class="legend">
    <div class="legend-item">
      <span><span class="dot" style="background:#60a5fa"></span><b>Frequency</b> (wt 0.35)</span>
      <span class="legend-desc">How often this exact failure recurs, relative to the largest cluster. Occurrences &divide; largest cluster's occurrences.</span>
    </div>
    <div class="legend-item">
      <span><span class="dot" style="background:#f87171"></span><b>Severity</b> (wt 0.30)</span>
      <span class="legend-desc">Average priority/urgency/impact across the cluster's incidents. P1 scores near 1.0, P5 scores near 0.2.</span>
    </div>
    <div class="legend-item">
      <span><span class="dot" style="background:#fbbf24"></span><b>Escalation</b> (wt 0.20)</span>
      <span class="legend-desc">Keyword hits in description/close notes &mdash; "recurring", "duplicate", "blocker", "CAB", "escalat...", etc.</span>
    </div>
    <div class="legend-item">
      <span><span class="dot" style="background:#4ade80"></span><b>Recency</b> (wt 0.15)</span>
      <span class="legend-desc">Exponential decay from the most recent occurrence's age. Still-happening today scores 1.0; 30 days old scores ~0.37.</span>
    </div>
  </div>

  <table>
    <tr>
      <th>Rank</th>
      <th>Top Program / Signature</th>
      <th>Occurrences</th>
      <th>Priority Score</th>
      <th>Risk Score</th>
      <th title="How often this exact failure recurs, relative to the largest cluster">Frequency</th>
      <th title="Average priority/urgency/impact across the cluster - P1 scores highest">Severity</th>
      <th title="Keyword hits signaling a known/worsening/unresolved problem in notes">Escalation</th>
      <th title="How recently this failure last occurred - decays over ~30 days">Recency</th>
      <th>Sample Incidents</th>
      <th>Description</th>
    </tr>
    {rows}
  </table>
  <div class="hint">Hover any factor column header for its definition. Full methodology in README.md.</div>

  <div class="footer">Each colored bar shows that factor's raw 0&ndash;1 score for this cluster before weighting. Priority Score = 0.35&times;Frequency + 0.30&times;Severity + 0.20&times;Escalation + 0.15&times;Recency. See README.md for full methodology.</div>
</body></html>"""

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default=os.path.join(HERE, "incidents_raw.json"))
    ap.add_argument("--output", default=os.path.join(HERE, "results.json"))
    ap.add_argument("--html", default=os.path.join(HERE, "phase1_summary.html"))
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--clustering", choices=["signature", "tfidf", "ensemble"], default="ensemble",
                     help="signature = rule-based only; tfidf = ML text clustering only; "
                          "ensemble (default) = rule-based, with ML text clustering filling in "
                          "for incidents with no structured signature.")
    args = ap.parse_args()

    if not os.path.exists(args.input):
        sys.exit(f"Input file not found: {args.input}\n"
                  f"Run fetch_incidents.py first, or point --input at a JSON list of incidents.")

    with open(args.input, encoding="utf-8") as f:
        records = json.load(f)
    if isinstance(records, dict) and "incidents" in records:
        records = records["incidents"]  # agent2_payload.json shape

    if not records:
        sys.exit("No incident records found in input file.")

    clusters = cluster_and_score(records, method=args.clustering)
    ml_assisted = sum(1 for c in clusters if c["cluster_method"] == "ml-text-similarity")

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump({
            "generated_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
            "clustering_method": args.clustering,
            "incidents_ingested": len(records),
            "clusters_formed": len(clusters),
            "ml_assisted_clusters": ml_assisted,
            "top_n": args.top,
            "clusters": clusters,
        }, f, indent=2)

    write_html_report(records, clusters, args.top, args.html, args.clustering)

    print(f"Clustering method: {args.clustering}")
    print(f"Incidents ingested: {len(records)}")
    print(f"Clusters formed: {len(clusters)} ({ml_assisted} via ML text similarity, "
          f"{len(clusters) - ml_assisted} via structured signature match)")
    print(f"\nTop {args.top} recurring/priority incident clusters:\n")
    for rank, c in enumerate(clusters[:args.top], start=1):
        print(f"#{rank}  {c['top_program']:<20} occurrences={c['incident_count']:<4} "
              f"priority_score={c['priority_score']:.2f}  risk={c['risk_score_pct']:.0f}%  "
              f"[{c['cluster_method']}]  e.g. {', '.join(c['member_numbers'][:3])}")
    print(f"\nFull results: {args.output}")
    print(f"HTML summary: {args.html}")


if __name__ == "__main__":
    main()
