#!/usr/bin/env python3
"""
Diagnostic test: creates exactly ONE incident and prints full diagnostic
detail on both success and failure. Uses the same env vars as
create_servicenow_incidents.py (SN_INSTANCE, SN_USER, SN_PASSWORD) -
run this in the same terminal session right after exporting them,
or via ./run_import.sh's prompts (just point it at this file instead).

Usage:
    export SN_INSTANCE="https://dev324854.service-now.com"
    export SN_USER="admin"
    export SN_PASSWORD="your-password"
    python3 test_single_incident.py
"""
import os
import sys
import json
import requests

INSTANCE = os.environ.get("SN_INSTANCE", "").rstrip("/")
USER = os.environ.get("SN_USER", "admin")
PASSWORD = os.environ.get("SN_PASSWORD", "")

if not INSTANCE or not PASSWORD:
    sys.exit("Set SN_INSTANCE, SN_USER, SN_PASSWORD environment variables first.")

url = f"{INSTANCE}/api/now/table/incident"
body = {
    "short_description": "TEST - diagnostic single-incident insert (safe to delete)",
    "description": "This is a one-off test record created to diagnose API authentication. Safe to delete.",
    "priority": "4",
    "urgency": "3",
    "impact": "3",
    "state": "1",
}

print(f"POST {url}")
print(f"Auth user: {USER}")
print(f"Password length: {len(PASSWORD)} characters")
print(f"Password first/last char: {PASSWORD[0]}...{PASSWORD[-1]}" if PASSWORD else "(empty)")
print("-" * 60)

session = requests.Session()
session.auth = (USER, PASSWORD)
session.headers.update({"Content-Type": "application/json", "Accept": "application/json"})

try:
    resp = session.post(url, data=json.dumps(body), timeout=30)
except requests.exceptions.RequestException as e:
    print(f"NETWORK/CONNECTION ERROR: {e}")
    sys.exit(1)

print(f"HTTP status: {resp.status_code}")
print(f"Response headers (selected):")
for h in ["Content-Type", "Set-Cookie", "WWW-Authenticate", "X-Is-Logged-In"]:
    if h in resp.headers:
        # never print Set-Cookie value itself, just note presence
        val = "(present)" if h == "Set-Cookie" else resp.headers[h]
        print(f"  {h}: {val}")
print("-" * 60)
print("Response body:")
print(resp.text[:2000])
print("-" * 60)

if resp.status_code in (200, 201):
    result = resp.json()["result"]
    print(f"SUCCESS. Created {result['number']} (sys_id {result['sys_id']}).")
    print("Basic Auth works fine for this instance/account.")
    print("You can safely delete this test incident from ServiceNow afterward.")
    print("READY: proceed with the full 100-record run_import.sh / create_servicenow_incidents.py")
    sys.exit(0)
elif resp.status_code == 401:
    print("DIAGNOSIS: 401 despite a working browser login usually means Basic Auth")
    print("itself is disabled or blocked for API requests on this instance, OR the")
    print("password was mistyped/mis-captured at the hidden prompt (invisible typo).")
    print("Next steps:")
    print("  1. Check System Properties for 'glide.basicauth.disabled' in the UI.")
    print("  2. Try this exact curl command from Terminal (single quotes matter -")
    print("     '!' in your password will trigger bash history expansion in double quotes):")
    print(f"     curl -u 'admin:YOUR_PASSWORD' '{url}?sysparm_limit=1'")
    sys.exit(1)
elif resp.status_code == 403:
    print("DIAGNOSIS: 403 means you authenticated successfully but lack permission")
    print("to create incident records (ACL/role issue) - unusual for an admin account.")
    sys.exit(1)
else:
    print(f"DIAGNOSIS: Unexpected status {resp.status_code} - see response body above.")
    sys.exit(1)
