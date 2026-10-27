"""
Χτίζει όλο το site στον φάκελο _site/.

1. Κατεβάζει τις επίσημες τιμές καυσίμων όλης της Γαλλίας (prix-carburants.gouv.fr).
2. Καθαρίζει λάθη (παλιές τιμές, εξωφρενικές τιμές, κλειστά πρατήρια, ελλείψεις).
3. Κρατάει ωράρια, 24ωρα μηχανήματα και υπηρεσίες κάθε πρατηρίου.
4. Γράφει ένα JSON ανά νομό, ένα συνοπτικό index.json και μια λίστα πόλεων για την αναζήτηση.
5. Φτιάχνει στατικές σελίδες HTML (αρχική, μία ανά νομό, νομικά), sitemap και robots.

Τρέξιμο:        python build.py
Δοκιμή offline: python build.py --file δείγμα.xml
"""

from __future__ import annotations

import argparse
import html
import io
import json
import re
import shutil
import statistics
import sys
import unicodedata
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from departements import DEPARTEMENTS, dep_from_cp

ROOT = Path(__file__).parent
OUT = ROOT / "_site"
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
SITE_NAME = CONFIG.get("site_name") or "Plein Pas Cher"
SITE_URL = (CONFIG.get("site_url") or "").rstrip("/")
CONTACT = CONFIG.get("contact_email") or ""

SOURCE_URL = "https://donnees.roulez-eco.fr/opendata/instantane"
MAX_AGE_DAYS = 7
MIN_STATIONS = 1000  # λιγότερα από αυτά σε όλη τη Γαλλία = κάτι χάλασε, δεν δημοσιεύουμε

# Όνομα στην πηγή -> σύντομο κλειδί
FUELS = {"Gazole": "gazole", "E10": "e10", "SP95": "sp95", "SP98": "sp98", "E85": "e85", "GPLc": "gplc"}
FUEL_FR = {"gazole": "Gazole", "e10": "SP95-E10", "sp95": "SP95", "sp98": "SP98", "e85": "E85", "gplc": "GPL"}

# Υπηρεσίες: κομμάτι κειμένου στην πηγή -> σύντομος κωδικός
SERVICES = [
    ("lavage", "wash"), ("boutique", "shop"), ("toilettes", "wc"), ("gonflage", "air"),
    ("bornes electriques", "ev"), ("recharge", "ev"), ("restauration", "food"), ("bar", "food"),
    ("dab", "atm"), ("distributeur automatique de billets", "atm"), ("automate cb", "cb24"),
    ("poids lourds", "hgv"), ("reparation", "repair"), ("entretien", "repair"), ("camping", "camper"),
]
SERVICE_FR = {"wash": "Lavage", "shop": "Boutique", "wc": "Toilettes", "air": "Gonflage", "ev": "Recharge électrique",
              "food": "Restauration", "atm": "Distributeur de billets", "cb24": "Automate CB 24/24",
              "hgv": "Piste poids lourds", "repair": "Atelier", "camper": "Aire camping-car"}
DAYS_FR = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]

NOW = datetime.now(timezone.utc)

try:
    from zoneinfo import ZoneInfo
    PARIS = ZoneInfo("Europe/Paris")
except Exception:  # χωρίς βάση ζωνών ώρας: δεχόμαστε μικρό λάθος 1-2 ωρών
    PARIS = timezone.utc


# ---------- Βοηθητικά ----------

def slugify(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def plain(s: str) -> str:
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()


def euro(v: float) -> str:
    return f"{v:.3f}".replace(".", ",") + " €"


def esc(s) -> str:
    return html.escape(str(s), quote=True)


def parse_price(raw):
    try:
        v = float(str(raw).replace(",", "."))
    except ValueError:
        return None
    if v > 100:  # παλιά μορφή: χιλιοστά του ευρώ
        v /= 1000
    return round(v, 3) if 0.3 < v < 5 else None


def parse_date(raw):
    """Οι ώρες της πηγής είναι ώρα Γαλλίας. Τις γυρίζουμε σε UTC."""
    if not raw:
        return None
    raw = raw.strip().replace("T", " ")[:19]
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=PARIS).astimezone(timezone.utc)
        except ValueError:
            pass
    return None


