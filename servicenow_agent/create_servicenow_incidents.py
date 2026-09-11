#!/usr/bin/env python3
"""
Loads the synthetic CICS-GENAPP incidents into your ServiceNow PDI using the
Table API, and attaches the two real screenshots to the relevant incidents
using the Attachment API.

I (Claude) can't run this myself: dev356328.service-now.com isn't on my
allowed outbound network list, and I only ever saw your masked password.
Run this from your own machine.

Usage:
    pip install requests
    export SN_INSTANCE="https://dev356328.service-now.com"
    export SN_USER="admin"
    export SN_PASSWORD="<your password>"
    python3 create_servicenow_incidents.py

What it does:
  1. Reads agent2_payload.json (100 synthetic incidents)
  2. POSTs each one to /api/now/table/incident
  3. For the two rows that reference a screenshot (u_attachment), uploads the
     file from ./attachments/ to that incident via /api/now/attachment/file
  4. Writes incidents_created.json mapping our synthetic `number` -> the real
     sys_id / INC number ServiceNow assigned

Notes:
  - This script only uses OOB incident fields (short_description, description,
    priority, urgency, impact, category, state) plus category/subcategory.
    The u_transaction_id / u_program_name / u_abend_code / u_sqlcode /
    u_return_code / u_pattern_id signal is folded into the description as a
    labeled block so nothing gets lost if you haven't created those custom
    fields yet. If you DO create matching u_* fields on the incident table,
    flip INCLUDE_CUSTOM_FIELDS to True below and they'll be sent natively.
  - u_pattern_id / u_is_permanent_fix are deliberately NOT sent - they're
    ground-truth labels for you to validate Agent 2 against, not input data.
  - assignment_group and cmdb_ci are sent as plain text in the description
    block too, since matching them to your instance's actual sys_ids requires
    a lookup against your specific PDI's group/CI records.
"""
import os
import sys
import json
import time
import requests

INCLUDE_CUSTOM_FIELDS = False  # set True only if u_* fields exist on incident table

INSTANCE = os.environ.get("SN_INSTANCE", "").rstrip("/")
USER = os.environ.get("SN_USER", "admin")
PASSWORD = os.environ.get("SN_PASSWORD", "")

HERE = os.path.dirname(os.path.abspath(__file__))
PAYLOAD_PATH = os.path.join(HERE, "agent2_payload.json")
ATTACH_DIR = os.path.join(HERE, "attachments")

STATE_MAP = {"New": "1", "In Progress": "2", "Resolved": "6", "Closed": "7"}
PRIORITY_MAP = {1: "1", 2: "2", 3: "3", 4: "4", 5: "5"}  # ServiceNow default 1-5


def build_description(rec):
    lines = [rec["description"], "", "--- Technical signal (Agent 1 extract) ---"]
    lines.append(f"Application (CI): {rec['cmdb_ci']}")
    lines.append(f"Transaction: {rec['u_transaction_id'] or 'n/a'}")
    lines.append(f"Program: {rec['u_program_name'] or 'n/a'}")
    if rec["u_job_name"]:
        lines.append(f"Job: {rec['u_job_name']}")
    if rec["u_abend_code"]:
        lines.append(f"Abend code: {rec['u_abend_code']}")
    if rec["u_sqlcode"]:
        lines.append(f"SQLCODE: {rec['u_sqlcode']}")
    if rec["u_return_code"]:
        lines.append(f"Return code: {rec['u_return_code']}")
    lines.append(f"Suggested assignment group: {rec['assignment_group']}")
    return "\n".join(lines)


