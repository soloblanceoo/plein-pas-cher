(() => {
  "use strict";

  const FUELS = ["gazole", "e10", "sp98", "sp95", "gplc", "e85"];
  const PAGE = 30;
  const body = document.body;
  const ROOT = body.dataset.root || "";
  const KIND = body.dataset.page;
  const $ = id => document.getElementById(id);

  // ================= Βοηθητικά =================
  const store = {
    get(k, d) { try { const v = localStorage.getItem(k); return v == null ? d : JSON.parse(v); } catch { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* ιδιωτική περιήγηση */ } },
  };
  let lang = store.get("lang", (navigator.language || "").toLowerCase().startsWith("el") ? "el" : "fr");
  if (!I18N[lang]) lang = "fr";
  const raw = key => (I18N[lang] && I18N[lang][key]) ?? I18N.fr[key] ?? key;
  const t = (key, vars = {}) => String(raw(key)).replace(/\{(\w+)\}/g, (_, k) => vars[k] ?? "");
  const locale = () => (lang === "el" ? "el-GR" : "fr-FR");
  const num = n => Number(n).toLocaleString(locale());
  const price3 = n => n.toFixed(3).replace(".", ",");
  const euro = n => price3(n) + " €";
  const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const norm = s => String(s ?? "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase().replace(/[-']/g, " ").trim();
  const debounce = (fn, ms) => { let id; return (...a) => { clearTimeout(id); id = setTimeout(() => fn(...a), ms); }; };

  // ================= Γλώσσα =================
  const rerenders = [];
  function applyLang() {
    document.documentElement.lang = lang;
    document.querySelectorAll("[data-i18n]").forEach(el => {
      el.textContent = t(el.dataset.i18n, { n: el.dataset.n ? num(+el.dataset.n) : "" });
    });
    document.querySelectorAll("[data-i18n-ph]").forEach(el => { el.placeholder = t(el.dataset.i18nPh); });
    document.querySelectorAll("[data-i18n-label]").forEach(el => { el.setAttribute("aria-label", t(el.dataset.i18nLabel)); });
    document.querySelectorAll("[data-only]").forEach(el => { el.hidden = el.dataset.only !== lang; });
    document.querySelectorAll(".lang button").forEach(b => b.setAttribute("aria-pressed", b.dataset.lang === lang));
    rerenders.forEach(fn => fn());
  }
  document.querySelectorAll(".lang button").forEach(b => b.addEventListener("click", () => {
    lang = b.dataset.lang; store.set("lang", lang); applyLang();
  }));

  // ================= Χρόνος & ωράρια =================
  const utc = s => new Date(s.replace(" ", "T") + (s.length <= 16 ? ":00Z" : "Z"));
  const ageH = s => (Date.now() - utc(s)) / 36e5;
  function ago(s) {
    const h = Math.floor(ageH(s));
    if (h < 1) return t("ago.now");
    if (h < 24) return t("ago.h", { n: h });
    const d = Math.round(h / 24);
    return d === 1 ? t("ago.d1") : t("ago.d", { n: d });
  }

  const TZ = { "971": "America/Guadeloupe", "972": "America/Martinique", "973": "America/Cayenne", "974": "Indian/Reunion", "976": "Indian/Mayotte" };
  const nowCache = {};
  function localNow(dep) {
    const tz = TZ[dep] || "Europe/Paris";
    const key = tz + Math.floor(Date.now() / 60000);
    if (nowCache[key]) return nowCache[key];
    let day, min;
    try {
      const parts = Object.fromEntries(new Intl.DateTimeFormat("en-GB", { timeZone: tz, weekday: "short", hour: "2-digit", minute: "2-digit", hourCycle: "h23" })
        .formatToParts(new Date()).map(p => [p.type, p.value]));
      day = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].indexOf(parts.weekday);
      min = (+parts.hour) * 60 + (+parts.minute);
    } catch {
      const d = new Date(); day = (d.getDay() + 6) % 7; min = d.getHours() * 60 + d.getMinutes();
    }
    return (nowCache[key] = { day, min });
  }
  const hm = m => {
    m = ((m % 1440) + 1440) % 1440;
    const h = Math.floor(m / 60), mm = String(m % 60).padStart(2, "0");
    return lang === "fr" ? `${h}h${mm}` : `${String(h).padStart(2, "0")}:${mm}`;
  };
  const allDay = s => s.o && s.o.every(d => d && d.some(([a, b]) => a === 0 && b >= 1440));

  // Κατάσταση πρατηρίου τώρα: open | auto | closed | unknown
  function status(s) {
    if (!s.o) return s.a24 ? { k: "auto", text: t("svc.cb24") } : { k: "unknown", text: t("st.unknown") };
    if (allDay(s)) return { k: "open", text: t("st.open24") };
    const { day, min } = localNow(s.d);
    const today = s.o[day] || [], yest = s.o[(day + 6) % 7] || [];
    for (const [a, b] of yest) if (b > 1440 && min < b - 1440) return { k: "open", text: t("st.open", { t: hm(b) }) };
    for (const [a, b] of today) {
      if (min >= a && min < b) return b >= 1440 && a === 0 ? { k: "open", text: t("st.open24") } : { k: "open", text: t("st.open", { t: hm(b) }) };
    }
    if (s.a24) return { k: "auto", text: t("st.auto") };
    const later = today.filter(([a]) => a > min).sort((x, y) => x[0] - y[0])[0];
    if (later) return { k: "closed", text: t("st.closed", { t: hm(later[0]) }) };
    for (let i = 1; i <= 7; i++) {
      const d = (day + i) % 7, slot = (s.o[d] || []).slice().sort((x, y) => x[0] - y[0])[0];
      if (slot) return { k: "closed", text: t("st.closedDay", { d: raw("days")[d], t: hm(slot[0]) }) };
    }
    return { k: "closed", text: t("st.closedLong") };
  }

  // ================= Δεδομένα =================
  const cache = {};
  function getJSON(path) {
    if (!cache[path]) {
      cache[path] = fetch(ROOT + path, { cache: "no-cache" }).then(r => { if (!r.ok) throw new Error(r.status); return r.json(); });
      cache[path].catch(() => delete cache[path]);
    }
    return cache[path];
  }
  function km(a, b) {
    const R = 6371, rad = x => x * Math.PI / 180;
    const dLa = rad(b.la - a.la), dLo = rad(b.lo - a.lo);
    const h = Math.sin(dLa / 2) ** 2 + Math.cos(rad(a.la)) * Math.cos(rad(b.la)) * Math.sin(dLo / 2) ** 2;
    return 2 * R * Math.asin(Math.sqrt(h));
  }

  // ================= Φίλτρα =================
  const DEFAULTS = { fuel: "gazole", open: false, a24: false, noHw: true, svc: [], fresh: 168, sort: "price", radius: 10 };
  const F = Object.assign({}, DEFAULTS, store.get("filters", {}));
  if (!FUELS.includes(F.fuel)) F.fuel = "gazole";
  const saveF = () => store.set("filters", F);
  const activeCount = () =>
    (F.open ? 1 : 0) + (F.a24 ? 1 : 0) + (F.noHw !== DEFAULTS.noHw ? 1 : 0) + F.svc.length + (F.fresh !== 168 ? 1 : 0) + (F.sort !== "price" ? 1 : 0);

  function passes(s, fuel, withDist) {
    if (s.p[fuel] == null) return false;
    if (F.noHw && s.h) return false;
    if (F.a24 && !(s.a24 || allDay(s))) return false;
    if (F.open) { const k = status(s).k; if (k !== "open" && k !== "auto") return false; }
    if (F.svc.length && !F.svc.every(x => (s.s || []).includes(x))) return false;
    if (F.fresh < 168 && ageH(s.u[fuel]) > F.fresh) return false;
    if (withDist && s.km > F.radius) return false;
    return true;
  }
  function sorter(fuel) {
    if (F.sort === "dist") return (a, b) => (a.km ?? 0) - (b.km ?? 0) || a.p[fuel] - b.p[fuel];
    if (F.sort === "fresh") return (a, b) => utc(b.u[fuel]) - utc(a.u[fuel]) || a.p[fuel] - b.p[fuel];
    return (a, b) => a.p[fuel] - b.p[fuel] || (a.km ?? 0) - (b.km ?? 0);
  }

  function bindFilters(onChange, { distance }) {
    const panel = $("fpanel"), btn = $("fbtn");
    if (!panel) return;
    if (!distance && F.sort === "dist") F.sort = "price";
    const sync = () => {
      panel.querySelectorAll("input[data-filter]").forEach(i => { i.checked = !!F[i.dataset.filter]; });
      panel.querySelectorAll(".seg[data-filter]").forEach(seg => {
        seg.querySelectorAll("button").forEach(b => b.setAttribute("aria-pressed", String(F[seg.dataset.filter]) === b.dataset.v));
      });
      panel.querySelectorAll("[data-svc]").forEach(b => b.setAttribute("aria-pressed", F.svc.includes(b.dataset.svc)));
      const n = activeCount();
      $("fcount").hidden = !n;
      $("fcount").textContent = n;
    };
    const changed = () => { saveF(); sync(); onChange(); };
    btn.addEventListener("click", () => {
      const open = panel.hidden;
      panel.hidden = !open;
      btn.setAttribute("aria-expanded", open);
    });
    panel.addEventListener("change", e => {
      const i = e.target.closest("input[data-filter]");
      if (i) { F[i.dataset.filter] = i.checked; changed(); }
    });
    panel.addEventListener("click", e => {
      const segBtn = e.target.closest(".seg[data-filter] button");
      if (segBtn) {
        const key = segBtn.parentElement.dataset.filter;
        F[key] = isNaN(+segBtn.dataset.v) ? segBtn.dataset.v : +segBtn.dataset.v;
        return changed();
      }
      const svc = e.target.closest("[data-svc]");
      if (svc) {
        const k = svc.dataset.svc;
        F.svc = F.svc.includes(k) ? F.svc.filter(x => x !== k) : [...F.svc, k];
        return changed();
      }
    });
    const reset = () => { const fuel = F.fuel; Object.assign(F, DEFAULTS, { fuel, svc: [] }); changed(); };
    $("freset").addEventListener("click", reset);
    sync();
    return { reset, sync };
  }

  function fuelTabs(el, available, get, set) {
    const draw = () => {
      el.innerHTML = FUELS.filter(f => available().has(f)).map(f =>
        `<button type="button" role="tab" data-fuel="${f}" aria-selected="${f === get()}">${esc(t("fuel." + f))}</button>`).join("");
    };
    el.addEventListener("click", e => {
      const b = e.target.closest("button[data-fuel]");
      if (b) { set(b.dataset.fuel); draw(); }
    });
    draw();
    rerenders.push(draw);
    return draw;
  }

  // ================= Κάρτες =================
  const openCards = new Set();
  const svcTag = k => `<span class="tag">${esc(t("svc." + k))}</span>`;

  function detailsHTML(s, fuel) {
    const fuels = FUELS.filter(f => s.p[f] != null).map(f =>
      `<div class="fuel${f === fuel ? " on" : ""}"><span>${esc(t("fuel." + f))} · ${esc(ago(s.u[f]))}</span><b>${euro(s.p[f])}</b></div>`).join("");
    let hours = `<p class="addr">${esc(t("d.none"))}</p>`;
    if (s.o) {
      const today = localNow(s.d).day, days = raw("days");
      hours = `<table class="hours">${s.o.map((slots, i) => {
        const txt = slots == null ? "–" : !slots.length ? t("h.closed")
          : slots.some(([a, b]) => a === 0 && b >= 1440) ? t("h.allday")
          : slots.map(([a, b]) => `${hm(a)}–${hm(b)}`).join(", ");
        return `<tr${i === today ? ' class="today"' : ""}><td>${esc(days[i])}</td><td>${esc(txt)}</td></tr>`;
      }).join("")}</table>`;
    }
    const svcs = (s.s || []).length ? `<div class="svcs">${s.s.map(svcTag).join("")}</div>` : `<p class="addr">${esc(t("d.none"))}</p>`;
    const nav = s.la && s.lo ? `
      <a class="btn primary" href="https://www.google.com/maps/dir/?api=1&destination=${s.la},${s.lo}" target="_blank" rel="noopener">${esc(t("route"))}</a>
      <a class="btn" href="https://waze.com/ul?ll=${s.la},${s.lo}&navigate=yes" target="_blank" rel="noopener">${esc(t("waze"))}</a>` : "";
    return `<div class="details">
      <div><h3>${esc(t("d.prices"))}</h3><div class="fuels">${fuels}</div></div>
      <div><h3>${esc(t("d.hours"))}</h3>${hours}</div>
      <div><h3>${esc(t("d.svc"))}</h3>${svcs}</div>
      <div class="actions">${nav}</div>
    </div>`;
  }

  function cardsHTML(rows, fuel, { limit, depNames, avg } = {}) {
    const min = Math.min(...rows.map(s => s.p[fuel]));
    return rows.slice(0, limit ?? rows.length).map((s, i) => {
      const p = s.p[fuel], best = p === min, st = status(s), open = openCards.has(s.i);
      const tags = [
        best ? `<span class="tag badge">${esc(t("badge"))}</span>` : "",
        `<span class="tag ${st.k === "open" || st.k === "auto" ? "open" : st.k === "closed" ? "closed" : ""}">${esc(st.text)}</span>`,
        s.km != null ? `<span class="tag">${esc(t("km", { x: s.km.toFixed(1).replace(".", ",") }))}</span>` : "",
        depNames ? `<span class="tag">${esc(depNames[s.d] || s.d)}</span>` : "",
        s.h ? `<span class="tag hw">${esc(t("highway"))}</span>` : "",
        `<span class="tag">${esc(t("updated", { ago: ago(s.u[fuel]) }))}</span>`,
      ].join("");
      const save = avg ? (avg - p) * 40 : 0;
      const diff = save >= 0.5 ? `<span class="diff" title="${esc(t("diff.title"))}">${esc(t("diff.cheaper", { x: save.toFixed(2).replace(".", ",") }))}</span>` : "";
      return `<li class="card${best ? " best" : ""}" data-id="${esc(s.i)}">
        <button class="card-main" type="button" aria-expanded="${open}">
          <span class="rank">${i + 1}</span>
          <span class="info">
            <span class="name">${esc(s.c)} <span class="cp">${esc(s.cp)}</span></span>
            <span class="addr">${esc(s.a)}</span>
            <span class="tags">${tags}</span>
          </span>
          <span class="price"><b>${price3(p)}<small>€</small></b>${diff}</span>
        </button>
        ${open ? detailsHTML(s, fuel) : ""}
      </li>`;
    }).join("");
  }

  function statsHTML(prices) {
    if (!prices.length) return "";
    const sorted = prices.slice().sort((a, b) => a - b);
    const avg = sorted.reduce((a, b) => a + b, 0) / sorted.length;
    return `
      <div class="stat low"><span>${esc(t("stat.min"))}</span><b>${euro(sorted[0])}</b></div>
      <div class="stat"><span>${esc(t("stat.avg", { n: num(sorted.length) }))}</span><b>${euro(avg)}</b></div>
      <div class="stat high"><span>${esc(t("stat.max"))}</span><b>${euro(sorted[sorted.length - 1])}</b></div>`;
  }

  // Λίστα με φίλτρα, «δείξε περισσότερα» και κάρτες που ανοίγουν
  function resultList({ source, withDist, onCount }) {
    let shown = PAGE;
    const listEl = $("list"), statsEl = $("stats"), moreEl = $("more");
    const draw = () => {
      const all = source();
      const fuel = F.fuel;
      const withFuel = all.filter(s => s.p[fuel] != null && (!withDist || s.km <= F.radius));
      const rows = all.filter(s => passes(s, fuel, withDist)).sort(sorter(fuel));
      onCount && onCount(rows.length, withFuel.length - rows.length);
      if (!rows.length) {
        statsEl.innerHTML = "";
        listEl.innerHTML = `<li class="empty">${esc(t("empty"))}${activeCount() ? `<br><button class="btn" type="button" data-reset>${esc(t("empty.reset"))}</button>` : ""}</li>`;
        moreEl.hidden = true;
        return;
      }
      const prices = rows.map(s => s.p[fuel]);
      statsEl.innerHTML = statsHTML(prices);
      const avg = prices.reduce((a, b) => a + b, 0) / prices.length;
      listEl.innerHTML = cardsHTML(rows, fuel, { limit: shown, avg });
      moreEl.hidden = rows.length <= shown;
      moreEl.textContent = t("more", { n: num(rows.length - shown) });
    };
    moreEl.addEventListener("click", () => { shown += PAGE; draw(); });
    rerenders.push(draw);
    return { draw, reset() { shown = PAGE; draw(); } };
  }

  // Άνοιγμα/κλείσιμο κάρτας (για όλες τις λίστες της σελίδας)
  document.addEventListener("click", e => {
    const main = e.target.closest(".card-main");
    if (!main) return;
    const id = main.closest(".card").dataset.id;
    openCards.has(id) ? openCards.delete(id) : openCards.add(id);
    rerenders.forEach(fn => fn());
  });

  function stamp(index) {
    const el = $("stamp");
    if (!el || !index) return;
    const draw = () => {
      const d = new Date(index.generated);
      const stale = Date.now() - d > 30 * 36e5;
      el.innerHTML = `<span class="dot"></span>${esc(t("stamp", { when: d.toLocaleString(locale(), { day: "numeric", month: "long", hour: "2-digit", minute: "2-digit" }) }) + (stale ? t("stamp.stale") : ""))}`;
      el.classList.toggle("stale", stale);
    };
    draw();
    rerenders.push(draw);
  }

  function setHint(key, vars = {}, err = false) {
    const h = $("hint");
    if (!h) return;
    h.textContent = key ? t(key, vars) : "";
    h.classList.toggle("err", err);
  }

  // ================= Αναζήτηση πόλης με προτάσεις =================
  function autocomplete(input, list, getItems, onPick) {
    let items = [], active = -1;
    const close = () => { list.hidden = true; input.setAttribute("aria-expanded", "false"); active = -1; };
    const highlight = (name, q) => {
      const i = norm(name).indexOf(q);
      return i < 0 || !q ? esc(name) : esc(name.slice(0, i)) + "<b>" + esc(name.slice(i, i + q.length)) + "</b>" + esc(name.slice(i + q.length));
    };
    const render = async () => {
      const q = norm(input.value);
      if (q.length < 2) return close();
      const all = await getItems();
      const isCp = /^\d/.test(q);
      items = all.filter(c => isCp ? c[1].startsWith(q) : norm(c[0]).startsWith(q))
        .concat(isCp ? [] : all.filter(c => !norm(c[0]).startsWith(q) && norm(c[0]).includes(q)))
        .slice(0, 8);
      list.innerHTML = items.length
        ? items.map((c, i) => `<li role="option" id="sg-${i}" data-i="${i}" aria-selected="false"><span class="s-name">${highlight(c[0], isCp ? "" : q)} <span class="s-meta">${esc(c[1])}</span></span><span class="s-meta">${esc(t("sugg.stations", { n: c[5] }))}</span></li>`).join("")
        : `<li aria-disabled="true"><span class="s-meta">${esc(t("sugg.none"))}</span></li>`;
      list.hidden = false;
      input.setAttribute("aria-expanded", "true");
      active = -1;
    };
    const mark = () => {
      list.querySelectorAll("li[data-i]").forEach(li => li.setAttribute("aria-selected", +li.dataset.i === active));
      input.setAttribute("aria-activedescendant", active >= 0 ? `sg-${active}` : "");
    };
    const pick = i => { const c = items[i]; if (!c) return; input.value = c[0]; close(); onPick(c); };
    input.addEventListener("input", debounce(render, 120));
    input.addEventListener("focus", () => { getItems(); if (input.value.length >= 2) render(); });
    input.addEventListener("keydown", e => {
      if (list.hidden && e.key !== "Enter") return;
      if (e.key === "ArrowDown") { active = Math.min(active + 1, items.length - 1); mark(); e.preventDefault(); }
      else if (e.key === "ArrowUp") { active = Math.max(active - 1, 0); mark(); e.preventDefault(); }
      else if (e.key === "Enter") { e.preventDefault(); pick(active >= 0 ? active : 0); }
      else if (e.key === "Escape") close();
    });
    list.addEventListener("mousedown", e => { const li = e.target.closest("li[data-i]"); if (li) { e.preventDefault(); pick(+li.dataset.i); } });
    document.addEventListener("click", e => { if (!e.target.closest(".field")) close(); });
  }

  // ================= Σελίδα νομού =================
  async function depPage() {
    const code = body.dataset.dep;
    let rows;
    try {
      [rows] = await Promise.all([getJSON(`data/${code}.json`), getJSON("data/index.json").then(stamp)]);
    } catch {
      $("list").innerHTML = `<li class="error">${esc(t("error"))}</li>`;
      return;
    }
    let q = "";
    const available = new Set(rows.flatMap(s => Object.keys(s.p)));
    if (!available.has(F.fuel)) F.fuel = FUELS.find(f => available.has(f));

    const list = resultList({
      source: () => {
        const nq = norm(q);
        return nq ? rows.filter(s => norm(s.c).includes(nq) || s.cp.startsWith(nq)) : rows;
      },
      onCount: (n, hidden) => setHint(null) || ($("hint").textContent = t("dep.found", { n: num(n) }) + (hidden ? t("res.hidden", { n: num(hidden) }) : "")),
    });
    fuelTabs($("tabs"), () => available, () => F.fuel, f => { F.fuel = f; saveF(); list.reset(); });
    const filters = bindFilters(() => list.reset(), { distance: false });
    $("list").addEventListener("click", e => { if (e.target.closest("[data-reset]")) filters.reset(); });

    // Αναζήτηση μέσα στον νομό: προτάσεις από τις πόλεις του νομού + φιλτράρισμα καθώς γράφεις
    const towns = (() => {
      const m = new Map();
      rows.forEach(s => { const k = s.c + "|" + s.cp; const v = m.get(k) || [s.c, s.cp, s.d, s.la, s.lo, 0]; v[5]++; m.set(k, v); });
      return [...m.values()].sort((a, b) => b[5] - a[5]);
    })();
    const input = $("where");
    autocomplete(input, $("sugg"), async () => towns, c => { q = c[0]; list.reset(); });
    input.addEventListener("input", debounce(() => { q = input.value; list.reset(); }, 150));
    list.draw();
  }

  // ================= Αρχική =================
  async function homePage() {
    let index;
    try { index = await getJSON("data/index.json"); } catch { return; } // μένει η στατική έκδοση
    stamp(index);
    setHint("near.private");
    const depNames = Object.fromEntries(Object.entries(index.deps).map(([c, d]) => [c, d.n]));

    // --- Φθηνότερα της Γαλλίας ---
    let topFuel = F.fuel in index.top ? F.fuel : "gazole";
    const drawTop = () => {
      const rows = index.top[topFuel] || [];
      $("top-list").innerHTML = rows.length ? cardsHTML(rows, topFuel, { depNames }) : `<li class="empty">${esc(t("empty"))}</li>`;
      $("nat-stats").innerHTML = `
        <div class="stat low"><span>${esc(t("stat.min"))}</span><b>${rows[0] ? euro(rows[0].p[topFuel]) : "–"}</b></div>
        <div class="stat"><span>${esc(t("stat.natavg"))}</span><b>${index.national[topFuel] ? euro(index.national[topFuel]) : "–"}</b></div>
        <div class="stat"><span>${esc(t("stat.nat"))}</span><b>${num(index.count)}</b></div>`;
      drawDeps();
    };
    // --- Πίνακας νομών ---
    let depQ = "";
    const drawDeps = () => {
      $("deps-fuel").textContent = t("fuel." + topFuel);
      const nq = norm(depQ);
      const deps = Object.entries(index.deps)
        .filter(([c, d]) => d.avg[topFuel] != null && (!nq || norm(d.n).includes(nq) || norm(c).startsWith(nq)))
        .sort((a, b) => a[1].avg[topFuel] - b[1].avg[topFuel]);
      $("deps-body").innerHTML = deps.map(([c, d]) =>
        `<tr><td class="code">${esc(c)}</td><td><a href="${ROOT}departement/${esc(d.s)}/">${esc(d.n)}</a></td>` +
        `<td class="num">${euro(d.avg[topFuel])}</td><td class="num hide-m">${num(d.c)}</td></tr>`).join("");
    };
    $("dep-filter").addEventListener("input", e => { depQ = e.target.value; drawDeps(); });
    fuelTabs($("top-tabs"), () => new Set(Object.keys(index.top)), () => topFuel, f => { topFuel = f; drawTop(); });
    rerenders.push(drawTop);
    drawTop();

    // --- Αποτελέσματα γύρω από ένα σημείο (τοποθεσία ή πόλη) ---
    const near = { pos: null, label: null, rows: [] };
    const res = $("res");
    const list = resultList({
      source: () => near.rows,
      withDist: true,
      onCount: (n, hidden) => {
        $("hint").classList.remove("err");
        $("hint").textContent = t("res.found", { n: num(n), r: F.radius }) + (hidden ? t("res.hidden", { n: num(hidden) }) : "");
      },
    });
    const drawTitle = () => { if (near.pos) $("res-title").textContent = near.label ? t("res.city", { city: near.label }) : t("res.near"); };
    rerenders.push(drawTitle);
    const nearFuels = () => new Set(near.rows.flatMap(s => Object.keys(s.p)));
    fuelTabs($("tabs"), () => (near.rows.length ? nearFuels() : new Set(FUELS)), () => F.fuel, f => { F.fuel = f; saveF(); list.reset(); });
    const filters = bindFilters(() => list.reset(), { distance: true });
    $("list").addEventListener("click", e => { if (e.target.closest("[data-reset]")) filters.reset(); });

    async function showAround(pos, label) {
      near.pos = pos; near.label = label;
      setHint("near.loading");
      // Νομοί που μπορεί να έχουν πρατήρια σε ακτίνα 40 km
      const codes = Object.entries(index.deps).filter(([, d]) => d.la != null)
        .map(([c, d]) => [c, km(pos, d)]).sort((a, b) => a[1] - b[1])
        .filter(([, dist], i) => i < 2 || dist < 120).slice(0, 8).map(([c]) => c);
      try {
        const sets = await Promise.all(codes.map(c => getJSON(`data/${c}.json`)));
        near.rows = sets.flat().filter(s => s.la && s.lo).map(s => ({ ...s, km: km(pos, s) })).filter(s => s.km <= 40);
      } catch {
        setHint("error", {}, true);
        return;
      }
      if (!nearFuels().has(F.fuel)) F.fuel = FUELS.find(f => nearFuels().has(f)) || "gazole";
      res.hidden = false;
      drawTitle();
      rerenders.forEach(fn => fn());
      list.reset();
      res.scrollIntoView({ behavior: "smooth", block: "start" });
    }

    const input = $("where");
    autocomplete(input, $("sugg"), () => getJSON("data/cities.json"), c => {
      input.blur();
      showAround({ la: c[3], lo: c[4] }, `${c[0]} (${c[1]})`);
    });

    $("locate").addEventListener("click", () => {
      if (!navigator.geolocation) return setHint("near.unsupported", {}, true);
      setHint("near.locating");
      navigator.geolocation.getCurrentPosition(
        p => { input.value = ""; showAround({ la: p.coords.latitude, lo: p.coords.longitude }, null); },
        err => setHint(err.code === 1 ? "near.denied" : "near.failed", {}, true),
        { enableHighAccuracy: false, timeout: 15000, maximumAge: 300000 }
      );
    });
  }

  // ================= Εκκίνηση =================
  applyLang();
  if (KIND === "dep") depPage();
  else if (KIND === "home") homePage();
  // Ανανέωση της κατάστασης «ανοιχτό/κλειστό» κάθε λεπτό
  setInterval(() => rerenders.forEach(fn => fn()), 60000);
})();