def parse_coord(raw):
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None
    if abs(v) > 1000:  # η πηγή δίνει ×100000
        v /= 100000
    return round(v, 5) if v else None


def parse_hm(raw):
    """'07.30' ή '07:30' -> 450 λεπτά."""
    m = re.match(r"^\s*(\d{1,2})[.:h](\d{2})", raw or "")
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    return h * 60 + mi if h <= 24 and mi < 60 else None


def nice_title(s: str) -> str:
    """'RUE DE LA GARE' -> 'Rue de la Gare'"""
    small = {"de", "du", "des", "la", "le", "les", "et", "d", "l", "sur", "en", "au", "aux"}
    words = re.split(r"(\s+|-|')", (s or "").strip().lower())
    out = []
    for i, w in enumerate(words):
        out.append(w if (w in small and i > 0) else w[:1].upper() + w[1:])
    return "".join(out)


# ---------- Δεδομένα ----------

def download() -> bytes:
    req = urllib.request.Request(SOURCE_URL, headers={"User-Agent": "plein-pas-cher/1.0"})
    with urllib.request.urlopen(req, timeout=90) as r:
        raw = r.read()
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        name = next(n for n in zf.namelist() if n.lower().endswith(".xml"))
        return zf.read(name)


def in_rupture(pdv, fuel_name: str) -> bool:
    for r in pdv.findall("rupture"):
        if r.get("nom") != fuel_name:
            continue
        start, end = parse_date(r.get("debut")), parse_date(r.get("fin"))
        if (start is None or start <= NOW) and (end is None or end > NOW):
            return True
    return False


def parse_hours(pdv):
    """
    Ωράριο ανά μέρα (0 = Δευτέρα ... 6 = Κυριακή) σε λεπτά: [[άνοιγμα, κλείσιμο], ...].
    Κλείσιμο > 1440 σημαίνει ότι κλείνει μετά τα μεσάνυχτα. [] = κλειστό όλη μέρα.
    Επιστρέφει (ωράριο ή None, 24ωρο μηχάνημα True/False).
    """
    hor = pdv.find("horaires")
    if hor is not None:
        auto24 = hor.get("automate-24-24") == "1"
        week = [None] * 7
        for jour in hor.findall("jour"):
            try:
                d = int(jour.get("id")) - 1
            except (TypeError, ValueError):
                continue
            if not 0 <= d <= 6:
                continue
            if jour.get("ferme") == "1":
                week[d] = []
                continue
            slots = []
            for h in jour.findall("horaire"):
                a, b = parse_hm(h.get("ouverture")), parse_hm(h.get("fermeture"))
                if a is None or b is None:
                    continue
                if a == b or (a == 0 and b >= 1439):
                    slots.append([0, 1440])  # ανοιχτό όλη μέρα
                else:
                    slots.append([a, b if b > a else b + 1440])
            week[d] = slots
        known = any(w is not None for w in week)
        return (week if known else None), auto24

    # Παλιά μορφή: <ouverture debut="06:00" fin="22:00" saufjour="Dimanche"/>
    ouv = pdv.find("ouverture")
    if ouv is not None:
        a, b = parse_hm(ouv.get("debut")), parse_hm(ouv.get("fin"))
        if a is not None and b is not None:
            closed = {d.strip().lower() for d in (ouv.get("saufjour") or "").split(",")}
            slot = [0, 1440] if a == b else [a, b if b > a else b + 1440]
            return [[] if DAYS_FR[i] in closed else [slot] for i in range(7)], False
    return None, False


def parse_services(pdv) -> list[str]:
    found = []
    for s in pdv.iter("service"):
        text = plain(s.text)
        for needle, code in SERVICES:
            if needle in text and code not in found:
                found.append(code)
    return found