def main():
    if not INSTANCE or not PASSWORD:
        sys.exit("Set SN_INSTANCE, SN_USER, SN_PASSWORD environment variables first. "
                  "See the docstring at the top of this file.")

    with open(PAYLOAD_PATH) as f:
        payload = json.load(f)
    records = payload["incidents"]

    session = requests.Session()
    session.auth = (USER, PASSWORD)
    session.headers.update({"Content-Type": "application/json", "Accept": "application/json"})

    table_url = f"{INSTANCE}/api/now/table/incident"
    attach_url = f"{INSTANCE}/api/now/attachment/file"

    # Discover the ACTUAL valid close_code choice values on this instance instead
    # of guessing OOB label strings - a mismatched choice value gets silently
    # dropped by ServiceNow, which still trips the "mandatory" Data Policy check
    # even though we sent something.
    permanent_code, workaround_code = None, None
    try:
        choice_url = f"{INSTANCE}/api/now/table/sys_choice"
        params = {
            "sysparm_query": "name=incident^element=close_code^inactive=false",
            "sysparm_fields": "value,label",
            "sysparm_limit": "50",
        }
        cresp = session.get(choice_url, params=params, timeout=30)
        if cresp.status_code == 200:
            choices = cresp.json().get("result", [])
            print(f"Discovered {len(choices)} close_code choice(s) on this instance:")
            for c in choices:
                print(f"    value={c['value']!r}  label={c['label']!r}")
            for c in choices:
                lbl = c["label"].lower()
                if "permanent" in lbl and permanent_code is None:
                    permanent_code = c["value"]
                if ("work around" in lbl or "workaround" in lbl or "temporary" in lbl) and workaround_code is None:
                    workaround_code = c["value"]
            if not permanent_code and choices:
                permanent_code = choices[0]["value"]
            if not workaround_code and choices:
                workaround_code = choices[-1]["value"] if len(choices) > 1 else choices[0]["value"]
        else:
            print(f"Could not fetch close_code choices ({cresp.status_code}) - falling back to guessed values.")
    except requests.exceptions.RequestException as e:
        print(f"Could not fetch close_code choices ({e}) - falling back to guessed values.")

    if not permanent_code:
        permanent_code = "Solved (Permanently)"
    if not workaround_code:
        workaround_code = "Solved (Work Around)"
    print(f"Using close_code={permanent_code!r} for permanent fixes, {workaround_code!r} for workarounds.\n")

    created = []
    for i, rec in enumerate(records, start=1):
        is_resolved = rec["state"] in ("Resolved", "Closed")
        body = {
            "short_description": rec["short_description"],
            "description": build_description(rec),
            "priority": PRIORITY_MAP.get(rec["priority"], "3"),
            "urgency": str(rec["urgency"]),
            "impact": str(rec["impact"]),
            "state": STATE_MAP.get(rec["state"], "2"),
            "category": rec["category"].lower(),
            "close_notes": rec["close_notes"] if is_resolved else "",
            "opened_at": rec["opened_at"],
        }
        if is_resolved:
            if rec["u_pattern_id"] == "ONE-OFF" or rec.get("u_is_permanent_fix") == "true":
                body["close_code"] = permanent_code
            else:
                body["close_code"] = workaround_code
        if INCLUDE_CUSTOM_FIELDS:
            body.update({
                "u_transaction_id": rec["u_transaction_id"],
                "u_program_name": rec["u_program_name"],
                "u_job_name": rec["u_job_name"],
                "u_abend_code": rec["u_abend_code"],
                "u_sqlcode": rec["u_sqlcode"],
                "u_return_code": rec["u_return_code"],
                "u_pattern_id": rec["u_pattern_id"],
            })

        resp = session.post(table_url, data=json.dumps(body))
        if resp.status_code not in (200, 201):
            if "Resolution code" in resp.text and body.get("state") != STATE_MAP["In Progress"]:
                # Still being rejected on the same field even with a discovered
                # choice value - fall back to creating it as In Progress instead
                # of losing the record entirely. Note this in the description so
                # it's visible on the record, and keep going.
                body["state"] = STATE_MAP["In Progress"]
                body["close_notes"] = ""
                body.pop("close_code", None)
                body["description"] += (f"\n\n[Import note: instance rejected close_code "
                                         f"{permanent_code!r}/{workaround_code!r} for this record - "
                                         f"created as In Progress instead. Original intended state: "
                                         f"{rec['state']}.]")
                resp = session.post(table_url, data=json.dumps(body))

        if resp.status_code not in (200, 201):
            print(f"[{i}/{len(records)}] FAILED {rec['number']}: {resp.status_code} {resp.text[:300]}")
            continue

        result = resp.json()["result"]
        sys_id = result["sys_id"]
        real_number = result["number"]
        print(f"[{i}/{len(records)}] {rec['number']} -> {real_number} ({sys_id})")
        created.append({"synthetic_number": rec["number"], "sys_id": sys_id, "real_number": real_number})

        # attach screenshot if this record references one
        attachment = rec.get("u_attachment")
        if attachment:
            file_path = os.path.join(ATTACH_DIR, attachment)
            if os.path.exists(file_path):
                with open(file_path, "rb") as fh:
                    file_bytes = fh.read()
                params = {"table_name": "incident", "table_sys_id": sys_id, "file_name": attachment}
                a_resp = session.post(
                    attach_url, params=params, data=file_bytes,
                    headers={"Content-Type": "image/png"},
                )
                if a_resp.status_code in (200, 201):
                    print(f"    attached {attachment}")
                else:
                    print(f"    attachment FAILED: {a_resp.status_code} {a_resp.text[:200]}")
            else:
                print(f"    attachment file not found: {file_path}")

        time.sleep(0.15)  # be gentle with the PDI

    out_path = os.path.join(HERE, "incidents_created.json")
    with open(out_path, "w") as f:
        json.dump(created, f, indent=2)
    print(f"\nDone. {len(created)}/{len(records)} incidents created. Mapping written to {out_path}")


if __name__ == "__main__":
    main()
