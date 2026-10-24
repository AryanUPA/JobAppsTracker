"""Match algorithm.

score = role fit + interview likelihood + location + company preference + confidence + urgency  (capped at 100)

Interview likelihood is weighted heavily on purpose: the goal is "roles I can actually get as a
second-year EE", not "most prestigious roles". It rewards fresh postings (apply early), less
saturated employers, class-year-friendly programs, and skill overlap; it penalizes known gaps.

Hard filters (listing is shown as ineligible, never picked): excluded title words (PhD, senior,
new grad, non-engineering…), all-non-US locations, wrong term, inactive/closed, a GPA minimum above
yours, Master's/PhD-only enrollment, or a graduation window that excludes your grad date.
"""
from __future__ import annotations

import datetime as dt
import re

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
SEASON_MONTH = {"spring": 5, "summer": 8, "fall": 12, "winter": 12}

ACTIONABLE = {None, "", "Researching", "Shortlisted", "Preparing"}


def _lc(s):
    return (s or "").lower()


def classify(title: str, cfg: dict):
    t = f" {_lc(title)} "
    best = None
    for cat in cfg["role_categories"]:
        if any(k in t for k in cat["keywords"]):
            if best is None or cat["weight"] > best["weight"]:
                best = cat
    return best


def _grad_month(cfg) -> tuple[int, int]:
    y, m = cfg["candidate"]["grad_month"].split("-")
    return int(y), int(m)


def _ym(month_word: str | None, year: str, default_month: int) -> tuple[int, int]:
    if month_word:
        w = month_word.lower()[:3]
        if w in MONTHS:
            return int(year), MONTHS[w]
        for s, mm in SEASON_MONTH.items():
            if month_word.lower().startswith(s):
                return int(year), mm
    return int(year), default_month


_GRAD_RANGE = re.compile(
    r"graduat\w*(?:\s+date)?[^.\n]{0,40}?(?:between|from|of)?\s*"
    r"(?P<m1>[A-Za-z]+)?\.?\s*(?P<y1>20\d\d)\s*(?:and|to|through|thru|–|—|-)\s*"
    r"(?P<m2>[A-Za-z]+)?\.?\s*(?P<y2>20\d\d)", re.I)
_GPA = re.compile(r"(?:gpa[^0-9\n]{0,25}(\d\.\d{1,2}))|(?:(\d\.\d{1,2})\s*(?:/\s*4\.0\s*)?(?:cumulative\s+)?gpa)", re.I)


def ineligible_reason(l: dict, cfg: dict) -> str | None:
    title = _lc(l.get("title"))
    desc = _lc(l.get("description"))
    if not l.get("active", True):
        return "Posting closed"
    if l.get("deadline"):
        try:
            if dt.date.fromisoformat(str(l["deadline"])[:10]) < dt.date.today():
                return "Deadline passed"
        except ValueError:
            pass
    allow = any(a in title for a in cfg.get("allow_if_also_contains", []))
    for k in cfg["exclude_title_keywords"]:
        if k in f" {title} " and not allow:
            return f"Title contains '{k.strip()}'"
    if not classify(l.get("title", ""), cfg):
        return "Not a target role type"
    locs = [_lc(x) for x in l.get("locations", []) if x]
    if locs and all(any(n in loc for n in cfg["non_us_keywords"]) for loc in locs):
        return "Outside the US"
    terms = [_lc(t) for t in l.get("terms", [])]
    if cfg.get("require_summer_2027") and terms:
        if not any("summer" in t and "2027" in t for t in terms):
            return "Not a Summer 2027 term (" + ", ".join(l.get("terms", [])) + ")"
    if re.search(r"\b20(2[0-6])\b", title) and "2027" not in title:
        return "Older posting year in title"
    for m in _GPA.finditer(desc):
        val = float(m.group(1) or m.group(2))
        if 2.0 <= val <= 4.0 and val > cfg["gpa_exclude_if_required_above"]:
            ctx = desc[max(0, m.start() - 60): m.end() + 20]
            if any(w in ctx for w in ("minimum", "at least", "or higher", "or above", "required", "must", "maintaining")):
                return f"Requires GPA ≥ {val}"
    if re.search(r"(enrolled|pursuing)[^.\n]{0,40}(master|ph\.?d)", desc) and not re.search(r"bachelor|undergrad|b\.?s\.?\b", desc):
        return "Master's/PhD students only"
    gy, gm = _grad_month(cfg)
    for m in _GRAD_RANGE.finditer(l.get("description", "") or ""):
        start = _ym(m.group("m1"), m.group("y1"), 1)
        end = _ym(m.group("m2"), m.group("y2"), 12)
        if start <= end and not (start <= (gy, gm) <= end):
            return f"Graduation window {m.group(0)[-40:].strip()} excludes {gm}/{gy}"
    return None


