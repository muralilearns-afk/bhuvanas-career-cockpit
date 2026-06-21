#!/usr/bin/env python3
"""
sync_tracker_state.py
======================
Runs inside the "Sync Tracker State" GitHub Action. That workflow fires
when a GitHub issue labeled `tracker-sync` is opened - which is exactly
what the dashboard's "Sync to Cloud" button does: it builds a pre-filled
github.com/<repo>/issues/new URL containing your local Applied/Discarded
status map as a fenced JSON block, and opens it in a new tab using your
own already-logged-in GitHub session. No token, secret, or credential of
any kind ever appears in the dashboard's JavaScript.

This script reads that issue body, extracts the JSON block, validates it,
and merges it into data/tracker_state.json using last-write-wins (by each
entry's `updatedAt` timestamp). The workflow's own GITHUB_TOKEN - which
GitHub provisions automatically for every workflow run, scoped only to
this repo, and which never appears in any file - is what the *next* step
in the workflow uses to commit the result. This script never touches it.

Inputs:  $ISSUE_BODY  (the triggering issue's body, set by the workflow)
Outputs (via $GITHUB_OUTPUT): merged=true|false, count=<int>
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
STATE_PATH = REPO_ROOT / "data" / "tracker_state.json"

REQUIRED_KEYS = {"status", "updatedAt"}
ALLOWED_STATUSES = {"applied", "discarded"}

JSON_BLOCK_RE = re.compile(r"```json\s*(.*?)\s*```", re.DOTALL)


def set_output(name: str, value: str) -> None:
    gh_output = os.environ.get("GITHUB_OUTPUT")
    line = f"{name}={value}\n"
    if gh_output:
        with open(gh_output, "a", encoding="utf-8") as f:
            f.write(line)
    else:
        print(line, end="")


def load_existing_state() -> dict:
    if STATE_PATH.exists():
        try:
            data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except ValueError:
            return {}
    return {}


def extract_payload(issue_body: str) -> dict | None:
    match = JSON_BLOCK_RE.search(issue_body or "")
    if not match:
        return None
    try:
        payload = json.loads(match.group(1))
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def is_valid_entry(entry: object) -> bool:
    if not isinstance(entry, dict):
        return False
    if not REQUIRED_KEYS.issubset(entry.keys()):
        return False
    if entry.get("status") not in ALLOWED_STATUSES:
        return False
    try:
        datetime.fromisoformat(str(entry["updatedAt"]).replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def main() -> None:
    issue_body = os.environ.get("ISSUE_BODY", "")
    payload = extract_payload(issue_body)

    if payload is None:
        print("No valid ```json block found in the issue body.", file=sys.stderr)
        set_output("merged", "false")
        set_output("count", "0")
        return

    existing = load_existing_state()
    changed = 0

    for lead_id, entry in payload.items():
        if not is_valid_entry(entry):
            print(f"Skipping malformed entry for id={lead_id!r}", file=sys.stderr)
            continue
        current = existing.get(lead_id)
        # Last-write-wins by updatedAt. Lexicographic comparison is valid
        # here because both sides are always ISO-8601 UTC strings produced
        # by `new Date().toISOString()` on the client.
        if current is None or entry.get("updatedAt", "") > current.get("updatedAt", ""):
            existing[lead_id] = entry
            changed += 1

    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(existing, indent=2, sort_keys=True), encoding="utf-8")

    if changed == 0:
        print("Parsed a valid payload, but nothing was newer than what's already stored.")
    else:
        print(f"Merged {changed} updated entr{'y' if changed == 1 else 'ies'} into {STATE_PATH}")

    set_output("merged", "true")
    set_output("count", str(changed))


if __name__ == "__main__":
    main()