def parse(xml_bytes: bytes) -> list[dict]:
    root = ET.fromstring(xml_bytes)
    cutoff = NOW - timedelta(days=MAX_AGE_DAYS)
    stations = []
    for pdv in root.iter("pdv"):
        dep = dep_from_cp(pdv.get("cp"))
        if not dep:
            continue
        ferm = pdv.find("fermeture")
        if ferm is not None and ferm.get("type") == "D":  # οριστικά κλειστό
            continue

        prices, updated = {}, {}
        for prix in pdv.findall("prix"):
            name = prix.get("nom", "")
            key = FUELS.get(name)
            if not key or in_rupture(pdv, name):
                continue
            v, when = parse_price(prix.get("valeur", "")), parse_date(prix.get("maj", ""))
            if v is None or when is None or when < cutoff:
                continue
            prices[key] = v
            updated[key] = when.strftime("%Y-%m-%d %H:%M")
        if not prices:
            continue

        st = {
            "i": pdv.get("id"),
            "a": nice_title(pdv.findtext("adresse")),
            "c": nice_title(pdv.findtext("ville")),
            "cp": (pdv.get("cp") or "").strip().zfill(5),
            "d": dep,
            "la": parse_coord(pdv.get("latitude")),
            "lo": parse_coord(pdv.get("longitude")),
            "p": prices,
            "u": updated,
        }
        if pdv.get("pop") == "A":
            st["h"] = 1  # αυτοκινητόδρομος
        hours, auto24 = parse_hours(pdv)
        services = parse_services(pdv)
        if auto24 or "cb24" in services:
            st["a24"] = 1
        if hours:
            st["o"] = hours
        if services:
            st["s"] = services
        stations.append(st)
    return stations


def drop_outliers(stations: list[dict]) -> int:
    """Αφαιρεί τιμές που είναι προφανή λάθη πληκτρολόγησης (π.χ. 0,199 € ή 9,99 €)."""
    removed = 0
    for key in FUEL_FR:
        vals = [s["p"][key] for s in stations if key in s["p"]]
        if len(vals) < 20:
            continue
        med = statistics.median(vals)
        for s in stations:
            v = s["p"].get(key)
            if v is not None and not (med * 0.6 <= v <= med * 1.6):
                del s["p"][key]
                s["u"].pop(key, None)
                removed += 1
    stations[:] = [s for s in stations if s["p"]]
    return removed


def summarize(stations: list[dict]) -> dict:
    deps = {}
    for code, name in DEPARTEMENTS.items():
        rows = [s for s in stations if s["d"] == code]
        if not rows:
            continue
        coords = [(s["la"], s["lo"]) for s in rows if s["la"] and s["lo"]]
        info = {"n": name, "s": f"{code.lower()}-{slugify(name)}", "c": len(rows), "avg": {}, "min": {}}
        if coords:
            info["la"] = round(statistics.median(c[0] for c in coords), 4)
            info["lo"] = round(statistics.median(c[1] for c in coords), 4)
        for key in FUEL_FR:
            vals = [s["p"][key] for s in rows if key in s["p"] and not s.get("h")]
            if vals:
                info["avg"][key] = round(statistics.fmean(vals), 3)
                info["min"][key] = min(vals)
        deps[code] = info

    national, top = {}, {}
    for key in FUEL_FR:
        rows = sorted((s for s in stations if key in s["p"] and not s.get("h")), key=lambda s: s["p"][key])
        if rows:
            national[key] = round(statistics.fmean(s["p"][key] for s in rows), 3)
            top[key] = rows[:10]

    return {
        "generated": NOW.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "count": len(stations),
        "national": national,
        "top": top,
        "deps": deps,
    }