def _days_since(iso: str | None) -> int | None:
    if not iso:
        return None
    try:
        return (dt.date.today() - dt.date.fromisoformat(iso[:10])).days
    except ValueError:
        return None


def _company_match(company: str, names: list[str]) -> bool:
    c = _lc(company)
    return any(n in c for n in names)


def score(l: dict, cfg: dict) -> tuple[int, list[dict]]:
    parts = []
    title = l.get("title", "")
    t = f" {_lc(title)} "
    text = f" {_lc(title)} {_lc(l.get('description'))} "

    cat = classify(title, cfg)
    if cat:
        parts.append({"label": "Role fit", "points": cat["weight"], "detail": cat["name"]})

    days = _days_since(l.get("date_posted"))
    rec = 0
    if days is not None:
        for max_days, pts in cfg["recency_points"]:
            if days <= max_days:
                rec = pts
                break
        parts.append({"label": "Freshness", "points": rec,
                      "detail": "posted today" if days == 0 else f"posted {days}d ago"})
    else:
        parts.append({"label": "Freshness", "points": 3, "detail": "post date unknown"})

    comp = cfg["competitiveness"]
    cpts = comp["default_points"]
    for tier in comp["tiers"]:
        if _company_match(l.get("company", ""), tier["companies"]):
            cpts = tier["points"]
            break
    parts.append({"label": "Odds at this employer", "points": cpts,
                  "detail": {2: "very competitive", 5: "competitive"}.get(cpts, "less saturated")})

    friendly = sum(p for k, p in cfg["positive_title_keywords"].items() if k in t)
    if friendly:
        parts.append({"label": "Class-year friendly", "points": min(friendly, 8), "detail": "program targets undergrads/early career"})

    have = [k.strip() for k, p in cfg["skills"]["have"].items() if k in text]
    hp = min(sum(cfg["skills"]["have"][k] for k in cfg["skills"]["have"] if k in text), 10)
    if hp:
        parts.append({"label": "Skill overlap", "points": hp, "detail": ", ".join(have[:6])})
    gaps = [k.strip() for k in cfg["skills"]["gaps"] if k in text]
    gp = max(sum(cfg["skills"]["gaps"][k] for k in cfg["skills"]["gaps"] if k in text), -10)
    if gp:
        parts.append({"label": "Skill gaps", "points": gp, "detail": ", ".join(gaps)})

    best = (0, "")
    locs = [_lc(x) for x in l.get("locations", [])] or [""]
    for loc in locs:
        for rule in cfg["locations"]:
            if (not rule["keywords"] and loc) or any(k in loc for k in rule["keywords"]):
                if rule["points"] > best[0]:
                    best = (rule["points"], rule["label"])
                break
    if best[0]:
        parts.append({"label": "Location", "points": best[0], "detail": best[1]})

    if _company_match(l.get("company", ""), cfg["company_preference"]["companies"]):
        parts.append({"label": "Target company", "points": cfg["company_preference"]["points"], "detail": l.get("company", "")})

    conf = 0
    detail = []
    if l.get("source_kind") == "official":
        conf += 3; detail.append("on employer's site")
    if any("summer" in _lc(x) and "2027" in x for x in l.get("terms", [])):
        conf += 2; detail.append("Summer 2027 confirmed")
    if conf:
        parts.append({"label": "Confidence", "points": conf, "detail": ", ".join(detail)})

    if l.get("deadline"):
        dleft = -(_days_since(str(l["deadline"])) or 0)
        if 0 <= dleft <= 10:
            parts.append({"label": "Deadline soon", "points": 4, "detail": f"{dleft} days left"})

    total = max(0, min(100, sum(p["points"] for p in parts)))
    return total, parts


def _applied_within(tracker: dict, listings_by_id: dict, company: str, days: int) -> int:
    n = 0
    for lid, t in tracker.items():
        l = listings_by_id.get(lid)
        if not l or _lc(l.get("company")) != _lc(company):
            continue
        d = t.get("date_applied")
        if d and _days_since(d) is not None and _days_since(d) <= days:
            n += 1
    return n


def pick_daily(listings: list[dict], tracker: dict, cfg: dict, today: dt.date | None = None) -> list[dict]:
    today = today or dt.date.today()
    by_id = {l["id"]: l for l in listings}
    picks, companies = [], set()
    pool = sorted((l for l in listings if not l.get("ineligible")), key=lambda x: (-x["score"], x["id"]))
    for l in pool:
        t = tracker.get(l["id"], {})
        if t.get("status") not in ACTIONABLE:
            continue
        if t.get("snooze_until") and t["snooze_until"] >= today.isoformat():
            continue
        co = _lc(l["company"])
        if co in companies:
            continue
        lim = cfg.get("rate_limits", {}).get(co)
        if lim and _applied_within(tracker, by_id, l["company"], lim["days"]) >= lim["max"]:
            continue
        companies.add(co)
        picks.append(l["id"])
        if len(picks) >= cfg.get("daily_picks", 3):
            break
    return picks
