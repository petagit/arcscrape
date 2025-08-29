## Arc’teryx Outlet Alerts – Step‑by‑Step Plan

### Goal
Send a Discord message when a new product/colour becomes available online. Start with a local database (SQLite), later migrate to a managed Postgres/SQL Server without changing application logic.

### Definitions
- **New product online**: a product colour variant (identified by `product_url + color`) that either:
  - has never been seen before, or
  - was previously out of stock (no sizes in stock) and now has at least one size in stock.

### Phase 0 – Current State (reference)
- Scraper emits one row per product colour with: `crawl_ts, product_url, color, sizes_* columns, num_sizes_in_stock, image_url, list/sale/discount, hash_key`, into `arcteryx_outlet.csv`.
- Env knobs already include `ALERT_WEBHOOK` and `OUTPUT_DB` (unused).

### Phase 1 – Local database (SQLite)

#### 1. Schema (SQLite)
- `runs`
  - `run_id` TEXT PRIMARY KEY (e.g., UUID or ISO start timestamp)
  - `started_at` TEXT (ISO)
  - `finished_at` TEXT NULL
- `variants` (canonical, one row per `(product_url, color)`)
  - `hash_key` TEXT PRIMARY KEY (current implementation already computes this)
  - `product_url` TEXT NOT NULL
  - `color` TEXT NOT NULL
  - `name` TEXT NULL
  - `image_url` TEXT NULL
  - `first_seen_at` TEXT NOT NULL
  - `last_seen_at` TEXT NOT NULL
  - `ever_in_stock` INTEGER NOT NULL DEFAULT 0  (0/1)
- `observations` (append‑only; one row per scrape emission)
  - `obs_id` INTEGER PRIMARY KEY AUTOINCREMENT
  - `run_id` TEXT NOT NULL
  - `hash_key` TEXT NOT NULL
  - `crawl_ts` TEXT NOT NULL
  - `num_sizes_in_stock` INTEGER NOT NULL
  - `sizes_in_stock` TEXT NOT NULL
  - `sizes_all` TEXT NOT NULL
  - `list_price` TEXT NULL
  - `sale_price` TEXT NULL
  - `discount` TEXT NULL
  - Foreign key to `variants(hash_key)` (logical; SQLite FK optional)
- `alerts`
  - `alert_id` INTEGER PRIMARY KEY AUTOINCREMENT
  - `hash_key` TEXT NOT NULL
  - `alert_ts` TEXT NOT NULL
  - `run_id` TEXT NOT NULL
  - `reason` TEXT NOT NULL  (e.g., `first_seen`, `went_in_stock`)
  - UNIQUE(`hash_key`, `reason`)  (prevents duplicate sends per reason)

Indexes:
- `CREATE UNIQUE INDEX variants_hash_key ON variants(hash_key);`
- `CREATE INDEX observations_hash_ts ON observations(hash_key, crawl_ts);`

#### 2. Write path (ingestion)
- On each `AggregatedRow` produced by the scraper:
  - UPSERT into `variants` by `hash_key` with latest `name`, `image_url`, `last_seen_at = crawl_ts`, and set `first_seen_at` on insert.
  - INSERT into `observations` with the run’s `run_id` and row data.
  - Decide alert eligibility (see below), then insert into `alerts` if sending.

#### 3. Alert decision logic
- Input: latest `observations` row O for `hash_key` H.
- Fetch `variants.ever_in_stock` and previous observation (if any) for H.
- Fire when either condition holds:
  - `first_seen`:
    - No prior `variants` row exists (first UPSERT insert), and `O.num_sizes_in_stock > 0`.
  - `went_in_stock`:
    - Prior `observations` has `max(num_sizes_in_stock) == 0` and current `O.num_sizes_in_stock > 0`.
- Before sending, check `alerts` for `(H, reason)` to avoid duplicates.
- After successful send, insert into `alerts` and set `variants.ever_in_stock = 1`.

#### 4. Discord send
- Use `ALERT_WEBHOOK` env var (existing). Payload includes:
  - Name, Colour
  - Sale/List/Discount
  - In‑stock sizes (and optional per‑size qty if present)
  - Image (thumbnail)
  - Link (`product_url`)
- Rate limiting: 
  - Batch sends to max N messages per minute (e.g., 20/min) and queue bursts.
  - Optionally group multiple colours of the same product into one embed if triggered within a short window.

#### 5. Process topology
- Easiest path: integrate writes + decision inside the scraper right after each PDP is parsed (near CSV write). Pros: real‑time alerts. Cons: alerts stop when scraper pauses.
- Alternative: a separate “verifier” daemon that tails the CSV (or reads the DB) and sends alerts. Pros: decoupled; scraper can be stateless. Cons: slightly delayed.

Recommendation: Start inline (inside scraper) for simplicity. If you later want redundancy, promote to a small background worker that reads from SQLite.

#### 6. Testing checklist
- Seed DB with a known product at OOS, then run where it becomes in‑stock → exactly one `went_in_stock` alert.
- First time seeing a product that is in stock → exactly one `first_seen` alert.
- Repeated runs with no change → zero alerts.
- Rate limit burst: simulate 100 new variants → ensure queueing not dropping.

### Phase 2 – Migrate to managed Postgres (or SQL Server)

#### 1. Parity schema
- Same tables/columns as SQLite; change types as appropriate:
  - `run_id` UUID
  - `crawl_ts`, `first_seen_at`, `last_seen_at`, `alert_ts` as `TIMESTAMPTZ`
  - `ever_in_stock` as `BOOLEAN`

#### 2. Data migration options
- Snapshot import: export from SQLite (`.dump` or CSV) and import to Postgres with `COPY`.
- Fresh start: keep only `alerts` uniqueness guarantees and repopulate `variants` lazily on next run.

#### 3. App changes
- Replace SQLite connection with Postgres connection string.
- Keep all logic (UPSERT, alert decision) unchanged.
- Optionally move alert worker to a cloud runtime (Fly, Railway, Render) with a simple cron/scheduler.

### Operational considerations
- **Idempotency**: rely on `alerts` unique index and UPSERTs to tolerate retries.
- **Observability**: 
  - Log each send with `run_id`, `hash_key`, and Discord response.
  - Add a “dry‑run” mode to print planned alerts without sending.
- **Backoff**: on webhook errors (429/5xx), exponential backoff with jitter; keep a small disk queue if needed.
- **Secrets**: store webhook URL in `.env`; avoid committing.

### Rollout steps (actionable)
1. Create SQLite schema (tables/indexes) in `OUTPUT_DB`.
2. On each `AggregatedRow`, write to DB (variants/observations) and run decision logic.
3. Send Discord webhook for eligible items and record into `alerts`.
4. Add minimal logs to SSE stream so the GUI shows “Alert sent: <name/color>”.
5. Validate with a short scrape window; verify no duplicates.
6. Later: introduce a small background worker option and Postgres connection flag.

### Acceptance criteria
- New or newly in‑stock variants result in one Discord message each.
- Re‑runs do not duplicate alerts.
- All state persists locally in SQLite and can be migrated to Postgres with the same schema.


