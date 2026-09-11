#!/usr/bin/env python3
"""
Offline validation: runs the exact same clustering/scoring engine from
prioritize.py against the synthetic dataset (which has ground-truth
u_pattern_id labels) and checks the output makes sense - without touching
ServiceNow at all. Run this any time you change the scoring weights or
clustering logic in prioritize.py, before trusting it against live data.
"""
import os
import json
from collections import Counter
from prioritize import cluster_and_score

HERE = os.path.dirname(os.path.abspath(__file__))
PAYLOAD = os.path.join(HERE, "agent2_payload.json")


def main():
    if not os.path.exists(PAYLOAD):
        print(f"Missing {PAYLOAD} - copy it in from the servicenow/ folder first.")
        return

    with open(PAYLOAD, encoding="utf-8") as f:
        data = json.load(f)
    records = data["incidents"]

    clusters = cluster_and_score(records)
    top = clusters[0]

    # Ground truth: what pattern do the top cluster's members actually belong to?
    numbers_to_pattern = {r["number"]: r["u_pattern_id"] for r in records}
    pattern_counts = Counter(numbers_to_pattern[n] for n in top["member_numbers"])
    dominant_pattern, dominant_count = pattern_counts.most_common(1)[0]
    purity = dominant_count / len(top["member_numbers"])

    print(f"Top-ranked cluster: {top['top_program']} ({top['incident_count']} incidents, "
          f"priority_score={top['priority_score']:.2f})")
    print(f"Ground-truth pattern breakdown of this cluster: {dict(pattern_counts)}")
    print(f"Cluster purity: {purity:.0%} (how much of the top cluster is really one root cause)")

    ok = True
    if dominant_pattern != "PTN-005":
        print("WARNING: expected PTN-005 (the flagship SSC1 pattern) to rank #1 - it didn't.")
        ok = False
    if purity < 0.9:
        print("WARNING: top cluster mixes multiple root causes - signature extraction may need tuning.")
        ok = False
    if ok:
        print("\nPASS: top-ranked cluster matches the known flagship recurring pattern cleanly.")
    else:
        print("\nCheck the warnings above before trusting this against live ServiceNow data.")


if __name__ == "__main__":
    main()
