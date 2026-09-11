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


def cluster_and_score(records, now=None):
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    clusters = defaultdict(list)
    for rec in records:
        clusters[extract_signature(rec)].append(rec)

    max_freq = max((len(v) for v in clusters.values()), default=1)

    results = []
    for sig, members in clusters.items():
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

        program, transaction, abend, sqlcode, return_code = sig
        top_program = program if program and program != "(text)" else (
            members[0].get("short_description", "")[:40]
        )

        results.append({
            "signature": {
                "program": program, "transaction": transaction, "abend": abend,
                "sqlcode": sqlcode, "return_code": return_code,
            },
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


def write_html_report(records, clusters, top_n, out_path):
    recurring = [c for c in clusters if c["is_recurring"]]
    top = clusters[:top_n]
    top_cluster = clusters[0] if clusters else None

    rows = ""
    for rank, c in enumerate(top, start=1):
        rows += f"""
        <tr>
          <td class="rank">#{rank}</td>
          <td>{c['top_program']}</td>
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
  .legend {{ display:flex; gap:20px; margin:16px 0 8px; font-size:12px; color:#94a3b8; flex-wrap:wrap; }}
  .legend span.dot {{ display:inline-block; width:10px; height:10px; border-radius:2px; margin-right:6px; }}
  .footer {{ margin-top:24px; color:#64748b; font-size:12px; }}
</style></head>
<body>
  <h1>Phase 1 &mdash; Incident Discovery Agent</h1>
  <div class="subtitle">Feed: ServiceNow &middot; Clustering by recurring root-cause signature &middot; Generated {datetime.now(timezone.utc).replace(tzinfo=None).strftime('%Y-%m-%d %H:%M UTC')}</div>

  <div class="metrics">
    <div class="metric"><div class="label">Incidents Ingested</div><div class="value">{len(records)}</div></div>
    <div class="metric"><div class="label">Clusters Formed</div><div class="value">{len(clusters)}</div></div>
    <div class="metric"><div class="label">Recurring Clusters</div><div class="value">{len(recurring)}</div></div>
    <div class="metric"><div class="label">Top Program</div><div class="value">{top_cluster['top_program'] if top_cluster else '-'}</div></div>
    <div class="metric"><div class="label">Priority Score</div><div class="value">{top_cluster['priority_score']:.2f} / 1.00</div></div>
    <div class="metric risk"><div class="label">Risk Score</div><div class="value">{top_cluster['risk_score_pct']:.0f}%</div></div>
  </div>

  <div class="legend">
    <span><span class="dot" style="background:#60a5fa"></span>Frequency (wt 0.35)</span>
    <span><span class="dot" style="background:#f87171"></span>Severity (wt 0.30)</span>
    <span><span class="dot" style="background:#fbbf24"></span>Escalation (wt 0.20)</span>
    <span><span class="dot" style="background:#4ade80"></span>Recency (wt 0.15)</span>
  </div>

  <table>
    <tr><th>Rank</th><th>Top Program / Signature</th><th>Occurrences</th><th>Priority Score</th><th>Risk Score</th><th>Frequency</th><th>Severity</th><th>Escalation</th><th>Recency</th><th>Sample Incidents</th><th>Description</th></tr>
    {rows}
  </table>

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

    clusters = cluster_and_score(records)

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump({
            "generated_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
            "incidents_ingested": len(records),
            "clusters_formed": len(clusters),
            "top_n": args.top,
            "clusters": clusters,
        }, f, indent=2)

    write_html_report(records, clusters, args.top, args.html)

    print(f"Incidents ingested: {len(records)}")
    print(f"Clusters formed: {len(clusters)}")
    print(f"\nTop {args.top} recurring/priority incident clusters:\n")
    for rank, c in enumerate(clusters[:args.top], start=1):
        print(f"#{rank}  {c['top_program']:<20} occurrences={c['incident_count']:<4} "
              f"priority_score={c['priority_score']:.2f}  risk={c['risk_score_pct']:.0f}%  "
              f"e.g. {', '.join(c['member_numbers'][:3])}")
    print(f"\nFull results: {args.output}")
    print(f"HTML summary: {args.html}")


if __name__ == "__main__":
    main()
