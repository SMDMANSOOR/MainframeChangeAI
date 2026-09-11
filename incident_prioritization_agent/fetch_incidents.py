#!/usr/bin/env python3
"""
Phase 1 - Incident Discovery Agent: fetch step.

Pulls all GENAPP incidents from your ServiceNow instance via the Table API
and writes them to incidents_raw.json for prioritize.py to consume.

I (Claude) cannot run this myself: the target instance isn't on my
allowed outbound network list, and I never have your live credentials.
Run this from your own machine, same pattern as the earlier loader scripts.

Usage:
    export SN_INSTANCE="https://dev324854.service-now.com"
    export SN_USER="admin"
    export SN_PASSWORD="your-password"
    python3 fetch_incidents.py

Filtering: by default this keeps only incidents whose description contains
the "Technical signal (Agent 1 extract)" marker that create_servicenow_incidents.py
writes into every GENAPP incident it creates - this is how we separate the
GENAPP dataset from the PDI's unrelated pre-existing demo data (the SAP/HR/etc.
incidents you saw in search results earlier) without needing a custom field.
Set SN_FILTER_MARKER="" to disable filtering and pull every incident instead.
"""
import os
import sys
import json
import requests

INSTANCE = os.environ.get("SN_INSTANCE", "").rstrip("/")
USER = os.environ.get("SN_USER", "admin")
PASSWORD = os.environ.get("SN_PASSWORD", "")
FILTER_MARKER = os.environ.get("SN_FILTER_MARKER", "Technical signal (Agent 1 extract)")
PAGE_SIZE = 200

FIELDS = ["number", "sys_id", "short_description", "description", "priority", "urgency",
          "impact", "state", "category", "subcategory", "assignment_group", "opened_at",
          "resolved_at", "close_notes"]

OUT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "incidents_raw.json")


def main():
    if not INSTANCE or not PASSWORD:
        sys.exit("Set SN_INSTANCE, SN_USER, SN_PASSWORD environment variables first.")

    session = requests.Session()
    session.auth = (USER, PASSWORD)
    session.headers.update({"Accept": "application/json"})

    url = f"{INSTANCE}/api/now/table/incident"
    all_records = []
    offset = 0

    print(f"Fetching incidents from {INSTANCE} ...")
    while True:
        params = {
            "sysparm_fields": ",".join(FIELDS),
            "sysparm_limit": str(PAGE_SIZE),
            "sysparm_offset": str(offset),
            "sysparm_display_value": "true",
        }
        resp = session.get(url, params=params, timeout=60)
        if resp.status_code != 200:
            sys.exit(f"Fetch failed: {resp.status_code} {resp.text[:300]}")
        batch = resp.json().get("result", [])
        if not batch:
            break
        all_records.extend(batch)
        print(f"  fetched {len(all_records)} so far...")
        if len(batch) < PAGE_SIZE:
            break
        offset += PAGE_SIZE

    print(f"Total incidents fetched: {len(all_records)}")

    if FILTER_MARKER:
        filtered = [r for r in all_records if FILTER_MARKER in (r.get("description") or "")]
        print(f"Filtered to {len(filtered)} GENAPP incidents "
              f"(matched marker {FILTER_MARKER!r}; set SN_FILTER_MARKER=\"\" to disable).")
    else:
        filtered = all_records
        print("No filter applied - keeping all fetched incidents.")

    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(filtered, f, indent=2)
    print(f"Wrote {len(filtered)} records to {OUT_PATH}")


if __name__ == "__main__":
    main()