def cities(stations: list[dict]) -> list[list]:
    """Λίστα πόλεων για την αναζήτηση: [όνομα, ΤΚ, νομός, lat, lon, πλήθος πρατηρίων]."""
    groups: dict[tuple, list] = {}
    for s in stations:
        if s["la"] and s["lo"] and s["c"]:
            groups.setdefault((s["c"], s["cp"], s["d"]), []).append(s)
    out = []
    for (c, cp, d), rows in groups.items():
        out.append([c, cp, d, round(statistics.median(r["la"] for r in rows), 4),
                    round(statistics.median(r["lo"] for r in rows), 4), len(rows)])
    out.sort(key=lambda r: (-r[5], r[0]))
    return out


# ---------- HTML ----------

LOGO = ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" '
        'stroke-linejoin="round" aria-hidden="true"><path d="M3 22V5a2 2 0 0 1 2-2h7a2 2 0 0 1 2 2v17"/>'
        '<path d="M2 22h13"/><path d="M3 10h11"/><path d="M14 13h2a2 2 0 0 1 2 2v3a1.5 1.5 0 0 0 3 0V9l-3-3"/></svg>')
ICON_LOCATE = ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" '
               'aria-hidden="true"><circle cx="12" cy="12" r="3"/><circle cx="12" cy="12" r="8"/>'
               '<path d="M12 1v3M12 20v3M1 12h3M20 12h3"/></svg>')
ICON_SEARCH = ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" '
               'aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>')
ICON_FILTER = ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" '
               'aria-hidden="true"><path d="M4 6h16M7 12h10M10 18h4"/></svg>')

ASSET_V = NOW.strftime("%Y%m%d%H")


def page(*, kind, title, desc, path, body, root, extra_head="", data_attrs=""):
    canonical = f'<link rel="canonical" href="{esc(SITE_URL + path)}">' if SITE_URL else ""
    og_url = f'<meta property="og:url" content="{esc(SITE_URL + path)}">' if SITE_URL else ""
    return f"""<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>{esc(title)}</title>
<meta name="description" content="{esc(desc)}">
{canonical}
<meta property="og:type" content="website">
<meta property="og:site_name" content="{esc(SITE_NAME)}">
<meta property="og:title" content="{esc(title)}">
<meta property="og:description" content="{esc(desc)}">
{og_url}
<meta name="color-scheme" content="dark">
<meta name="theme-color" content="#0a0c0b">
<link rel="icon" href="{root}static/favicon.svg" type="image/svg+xml">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=Space+Grotesk:wght@500;600;700&display=swap">
<link rel="stylesheet" href="{root}static/style.css?v={ASSET_V}">
{extra_head}
</head>
<body data-page="{kind}" data-root="{root}" {data_attrs}>
<nav class="top">
  <div class="wrap top-in">
    <a class="brand" href="{root}"><span class="logo">{LOGO}</span><span>{esc(SITE_NAME)}</span></a>
    <div class="lang" role="group" aria-label="Langue / Γλώσσα">
      <button type="button" data-lang="fr" aria-pressed="true">FR</button>
      <button type="button" data-lang="el" aria-pressed="false">EL</button>
    </div>
  </div>
</nav>
<main class="wrap">
{body}
</main>
<footer class="wrap">
  <p data-i18n="foot.source">Prix officiels publiés par les stations sur prix-carburants.gouv.fr (ministère de l'Économie), sous Licence Ouverte. Mise à jour automatique plusieurs fois par jour. Vérifiez le prix à la pompe.</p>
  <p><a href="{root}mentions-legales/" data-i18n="foot.legal">Mentions légales et confidentialité</a> · © {NOW.year} {esc(SITE_NAME)}</p>
</footer>
<script src="{root}static/i18n.js?v={ASSET_V}"></script>
<script src="{root}static/app.js?v={ASSET_V}"></script>
</body>
</html>
"""


