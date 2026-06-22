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
6. Simplify.jobs                - structured listings.json feed (the same
                                   public, machine-readable data that backs
                                   Simplify's own internship board), filtered
                                   to Software/Hardware/Data Science/Systems.

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
    # SimplifyJobs/Summer2026-Internships is intentionally NOT diffed here as
    # raw markdown. That repo publishes a structured listings.json sidecar
    # file - the same data backing Simplify.jobs' own board - which gives
    # clean company/title/url/date fields directly. See fetch_simplify_jobs()
    # below; dedupe() still protects against any overlap either way.
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

# --------------------------------------------------------------------------
# Location/country standardization - feeds the dashboard's Country filter.
# Best-effort: these sources are overwhelmingly US tech-internship boards,
# so an unmatched token falls back to "United States" rather than leaving
# a dead "Unknown" bucket in the filter dropdown.
# --------------------------------------------------------------------------

US_STATE_ABBREVIATIONS = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID",
    "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS",
    "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK",
    "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV",
    "WI", "WY", "DC",
}

US_STATE_NAMES = {
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
    "connecticut", "delaware", "florida", "georgia", "hawaii", "idaho",
    "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana",
    "maine", "maryland", "massachusetts", "michigan", "minnesota",
    "mississippi", "missouri", "montana", "nebraska", "nevada",
    "new hampshire", "new jersey", "new mexico", "new york",
    "north carolina", "north dakota", "ohio", "oklahoma", "oregon",
    "pennsylvania", "rhode island", "south carolina", "south dakota",
    "tennessee", "texas", "utah", "vermont", "virginia", "washington",
    "west virginia", "wisconsin", "wyoming", "district of columbia",
}

COUNTRY_ALIASES = {
    "usa": "United States", "us": "United States", "u.s.": "United States",
    "u.s.a.": "United States", "united states": "United States",
    "united states of america": "United States",
    "uk": "United Kingdom", "u.k.": "United Kingdom",
    "united kingdom": "United Kingdom", "england": "United Kingdom",
    "scotland": "United Kingdom", "wales": "United Kingdom",
    "india": "India", "canada": "Canada", "germany": "Germany",
    "france": "France", "ireland": "Ireland", "singapore": "Singapore",
    "australia": "Australia", "china": "China", "japan": "Japan",
    "netherlands": "Netherlands", "israel": "Israel",
    "switzerland": "Switzerland", "spain": "Spain", "italy": "Italy",
    "sweden": "Sweden", "poland": "Poland", "brazil": "Brazil",
    "mexico": "Mexico", "south korea": "South Korea", "korea": "South Korea",
}


