# Arc’teryx Outlet Scraper — Requirements Spec

_Last updated: 2025-08-23 (America/Vancouver)_

## 1) Objective

Build a robust, polite scraper that collects **all discounted items** from the Arc’teryx **Outlet** storefront(s), capturing the **deepest discount per color** and **size availability** across locales (US/CAD). Output to CSV and ; optionally notify when new items drop or prices change.

---

## 2) Scope

- **In scope**
  - Crawl Outlet category/grids for Men, Women, and (optional) Veilance.
  - Visit **every Product Detail Page (PDP)** discovered on grid pages.
  - Iterate **color swatches** and capture list/sale prices + discount %, then iterate **sizes** for stock status.
  - Persist results to **CSV** and **SQLite** (de-dupe by `(product_url, color, size)`).
  - Support **CA (CAD)** and **US (USD)** locales via configuration.
  - Provide **change detection** & **basic alerting** (e.g., Slack/Webhook/Email) as optional modules.
- **Out of scope**
  - Non-outlet full-price site.
  - Automated purchasing / carting / checkout.
  - Exotic bot evasion (only light IP masking / proxy rotation).

---

## 3) Start URLs (configurable)

- Canada: `https://outlet.arcteryx.com/ca/en`
- United States: `https://outlet.arcteryx.com/us/en`

Crawler should expand via on-page links to category paths, then product grids.

---

## 4) Data Model

**Products (flattened per color-size):**

| Field | Type | Example |
|---|---|---|
| `crawl_ts` | ISO8601 string | `2025-08-23T02:10:00-07:00` |
| `locale` | string enum | `ca-en` / `us-en` |
| `category_path` | string | `Men > Jackets > Insulated` |
| `name` | string | `Sabre Pant` |
| `sku` | string \| null | `X000007123` |
| `product_url` | string | `https://outlet.arcteryx.com/.../shop/sabre-pant` |
| `color` | string | `Blue Tetra` |
| `list_price` | money string | `$750.00` |
| `sale_price` | money string | `$150.00` |
| `discount` | string | `80%` |
| `size` | string | `M` |
| `in_stock` | boolean | `true` |
| `image_url` | string \| null | Hero or selected-color image |
| `hash_key` | string | SHA1 of `product_url+color+size` |
| `source` | string | `arcteryx-outlet` |

**Relational option (SQLite):**
- `products`: `id`, `product_url`, `sku`, `name`, `category_path`, `locale`
- `variants`: `id`, `product_id`, `color`, `image_url`
- `offers`: `variant_id`, `size`, `list_price`, `sale_price`, `discount`, `in_stock`, `seen_at`

---

## 5) Functional Requirements

1. **Grid Discovery**
   - Start from configured roots (Men/Women/Veilance).
   - Handle **infinite scroll/lazy load** until no new cards appear.
   - Collect unique product links (`/shop/...`).

2. **PDP Extraction**
   - Extract `name`, `category_path` (via breadcrumb if present), `sku` (via JSON-LD if available), canonical URL.
   - Iterate **each color swatch**; after selecting a color, wait for prices/sizes to update.
   - Parse both **list** and **sale** prices and **discount %** (“Save X%”). If only one price appears, treat it as `sale_price` and keep `list_price = null`.
   - Enumerate **size chips**; mark disabled or missing sizes as `in_stock=false`.
   - Capture **selected-color hero image** if accessible in DOM/JSON-LD.

3. **Locales**
   - Locale base set via env/config; reuse the same logic for `/us/en` and `/ca/en`.
   - Price strings should be stored as-is (with currency symbol) for fidelity; normalization optional in analytics layer.

4. **Politeness**
   - Single-tab or low-concurrency crawl (1–2 pages/sec max).
   - Randomized jitter between requests and user actions.
   - Identify with a custom UA string (configurable).

5. **Resilience**
   - **Retry** with exponential backoff on navigation timeouts/500s.
   - **Fail-soft parsing**: if JSON-LD absent, parse visible DOM text.
   - **Checkpoints**: periodically flush results and maintain a URL cursor to resume.

6. **De-duplication**
   - De-dupe rows based on `(product_url, color, size)` or stored `hash_key`.

7. **Change Detection (Optional)**
   - Compare fresh crawl against last snapshot; produce a diff of **new products**, **price drops**, **stock flips**.
   - Send a brief summary to Slack/Webhook/Email.

---

## 6) Non-Functional Requirements

- **Runtime**: Python 3.10+
- **Headless Browser**: Playwright (Chromium). NodeJS variant acceptable as alt implementation.
- **OS**: Linux/macOS.
- **Observability**: Structured logs (JSON), rotating file logs, simple metrics (pages crawled, PDPs parsed, errors).

---

## 7) Anti-Blocking & IP Masking (Lightweight)

> Goal is to **reduce bans without being abusive**. Always respect site terms and robots; this project is for personal price tracking and research.