def search_box(placeholder_key: str, placeholder: str, locate: bool) -> str:
    loc = (f'<button class="locate" id="locate" type="button" aria-label="Autour de moi" data-i18n-label="near.btn">'
           f'{ICON_LOCATE}<span data-i18n="near.short">Autour de moi</span></button>') if locate else ""
    return f"""
  <div class="search" role="search">
    <div class="field">
      {ICON_SEARCH}
      <input id="where" type="search" autocomplete="off" spellcheck="false" enterkeyhint="search"
             role="combobox" aria-expanded="false" aria-controls="sugg" aria-autocomplete="list"
             placeholder="{esc(placeholder)}" data-i18n-ph="{placeholder_key}">
      <ul class="sugg" id="sugg" role="listbox" hidden></ul>
    </div>
    {loc}
  </div>
  <p class="hint" id="hint"></p>"""


def toolbar(distance: bool) -> str:
    """Καύσιμα + κουμπί φίλτρων + πάνελ φίλτρων. Κοινό για αρχική και νομούς."""
    radius = """
        <div class="fgroup" id="g-radius">
          <span class="flabel" data-i18n="f.radius">Rayon</span>
          <div class="seg" data-filter="radius">
            <button type="button" data-v="5">5 km</button><button type="button" data-v="10">10 km</button>
            <button type="button" data-v="20">20 km</button><button type="button" data-v="40">40 km</button>
          </div>
        </div>""" if distance else ""
    dist_sort = '<button type="button" data-v="dist" data-i18n="sort.dist">Distance</button>' if distance else ""
    services = "".join(
        f'<button type="button" class="chip" data-svc="{k}" aria-pressed="false" data-i18n="svc.{k}">{esc(SERVICE_FR[k])}</button>'
        for k in ("wash", "shop", "wc", "air", "ev", "food", "atm"))
    return f"""
    <div class="toolbar">
      <div class="tabs" role="tablist" id="tabs" aria-label="Carburant"></div>
      <button class="fbtn" id="fbtn" type="button" aria-expanded="false" aria-controls="fpanel">
        {ICON_FILTER}<span data-i18n="f.btn">Filtres</span><b class="count" id="fcount" hidden></b>
      </button>
    </div>
    <div class="fpanel" id="fpanel" hidden>
      <div class="fgrid">
        <div class="fgroup">
          <span class="flabel" data-i18n="f.when">Disponibilité</span>
          <label class="toggle"><input type="checkbox" data-filter="open"><span></span><b data-i18n="f.open">Ouvert maintenant</b></label>
          <label class="toggle"><input type="checkbox" data-filter="a24"><span></span><b data-i18n="f.a24">24h/24 (automate carte)</b></label>
          <label class="toggle"><input type="checkbox" data-filter="noHw"><span></span><b data-i18n="f.nohw">Masquer les autoroutes</b></label>
        </div>
        <div class="fgroup">
          <span class="flabel" data-i18n="f.svc">Services</span>
          <div class="chips">{services}</div>
        </div>
        <div class="fgroup">
          <span class="flabel" data-i18n="f.fresh">Prix mis à jour</span>
          <div class="seg" data-filter="fresh">
            <button type="button" data-v="168" data-i18n="fresh.all">7 jours</button>
            <button type="button" data-v="72" data-i18n="fresh.3d">3 jours</button>
            <button type="button" data-v="24" data-i18n="fresh.24h">24 h</button>
          </div>
        </div>
        <div class="fgroup">
          <span class="flabel" data-i18n="f.sort">Trier par</span>
          <div class="seg" data-filter="sort">
            <button type="button" data-v="price" data-i18n="sort.price">Prix</button>{dist_sort}
            <button type="button" data-v="fresh" data-i18n="sort.fresh">Mise à jour</button>
          </div>
        </div>{radius}
      </div>
      <button class="reset" id="freset" type="button" data-i18n="f.reset">Réinitialiser les filtres</button>
    </div>"""


def static_list(rows, key, limit=10):
    """Λίστα σε καθαρό HTML: τη βλέπουν οι μηχανές αναζήτησης και όσοι δεν έχουν JavaScript."""
    if not rows:
        return '<li class="empty">Aucune station.</li>'
    out = []
    for i, s in enumerate(rows[:limit], 1):
        out.append(
            f'<li class="card{" best" if i == 1 else ""}"><div class="card-main">'
            f'<span class="rank">{i}</span>'
            f'<div class="info"><div class="name">{esc(s["c"])} <span class="cp">{esc(s["cp"])}</span></div>'
            f'<div class="addr">{esc(s["a"])}</div></div>'
            f'<div class="price"><b>{euro(s["p"][key])}</b></div></div></li>')
    return "\n".join(out)