def standardize_location(raw_location: str) -> tuple[str, str]:
    """Returns (cleaned_location, country) derived from a free-text location
    string like 'Austin, TX', 'Remote', or 'London, UK'."""
    loc = (raw_location or "").strip()
    if not loc or loc.lower() in ("unspecified", "n/a", "tbd", "-"):
        return (loc or "Unspecified"), "Unspecified"

    if loc.lower() == "remote":
        return "Remote", "Remote"

    # Some tracker rows list multiple offices separated by ';' - use the first.
    first = re.split(r"\s*;\s*", loc)[0].strip()
    parts = [p.strip() for p in first.split(",")]
    last = parts[-1] if parts else ""
    last_lower = last.lower()

    if last.upper() in US_STATE_ABBREVIATIONS or last_lower in US_STATE_NAMES:
        return first, "United States"

    if last_lower in COUNTRY_ALIASES:
        return first, COUNTRY_ALIASES[last_lower]

    if "remote" in loc.lower() and "us" in loc.lower():
        return first, "United States"

    # No confident match - default to United States rather than "Unknown".
    return first, "United States"

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
    country: str
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
    location, country = standardize_location(location)

    age_match = AGE_DAY_PATTERN.search(row)
    age_days = int(age_match.group(1)) if age_match else None

    return Lead(
        company=company,
        title=title,
        location=location,
        country=country,
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
            location, country = standardize_location(location)
            leads.append(
                Lead(
                    company=slug.capitalize(),
                    title=title,
                    location=location,
                    country=country,
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
            location, country = standardize_location(location)
            leads.append(
                Lead(
                    company=slug.capitalize(),
                    title=title,
                    location=location,
                    country=country,
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
        location, country = standardize_location(job.get("location", "Unspecified"))
        leads.append(
            Lead(
                company=company,
                title=title,
                location=location,
                country=country,
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
        location, country = standardize_location(job.get("location", "Unspecified"))
        leads.append(
            Lead(
                company=company,
                title=title,
                location=location,
                country=country,
                date_found=datetime.now(timezone.utc).date().isoformat(),
                source="Otta",
                url=job.get("url") or url,
            )
        )
    log.info("  -> %d intern postings parsed from Otta/WTTJ", len(leads))
    return leads


# --------------------------------------------------------------------------
# SOURCE 6 - Simplify.jobs structured listings feed
# --------------------------------------------------------------------------
#
# Simplify.jobs doesn't publish a documented public REST API. What IS public
# is the structured JSON file the SimplifyJobs/Summer2026-Internships repo
# ships alongside its README - the same machine-readable data that drives
# Simplify's own internship board. Pulling that file directly gives clean
# company/title/url/date fields with no markdown-table scraping involved.

SIMPLIFY_LISTINGS_URL = (
    "https://raw.githubusercontent.com/SimplifyJobs/Summer2026-Internships"
    "/dev/.github/scripts/listings.json"
)

# Per spec: only keep postings that map cleanly onto one of these four
# categories. Keyword-based, since the feed's own category labels (if any)
# vary in naming across schema revisions - this is the same defensive,
# "match a stable-enough signature" approach used by _find_job_like_dicts
# for the other best-effort sources above.
SIMPLIFY_CATEGORY_KEYWORDS: dict[str, re.Pattern] = {
    "Software Engineering": re.compile(
        r"software|full[\s-]?stack|front[\s-]?end|back[\s-]?end|\bweb\b|mobile|\bios\b|android|\bsde\b|\bswe\b",
        re.IGNORECASE,
    ),
    "Hardware Engineering": re.compile(
        r"hardware|embedded|firmware|electrical|\basic\b|\bfpga\b|\bpcb\b|mechanical|silicon|\bchip\b",
        re.IGNORECASE,
    ),
    "Data Science": re.compile(
        r"data scien|machine learning|\bml\b|\bai\b|analytics|data engineer|applied scientist|research scientist",
        re.IGNORECASE,
    ),
    "Systems": re.compile(
        r"systems engineer|infrastructure|platform engineer|site reliability|\bsre\b|devops|distributed systems|cloud engineer|network engineer",
        re.IGNORECASE,
    ),
}


def _classify_simplify_category(title: str) -> str | None:
    """Maps a job title onto one of the four categories this source is
    scoped to. Returns None (caller skips the row) for everything else -
    e.g. product, design, or finance internships that also live in this
    feed but fall outside what was asked for."""
    for category, pattern in SIMPLIFY_CATEGORY_KEYWORDS.items():
        if pattern.search(title or ""):
            return category
    return None


def _simplify_date_added(job: dict) -> str:
    """'Date Added' has shown up in a few different shapes across this
    feed's schema revisions (unix seconds, unix milliseconds, ISO string).
    Try each; fall back to today (UTC) so a schema drift never crashes the
    run - same philosophy as the rest of this file's best-effort sources."""
    raw = (
        job.get("date_posted")
        or job.get("date_added")
        or job.get("datePosted")
        or job.get("createdAt")
    )
    if isinstance(raw, (int, float)):
        try:
            ts = raw / 1000 if raw > 10_000_000_000 else raw
            return datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()
        except (ValueError, OSError, OverflowError):
            pass
    if isinstance(raw, str):
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).date().isoformat()
        except ValueError:
            pass
    return datetime.now(timezone.utc).date().isoformat()


def fetch_simplify_jobs() -> list[Lead]:
    """
    Source 6: Simplify.jobs structured internship feed.

    Filtered to 'position: Internship' (via INTERN_KEYWORDS, same gate every
    other source uses) and to postings that map onto Software Engineering,
    Hardware Engineering, Data Science, or Systems. Rows explicitly marked
    closed (active/is_visible == False) are skipped.
    """
    resp = _get(SIMPLIFY_LISTINGS_URL)
    time.sleep(RATE_LIMIT_SECONDS)
    if resp is None:
        return []

    try:
        listings = resp.json()
    except ValueError:
        log.warning("Simplify.jobs: listings.json was not valid JSON")
        return []

    if not isinstance(listings, list):
        log.warning(
            "Simplify.jobs: listings.json was not a list (got %s) - schema may have changed",
            type(listings).__name__,
        )
        return []

    leads: list[Lead] = []
    for job in listings:
        if not isinstance(job, dict):
            continue

        # "active"/"is_visible" reflect whether Simplify still considers
        # the posting live - skip anything explicitly marked closed.
        if job.get("active") is False or job.get("is_visible") is False:
            continue

        title = (job.get("title") or "").strip()
        company = (job.get("company_name") or job.get("company") or "").strip()
        if not title or not company or not INTERN_KEYWORDS.search(title):
            continue  # not an internship posting - "position: Internship" gate

        if _classify_simplify_category(title) is None:
            continue  # outside Software/Hardware/Data Science/Systems - skip

        locations = job.get("locations")
        if isinstance(locations, list) and locations:
            raw_location = locations[0]
        elif isinstance(locations, str) and locations:
            raw_location = locations
        else:
            raw_location = job.get("location", "Unspecified")
        location, country = standardize_location(raw_location)

        url = job.get("url") or job.get("company_url") or job.get("apply_url") or ""
        if not url and job.get("id"):
            url = f"https://simplify.jobs/p/{job['id']}"

        leads.append(
            Lead(
                company=company,
                title=title,
                location=location,
                country=country,
                date_found=_simplify_date_added(job),
                source="Simplify",
                url=url,
            )
        )

    log.info("  -> %d intern postings parsed from Simplify.jobs", len(leads))
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
    leads_payload = [
        {"id": lead.id(), **asdict(lead)}
        for lead in ordered
    ]
    # Stamped right before the write so it reflects exactly when this run
    # finished. GitHub Actions runners are always UTC - the dashboard knows
    # to treat this as UTC and converts it to each viewer's local time.
    last_updated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    payload = {"last_updated": last_updated, "leads": leads_payload}
    OUTPUT_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    log.info(
        "Wrote %d leads to %s (last_updated=%s UTC)",
        len(leads_payload), OUTPUT_PATH, last_updated,
    )


def main() -> None:
    log.info("Starting daily sourcing run (%s UTC)", datetime.now(timezone.utc).isoformat())
    all_leads: list[Lead] = []

    source_fns = [
        fetch_github_repo_deltas,
        fetch_greenhouse,
        fetch_lever,
        fetch_levels_fyi,
        fetch_otta,
        fetch_simplify_jobs,
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
