#!/usr/bin/env python3
"""
fetch_all_opportunities.py
===========================
Daily sourcing engine for the Career Cockpit dashboard.

Aggregates newly-posted tech internship listings from several public,
ToS-friendly sources, deduplicates them by (company, title), and writes
a single unified JSON file that the frontend reads directly.

SOURCES
-------
1. GitHub README diff engine   - SimplifyJobs / vanshb03 style internship
                                   tracker repos. Diffs the raw markdown
                                   against the previous run's cached copy
                                   so only newly-added rows survive.
2. Greenhouse public API       - https://boards-api.greenhouse.io
3. Lever public API            - https://api.lever.co
4. Levels.fyi public jobs page - best-effort parse of the embedded
                                   page JSON. No login, no anti-bot
                                   evasion, no Workday/Cloudflare bypass.
5. Otta / Welcome to the Jungle - best-effort parse of the embedded
                                   page JSON, same constraints as above.

Design notes
------------
* Every source is wrapped in its own try/except. One source failing
  (site redesign, rate limit, network blip) never takes down the run -
  it just logs a warning and contributes zero rows that day.
* Sources 4 and 5 scrape public, logged-out pages by parsing the JSON
  payload the page itself ships to the browser. There is no headless
  browser, no stealth plugin, and no attempt to defeat bot detection
  anywhere in this file. If a target wants to block this, a normal
  rate-limited GET will simply start failing and the source quietly
  contributes nothing until the parser is updated.
* "Zero maintenance" is the goal, not a guarantee: sites #4 and #5 are
  HTML-shape-dependent and may need a parser tweak if they redesign.
  Sources #1-3 (GitHub, Greenhouse, Lever) are stable, documented, and
  very unlikely to need upkeep.

Output: <repo_root>/data/daily_leads.json
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import requests

# --------------------------------------------------------------------------
# CONFIG - edit these lists to add/remove sources without touching logic
# --------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent
DATA_DIR = REPO_ROOT / "data"
OUTPUT_PATH = DATA_DIR / "daily_leads.json"
CACHE_DIR = REPO_ROOT / ".sourcing_cache"

REQUEST_TIMEOUT = 20
REQUEST_HEADERS = {
    "User-Agent": "career-cockpit-sourcing-bot/1.0 (+https://github.com/; personal internship tracker)"
}
RATE_LIMIT_SECONDS = 1.0  # politeness delay between requests to the same host

# GitHub internship tracker repos to diff. branch is usually "dev" for these
# trackers (the "main" branch is often just a redirect/readme stub).
GITHUB_REPOS = [
    {"repo": "vanshb03/Summer2027-Internships", "branch": "dev", "path": "README.md"},
    {"repo": "SimplifyJobs/Summer2026-Internships", "branch": "dev", "path": "README.md"},
]

# Companies known to use Greenhouse's public job board API.
# boards-api.greenhouse.io/v1/boards/<slug>/jobs
GREENHOUSE_SLUGS = [
    "stripe", "airbnb", "doordash", "robinhood", "affirm", "coinbase",
    "databricks", "snowflake", "figma", "asana", "doordash",
]

# Companies known to use Lever's public posting API.
# api.lever.co/v0/postings/<slug>?mode=json
LEVER_SLUGS = [
    "netflix", "github", "shopify", "palantir", "plaid", "twitch",
]

INTERN_KEYWORDS = re.compile(r"\bintern(ship)?\b", re.IGNORECASE)
AGE_DAY_PATTERN = re.compile(r"(\d+)\s*d\b", re.IGNORECASE)
MAX_AGE_DAYS = 2  # GitHub-diff fallback window when no cache exists yet

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("sourcing-engine")


@dataclass
class Lead:
    company: str
    title: str
    location: str
    date_found: str
    source: str
    url: str

    def dedupe_key(self) -> str:
        norm = lambda s: re.sub(r"\s+", " ", s or "").strip().lower()
        return f"{norm(self.company)}::{norm(self.title)}"

    def id(self) -> str:
        return hashlib.sha1(self.dedupe_key().encode("utf-8")).hexdigest()[:12]


def _get(url: str, **kwargs: Any) -> requests.Response | None:
    try:
        resp = requests.get(url, headers=REQUEST_HEADERS, timeout=REQUEST_TIMEOUT, **kwargs)
        resp.raise_for_status()
        return resp
    except requests.RequestException as exc:
        log.warning("GET failed for %s: %s", url, exc)
        return None


# --------------------------------------------------------------------------
# SOURCE 1 - GitHub repo README diff engine
# --------------------------------------------------------------------------

def _parse_markdown_table_rows(markdown: str) -> list[str]:
    """Return raw markdown table row lines (lines starting with '|')."""
    rows = []
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|") and "---" not in stripped:
            rows.append(stripped)
    return rows


def _row_to_lead(row: str, source_label: str) -> Lead | None:
    """
    Best-effort parse of a single markdown table row from a tracker repo.
    These repos generally use a column order similar to:
    | Company | Role | Location | Application/Link | Age |
    Column order has drifted across repos/years, so we parse defensively:
    take the first cell as company, second as title, look for a markdown
    link [..](url) anywhere in the row for the application URL, and search
    the trailing cells for a location string and an age token like "2d".
    """
    cells = [c.strip() for c in row.strip("|").split("|")]
    if len(cells) < 2:
        return None

    company = re.sub(r"\*\*|\[|\]\(.*?\)", "", cells[0]).strip()
    title = re.sub(r"\*\*", "", cells[1]).strip()
    if not company or not title or not INTERN_KEYWORDS.search(title):
        return None

    link_match = re.search(r"\[.*?\]\((https?://[^\s)]+)\)", row)
    url = link_match.group(1) if link_match else ""

    location = cells[2] if len(cells) > 2 else ""
    location = re.sub(r"\*\*|\[|\]\(.*?\)", "", location).strip() or "Unspecified"

    age_match = AGE_DAY_PATTERN.search(row)
    age_days = int(age_match.group(1)) if age_match else None

    return Lead(
        company=company,
        title=title,
        location=location,
        date_found=datetime.now(timezone.utc).date().isoformat(),
        source=source_label,
        url=url,
    ), age_days


def fetch_github_repo_deltas() -> list[Lead]:
    leads: list[Lead] = []
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    for entry in GITHUB_REPOS:
        repo, branch, path = entry["repo"], entry["branch"], entry["path"]
        raw_url = f"https://raw.githubusercontent.com/{repo}/{branch}/{path}"
        log.info("Fetching GitHub source: %s", raw_url)
        resp = _get(raw_url)
        time.sleep(RATE_LIMIT_SECONDS)
        if resp is None:
            continue

        current_rows = set(_parse_markdown_table_rows(resp.text))
        cache_file = CACHE_DIR / f"{repo.replace('/', '_')}.snapshot.txt"

        previous_rows: set[str] = set()
        if cache_file.exists():
            previous_rows = set(cache_file.read_text(encoding="utf-8").splitlines())

        new_rows = current_rows - previous_rows
        cache_file.write_text("\n".join(sorted(current_rows)), encoding="utf-8")

        # First run ever for this repo: no baseline to diff against, so
        # fall back to the Age column and only keep rows posted recently.
        first_run = not previous_rows
        for row in new_rows:
            parsed = _row_to_lead(row, source_label="GitHub")
            if not parsed:
                continue
            lead, age_days = parsed
            if first_run and age_days is not None and age_days > MAX_AGE_DAYS:
                continue
            leads.append(lead)

        log.info("  -> %d new intern rows from %s", len(new_rows), repo)

    return leads


# --------------------------------------------------------------------------
# SOURCE 2 - Greenhouse public board API
# --------------------------------------------------------------------------

def fetch_greenhouse() -> list[Lead]:
    leads: list[Lead] = []
    for slug in dict.fromkeys(GREENHOUSE_SLUGS):  # dedupe slugs, keep order
        url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=false"
        resp = _get(url)
        time.sleep(RATE_LIMIT_SECONDS)
        if resp is None:
            continue
        try:
            jobs = resp.json().get("jobs", [])
        except ValueError:
            log.warning("Greenhouse response for %s was not valid JSON", slug)
            continue

        for job in jobs:
            title = job.get("title", "")
            if not INTERN_KEYWORDS.search(title):
                continue
            location = (job.get("location") or {}).get("name", "Unspecified")
            leads.append(
                Lead(
                    company=slug.capitalize(),
                    title=title,
                    location=location,
                    date_found=datetime.now(timezone.utc).date().isoformat(),
                    source="Greenhouse",
                    url=job.get("absolute_url", ""),
                )
            )
        log.info("  -> Greenhouse/%s checked", slug)
    return leads


# --------------------------------------------------------------------------
# SOURCE 3 - Lever public posting API
# --------------------------------------------------------------------------

def fetch_lever() -> list[Lead]:
    leads: list[Lead] = []
    for slug in dict.fromkeys(LEVER_SLUGS):
        url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
        resp = _get(url)
        time.sleep(RATE_LIMIT_SECONDS)
        if resp is None:
            continue
        try:
            postings = resp.json()
        except ValueError:
            log.warning("Lever response for %s was not valid JSON", slug)
            continue
        if not isinstance(postings, list):
            continue

        for posting in postings:
            title = posting.get("text", "")
            if not INTERN_KEYWORDS.search(title):
                continue
            categories = posting.get("categories", {}) or {}
            location = categories.get("location", "Unspecified")
            leads.append(
                Lead(
                    company=slug.capitalize(),
                    title=title,
                    location=location,
                    date_found=datetime.now(timezone.utc).date().isoformat(),
                    source="Lever",
                    url=posting.get("hostedUrl", ""),
                )
            )
        log.info("  -> Lever/%s checked", slug)
    return leads


# --------------------------------------------------------------------------
# SOURCE 4 - Levels.fyi (best-effort, public page JSON only)
# --------------------------------------------------------------------------

def fetch_levels_fyi() -> list[Lead]:
    """
    Parses the __NEXT_DATA__ JSON blob that Levels.fyi's own Next.js
    frontend ships inside the public, logged-out internship listing page.
    No private endpoints, no auth, no headless browser. If the site
    redesigns and the blob disappears, this returns an empty list and
    logs a warning rather than raising.
    """
    url = "https://www.levels.fyi/jobs/level/internship"
    resp = _get(url)
    time.sleep(RATE_LIMIT_SECONDS)
    if resp is None:
        return []

    match = re.search(
        r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', resp.text, re.DOTALL
    )
    if not match:
        log.warning("Levels.fyi: __NEXT_DATA__ blob not found, page structure may have changed")
        return []

    try:
        payload = json.loads(match.group(1))
    except ValueError:
        log.warning("Levels.fyi: __NEXT_DATA__ blob was not valid JSON")
        return []

    # The exact path inside __NEXT_DATA__ is site-structure-dependent and
    # may shift between deploys; we search broadly for a list of dicts
    # that look like job postings rather than hardcoding a brittle path.
    job_dicts = _find_job_like_dicts(payload)
    leads: list[Lead] = []
    for job in job_dicts:
        title = job.get("title") or job.get("jobTitle") or ""
        company = job.get("company") or job.get("companyName") or ""
        if not title or not company or not INTERN_KEYWORDS.search(title):
            continue
        leads.append(
            Lead(
                company=company,
                title=title,
                location=job.get("location", "Unspecified"),
                date_found=datetime.now(timezone.utc).date().isoformat(),
                source="Levels.fyi",
                url=job.get("url") or job.get("applyUrl") or url,
            )
        )
    log.info("  -> %d intern postings parsed from Levels.fyi", len(leads))
    return leads


def _find_job_like_dicts(node: Any, depth: int = 0, max_depth: int = 12) -> list[dict]:
    """Walk an arbitrary nested JSON structure looking for dicts that have
    both a title-ish key and a company-ish key, which is a stable enough
    signature even if the surrounding schema changes."""
    found: list[dict] = []
    if depth > max_depth:
        return found
    if isinstance(node, dict):
        keys = {k.lower() for k in node.keys()}
        has_title = any(k in keys for k in ("title", "jobtitle"))
        has_company = any(k in keys for k in ("company", "companyname"))
        if has_title and has_company:
            found.append(node)
        for v in node.values():
            found.extend(_find_job_like_dicts(v, depth + 1, max_depth))
    elif isinstance(node, list):
        for item in node:
            found.extend(_find_job_like_dicts(item, depth + 1, max_depth))
    return found


# --------------------------------------------------------------------------
# SOURCE 5 - Otta / Welcome to the Jungle (best-effort, public page JSON only)
# --------------------------------------------------------------------------

def fetch_otta() -> list[Lead]:
    """
    Same best-effort, logged-out, no-evasion approach as Levels.fyi:
    fetch the public search page and look for an embedded JSON state
    blob. Otta merged into Welcome to the Jungle's platform, so this
    targets their public job search surface. Returns [] quietly if the
    expected blob isn't present.
    """
    url = "https://www.welcometothejungle.com/en/jobs?query=software+intern"
    resp = _get(url)
    time.sleep(RATE_LIMIT_SECONDS)
    if resp is None:
        return []

    # Welcome to the Jungle ships a Next.js __NEXT_DATA__ blob like many
    # modern job boards. Same generic walk-and-find approach as above.
    match = re.search(
        r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', resp.text, re.DOTALL
    )
    if not match:
        log.warning("Otta/WTTJ: __NEXT_DATA__ blob not found, page structure may have changed")
        return []

    try:
        payload = json.loads(match.group(1))
    except ValueError:
        log.warning("Otta/WTTJ: __NEXT_DATA__ blob was not valid JSON")
        return []

    job_dicts = _find_job_like_dicts(payload)
    leads: list[Lead] = []
    for job in job_dicts:
        title = job.get("title") or job.get("jobTitle") or job.get("name") or ""
        company = job.get("company") or job.get("companyName") or job.get("organizationName") or ""
        if not title or not company or not INTERN_KEYWORDS.search(title):
            continue
        leads.append(
            Lead(
                company=company,
                title=title,
                location=job.get("location", "Unspecified"),
                date_found=datetime.now(timezone.utc).date().isoformat(),
                source="Otta",
                url=job.get("url") or url,
            )
        )
    log.info("  -> %d intern postings parsed from Otta/WTTJ", len(leads))
    return leads


# --------------------------------------------------------------------------
# DEDUPE + OUTPUT
# --------------------------------------------------------------------------

def dedupe(leads: Iterable[Lead]) -> list[Lead]:
    seen: dict[str, Lead] = {}
    for lead in leads:
        key = lead.dedupe_key()
        if key not in seen:
            seen[key] = lead
    return list(seen.values())


def write_output(leads: list[Lead]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ordered = sorted(leads, key=lambda l: (l.date_found, l.company.lower()), reverse=True)
    payload = [
        {"id": lead.id(), **asdict(lead)}
        for lead in ordered
    ]
    OUTPUT_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    log.info("Wrote %d leads to %s", len(payload), OUTPUT_PATH)


def main() -> None:
    log.info("Starting daily sourcing run (%s UTC)", datetime.now(timezone.utc).isoformat())
    all_leads: list[Lead] = []

    source_fns = [
        fetch_github_repo_deltas,
        fetch_greenhouse,
        fetch_lever,
        fetch_levels_fyi,
        fetch_otta,
    ]
    for fn in source_fns:
        try:
            result = fn()
            all_leads.extend(result)
        except Exception as exc:  # noqa: BLE001 - a source must never kill the run
            log.error("Source %s raised an unexpected error: %s", fn.__name__, exc)

    deduped = dedupe(all_leads)
    log.info("Total leads before dedupe: %d, after dedupe: %d", len(all_leads), len(deduped))
    write_output(deduped)


if __name__ == "__main__":
    main()