def stamp_html():
    return f'<p class="stamp" id="stamp"><span class="dot"></span>Mis à jour le {NOW:%d/%m/%Y à %Hh%M} UTC</p>'


def build_home(summary):
    nat = summary["national"]
    deps_sorted = sorted(summary["deps"].items(), key=lambda kv: kv[1]["avg"].get("gazole", 99))
    table = "\n".join(
        f'<tr><td class="code">{esc(c)}</td><td><a href="departement/{esc(d["s"])}/">{esc(d["n"])}</a></td>'
        f'<td class="num">{euro(d["avg"]["gazole"]) if "gazole" in d["avg"] else "–"}</td>'
        f'<td class="num hide-m">{d["c"]}</td></tr>'
        for c, d in deps_sorted)
    body = f"""
  <header class="hero">
    {stamp_html()}
    <h1 data-i18n="home.h1">Le plein au meilleur prix, partout en France</h1>
    <p class="lead" data-i18n="home.lead" data-n="{summary['count']}">{summary['count']:,} stations comparées avec les prix officiels.</p>
{search_box("search.ph.home", "Ville ou code postal…", True)}
  </header>

  <section id="res" class="results" hidden>
    <div class="res-head">
      <h2 id="res-title"></h2>
    </div>
{toolbar(True)}
    <div class="stats" id="stats"></div>
    <ol class="list" id="list" aria-live="polite"></ol>
    <button class="btn more" id="more" type="button" hidden></button>
  </section>

  <section id="nat">
    <div class="sec-head">
      <h2 data-i18n="home.top">Les moins chères de France</h2>
      <div class="tabs small" role="tablist" id="top-tabs" aria-label="Carburant"></div>
    </div>
    <div class="stats" id="nat-stats">
      <div class="stat"><span data-i18n="stat.natavg">Moyenne nationale</span><b>{euro(nat["gazole"]) if "gazole" in nat else "–"}</b></div>
    </div>
    <ol class="list" id="top-list">
{static_list(summary["top"].get("gazole", []), "gazole")}
    </ol>
  </section>

  <section>
    <div class="sec-head">
      <h2 data-i18n="home.deps">Prix moyen par département</h2>
      <input class="mini-search" id="dep-filter" type="search" placeholder="Filtrer…" data-i18n-ph="home.depfilter" autocomplete="off">
    </div>
    <div class="table-wrap">
    <table class="deps">
      <thead><tr><th>N°</th><th data-i18n="th.dep">Département</th><th class="num" id="deps-fuel">Gazole</th><th class="num hide-m" data-i18n="th.n">Stations</th></tr></thead>
      <tbody id="deps-body">
{table}
      </tbody>
    </table>
    </div>
  </section>
"""
    desc = (f"Comparez les prix des carburants dans {summary['count']:,} stations en France. "
            f"Gazole, SP95-E10, SP98, E85, GPL : trouvez la station la moins chère et ouverte près de chez vous. Prix officiels mis à jour plusieurs fois par jour.")
    return page(kind="home", title=f"Prix carburant en France : station la moins chère près de chez vous | {SITE_NAME}",
                desc=desc, path="/", body=body, root="")


