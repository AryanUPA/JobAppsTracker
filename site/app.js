/* Internship Radar — client. Reads site/data/*.json (refreshed daily by GitHub Actions),
   overlays your tracker edits from this browser, and recomputes today's top 3 live. */
(() => {
  "use strict";

  const STATUSES = ["Researching", "Shortlisted", "Preparing", "Awaiting my review", "Applied", "Online assessment",
    "Recruiter screen", "Interviewing", "Offer", "Rejected", "Withdrawn", "Expired"];
  const ACTIONABLE = new Set(["", "Researching", "Shortlisted", "Preparing"]);
  const LS_TRACKER = "radar.tracker.v1";
  const LS_GH = "radar.github.v1";
  const LS_UI = "radar.ui.v1";

  const S = { listings: [], byId: {}, meta: {}, daily: {}, base: {}, local: {}, ui: { view: "today", filter: "eligible", q: "", cat: "", loc: "", sort: "score" } };

  // ---------- utils ----------
  const $ = (sel, el = document) => el.querySelector(sel);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const safeUrl = (u) => /^https?:\/\//i.test(u || "") ? u : "#";
  const todayISO = () => new Date().toLocaleDateString("en-CA");
  const addDays = (n) => { const d = new Date(); d.setDate(d.getDate() + n); return d.toLocaleDateString("en-CA"); };
  const fmtMonth = (ym) => { if (!ym) return ""; const [y, m] = String(ym).split("-"); return m ? new Date(+y, +m - 1, 1).toLocaleDateString(undefined, { month: "long", year: "numeric" }) : ym; };
  const daysSince = (iso) => iso ? Math.floor((Date.parse(todayISO()) - Date.parse(iso.slice(0, 10))) / 864e5) : null;
  const store = { get(k, d) { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch { return d; } },
                  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* private mode */ } } };
  const loadJSON = async (p, d) => { try { const r = await fetch(p + "?v=" + Date.now()); return r.ok ? await r.json() : d; } catch { return d; } };

  function toast(msg) {
    const t = $("#toast"); t.textContent = msg; t.hidden = false;
    clearTimeout(toast._t); toast._t = setTimeout(() => (t.hidden = true), 2400);
  }

  // ---------- tracker ----------
  const tr = (id) => ({ ...(S.base[id] || {}), ...(S.local[id] || {}) });
  const status = (id) => tr(id).status || "";
  function setTr(id, patch) {
    S.local[id] = { ...(S.local[id] || {}), ...patch, updated: new Date().toISOString() };
    if (patch.status === "Applied" && !tr(id).date_applied) S.local[id].date_applied = todayISO();
    if (!tr(id).date_found) S.local[id].date_found = todayISO();
    store.set(LS_TRACKER, S.local);
    renderAll();
    scheduleSync();
  }
  const mergedTracker = () => {
    const out = {};
    for (const id of new Set([...Object.keys(S.base), ...Object.keys(S.local)])) out[id] = tr(id);
    return out;
  };

  // ---------- daily picks (mirrors scraper/scoring.py pick_daily) ----------
  function computePicks() {
    const rules = S.meta.pick_rules || { daily_picks: 3, rate_limits: {} };
    const today = todayISO();
    const picks = [], companies = new Set();
    const pool = S.listings.filter((l) => !l.ineligible).sort((a, b) => b.score - a.score || a.id.localeCompare(b.id));
    for (const l of pool) {
      const t = tr(l.id);
      if (!ACTIONABLE.has(t.status || "")) continue;
      if (t.snooze_until && t.snooze_until >= today) continue;
      const co = l.company.toLowerCase();
      if (companies.has(co)) continue;
      const lim = rules.rate_limits?.[co];
      if (lim) {
        const n = S.listings.filter((x) => x.company.toLowerCase() === co && tr(x.id).date_applied && daysSince(tr(x.id).date_applied) <= lim.days).length;
        if (n >= lim.max) continue;
      }
      companies.add(co); picks.push(l);
      if (picks.length >= rules.daily_picks) break;
    }
    return picks;
  }

  // ---------- pieces ----------
  const ring = (v, sm) => `<div class="ring${sm ? " sm" : ""}" style="--v:${Math.max(0, Math.min(100, v))}" aria-label="Match score ${v}"><b>${v}</b></div>`;
  const locText = (l) => (l.locations || []).slice(0, 3).join(" · ") + ((l.locations || []).length > 3 ? ` +${l.locations.length - 3}` : "");
  const srcChip = (l) => l.source_kind === "official" ? `<span class="chip strong">Employer site</span>` : `<span class="chip">Via ${esc((l.source || "aggregator").replace(/\s*\(.*\)/, ""))}</span>`;
  const statusChip = (id) => status(id) ? `<span class="chip strong">${esc(status(id))}</span>` : "";
  const isNew = (l) => l.first_seen === (S.meta.generated_at || "").slice(0, 10) && !l.ineligible;

  function reasonsList(l, max = 6) {
    const parts = (l.breakdown || []).slice().sort((a, b) => Math.abs(b.points) - Math.abs(a.points)).slice(0, max);
    return `<ul class="reasons">${parts.map((p) => `
      <li><span>${esc(p.label)} · <span class="faint">${esc(p.detail)}</span></span><span>${p.points > 0 ? "+" : ""}${p.points}</span>
      <span class="bar${p.points < 0 ? " neg" : ""}"><i style="width:${Math.min(100, Math.abs(p.points) / 30 * 100)}%"></i></span></li>`).join("")}</ul>`;
  }

  // ---------- views ----------
  function renderToday() {
    const el = $("#view-today");
    const picks = computePicks();
    const elig = S.listings.filter((l) => !l.ineligible);
    const newToday = elig.filter(isNew).length;
    const applied = Object.values(mergedTracker()).filter((t) => t.status && !ACTIONABLE.has(t.status) && t.status !== "Expired" && t.status !== "Withdrawn").length;
    const pickIds = new Set(picks.map((p) => p.id));
    const next = elig.filter((l) => !pickIds.has(l.id) && ACTIONABLE.has(status(l.id))).sort((a, b) => b.score - a.score).slice(0, 5);
    const dateStr = new Date().toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" });

    el.innerHTML = `
      <div class="hero">
        <div class="glass hero-card">
          <span class="eyebrow">${esc(dateStr)}</span>
          <h2>${picks.length ? `Your ${picks.length === 3 ? "three" : picks.length} best shots today` : "Nothing left to pick today"}</h2>
          <p class="muted">Ranked by your odds of landing an interview, not just prestige: fresh postings, employers that hire second-years, skills you can back up, and places you want to live.</p>
        </div>
        <div class="glass stats">
          <div class="stat"><b>${elig.length}</b><span>eligible roles</span></div>
          <div class="stat"><b>${newToday}</b><span>new today</span></div>
          <div class="stat"><b>${applied}</b><span>in progress</span></div>
        </div>
      </div>
      <div class="picks">${picks.map((l, i) => `
        <article class="glass pick">
          <div class="pick-top">
            <div><span class="rank">#${i + 1} TODAY</span>
              <div class="company">${esc(l.company)}</div>
              <h3>${esc(l.title)}</h3>
              <div class="meta-line"><span>${esc(locText(l))}</span></div>
            </div>${ring(l.score)}
          </div>
          <div class="chips">${srcChip(l)}${statusChip(l.id)}${l.deadline ? `<span class="chip strong">Due ${esc(l.deadline)}</span>` : ""}</div>
          ${reasonsList(l, 5)}
          <div class="actions">
            <a class="btn primary small" href="${esc(safeUrl(l.url))}" target="_blank" rel="noopener">Open posting</a>
            <button class="btn small" data-act="prep" data-id="${l.id}">Start</button>
            <button class="btn small" data-act="applied" data-id="${l.id}">Applied</button>
            <button class="btn small ghost" data-act="snooze" data-id="${l.id}" title="Hide from picks for a week">Snooze</button>
            <button class="btn small ghost" data-act="open" data-id="${l.id}">Details</button>
          </div>
        </article>`).join("") || `<div class="glass empty" style="grid-column:1/-1">Every eligible role is applied, snoozed, or closed. Check back after tomorrow's refresh.</div>`}
      </div>
      ${next.length ? `<div class="glass upnext list"><h3>Up next</h3>${next.map(rowHTML).join("")}</div>` : ""}`;
  }

  function rowHTML(l) {
    return `<button class="row${l.ineligible ? " dim" : ""}" data-act="open" data-id="${l.id}">
      ${ring(l.score, true)}
      <div><div class="t">${esc(l.title)}</div><div class="c">${esc(l.company)}${l.category ? " · " + esc(l.category) : ""}</div></div>
      <div class="l">${esc(locText(l))}</div>
      <div class="row-tags">${isNew(l) ? `<span class="chip strong">New</span>` : ""}${statusChip(l.id)}${l.ineligible ? `<span class="chip">${esc(l.ineligible)}</span>` : srcChip(l)}</div>
    </button>`;
  }

  function renderListings() {
    const el = $("#view-listings");
    const u = S.ui;
    const cats = [...new Set(S.listings.map((l) => l.category).filter(Boolean))].sort();
    const locs = ["Bay Area", "Sacramento/Folsom", "Southern California", "Seattle", "Austin", "Boston", "Remote"];
    let rows = S.listings.filter((l) => {
      if (u.filter === "eligible" && l.ineligible) return false;
      if (u.filter === "new" && !isNew(l)) return false;
      if (u.filter === "tracked" && !status(l.id)) return false;
      if (u.cat && l.category !== u.cat) return false;
      if (u.loc && !(l.breakdown || []).some((p) => p.label === "Location" && p.detail === u.loc)) return false;
      if (u.q) { const h = `${l.company} ${l.title} ${(l.locations || []).join(" ")}`.toLowerCase(); if (!u.q.toLowerCase().split(/\s+/).every((w) => h.includes(w))) return false; }
      return true;
    });
    const sorters = { score: (a, b) => b.score - a.score, newest: (a, b) => (b.date_posted || b.first_seen || "").localeCompare(a.date_posted || a.first_seen || ""), company: (a, b) => a.company.localeCompare(b.company) };
    rows.sort(sorters[u.sort] || sorters.score);
    el.innerHTML = `
      <div class="section-head"><div><h2>Listings</h2><p>Every Summer 2027 role found today, scored for you. Ineligible roles stay visible with the reason, so nothing is silently hidden.</p></div></div>
      <div class="glass toolbar">
        <label class="search"><span aria-hidden="true">⌕</span><input id="q" type="search" placeholder="Search company, role, city" value="${esc(u.q)}" aria-label="Search"></label>
        <div class="segmented" role="group" aria-label="Filter">${[["eligible", "Eligible"], ["new", "New"], ["tracked", "Tracked"], ["all", "All"]].map(([k, v]) => `<button data-filter="${k}" class="${u.filter === k ? "on" : ""}">${v}</button>`).join("")}</div>
        <select id="cat" aria-label="Role type"><option value="">All role types</option>${cats.map((c) => `<option ${u.cat === c ? "selected" : ""}>${esc(c)}</option>`).join("")}</select>
        <select id="loc" aria-label="Location"><option value="">Any location</option>${locs.map((c) => `<option ${u.loc === c ? "selected" : ""}>${esc(c)}</option>`).join("")}</select>
        <select id="sort" aria-label="Sort"><option value="score" ${u.sort === "score" ? "selected" : ""}>Best match</option><option value="newest" ${u.sort === "newest" ? "selected" : ""}>Newest</option><option value="company" ${u.sort === "company" ? "selected" : ""}>Company</option></select>
      </div>
      <p class="count-line">${rows.length} of ${S.listings.length} roles</p>
      <div class="glass list">${rows.map(rowHTML).join("") || `<div class="empty">No roles match these filters.</div>`}</div>`;
    const q = $("#q", el);
    q.addEventListener("input", () => { S.ui.q = q.value; saveUI(); renderListings(); const n = $("#q"); n.focus(); n.setSelectionRange(n.value.length, n.value.length); });
    for (const id of ["cat", "loc", "sort"]) $("#" + id, el).addEventListener("change", (e) => { S.ui[id] = e.target.value; saveUI(); renderListings(); });
    el.querySelectorAll("[data-filter]").forEach((b) => b.addEventListener("click", () => { S.ui.filter = b.dataset.filter; saveUI(); renderListings(); }));
  }

  function renderTracker() {
    const el = $("#view-tracker");
    const t = mergedTracker();
    const groups = [
      ["To apply", ["Shortlisted", "Preparing", "Awaiting my review"]],
      ["Applied", ["Applied"]],
      ["Interviewing", ["Online assessment", "Recruiter screen", "Interviewing", "Offer"]],
      ["Closed", ["Rejected", "Withdrawn", "Expired"]],
    ];
    const card = (id) => { const l = S.byId[id]; if (!l) return ""; const x = t[id];
      return `<button class="card" data-act="open" data-id="${id}"><div class="t">${esc(l.title)}</div><div class="c">${esc(l.company)} · ${esc(x.status)}${x.date_applied ? " · applied " + esc(x.date_applied) : ""}</div>${x.follow_up ? `<div class="c">Follow up ${esc(x.follow_up)}</div>` : ""}</button>`; };
    const due = Object.entries(t).filter(([, x]) => x.follow_up && x.follow_up <= todayISO() && !["Rejected", "Withdrawn", "Expired", "Offer"].includes(x.status));
    el.innerHTML = `
      <div class="section-head"><div><h2>Tracker</h2><p>Changes save in this browser instantly. Connect GitHub on the Sources tab to sync them everywhere and let the daily job skip roles you've applied to.</p></div>
        <div class="chips"><button class="btn small" id="export">Export tracker.json</button><label class="btn small ghost" for="import">Import<input id="import" type="file" accept="application/json" hidden></label></div></div>
      ${due.length ? `<div class="notice" style="margin-bottom:14px"><b>${due.length} follow-up${due.length > 1 ? "s" : ""} due:</b> ${due.map(([id]) => esc(S.byId[id]?.company || id)).join(", ")}</div>` : ""}
      <div class="board">${groups.map(([name, sts]) => { const ids = Object.keys(t).filter((id) => sts.includes(t[id].status));
        return `<div class="glass col"><h3>${name}<span class="faint">${ids.length}</span></h3>${ids.map(card).join("") || `<p class="faint" style="margin:4px">Nothing here yet.</p>`}</div>`; }).join("")}</div>`;
    $("#export", el).addEventListener("click", () => {
      const blob = new Blob([JSON.stringify(mergedTracker(), null, 1)], { type: "application/json" });
      const a = Object.assign(document.createElement("a"), { href: URL.createObjectURL(blob), download: "tracker.json" }); a.click();
      toast("Saved tracker.json — commit it to site/data/");
    });
    $("#import", el).addEventListener("change", async (e) => {
      try { const data = JSON.parse(await e.target.files[0].text()); S.local = { ...S.local, ...data }; store.set(LS_TRACKER, S.local); renderAll(); toast("Tracker imported"); }
      catch { toast("That file isn't a valid tracker.json"); }
    });
  }

  function renderSources() {
    const el = $("#view-sources");
    const m = S.meta, gh = store.get(LS_GH, {});
    const when = m.generated_at ? new Date(m.generated_at).toLocaleString() : "never";
    el.innerHTML = `
      <div class="section-head"><div><h2>Sources & matching</h2><p>Last refresh: ${esc(when)}. The site refreshes itself every morning at about 6:15 AM Pacific.</p></div></div>
      <div class="grid2">
        <div class="glass panel"><h3>Today's sources</h3>${(m.sources || []).map((s) => `
          <div class="src"><span class="dot${s.ok ? "" : " off"}"></span><div>${esc(s.source)}${s.ok ? "" : `<span class="err">${esc(s.error || "failed")}</span>`}</div><span class="faint">${s.ok ? s.count + " roles" : "skipped"}</span></div>`).join("") || `<p class="faint">No refresh has run yet.</p>`}
          <p class="faint" style="font-size:12px;margin-top:12px">A failed source is skipped for the day; everything else still updates. Add companies in <code>config/sources.json</code>.</p></div>
        <div class="glass panel"><h3>How the match score works</h3>
          <div class="formula">
            <b>Role fit</b> up to 30 · your priority order, hardware and embedded first<br>
            <b>Interview odds</b> up to ~40 · freshness (apply early), employer saturation, undergrad-friendly programs, skill overlap minus known gaps<br>
            <b>Location</b> up to 10 · Bay Area and Sacramento highest<br>
            <b>Target company</b> +5 · <b>Confidence</b> up to +5 · <b>Deadline soon</b> +4<br><br>
            Hard filters remove PhD/MS-only, non-US, wrong term, closed roles, GPA minimums above ${esc(m.candidate?.gpa ?? "yours")}, and grad windows that exclude ${esc(fmtMonth(m.candidate?.grad_month) || "your date")}.
            Daily picks take the top score per company, skip anything you've applied to or snoozed, and respect Zipline's 3-applications-per-30-days rule.
          </div></div>
        <div class="glass panel"><h3>Sync with GitHub (optional)</h3>
          <p class="muted" style="font-size:13px;margin-top:0">Saves your tracker to <code>site/data/tracker.json</code> in your repo so it follows you across devices and the daily job knows what you've applied to. Use a fine-grained token limited to this one repo with <b>Contents: read and write</b>. It's stored only in this browser.</p>
          <label class="stack">Repository<input class="text" id="gh-repo" placeholder="AryanUPA/JobAppsTracker" value="${esc(gh.repo || "AryanUPA/JobAppsTracker")}"></label>
          <label class="stack">Token<input class="text" id="gh-token" type="password" placeholder="github_pat_…" value="${esc(gh.token || "")}"></label>
          <div class="chips"><button class="btn primary small" id="gh-save">Save & sync now</button>${gh.token ? `<button class="btn small ghost" id="gh-clear">Disconnect</button>` : ""}</div>
          <p class="faint" style="font-size:12px" id="gh-status">${gh.last ? "Last synced " + esc(new Date(gh.last).toLocaleString()) : "Not connected"}</p></div>
        <div class="glass panel"><h3>Profile used for matching</h3>
          <div class="formula">${esc(m.candidate?.name || "")}<br>${esc(m.candidate?.degree || "")}, ${esc(m.candidate?.school || "")}<br>Graduating ${esc(fmtMonth(m.candidate?.grad_month))} · GPA ${esc(m.candidate?.gpa ?? "")} · ${esc(m.candidate?.work_authorization || "")}<br>Target: ${esc(m.candidate?.target_term || "")}</div>
          <p class="faint" style="font-size:12px">Edit weights, locations and skills in <code>config/profile.json</code>.</p></div>
      </div>`;
    $("#gh-save", el).addEventListener("click", () => {
      store.set(LS_GH, { ...store.get(LS_GH, {}), repo: $("#gh-repo").value.trim(), token: $("#gh-token").value.trim() });
      syncNow(true);
    });
    $("#gh-clear", el)?.addEventListener("click", () => { store.set(LS_GH, {}); renderSources(); toast("Disconnected from GitHub"); });
  }

  // ---------- GitHub sync ----------
  let syncTimer;
  function scheduleSync() { if (store.get(LS_GH, {}).token) { clearTimeout(syncTimer); syncTimer = setTimeout(() => syncNow(false), 1500); } }
  async function syncNow(loud) {
    const gh = store.get(LS_GH, {});
    if (!gh.token || !/^[\w.-]+\/[\w.-]+$/.test(gh.repo || "")) { if (loud) toast("Add a repository and token first"); return; }
    const api = `https://api.github.com/repos/${gh.repo}/contents/site/data/tracker.json`;
    const headers = { Authorization: `Bearer ${gh.token}`, Accept: "application/vnd.github+json" };
    try {
      let sha, remote = {};
      const g = await fetch(api, { headers, cache: "no-store" });
      if (g.ok) { const j = await g.json(); sha = j.sha; remote = JSON.parse(decodeURIComponent(escape(atob(j.content.replace(/\n/g, ""))))); }
      // newest edit per role wins
      const merged = { ...remote };
      for (const [id, v] of Object.entries(mergedTracker())) if (!merged[id] || (v.updated || "") >= (merged[id].updated || "")) merged[id] = v;
      const body = { message: "Update tracker from Internship Radar", content: btoa(unescape(encodeURIComponent(JSON.stringify(merged, null, 1) + "\n"))), ...(sha ? { sha } : {}) };
      const p = await fetch(api, { method: "PUT", headers, body: JSON.stringify(body) });
      if (!p.ok) throw new Error(`GitHub said ${p.status}`);
      S.base = merged; S.local = {}; store.set(LS_TRACKER, {});
      store.set(LS_GH, { ...gh, last: new Date().toISOString() });
      if (loud) toast("Tracker synced to GitHub");
      renderAll();
    } catch (e) { toast("Sync failed: " + e.message); }
  }

  // ---------- detail sheet ----------
  function openSheet(id) {
    const l = S.byId[id]; if (!l) return;
    const t = tr(id);
    const sheet = $("#sheet");
    sheet.innerHTML = `
      <button class="btn small ghost close" data-act="close" aria-label="Close">Close</button>
      <span class="eyebrow">${esc(l.company)}</span>
      <h2 id="sheet-title">${esc(l.title)}</h2>
      <div class="meta-line">${esc((l.locations || []).join(" · "))}</div>
      <div class="chips" style="margin-top:12px">${srcChip(l)}${(l.terms || []).map((x) => `<span class="chip">${esc(x)}</span>`).join("")}${l.job_id ? `<span class="chip">ID ${esc(l.job_id)}</span>` : ""}${l.date_posted ? `<span class="chip">Posted ${esc(l.date_posted)}</span>` : ""}</div>
      ${l.ineligible ? `<section><div class="notice"><b>Not eligible:</b> ${esc(l.ineligible)}</div></section>` : ""}
      ${l.source_kind !== "official" ? `<section><div class="notice">Found on an aggregator. Confirm the posting on the employer's own careers site before applying.</div></section>` : ""}
      <section style="display:flex;gap:16px;align-items:center">${ring(l.score)}<div><b>Match score</b><div class="muted" style="font-size:13px">Built from the factors below</div></div></section>
      <section>${reasonsList(l, 12)}</section>
      <section><h4>Your tracker</h4>
        <div class="form-grid">
          <label class="stack">Status<select id="f-status"><option value="">Not tracked</option>${STATUSES.map((s) => `<option ${t.status === s ? "selected" : ""}>${s}</option>`).join("")}</select></label>
          <label class="stack">Date applied<input class="text" type="date" id="f-applied" value="${esc(t.date_applied || "")}"></label>
          <label class="stack">Follow-up date<input class="text" type="date" id="f-follow" value="${esc(t.follow_up || "")}"></label>
          <label class="stack">Recruiter / referral<input class="text" id="f-contact" value="${esc(t.contact || "")}"></label>
        </div>
        <label class="stack">Notes<textarea class="text" id="f-notes">${esc(t.notes || "")}</textarea></label>
        <div class="actions"><button class="btn primary small" data-act="save" data-id="${id}">Save</button><a class="btn small" href="${esc(safeUrl(l.url))}" target="_blank" rel="noopener">Open posting</a><button class="btn small ghost" data-act="snooze" data-id="${id}">Snooze a week</button></div>
      </section>
      ${l.description ? `<section><h4>From the posting</h4><div class="desc">${esc(l.description.slice(0, 4000))}</div></section>` : ""}
      ${(l.also_seen_on || []).length ? `<section class="faint" style="font-size:12px">Also listed on: ${esc(l.also_seen_on.join(", "))}</section>` : ""}`;
    $("#scrim").hidden = false; sheet.hidden = false;
    sheet.querySelector(".close").focus();
  }
  function closeSheet() { $("#sheet").hidden = true; $("#scrim").hidden = true; }

  // ---------- events ----------
  document.addEventListener("click", (e) => {
    const b = e.target.closest("[data-act]"); if (!b) return;
    const id = b.dataset.id;
    switch (b.dataset.act) {
      case "open": openSheet(id); break;
      case "close": closeSheet(); break;
      case "prep": setTr(id, { status: "Preparing" }); toast("Moved to Preparing"); break;
      case "applied": setTr(id, { status: "Applied", date_applied: todayISO(), follow_up: tr(id).follow_up || addDays(14) }); toast("Marked applied · follow-up in 2 weeks"); break;
      case "snooze": setTr(id, { snooze_until: addDays(7) }); toast("Hidden from picks for a week"); closeSheet(); break;
      case "save":
        setTr(id, { status: $("#f-status").value, date_applied: $("#f-applied").value, follow_up: $("#f-follow").value, contact: $("#f-contact").value, notes: $("#f-notes").value });
        toast("Saved"); closeSheet(); break;
    }
  });
  $("#scrim").addEventListener("click", closeSheet);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeSheet(); });
  document.addEventListener("pointermove", (e) => {
    const g = e.target.closest?.(".glass"); if (!g) return;
    const r = g.getBoundingClientRect();
    g.style.setProperty("--mx", `${e.clientX - r.left}px`); g.style.setProperty("--my", `${e.clientY - r.top}px`);
  }, { passive: true });

  function setView(v) {
    S.ui.view = v; saveUI();
    document.querySelectorAll(".tabbar button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.view === v)));
    document.querySelectorAll(".view").forEach((s) => s.classList.toggle("active", s.id === "view-" + v));
    moveIndicator();
  }
  function moveIndicator() {
    const b = $(`.tabbar button[aria-selected="true"]`), ind = $(".tab-indicator"); if (!b) return;
    ind.style.width = b.offsetWidth + "px"; ind.style.transform = `translateX(${b.offsetLeft - 5}px)`;
  }
  document.querySelectorAll(".tabbar button").forEach((b) => b.addEventListener("click", () => setView(b.dataset.view)));
  window.addEventListener("resize", moveIndicator);
  const saveUI = () => store.set(LS_UI, S.ui);

  function renderAll() {
    renderToday(); renderListings(); renderTracker(); renderSources();
    const first = (S.meta.candidate?.name || "").split(" ")[0];
    const h = new Date().getHours();
    $("#greeting").textContent = `${h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening"}${first ? ", " + first : ""}`;
    const when = S.meta.generated_at ? new Date(S.meta.generated_at) : null;
    $("#subline").textContent = when ? `Listings refreshed ${when.toLocaleDateString(undefined, { month: "short", day: "numeric" })} at ${when.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}` : "Waiting for first refresh";
  }

  async function init() {
    const [L, M, D, T] = await Promise.all([loadJSON("data/listings.json", { listings: [] }), loadJSON("data/meta.json", {}), loadJSON("data/daily.json", {}), loadJSON("data/tracker.json", {})]);
    S.listings = L.listings || []; S.meta = M; S.daily = D; S.base = T;
    S.byId = Object.fromEntries(S.listings.map((l) => [l.id, l]));
    S.local = store.get(LS_TRACKER, {});
    S.ui = { ...S.ui, ...store.get(LS_UI, {}), q: "" };
    renderAll(); setView(S.ui.view || "today");
    requestAnimationFrame(moveIndicator);
  }
  init();
})();
