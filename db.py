import sqlite3
import uuid
from pathlib import Path
from typing import Optional


class AlertDB:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        return conn

    def ensure_schema(self) -> None:
        with self._conn() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS runs (
                  run_id TEXT PRIMARY KEY,
                  started_at TEXT NOT NULL,
                  finished_at TEXT
                );

                CREATE TABLE IF NOT EXISTS variants (
                  hash_key TEXT PRIMARY KEY,
                  product_url TEXT NOT NULL,
                  color TEXT NOT NULL,
                  name TEXT,
                  image_url TEXT,
                  first_seen_at TEXT NOT NULL,
                  last_seen_at TEXT NOT NULL,
                  ever_in_stock INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS observations (
                  obs_id INTEGER PRIMARY KEY AUTOINCREMENT,
                  run_id TEXT NOT NULL,
                  hash_key TEXT NOT NULL,
                  crawl_ts TEXT NOT NULL,
                  num_sizes_in_stock INTEGER NOT NULL,
                  sizes_in_stock TEXT NOT NULL,
                  sizes_all TEXT NOT NULL,
                  size_quantities TEXT,
                  list_price TEXT,
                  sale_price TEXT,
                  discount TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_obs_hash_ts ON observations(hash_key, crawl_ts);
                """
            )
            # Best-effort migration if table exists without new column
            try:
                conn.execute("ALTER TABLE observations ADD COLUMN size_quantities TEXT")
            except Exception:
                pass

    def begin_run(self, started_at_iso: str) -> str:
        run_id = str(uuid.uuid4())
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO runs(run_id, started_at) VALUES (?, ?)",
                (run_id, started_at_iso),
            )
        return run_id

    def finish_run(self, run_id: str, finished_at_iso: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE runs SET finished_at = ? WHERE run_id = ?",
                (finished_at_iso, run_id),
            )

    def upsert_variant(
        self,
        *,
        hash_key: str,
        product_url: str,
        color: Optional[str],
        name: Optional[str],
        image_url: Optional[str],
        crawl_ts: str,
        num_in_stock: int,
    ) -> None:
        color_val = color or ""
        with self._conn() as conn:
            # Insert or update last_seen_at/name/image_url; set first_seen_at on insert
            conn.execute(
                """
                INSERT INTO variants(hash_key, product_url, color, name, image_url, first_seen_at, last_seen_at, ever_in_stock)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(hash_key) DO UPDATE SET
                  product_url = excluded.product_url,
                  color = excluded.color,
                  name = COALESCE(excluded.name, variants.name),
                  image_url = COALESCE(excluded.image_url, variants.image_url),
                  last_seen_at = excluded.last_seen_at,
                  ever_in_stock = CASE WHEN excluded.ever_in_stock = 1 THEN 1 ELSE variants.ever_in_stock END
                """,
                (
                    hash_key,
                    product_url,
                    color_val,
                    name,
                    image_url,
                    crawl_ts,
                    crawl_ts,
                    1 if num_in_stock > 0 else 0,
                ),
            )

    def insert_observation(
        self,
        *,
        run_id: str,
        hash_key: str,
        crawl_ts: str,
        num_sizes_in_stock: int,
        sizes_in_stock: str,
        sizes_all: str,
        size_quantities: Optional[str],
        list_price: Optional[str],
        sale_price: Optional[str],
        discount: Optional[str],
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO observations(run_id, hash_key, crawl_ts, num_sizes_in_stock, sizes_in_stock, sizes_all, size_quantities, list_price, sale_price, discount)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    hash_key,
                    crawl_ts,
                    num_sizes_in_stock,
                    sizes_in_stock,
                    sizes_all,
                    size_quantities,
                    list_price,
                    sale_price,
                    discount,
                ),
            )