- **Residential/Datacenter Proxies** (config):
  - Support passing a single **HTTPS proxy** or a **pool** of proxies.
  - Use **sticky sessions** where available (e.g., 5–10 min) to avoid fingerprint thrash on a single PDP.
  - Rotate proxy **per browser context** or **every N PDPs**.
- **Network Hygiene**
  - **Throttle**: jitter 700–1500ms between actions; 2–4s before new PDP.
  - **Off-hours** schedule.
  - **Backoff** aggressively on 403/429.
- **Browser Hygiene**
  - Randomize **user agent**, **viewport** (e.g., common laptop profiles).
  - Occasionally run **headful** mode for tough pages.
  - Disable obvious automation flags (`--disable-blink-features=AutomationControlled`).
- **Persistence of Cookies** (optional): reuse context storage to look “normal.”
- **Ethics**: If the site disallows scraping in robots or ToS, **stop**; consider official APIs or partner data.

_Implementation notes:_ In Playwright, set a proxy on `browser.new_context(proxy=...)`. Keep rates low; the aim is stability, not speed.

---

## 8) Configuration

`.env` keys (example):
```
OUTLET_BASE=https://outlet.arcteryx.com/ca/en
INCLUDE_VEILANCE=false
CONCURRENCY=1
REQUEST_JITTER_MS_MIN=700
REQUEST_JITTER_MS_MAX=1500
PDP_DELAY_MS=2500
USER_AGENT=TopologyScraper/1.0 (+contact: you@example.com)
PROXY_URL=
PROXY_ROTATE_EVERY=10        # rotate every N PDPs
OUTPUT_CSV=arcteryx_outlet.csv
OUTPUT_DB=arcteryx_outlet.sqlite
ALERT_WEBHOOK=               # optional
```

---

## 9) System Design & Flow

1. **Seed Queue** ← from `OUTLET_BASE` + discovered category links.
2. **Grid Crawler**
   - Visit a category; infinite scroll until no change in product-card count for 2–3 iterations.
   - Extract product URLs; enqueue PDPs.
3. **PDP Worker**
   - For each product URL:
     - Open PDP with proxy/context.
     - Extract base metadata.
     - Iterate colors → for each, parse prices, sizes, image.
     - Emit flattened rows.
4. **Sink**
   - Append rows to CSV and upsert into SQLite.
   - Emit checkpoint and metrics.
5. **Diff/Alerts (optional)**
   - Compare with previous snapshot; send notifications.

---

## 10) Parsing Strategy (Fallback-Friendly)

- **Primary**: JSON-LD (`<script type="application/ld+json">`) for `name`, `sku`, `images`, `offers` if provided.
- **Secondary**: Visible DOM:
  - Price text often includes both **sale** and **list** and **“Save X%”**.
  - Sizes appear as clickable chips; `disabled`/`aria-disabled` can imply OOS.
  - Category via breadcrumb/nav landmarks.

---

## 11) Error Handling

- Navigation timeout → **retry up to 3x** with backoff (1s, 3s, 9s).
- DOM selectors missing → **try alternates**; log a structured warning.
- Proxy error → **rotate** and retry once; if still failing, skip URL and log.

---

## 12) Deliverables & Acceptance

- **Repo** with:
  - `scraper.py` (Playwright/Python) — or `cli.ts` (Node) equivalent
  - `requirements.txt` / `pyproject.toml`
  - `README.md` quickstart
  - This spec as `arcteryx_outlet_scraper_requirements.md`
- **Outputs**:
  - `arcteryx_outlet.csv` with flattened rows
  - Optional `arcteryx_outlet.sqlite` with normalized tables
  - Sample diffs report (`diff_YYYYMMDD.json`)
- **Acceptance Criteria**
  - Captures ≥95% of PDPs reachable from Men/Women roots.
  - For a random sample of 20 products, **color-specific** deepest discount present and **sizes** parsed.
  - Crawl completes under polite rate limits without triggering blocks.

---

## 13) Implementation Hints (Playwright/Python)

- Use a single `browser` and create **new contexts** to rotate proxies & profiles.
- After clicking a color swatch, wait for **DOM mutations** (e.g., price node’s text to change) rather than a fixed sleep where possible.
- Save **context storage** periodically if you want warmer sessions.
- Compute `hash_key = sha1(f"{product_url}|{color}|{size}")` to dedupe/change-detect.
- Keep a JSON index of product URLs to the **last-seen** state; diff against previous snapshot.

---

---

## 15) Future Enhancements (Nice-to-have)

- Google Sheets / Notion sync.
- Simple dashboard (Streamlit) to view discounts by category/color.
- RSS/Atom bridge that emits **new/price-drop** items.
- Headless Chrome CDP fingerprints & device profiles.
- S3 backup of daily CSVs and diffs.

