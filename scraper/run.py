"""Daily refresh: fetch all sources, merge with history + curated seed, score, pick today's top 3.

Usage:  python -m scraper.run            (writes site/data/*.json)
        python -m scraper.run --offline  (skip network; rescore existing data + seed)
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import re
import sys
import traceback

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scraper import sources, scoring  # noqa: E402

DATA = ROOT / "site" / "data"
CFG = ROOT / "config"


def load(path, default):
    try:
        return json.loads(pathlib.Path(path).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save(path, obj):
    pathlib.Path(path).parent.mkdir(parents=True, exist_ok=True)
    pathlib.Path(path).write_text(json.dumps(obj, indent=1, ensure_ascii=False) + "\n")


def dedupe_key(l):
    title = re.sub(r"[^a-z0-9]", "", l["title"].lower())
    return (l["company"].strip().lower(), title)


def fetch_all(src_cfg, profile) -> tuple[list[dict], list[dict]]:
    found, health = [], []

    def run(name, fn):
        started = dt.datetime.now(dt.timezone.utc)
        try:
            rows = fn()
            health.append({"source": name, "ok": True, "count": len(rows), "at": started.isoformat(timespec="seconds")})
            found.extend(rows)
        except Exception as e:  # never let one source kill the refresh
            health.append({"source": name, "ok": False, "count": 0, "error": f"{type(e).__name__}: {e}"[:300],
                           "at": started.isoformat(timespec="seconds")})
            traceback.print_exc()

    if src_cfg.get("simplify", {}).get("enabled"):
        run("SimplifyJobs Summer 2027 list", lambda: sources.fetch_simplify(src_cfg["simplify"]))
    for e in src_cfg.get("greenhouse", []):
        run(f"{e['company']} (Greenhouse)", lambda e=e: sources.fetch_greenhouse(e))
    for e in src_cfg.get("lever", []):
        run(f"{e['company']} (Lever)", lambda e=e: sources.fetch_lever(e))
    for e in src_cfg.get("workday", []):
        run(f"{e['company']} (Workday)", lambda e=e: sources.fetch_workday(
            e, src_cfg.get("workday_search_terms", ["intern"]), src_cfg.get("max_detail_fetches_per_source", 40)))
    return found, health


def merge(previous: list[dict], fetched: list[dict], seed: list[dict], healthy_sources: set[str], today: str) -> list[dict]:
    merged: dict[tuple, dict] = {}

    def put(l, origin):
        l = dict(l)
        l.setdefault("id", sources.make_id(l["company"], l["title"], l["url"]))
        k = dedupe_key(l)
        cur = merged.get(k)
        if cur is None:
            l["_origin"] = origin
            merged[k] = l
            return
        # prefer official postings, then the freshest data; keep earliest first_seen and any curated notes
        l_off, c_off = l.get("source_kind") == "official", cur.get("source_kind") == "official"
        if l_off != c_off:
            src = l if l_off else cur
        else:
            src = l if (origin == "fetched" or cur["_origin"] != "fetched") else cur
        other = cur if src is l else l
        keep = dict(src)
        for f in ("description", "job_id", "deadline", "date_posted"):
            if not keep.get(f) and other.get(f):
                keep[f] = other[f]
        if other.get("description") and len(other["description"]) > len(keep.get("description") or ""):
            keep["description"] = other["description"]
        keep["first_seen"] = min(x for x in (cur.get("first_seen"), l.get("first_seen"), today) if x)
        keep["id"] = cur["id"]  # stable id so tracker entries keep working
        keep["_origin"] = "fetched" if "fetched" in (origin, cur["_origin"]) else keep.get("_origin", origin)
        keep.setdefault("also_seen_on", [])
        if other.get("source") and other["source"] != keep.get("source") and other["source"] not in keep["also_seen_on"]:
            keep["also_seen_on"].append(other["source"])
        merged[k] = keep

    for l in previous:
        put(l, "previous")
    for l in seed:
        put(l, "seed")
    for l in fetched:
        l.setdefault("first_seen", today)
        put(l, "fetched")

    out = []
    for l in merged.values():
        origin = l.pop("_origin", "")
        l.setdefault("first_seen", today)
        # a listing we used to see from a source that worked today, but didn't see now, has been taken down
        if origin == "previous" and l.get("source") in healthy_sources and l.get("active", True):
            l["active"] = False
            l["removed_on"] = today
        if origin == "fetched":
            l["last_seen"] = today
            l.pop("removed_on", None)
        out.append(l)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true")
    args = ap.parse_args()

    profile = load(CFG / "profile.json", {})
    src_cfg = load(CFG / "sources.json", {})
    seed = load(ROOT / "scraper" / "seed.json", [])
    previous = load(DATA / "listings.json", {}).get("listings", [])
    tracker = load(DATA / "tracker.json", {})
    today = dt.date.today().isoformat()

    if args.offline:
        fetched, health = [], [{"source": "Curated seed list (live sources run on the next daily refresh)", "ok": True, "count": len(seed), "at": today}]
    else:
        fetched, health = fetch_all(src_cfg, profile)
    source_names = {l["source"] for l in fetched}
    healthy = {name for name in source_names}  # sources that returned rows today

    listings = merge(previous, fetched, seed, healthy, today)

    max_age = profile.get("max_listing_age_days", 120)
    kept = []
    for l in listings:
        reason = scoring.ineligible_reason(l, profile)
        l["ineligible"] = reason
        l["category"] = (scoring.classify(l["title"], profile) or {}).get("name")
        l["score"], l["breakdown"] = scoring.score(l, profile)
        if reason and not tracker.get(l["id"]) and not l.get("active", True) and l.get("removed_on") and \
                (dt.date.today() - dt.date.fromisoformat(l["removed_on"])).days > 30:
            continue  # forget long-gone listings you never tracked
        if reason == "Not a target role type" and not tracker.get(l["id"]):
            continue  # keep the file small: drop irrelevant roles entirely
        if l.get("date_posted") and not tracker.get(l["id"]):
            try:
                if (dt.date.today() - dt.date.fromisoformat(l["date_posted"][:10])).days > max_age and reason:
                    continue
            except ValueError:
                pass
        kept.append(l)

    kept.sort(key=lambda x: (x["ineligible"] is not None, -x["score"], x["company"]))
    picks = scoring.pick_daily(kept, tracker, profile)

    save(DATA / "listings.json", {"generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                                  "listings": kept})
    save(DATA / "daily.json", {"date": today, "picks": picks})
    history = load(DATA / "history.json", [])
    history = [h for h in history if h.get("date") != today] + [{"date": today, "picks": picks}]
    save(DATA / "history.json", history[-120:])
    if not (DATA / "tracker.json").exists():
        save(DATA / "tracker.json", {})
    eligible = [l for l in kept if not l["ineligible"]]
    save(DATA / "meta.json", {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "sources": health,
        "counts": {"total": len(kept), "eligible": len(eligible),
                   "new_today": sum(1 for l in eligible if l.get("first_seen") == today)},
        "candidate": profile.get("candidate", {}),
        "pick_rules": {"daily_picks": profile.get("daily_picks", 3), "rate_limits": {
            k: v for k, v in profile.get("rate_limits", {}).items() if not k.startswith("_")}},
    })
    print(f"{len(kept)} listings ({len(eligible)} eligible); picks: {picks}")
    for h in health:
        print(("  ok  " if h["ok"] else "  FAIL"), h["source"], h.get("count"), h.get("error", ""))


if __name__ == "__main__":
    main()
