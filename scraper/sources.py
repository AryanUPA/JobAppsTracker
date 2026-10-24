"""Job sources. Every fetcher returns a list of normalized listing dicts and raises on failure;
run.py catches failures per source so one broken site never stops the daily refresh.

Normalized listing:
  id, company, title, locations[list], url, source, source_kind ("official" | "aggregator"),
  date_posted (ISO date or None), terms[list], active(bool), description(str, may be "")
"""
from __future__ import annotations

import datetime as dt
import hashlib
import html
import re
import time

import requests

UA = {"User-Agent": "JobAppsTracker/1.0 (personal internship tracker; daily refresh)"}
TIMEOUT = 30


def make_id(company: str, title: str, url: str) -> str:
    key = f"{company.strip().lower()}|{title.strip().lower()}|{normalize_url(url)}"
    return hashlib.sha1(key.encode()).hexdigest()[:12]


def normalize_url(url: str) -> str:
    if not url:
        return ""
    url = url.strip()
    url = re.sub(r"[?&](utm_[^=&]+|ref|source|gh_src|src)=[^&]*", "", url)
    return url.rstrip("/?&").lower()


def strip_html(s: str) -> str:
    if not s:
        return ""
    s = html.unescape(html.unescape(s))
    s = re.sub(r"<(br|/p|/li|/h\d)[^>]*>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"[ \t]+", " ", s).strip()


def terms_from_text(text: str) -> list[str]:
    found = []
    for season in ("Spring", "Summer", "Fall", "Winter"):
        for m in re.finditer(rf"{season}\s*(?:/\s*\w+\s*)?(20\d\d)", text, flags=re.I):
            found.append(f"{season} {m.group(1)}")
    return sorted(set(found))


def _iso_from_epoch(v) -> str | None:
    try:
        v = float(v)
        if v > 1e12:  # milliseconds
            v /= 1000
        return dt.datetime.fromtimestamp(v, dt.timezone.utc).date().isoformat()
    except (TypeError, ValueError):
        return None


def _get(url, **kw):
    r = requests.get(url, headers=UA, timeout=TIMEOUT, **kw)
    r.raise_for_status()
    return r


# ---------------------------------------------------------------- Simplify (aggregator)
def parse_simplify_listings(data: list) -> list[dict]:
    out = []
    for j in data:
        if not isinstance(j, dict):
            continue
        if j.get("is_visible") is False:
            continue
        company = j.get("company_name") or j.get("company") or ""
        title = j.get("title") or ""
        url = j.get("url") or ""
        if not (company and title and url):
            continue
        locs = j.get("locations") or []
        if isinstance(locs, str):
            locs = [locs]
        terms = j.get("terms") or j.get("season") or []
        if isinstance(terms, str):
            terms = [terms]
        out.append({
            "id": make_id(company, title, url),
            "company": company.strip(),
            "title": title.strip(),
            "locations": [str(x).strip() for x in locs],
            "url": url,
            "source": "SimplifyJobs (GitHub)",
            "source_kind": "aggregator",
            "date_posted": _iso_from_epoch(j.get("date_posted")),
            "terms": terms or terms_from_text(title),
            "active": bool(j.get("active", True)),
            "description": "",
            "sponsorship": j.get("sponsorship") or "",
        })
    return out


_ROW = re.compile(r"<tr>(.*?)</tr>", re.S | re.I)
_CELL = re.compile(r"<td[^>]*>(.*?)</td>", re.S | re.I)
_HREF = re.compile(r'href="([^"]+)"', re.I)


def parse_simplify_readme(md: str) -> list[dict]:
    """Fallback: parse the README's HTML tables (Company | Role | Location | Application | Age)."""
    out, last_company = [], ""
    for row in _ROW.findall(md):
        cells = _CELL.findall(row)
        if len(cells) < 4:
            continue
        company = strip_html(cells[0]).replace("🔥", "").strip()
        if company in ("↳", ""):
            company = last_company
        last_company = company
        title = strip_html(cells[1])
        loc = strip_html(cells[2].replace("</br>", ", ").replace("<br>", ", "))
        links = [h for h in _HREF.findall(cells[3]) if "simplify.jobs" not in h]
        if not links or "🔒" in cells[3]:
            continue
        age = strip_html(cells[4]) if len(cells) > 4 else ""
        posted = None
        m = re.match(r"(\d+)\s*d", age)
        if m:
            posted = (dt.date.today() - dt.timedelta(days=int(m.group(1)))).isoformat()
        out.append({
            "id": make_id(company, title, links[0]), "company": company, "title": title,
            "locations": [x.strip() for x in loc.split(",") if x.strip()], "url": links[0],
            "source": "SimplifyJobs (GitHub README)", "source_kind": "aggregator",
            "date_posted": posted, "terms": ["Summer 2027"], "active": True, "description": "",
        })
    return out


def fetch_simplify(cfg: dict) -> list[dict]:
    errors = []
    for url in cfg.get("listings_urls", []):
        try:
            return parse_simplify_listings(_get(url).json())
        except Exception as e:  # try next / fallback
            errors.append(f"{url}: {e}")
    for url in cfg.get("readme_fallback_urls", []):
        try:
            rows = parse_simplify_readme(_get(url).text)
            if rows:
                return rows
        except Exception as e:
            errors.append(f"{url}: {e}")
    raise RuntimeError("; ".join(errors) or "no Simplify URLs configured")


# ---------------------------------------------------------------- Greenhouse (official)
def parse_greenhouse(company: str, data: dict) -> list[dict]:
    out = []
    for j in data.get("jobs", []):
        title = j.get("title", "")
        if "intern" not in title.lower() and "co-op" not in title.lower():
            continue
        desc = strip_html(j.get("content", ""))
        loc = (j.get("location") or {}).get("name", "")
        url = j.get("absolute_url", "")
        out.append({
            "id": make_id(company, title, url), "company": company, "title": title.strip(),
            "locations": [x.strip() for x in re.split(r";|\|", loc) if x.strip()], "url": url,
            "source": f"{company} careers (Greenhouse)", "source_kind": "official",
            "date_posted": (j.get("first_published") or j.get("updated_at") or "")[:10] or None,
            "terms": terms_from_text(title + " " + desc[:3000]), "active": True,
            "description": desc[:6000], "job_id": str(j.get("id", "")),
            "deadline": (j.get("application_deadline") or None),
        })
    return out


def fetch_greenhouse(entry: dict) -> list[dict]:
    url = f"https://boards-api.greenhouse.io/v1/boards/{entry['board']}/jobs?content=true"
    return parse_greenhouse(entry["company"], _get(url).json())


# ---------------------------------------------------------------- Lever (official)
def parse_lever(company: str, data: list) -> list[dict]:
    out = []
    for j in data:
        title = j.get("text", "")
        if "intern" not in title.lower():
            continue
        cats = j.get("categories") or {}
        url = j.get("hostedUrl", "")
        desc = (j.get("descriptionPlain") or "") + "\n" + "\n".join(
            strip_html(l.get("content", "")) for l in j.get("lists", []))
        locs = cats.get("allLocations") or [cats.get("location", "")]
        out.append({
            "id": make_id(company, title, url), "company": company, "title": title.strip(),
            "locations": [x for x in locs if x], "url": url,
            "source": f"{company} careers (Lever)", "source_kind": "official",
            "date_posted": _iso_from_epoch(j.get("createdAt")),
            "terms": terms_from_text(title + " " + desc[:3000]), "active": True,
            "description": desc[:6000], "job_id": j.get("id", ""),
        })
    return out


def fetch_lever(entry: dict) -> list[dict]:
    return parse_lever(entry["company"], _get(f"https://api.lever.co/v0/postings/{entry['slug']}?mode=json").json())


# ---------------------------------------------------------------- Workday (official)
def workday_posted_to_iso(s: str) -> str | None:
    s = (s or "").lower()
    today = dt.date.today()
    if "today" in s:
        return today.isoformat()
    if "yesterday" in s:
        return (today - dt.timedelta(days=1)).isoformat()
    m = re.search(r"(\d+)\+?\s*day", s)
    if m:
        return (today - dt.timedelta(days=int(m.group(1)))).isoformat()
    return None


def fetch_workday(entry: dict, search_terms: list[str], max_details: int) -> list[dict]:
    host, tenant, site = entry["host"], entry["tenant"], entry["site"]
    api = f"https://{host}/wday/cxs/{tenant}/{site}"
    seen, out = set(), []
    for term in search_terms:
        offset = 0
        while offset < 200:
            r = requests.post(f"{api}/jobs", json={"appliedFacets": {}, "limit": 20, "offset": offset, "searchText": term},
                              headers={**UA, "Content-Type": "application/json", "Accept": "application/json"}, timeout=TIMEOUT)
            r.raise_for_status()
            posts = r.json().get("jobPostings", [])
            if not posts:
                break
            for p in posts:
                path = p.get("externalPath", "")
                if path in seen or "intern" not in p.get("title", "").lower():
                    continue
                seen.add(path)
                out.append({"_path": path, "title": p.get("title", ""), "loc": p.get("locationsText", ""),
                            "posted": workday_posted_to_iso(p.get("postedOn", "")),
                            "job_id": ", ".join(p.get("bulletFields") or [])})
            offset += 20
            time.sleep(0.4)
    listings = []
    for i, p in enumerate(out):
        url = f"https://{host}/en-US/{site}{p['_path']}"
        desc, locs = "", [p["loc"]]
        if i < max_details:
            try:
                info = requests.get(f"{api}{p['_path']}", headers={**UA, "Accept": "application/json"},
                                    timeout=TIMEOUT).json().get("jobPostingInfo", {})
                desc = strip_html(info.get("jobDescription", ""))
                extra = info.get("additionalLocations") or []
                locs = [info.get("location") or p["loc"], *extra]
                p["job_id"] = info.get("jobReqId") or p["job_id"]
                time.sleep(0.3)
            except Exception:
                pass
        listings.append({
            "id": make_id(entry["company"], p["title"], url), "company": entry["company"], "title": p["title"].strip(),
            "locations": [x for x in locs if x], "url": url,
            "source": f"{entry['company']} careers (Workday)", "source_kind": "official",
            "date_posted": p["posted"], "terms": terms_from_text(p["title"] + " " + desc[:3000]),
            "active": True, "description": desc[:6000], "job_id": p["job_id"],
        })
    return listings
