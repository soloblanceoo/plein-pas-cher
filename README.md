⛽ Plein Pas Cher

Find the cheapest fuel in France: every station, official prices, updated automatically.

🔗 Live site: https://soloblanceoo.github.io/plein-pas-cher/

Plein Pas Cher screenshot

What it does

Plein Pas Cher compares fuel prices at around 10,000 petrol stations across France, including Corsica and the overseas departments. It ranks them from cheapest to most expensive, so drivers can save money on every fill-up.

Search any town or postcode with live suggestions as you type
"Around me": uses the phone's location to show the cheapest stations nearby (5–40 km radius), with distance
Smart filters:
open right now
24/7 card pumps
hide motorway stations
services: car wash, shop, toilets, air, EV charging, food, ATM
price freshness
sort by price, distance or last update
Station cards that expand to show all fuel prices, weekly opening hours, services, and one-tap directions in Google Maps or Waze
101 department pages (e.g. /departement/75-paris/) with their own title and description, so they can be found on Google
Bilingual: French by default, with a Greek language switch
Privacy-friendly: no cookies, no tracking, and the location never leaves the device
How it works
prix-carburants.gouv.fr  ──►  build.py (GitHub Actions, every 6 h)  ──►  static site on GitHub Pages
   official open data          download · clean · split · render          HTML + JSON, no server
Data: the French Ministry of Economy publishes every station's prices as open data (Licence Ouverte). French law requires stations to report their prices.
Build (build.py, Python, standard library only):
downloads and parses the XML feed
removes stale prices (older than 7 days), obvious typos (outliers against the national median), closed stations and fuels out of stock
converts station hours from French local time
writes one compact JSON file per department, a national summary and a town index for search
generates static HTML pages, a sitemap and robots.txt
Automation (GitHub Actions):
rebuilds and redeploys every 6 hours
retries if the source is down
never publishes when the data looks broken, so the last good version stays live
a monthly keep-alive job stops GitHub from disabling the schedule
Front end: plain HTML, CSS and JavaScript, with no framework and no build step. It loads only the departments it needs, so pages stay fast on mobile.
Tech stack
Area	Tools
Data pipeline	Python 3 (xml.etree, statistics, zoneinfo)
Automation & hosting	GitHub Actions, GitHub Pages
Front end	HTML, CSS (dark theme, responsive), vanilla JavaScript
Data source	prix-carburants.gouv.fr open data
Run it locally
bash
python3 build.py                          # downloads live data and builds the site into _site/
cd _site && python3 -m http.server 8000   # open http://localhost:8000
Project structure
build.py            data pipeline + page generator
departements.py     the 101 French departments and postcode mapping
config.json         site name, URL, contact email
static/             style.css · app.js · i18n.js (FR/EL translations) · favicon
.github/workflows/  deploy.yml (build every 6 h) · keepalive.yml
Background

The project started as a small page comparing fuel prices in Athens. Greece has no free, reusable per-station price data, so I looked for countries that publish it as open data. France had the most complete feed, so I rebuilt the project as a full site covering the whole country.

Data licence

Fuel price data © Ministère de l'Économie, des Finances et de la Souveraineté industrielle et numérique, reused under the Licence Ouverte / Open Licence 2.0. Prices are reported by the stations themselves and may differ at the pump.
