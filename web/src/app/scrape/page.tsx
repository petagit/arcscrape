"use client";

import { useEffect, useRef, useState } from "react";

type Row = {
  crawl_ts: string;
  locale: string;
  category_path: string | null;
  name: string;
  sku: string | null;
  product_url: string;
  color: string | null;
  list_price: string | null;
  sale_price: string | null;
  discount: string | null;
  image_url: string | null;
  inventory_amount: number | string | null;
  size_quantities?: string | null;
  sizes_all: string | null;
  sizes_in_stock: string | null;
  sizes_out_of_stock: string | null;
  num_sizes_in_stock: number | string | null;
  hash_key: string;
  source: string;
};

export default function ScrapePage() {
  const [urlList, setUrlList] = useState<string>(
    [
      "https://outlet.arcteryx.com/ca/en/c/mens",
      "https://outlet.arcteryx.com/ca/en/c/womens",
      "https://arcteryx.com/ca/en/c/mens",
      "https://arcteryx.com/ca/en/c/womens",
    ].join("\n"),
  );
  const [running, setRunning] = useState(false);
  const [log, setLog] = useState<string>("");
  const [rows, setRows] = useState<Row[]>([]);
  const [startedAt, setStartedAt] = useState<number | null>(null);
  const evtRef = useRef<EventSource | null>(null);
  const pollRef = useRef<NodeJS.Timeout | null>(null);

  useEffect(() => {
    const es = new EventSource("/api/scrape");
    evtRef.current = es;
    es.onmessage = (ev) => {
      setLog((prev) => prev + ev.data + "\n");
      if (ev.data?.includes("Started scrape")) {
        setRunning(true);
        setStartedAt(Date.now());
        setRows([]);
      }
      if (ev.data?.includes("Scrape exited") || ev.data?.includes("stopped by user")) setRunning(false);
    };
    es.onerror = () => {
      // Keep it simple; SSE will reconnect by default in most cases
    };
    return () => {
      es.close();
      evtRef.current = null;
    };
  }, []);

  // Poll results while running and also periodically when idle
  useEffect(() => {
    async function fetchRows() {
      try {
        const res = await fetch("/api/scrape/results", { cache: "no-store" });
        if (!res.ok) return;
        const data = (await res.json()) as { rows: Row[] };
        let newRows = data.rows ?? [];
        if (startedAt) {
          newRows = newRows.filter((r) => {
            const ts = Date.parse(r.crawl_ts);
            return !Number.isNaN(ts) && ts >= startedAt!;
          });
        }
        setRows(newRows);
      } catch {}
    }
    // initial load
    fetchRows();
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(fetchRows, running ? 3000 : 15000) as unknown as NodeJS.Timeout;
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
      pollRef.current = null;
    };
  }, [running, startedAt]);

  async function startScrape() {
    setLog("");
    const urls = urlList.split(/\r?\n/).map((u) => u.trim()).filter(Boolean);
    const res = await fetch("/api/scrape", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ urls }),
    });
    if (res.ok) {
      setRunning(true);
    } else {
      const j = (await res.json().catch(() => undefined)) as unknown;
      const errMsg = (j && typeof j === "object" && "error" in j && typeof (j as { error?: unknown }).error === "string")
        ? ((j as { error: string }).error)
        : "Failed to start scrape";
      alert(errMsg);
    }
  }

  async function stopScrape() {
    const res = await fetch("/api/scrape", { method: "DELETE" });
    if (res.ok) {
      setRunning(false);
    } else {
      const j = (await res.json().catch(() => undefined)) as unknown;
      const errMsg = (j && typeof j === "object" && "error" in j && typeof (j as { error?: unknown }).error === "string")
        ? ((j as { error: string }).error)
        : "Failed to stop scrape";
      alert(errMsg);
    }
  }

  async function clearScrape() {
    try {
      // Stop if running, then clear local UI state
      await fetch("/api/scrape", { method: "DELETE" });
    } catch {}
    setRunning(false);
    setLog("");
    setRows([]);
  }

  return (
    <main className="mx-auto max-w-5xl p-6 space-y-6">
      <h1 className="text-2xl font-semibold">Scraper Control</h1>
      <div className="space-y-3">
        <label className="block">
          <span className="text-sm text-gray-700">Start URLs (one per line)</span>
          <textarea
            className="mt-1 w-full rounded border px-3 py-2 h-24"
            value={urlList}
            onChange={(e) => setUrlList(e.target.value)}
          />
        </label>
        <div className="flex gap-3">
          <button
            onClick={startScrape}
            disabled={running}
            className="rounded bg-black text-white px-4 py-2 disabled:opacity-50"
          >
            Start
          </button>
          <button
            onClick={stopScrape}
            disabled={!running}
            className="rounded bg-gray-700 text-white px-4 py-2 disabled:opacity-50"
          >
            Stop
          </button>
          <button
            onClick={clearScrape}
            className="rounded bg-gray-200 text-gray-900 px-4 py-2 hover:bg-gray-300"
          >
            Clear
          </button>
        </div>
      </div>

      <div>
        <h2 className="text-lg font-semibold mb-2">Live Logs</h2>
        <pre className="h-64 overflow-auto bg-gray-50 p-3 whitespace-pre-wrap border rounded">
{log}
        </pre>
      </div>

      <div>
        <h2 className="text-lg font-semibold mb-2">Recent Rows</h2>
        <div className="overflow-x-auto border rounded">
          <table className="min-w-full text-sm">
            <thead>
              <tr className="text-left border-b bg-gray-50">
                <th className="py-2 px-3">Time</th>
                <th className="py-2 px-3">Name</th>
                <th className="py-2 px-3">Image</th>
                <th className="py-2 px-3">Color</th>
                <th className="py-2 px-3">Sale</th>
                <th className="py-2 px-3">List</th>
                <th className="py-2 px-3">Discount</th>
                <th className="py-2 px-3">In-Stock Sizes</th>
                <th className="py-2 px-3">Qty/Size</th>
                <th className="py-2 px-3">All Sizes</th>
                <th className="py-2 px-3">Inventory</th>
                <th className="py-2 px-3">Link</th>
              </tr>
            </thead>
            <tbody>
              {rows.length === 0 ? (
                <tr>
                  <td className="py-3 px-3 text-gray-500" colSpan={12}>No rows yet</td>
                </tr>
              ) : (
                rows.map((r, i) => (
                  <tr key={i} className="border-b align-top">
                    <td className="py-2 px-3 whitespace-nowrap">{new Date(r.crawl_ts).toLocaleTimeString()}</td>
                    <td className="py-2 px-3">{r.name}</td>
                    <td className="py-2 px-3">
                      {r.image_url ? (
                        r.product_url ? (
                          <a href={r.product_url} target="_blank" rel="noreferrer">
                            <img src={r.image_url} alt={r.name ?? ""} className="h-16 w-16 object-cover rounded border" />
                          </a>
                        ) : (
                          <img src={r.image_url} alt={r.name ?? ""} className="h-16 w-16 object-cover rounded border" />
                        )
                      ) : (
                        <span className="text-gray-400">—</span>
                      )}
                    </td>
                    <td className="py-2 px-3">{r.color}</td>
                    <td className="py-2 px-3">{r.sale_price}</td>
                    <td className="py-2 px-3">{r.list_price}</td>
                    <td className="py-2 px-3">{r.discount}</td>
                    <td className="py-2 px-3">{r.sizes_in_stock}</td>
                    <td className="py-2 px-3 text-gray-800">
                      {(() => {
                        try {
                          if (!r.size_quantities) return "";
                          const map = JSON.parse(String(r.size_quantities)) as Record<string, number>;
                          const pairs = Object.entries(map)
                            .sort(([a], [b]) => a.localeCompare(b))
                            .map(([s, q]) => `${s}(${q})`);
                          return pairs.join(", ");
                        } catch {
                          return "";
                        }
                      })()}
                    </td>
                    <td className="py-2 px-3 text-gray-600">{r.sizes_all}</td>
                    <td className="py-2 px-3">{r.inventory_amount ?? ""}</td>
                    <td className="py-2 px-3">
                      {r.product_url ? (
                        <a href={r.product_url} target="_blank" rel="noreferrer" className="text-blue-600 underline">open</a>
                      ) : null}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </main>
  );
}


