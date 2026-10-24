# Internship Radar

A personal Summer 2027 internship tracker for Aryan Upadhyaya (B.S. EE, SJSU, May 2028).

Every morning a GitHub Action scrapes job sources, merges them with yesterday's data, scores every
role for **odds of actually getting an interview**, picks the three best applications for the day,
and redeploys the site to GitHub Pages.

## What's in the site

| Tab | What it does |
|---|---|
| **Today** | The 3 best applications to fill out today, with the score breakdown behind each pick, plus "up next". Start / Applied / Snooze buttons update the picks immediately. |
| **Listings** | Every role found, searchable and filterable. Ineligible roles stay visible with the reason. |
| **Tracker** | Board view of your pipeline (to apply → applied → interviewing → closed), follow-up reminders, export/import. |
| **Sources** | Which sources worked today, how scoring works, optional GitHub sync. |

## One-time setup

1. Push this repo to `main`.
2. **Settings → Pages → Build and deployment → Source: GitHub Actions.**
3. **Actions tab → Daily refresh → Run workflow** to do the first live scrape.
   The site appears at `https://aryanupa.github.io/JobAppsTracker/`.
4. Optional, to sync tracker edits across devices: create a
   [fine-grained token](https://github.com/settings/personal-access-tokens/new) for *only this repo*
   with **Contents: Read and write**, and paste it in **Sources → Sync with GitHub**. It is stored only in
   your browser. Without it, edits live in that browser; use **Export tracker.json** and commit it to
   `site/data/` to make the daily job aware of what you've applied to.

## How the daily picks work

`score = role fit (≤30) + interview odds (≤~40) + location (≤10) + target company (5) + confidence (≤5) + deadline soon (4)`, capped at 100.

- **Interview odds**: fresh postings score highest (apply early), less-saturated employers beat mega-caps,
  sophomore/undergrad programs get a boost, skills you can back up add points, known gaps
  (SystemVerilog/UVM, C++, Rust…) subtract.
- **Hard filters**: PhD/MS-only, non-US, not Summer 2027, closed or past deadline, GPA minimum above 3.39,
  graduation windows that exclude May 2028, non-engineering roles.
- **Picks**: highest scores first, one company per day, skip anything applied/snoozed/closed, and respect
  per-company limits (Zipline: 3 applications per 30 days).

Tune everything in [`config/profile.json`](config/profile.json). Add companies in
[`config/sources.json`](config/sources.json) — Greenhouse boards, Lever slugs and Workday sites are supported;
the SimplifyJobs Summer 2027 list provides broad coverage. A source that fails is skipped for the day and
shown on the Sources tab; the rest still update.

Hand-researched roles live in [`scraper/seed.json`](scraper/seed.json). Roles found on aggregators are
labelled "Via …" — confirm them on the employer's own site before applying.

## Run locally

```bash
pip install -r requirements.txt
python -m scraper.run            # live scrape → site/data/*.json
python -m scraper.run --offline  # rescore existing data without network
python -m pytest -q tests
python -m http.server -d site 8000   # open http://localhost:8000
```