def build_dep(code, info, rows):
    root = "../../"
    name = info["n"]
    road = [s for s in rows if not s.get("h")]
    g = sorted((s for s in road if "gazole" in s["p"]), key=lambda s: s["p"]["gazole"])
    e = sorted((s for s in road if "e10" in s["p"]), key=lambda s: s["p"]["e10"])
    parts = []
    if g:
        parts.append(f"gazole dès {euro(g[0]['p']['gazole'])}")
    if e:
        parts.append(f"SP95-E10 dès {euro(e[0]['p']['e10'])}")
    desc = (f"Prix des carburants aujourd'hui, {name} ({code}) : {', '.join(parts)}. "
            f"{len(rows)} stations comparées, avec horaires et services.")
    example = rows[0]["c"] if rows else ""
    body = f"""
  <header class="hero compact">
    <p class="crumbs"><a href="{root}" data-i18n="crumb.home">France</a> <span>›</span> {esc(name)} ({esc(code)})</p>
    {stamp_html()}
    <h1><span data-i18n="dep.h1">Carburant pas cher</span> · {esc(name)} <span class="code-pill">{esc(code)}</span></h1>
    <p class="lead" data-i18n="dep.lead" data-n="{len(rows)}">{len(rows)} stations, de la moins chère à la plus chère.</p>
{search_box("search.ph.dep", f"Ville ou code postal, ex. {example}", False)}
  </header>
  <section class="results">
{toolbar(False)}
    <div class="stats" id="stats"></div>
    <h2 class="sr" data-i18n="dep.listh">Stations les moins chères</h2>
    <ol class="list" id="list" aria-live="polite">
{static_list(g, "gazole", 15)}
    </ol>
    <button class="btn more" id="more" type="button" hidden></button>
  </section>
"""
    title = f"Prix carburant {name} ({code}) : stations les moins chères aujourd'hui | {SITE_NAME}"
    return page(kind="dep", title=title, desc=desc, path=f"/departement/{info['s']}/", body=body, root=root,
                data_attrs=f'data-dep="{esc(code)}"')


def build_legal():
    root = "../"
    contact = (f'<a href="mailto:{esc(CONTACT)}">{esc(CONTACT)}</a>' if CONTACT
               else "<em>adresse e-mail à compléter dans config.json</em>")
    body = f"""
  <header class="hero compact"><h1 data-i18n="legal.h1">Mentions légales et confidentialité</h1></header>
  <div class="prose" lang="fr" data-only="fr">
    <h2>Éditeur</h2>
    <p>{esc(SITE_NAME)} est un site personnel et non commercial. Contact : {contact}</p>
    <h2>Hébergement</h2>
    <p>GitHub, Inc., 88 Colin P Kelly Jr Street, San Francisco, CA 94107, États-Unis (GitHub Pages).</p>
    <h2>Source des données</h2>
    <p>Les prix, horaires et services proviennent du site officiel <a href="https://www.prix-carburants.gouv.fr/">prix-carburants.gouv.fr</a> (ministère de l'Économie, des Finances et de la Souveraineté industrielle et numérique), réutilisés sous <a href="https://www.etalab.gouv.fr/licence-ouverte-open-licence/">Licence Ouverte / Open Licence</a>. Ils sont déclarés par les stations elles-mêmes ; {esc(SITE_NAME)} ne peut garantir leur exactitude. Les prix de plus de {MAX_AGE_DAYS} jours et les valeurs manifestement erronées ne sont pas affichés. Vérifiez toujours le prix à la pompe.</p>
    <h2>Confidentialité</h2>
    <p>Ce site n'utilise ni cookies, ni publicité, ni outil de mesure d'audience. Si vous utilisez « autour de moi », votre position est utilisée uniquement dans votre navigateur et n'est jamais envoyée. Vos choix (langue, carburant, filtres) sont enregistrés dans votre navigateur (stockage local). Les polices de caractères sont chargées depuis Google Fonts.</p>
  </div>
  <div class="prose" lang="el" data-only="el" hidden>
    <h2>Εκδότης</h2>
    <p>Το {esc(SITE_NAME)} είναι προσωπικό, μη εμπορικό site. Επικοινωνία: {contact}</p>
    <h2>Φιλοξενία</h2>
    <p>GitHub, Inc., 88 Colin P Kelly Jr Street, San Francisco, CA 94107, ΗΠΑ (GitHub Pages).</p>
    <h2>Πηγή δεδομένων</h2>
    <p>Οι τιμές, τα ωράρια και οι υπηρεσίες προέρχονται από το επίσημο <a href="https://www.prix-carburants.gouv.fr/">prix-carburants.gouv.fr</a> του γαλλικού υπουργείου Οικονομικών, υπό την άδεια <a href="https://www.etalab.gouv.fr/licence-ouverte-open-licence/">Licence Ouverte</a>. Τα δηλώνουν τα ίδια τα πρατήρια, οπότε δεν μπορούμε να εγγυηθούμε ότι είναι ακριβή. Τιμές παλαιότερες από {MAX_AGE_DAYS} ημέρες και προφανή λάθη δεν εμφανίζονται. Έλεγξε πάντα την τιμή στην αντλία.</p>
    <h2>Απόρρητο</h2>
    <p>Το site δεν χρησιμοποιεί cookies, διαφημίσεις ή εργαλεία μέτρησης επισκεψιμότητας. Αν πατήσεις «κοντά μου», η τοποθεσία σου χρησιμοποιείται μόνο μέσα στον browser σου. Οι επιλογές σου (γλώσσα, καύσιμο, φίλτρα) αποθηκεύονται στον browser σου. Οι γραμματοσειρές φορτώνονται από το Google Fonts.</p>
  </div>
"""
    return page(kind="legal", title=f"Mentions légales | {SITE_NAME}", desc=f"Mentions légales, sources et confidentialité de {SITE_NAME}.",
                path="/mentions-legales/", body=body, root=root, extra_head='<meta name="robots" content="noindex">')


