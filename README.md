 ## Arc’teryx Outlet Scraper

 Scrapes Arc’teryx Outlet product data with Playwright (Python). Focuses on Men's outlet by default and can be redirected to any outlet category.

 ### Features
 - Grid discovery with infinite scroll until stable
 - PDP parsing per color and size with price and discount heuristics
 - CSV sink with de-duplication by `(product_url, color, size)`
 - Politeness: jittered delays, custom user-agent, optional proxy

 ### Requirements
 - Python 3.10+
 - macOS/Linux

 ### Setup
 ```bash
 cd /Users/fengzhiping/arc-site-scraper
 python3 -m venv .venv
 source .venv/bin/activate
 pip install -r requirements.txt
 python -m playwright install chromium
 ```

 Optional environment variables (create a `.env` in the project root):
 ```
 OUTLET_BASE=https://outlet.arcteryx.com/us/en
 INCLUDE_VEILANCE=false
 CONCURRENCY=1
 REQUEST_JITTER_MS_MIN=700
 REQUEST_JITTER_MS_MAX=1500
 PDP_DELAY_MS=2500
 USER_AGENT=TopologyScraper/1.0 (+contact: you@example.com)
 PROXY_URL=
 PROXY_ROTATE_EVERY=10
 OUTPUT_CSV=arcteryx_outlet.csv
 OUTPUT_DB=arcteryx_outlet.sqlite
 ALERT_WEBHOOK=
 ```

 ### Usage
 - Default (Men's outlet):
 ```bash
 python scraper.py
 ```
 - Specific outlet category:
 ```bash
 python scraper.py https://outlet.arcteryx.com/us/en/c/mens
 ```

 Output CSV: `arcteryx_outlet.csv`

 ### Notes
 - Selectors are heuristic-based and may need adjustment if the site markup changes.
 - Keep crawl rates polite to avoid blocks; consider running off-hours.
 - This project is for personal research and price tracking. Respect robots/ToS.


