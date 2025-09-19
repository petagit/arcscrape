"use client";

import { useEffect, useState } from "react";

type Variant = {
  hash_key: string;
  product_url: string;
  color: string;
  name: string | null;
  image_url: string | null;
  first_seen_at: string;
  last_seen_at: string;
  ever_in_stock: 0 | 1;
};

type Obs = {
  hash_key: string;
  crawl_ts: string;
  num_sizes_in_stock: number;
  sizes_in_stock: string;
  sizes_all?: string;
  size_quantities?: string | null;
  list_price: string | null;
  sale_price: string | null;
  discount: string | null;
};

export default function Dashboard() {
  const [variants, setVariants] = useState<Variant[]>([]);
  const [obs, setObs] = useState<Obs[]>([]);
  const [q, setQ] = useState("");
  const [store, setStore] = useState<string>("");
  const [rangeHours] = useState<number>(48);
  const [snapshotAt] = useState<Date>(new Date());

  useEffect(() => {
    (async () => {
      const res = await fetch("/api/dashboard", { cache: "no-store" });
      const json = await res.json();
      setVariants(json.variants || []);
      setObs(json.recentObs || []);
    })();
  }, []);

  const filtered = variants.filter((v) => {
    const key = `${v.name ?? ""} ${v.color ?? ""}`.toLowerCase();
    const s = storeFromUrl(v.product_url);
    const storeOk = store ? s === store : true;
    return key.includes(q.toLowerCase()) && storeOk;
  });

  const msCutoff = Date.now() - rangeHours * 60 * 60 * 1000;
  const recentObs = obs.filter((o) => {
    const t = Date.parse(o.crawl_ts);
    return !Number.isNaN(t) && t >= msCutoff;
  });

  const obsByHash = new Map<string, Obs>();
  for (const o of recentObs) {
    const prev = obsByHash.get(o.hash_key);
    if (!prev || new Date(o.crawl_ts) > new Date(prev.crawl_ts)) obsByHash.set(o.hash_key, o);
  }

  function storeFromUrl(url: string | null | undefined): string {
    if (!url) return "";
    try {
      const u = new URL(url);
      const host = u.hostname; // e.g., outlet.arcteryx.com or arcteryx.com
      const parts = u.pathname.split("/").filter(Boolean); // [us, en, ...]
      const region = (parts[0] || "").toUpperCase();
      const isOutlet = host.includes("outlet.");
      if (region === "US" || region === "CA") {
        return `${region} ${isOutlet ? "Outlet" : "Store"}`;
      }
      // Fallback: infer by host if possible
      return isOutlet ? "Outlet" : host;
    } catch {
      return "";
    }
  }

  const storeOptions = Array.from(
    new Set(variants.map((v) => storeFromUrl(v.product_url)).filter(Boolean)),
  ).sort();

  return (
    <main className="mx-auto max-w-6xl p-6 space-y-6">
      <h1 className="text-2xl font-semibold">Inventory Dashboard</h1>
      <div className="flex items-center gap-4">
        <button className="rounded bg-black text-white px-4 py-2 text-sm">Last 48 Hours</button>
        <div className="rounded border px-3 py-2 text-sm bg-white text-gray-900">
          {snapshotAt.toLocaleString()}
        </div>
      </div>
      <div className="text-sm text-gray-600">Snapshot: Last 48 Hours</div>
      <div className="flex gap-3 items-center">
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Filter by name or color"
          className="rounded border px-3 py-2 w-full"
        />
        <div className="flex flex-wrap gap-2">
          {["", ...storeOptions].map((s) => {
            const label = s || "All Stores";
            const active = store === s;
            return (
              <button
                key={label}
                onClick={() => setStore(s)}
                className={
                  (active
                    ? "bg-black text-white"
                    : "bg-white text-gray-800 border") +
                  " rounded px-3 py-2 text-sm"
                }
              >
                {label}
              </button>
            );
          })}
        </div>
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {filtered.map((v) => {
          const o = obsByHash.get(v.hash_key);
          // Parse per-size quantities if present
          let sizePairs: string[] = [];
          try {
            if (o?.size_quantities) {
              const map = JSON.parse(String(o.size_quantities)) as Record<string, number>;
              sizePairs = Object.entries(map)
                .sort(([a], [b]) => a.localeCompare(b))
                .map(([s, q]) => `${s}(${q})`);
            }
          } catch {}
          return (
            <div key={v.hash_key} className="border rounded p-3 flex gap-3">
              <div>
                {v.image_url ? (
                  <a href={v.product_url} target="_blank" rel="noreferrer">
                    <img src={v.image_url} alt={v.name ?? ""} className="h-24 w-24 object-cover rounded" />
                  </a>
                ) : (
                  <div className="h-24 w-24 bg-gray-100 rounded" />
                )}
              </div>
              <div className="flex-1">
                <div className="font-medium">{v.name ?? "(no name)"}</div>
                <div className="text-sm text-gray-600">{v.color}</div>
                <div className="text-xs text-gray-500 mt-0.5">{storeFromUrl(v.product_url)}</div>
                <div className="text-sm mt-1">
                  {o ? (
                    <>
                      <span className="font-semibold">In stock:</span> {o.num_sizes_in_stock} &nbsp;|
                      &nbsp;<span className="font-semibold">Sizes:</span> {o.sizes_in_stock || "—"}
                      {sizePairs.length ? (
                        <>
                          <br />
                          <span className="font-semibold">Qty/Size:</span> {sizePairs.join(", ")}
                        </>
                      ) : null}
                      {o.sale_price ? (
                        <>
                          &nbsp;|&nbsp;<span className="font-semibold">Price:</span> {o.sale_price}
                          {o.list_price ? ` (was ${o.list_price})` : ""}
                          {o.discount ? ` — Save ${o.discount}` : ""}
                        </>
                      ) : null}
                    </>
                  ) : (
                    <span className="text-gray-500">No recent observation</span>
                  )}
                </div>
                <div className="text-xs text-gray-500 mt-1">Last seen: {new Date(v.last_seen_at).toLocaleString()}</div>
              </div>
            </div>
          );
        })}
      </div>
    </main>
  );
}