def write(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", help="τοπικό XML αντί για λήψη (για δοκιμή)")
    ap.add_argument("--min-stations", type=int, default=MIN_STATIONS)
    args = ap.parse_args()

    xml_bytes = Path(args.file).read_bytes() if args.file else download()
    stations = parse(xml_bytes)
    removed = drop_outliers(stations)
    if len(stations) < args.min_stations:
        print(f"ΣΦΑΛΜΑ: μόνο {len(stations)} πρατήρια. Δεν δημοσιεύεται τίποτα.", file=sys.stderr)
        return 1

    summary = summarize(stations)

    if OUT.exists():
        shutil.rmtree(OUT)
    shutil.copytree(ROOT / "static", OUT / "static")

    write(OUT / "data" / "index.json", dump(summary))
    write(OUT / "data" / "cities.json", dump(cities(stations)))
    for code, info in summary["deps"].items():
        rows = [s for s in stations if s["d"] == code]
        write(OUT / "data" / f"{code}.json", dump(rows))
        write(OUT / "departement" / info["s"] / "index.html", build_dep(code, info, rows))

    write(OUT / "index.html", build_home(summary))
    write(OUT / "mentions-legales" / "index.html", build_legal())
    write(OUT / "404.html", page(kind="404", title=f"Page introuvable | {SITE_NAME}", desc="", path="/404", root="/",
                                 body='<header class="hero compact"><h1>404</h1><p><a href="/">Accueil</a></p></header>'))
    (OUT / ".nojekyll").write_text("")

    if SITE_URL:
        urls = [f"{SITE_URL}/"] + [f"{SITE_URL}/departement/{d['s']}/" for d in summary["deps"].values()]
        day = NOW.strftime("%Y-%m-%d")
        write(OUT / "sitemap.xml", '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
              + "\n".join(f"<url><loc>{esc(u)}</loc><lastmod>{day}</lastmod><changefreq>daily</changefreq></url>" for u in urls)
              + "\n</urlset>\n")
        write(OUT / "robots.txt", f"User-agent: *\nAllow: /\nSitemap: {SITE_URL}/sitemap.xml\n")
    else:
        write(OUT / "robots.txt", "User-agent: *\nAllow: /\n")

    with_hours = sum(1 for s in stations if "o" in s)
    print(f"OK: {len(stations)} πρατήρια, {len(summary['deps'])} νομοί, {with_hours} με ωράριο, "
          f"{removed} λανθασμένες τιμές αφαιρέθηκαν -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
