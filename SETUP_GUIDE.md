# Setting Up the Daily Sourcing Engine + GitHub Pages

I don't have a GitHub connection, so I can't create the repo or push code for you — but everything below is ready to push as-is. This is a one-time setup; once it's live, the daily sync runs itself.

## 1. Repo layout

Create a new GitHub repo (public or private, either works with GitHub Pages on a personal account) and arrange the files exactly like this:

```
your-repo/
├── index.html                      <- the dashboard (already built)
├── fetch_all_opportunities.py       <- the sourcing engine
├── sync_tracker_state.py            <- merges "Sync to Cloud" issues into tracker_state.json
├── requirements.txt
├── data/
│   ├── daily_leads.json            <- seed file, gets overwritten nightly
│   └── tracker_state.json          <- Applied/Discarded state, merged on each sync
└── .github/
    └── workflows/
        ├── daily_job_sync.yml
        └── sync_tracker_state.yml
```

Two files I generated need to be moved into `.github/workflows/` — `daily_job_sync.yml` and `sync_tracker_state.yml` (GitHub only picks up workflows from that exact path). `sync_tracker_state.py` stays at the repo root, next to `fetch_all_opportunities.py`.

## 2. Push it

```bash
cd your-repo
git init
git add .
git commit -m "Career Cockpit: dashboard + daily sourcing engine"
git branch -M main
git remote add origin https://github.com/<your-username>/<your-repo>.git
git push -u origin main
```

## 3. Turn on GitHub Pages

Repo → **Settings → Pages** → under "Build and deployment," set Source to **Deploy from a branch**, branch **main**, folder **/ (root)** → Save. GitHub will give you a `https://<username>.github.io/<repo>/` URL within a minute or two. Bookmark that — it's the live dashboard.

## 4. Let the workflow push commits

Repo → **Settings → Actions → General** → scroll to "Workflow permissions" → select **Read and write permissions** → Save. Without this, the daily sync will run but fail to commit the updated `daily_leads.json` back to the repo.

## 5. Test it manually before waiting for 5 PM

Repo → **Actions** tab → **Daily Job Sync** (left sidebar) → **Run workflow** button → Run. Watch it go green. Check the repo's `data/daily_leads.json` — it should now have today's real leads instead of the seed sample I shipped. Refresh the live Pages URL and open the **Opportunities & Tracker** tab.

## What happens automatically after that

Every day at 11:00 PM UTC (5:00 PM CST / 6:00 PM CDT during daylight saving — see the comment in the workflow file if you want it pinned to exactly 5pm year-round), the workflow: fetches new leads, commits the updated JSON, and GitHub Pages redeploys on its own within a minute or two of that push. No server, no database, nothing to maintain day-to-day.

## 6. Turn on the Opportunities & Tracker Hub's "Sync to Cloud" button

The Hub tracks Applied/Discarded leads instantly in your browser's local storage — that part works with zero setup. But local storage disappears if you clear your browser cache, so there's a "Sync to Cloud" button that permanently commits your tracked statuses to `data/tracker_state.json`. It's disabled until you do two things:

**a. Create the `tracker-sync` label.** Repo → **Issues** tab → **Labels** → **New label** → name it exactly `tracker-sync` (any color) → Create. This label is also a security gate: GitHub only lets people with write access to the repo attach a label through a URL, so a random visitor can't trigger the sync workflow just by opening an issue.

**b. Point the dashboard at your repo.** Open `index.html`, search for `GITHUB_REPO`, and fill in your username and repo name:

```js
const GITHUB_REPO = {
  owner: "YOUR_GITHUB_USERNAME",
  repo: "YOUR_REPO_NAME"
};
```

Save and push. The button lights up next time you load the dashboard.

**How it works, and why no token is ever in the JavaScript:** clicking "Sync to Cloud" opens a pre-filled GitHub issue (in a new tab) containing your changed statuses as a small JSON block, using your browser's own already-logged-in GitHub session — not a credential embedded in the page. That issue gets the `tracker-sync` label automatically from the URL. The `sync_tracker_state.yml` workflow watches for issues with that label, hands the body to `sync_tracker_state.py`, which validates the JSON, merges it into `data/tracker_state.json` (last-write-wins by timestamp, so it's safe to sync from multiple devices), and the workflow commits the result using its own auto-generated `GITHUB_TOKEN` — a credential that only ever exists inside GitHub's servers for the duration of that run. The workflow then comments on the issue and closes it automatically. Nothing you can extract from the page's source ever lets someone write to your repo.

## Customizing sources

Open `fetch_all_opportunities.py` and edit the lists near the top:
- `GITHUB_REPOS` — internship tracker repos to diff (defaults to `vanshb03/Summer2027-Internships` and `SimplifyJobs/Summer2026-Internships`)
- `GREENHOUSE_SLUGS` / `LEVER_SLUGS` — company board slugs (the slug is the bit in the company's careers URL, e.g. `boards.greenhouse.io/stripe` → `stripe`)

## Known limitations, honestly

- **Workday was intentionally left out.** The original spec asked for a Cloudflare bypass via `playwright-stealth` to scrape enterprise Workday portals (AMD, TI, Apple, NVIDIA). That's deliberately evading a security control those companies put up, so I didn't build it. If you want AMD/TI postings, the GitHub tracker repos and Greenhouse/Lever sources already pick up a lot of what gets posted publicly, and Greenhouse/Lever slugs can be added for any company that uses those ATSs.
- **Levels.fyi and Otta are best-effort.** Both sources work by parsing the JSON blob the page itself ships to a logged-out browser — no login, no anti-bot evasion. If either site redesigns its frontend, that one source will quietly return zero results (logged as a warning) rather than breaking the whole run, but it'll need a small parser update to start working again.
- **"Zero maintenance" is the goal, not a hard guarantee** — GitHub/Greenhouse/Lever are stable, documented APIs unlikely to need touching; the other two are inherently a bit more fragile because they're not official APIs.
