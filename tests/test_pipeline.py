import datetime as dt
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scraper import scoring, sources  # noqa: E402
from scraper.run import merge  # noqa: E402

CFG = json.loads((ROOT / "config" / "profile.json").read_text())
TODAY = dt.date.today().isoformat()


def L(**kw):
    base = {"company": "Acme Robotics", "title": "Embedded Firmware Intern (Summer 2027)", "locations": ["San Jose, CA"],
            "url": "https://acme.example/jobs/1", "source": "Acme (Greenhouse)", "source_kind": "official",
            "date_posted": TODAY, "terms": ["Summer 2027"], "active": True, "description": ""}
    base.update(kw)
    base.setdefault("id", sources.make_id(base["company"], base["title"], base["url"]))
    return base


def test_simplify_parse():
    rows = sources.parse_simplify_listings([
        {"company_name": "Acme", "title": "Hardware Engineering Intern", "locations": ["Austin, TX"], "url": "https://a/1",
         "date_posted": 1761000000, "active": True, "is_visible": True, "terms": ["Summer 2027"]},
        {"company_name": "Hidden", "title": "x", "url": "https://b", "is_visible": False},
    ])
    assert len(rows) == 1 and rows[0]["company"] == "Acme" and rows[0]["date_posted"].startswith("2025")


def test_readme_parse():
    md = """<table><tr><td><strong><a href="https://simplify.jobs/c/Acme">Acme</a></strong></td><td>Firmware Intern</td>
    <td>San Jose, CA</td><td><a href="https://acme.example/apply"><img></a> <a href="https://simplify.jobs/p/1">s</a></td><td>3d</td></tr>
    <tr><td>↳</td><td>Hardware Test Intern</td><td>Austin, TX</br>Boston, MA</td><td><a href="https://acme.example/2">x</a></td><td>0d</td></tr></table>"""
    rows = sources.parse_simplify_readme(md)
    assert [r["company"] for r in rows] == ["Acme", "Acme"]
    assert rows[0]["url"] == "https://acme.example/apply"
    assert rows[1]["locations"] == ["Austin", "TX", "Boston", "MA"] or "Boston" in " ".join(rows[1]["locations"])


def test_greenhouse_parse_filters_interns():
    data = {"jobs": [{"title": "Hardware Test Intern (Summer 2027)", "absolute_url": "https://z/1", "id": 1,
                      "location": {"name": "South San Francisco, California, USA"}, "first_published": "2026-08-19T10:00:00-04:00",
                      "content": "&lt;p&gt;Use oscilloscopes&lt;/p&gt;"},
                     {"title": "Senior Engineer", "absolute_url": "https://z/2", "location": {"name": "X"}}]}
    rows = sources.parse_greenhouse("Zipline", data)
    assert len(rows) == 1 and rows[0]["terms"] == ["Summer 2027"] and "oscilloscopes" in rows[0]["description"]


def test_filters():
    assert scoring.ineligible_reason(L(), CFG) is None
    assert "phd" in scoring.ineligible_reason(L(title="Hardware PhD Intern"), CFG).lower()
    assert scoring.ineligible_reason(L(title="Hardware Engineering Intern, BS/MS"), CFG) is None
    assert scoring.ineligible_reason(L(locations=["Toronto, Canada"]), CFG) == "Outside the US"
    assert "Summer 2027" in scoring.ineligible_reason(L(terms=["Fall 2026"]), CFG)
    assert "GPA" in scoring.ineligible_reason(L(description="Minimum GPA of 3.5 required."), CFG)
    assert scoring.ineligible_reason(L(description="Cumulative GPA is at least 3.20"), CFG) is None
    assert scoring.ineligible_reason(L(description="expected graduation date between Fall 2027 and Summer 2028"), CFG) is None
    assert "Graduation" in scoring.ineligible_reason(L(description="graduating between September 2026 and June 2027"), CFG)
    assert scoring.ineligible_reason(L(description="currently enrolled in a Master's program"), CFG)
    assert scoring.ineligible_reason(L(title="Marketing Intern"), CFG)


def test_scoring_prefers_likely_wins():
    small = scoring.score(L(company="Acme Robotics"), CFG)[0]
    big = scoring.score(L(company="Google"), CFG)[0]
    old = scoring.score(L(date_posted=(dt.date.today() - dt.timedelta(days=50)).isoformat()), CFG)[0]
    far = scoring.score(L(locations=["Omaha, NE"]), CFG)[0]
    assert small > big and small > old and small > far
    s, parts = scoring.score(L(description="Arduino, Verilog, Python, Linux"), CFG)
    assert any(p["label"] == "Skill overlap" for p in parts) and 0 <= s <= 100


def test_pick_daily_rules():
    ls = []
    for i, co in enumerate(["Zipline", "Zipline", "Acme", "Beta", "Gamma"]):
        l = L(company=co, url=f"https://x/{i}", title=f"Hardware Test Intern {i} (Summer 2027)")
        l["score"], l["ineligible"] = 90 - i, None
        ls.append(l)
    picks = scoring.pick_daily(ls, {}, CFG)
    assert len(picks) == 3 and picks[0] == ls[0]["id"] and ls[1]["id"] not in picks  # one per company
    tracker = {ls[0]["id"]: {"status": "Applied", "date_applied": TODAY}}
    assert ls[0]["id"] not in scoring.pick_daily(ls, tracker, CFG)
    # Zipline rate limit: 3 applications in 30 days blocks further Zipline picks
    zl = [L(company="Zipline", url=f"https://z/{i}", title=f"Firmware Intern {i}") for i in range(4)]
    tr = {z["id"]: {"status": "Applied", "date_applied": TODAY} for z in zl[:3]}
    zl[3]["score"], zl[3]["ineligible"] = 99, None
    by = zl[:3] + [zl[3]]
    for z in zl[:3]:
        z["score"], z["ineligible"] = 50, None
    assert zl[3]["id"] not in scoring.pick_daily(by, tr, CFG)


def test_merge_prefers_official_and_marks_removed():
    agg = L(source="SimplifyJobs (GitHub)", source_kind="aggregator", url="https://agg/1")
    off = L(url="https://acme.example/jobs/1", description="detailed")
    out = merge([], [agg], [off], {"SimplifyJobs (GitHub)"}, TODAY)
    assert len(out) == 1 and out[0]["source_kind"] == "official"
    gone = L(title="Old Hardware Intern", url="https://acme.example/old")
    out = merge([gone], [], [], {"Acme (Greenhouse)"}, TODAY)
    assert out[0]["active"] is False and out[0]["removed_on"] == TODAY
