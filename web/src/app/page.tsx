"use client";

import { useState } from "react";

type ApiResult = {
  results: Array<{
    query: { item: string; color?: string | null; size?: string | null };
    status: "IN_STOCK" | "OUT_OF_STOCK" | "VARIANT_NOT_FOUND" | "ITEM_NOT_FOUND";
    matches: Array<{ name: string; color: string; size: string; inStock: boolean; productUrl?: string; sizeQty?: number }>;
  }>;
};

export default function Home() {
  const [file, setFile] = useState<File | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [data, setData] = useState<ApiResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setData(null);
    if (!file) return;
    const formData = new FormData();
    formData.append("file", file);
    setIsLoading(true);
    try {
      const res = await fetch("/api/upload", { method: "POST", body: formData });
      const json = await res.json();
      if (!res.ok) throw new Error(json.error || "Upload failed");
      setData(json as ApiResult);
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Unexpected error";
      setError(message);
    } finally {
      setIsLoading(false);
    }
  }

  return (
    <main className="mx-auto max-w-5xl p-6 space-y-6">
      <h1 className="text-2xl font-semibold">Arc’teryx Outlet Stock Checker</h1>
      <p className="text-sm text-gray-500">Upload an Excel file with columns like Item/Number/SKU, Color, Size.</p>

      <form onSubmit={handleSubmit} className="space-y-4">
        <input
          type="file"
          accept=".xlsx,.xls"
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          className="block w-full"
        />
        <button
          type="submit"
          disabled={!file || isLoading}
          className="rounded bg-black text-white px-4 py-2 disabled:opacity-50"
        >
          {isLoading ? "Checking..." : "Upload & Check"}
        </button>
      </form>

      {error && <div className="text-red-600">{error}</div>}

      {data && (
        <div className="overflow-x-auto">
          <table className="min-w-full text-sm">
            <thead>
              <tr className="text-left border-b">
                <th className="py-2 pr-4">Item</th>
                <th className="py-2 pr-4">Color</th>
                <th className="py-2 pr-4">Size</th>
                <th className="py-2 pr-4">Status</th>
                <th className="py-2 pr-4">Matches</th>
              </tr>
            </thead>
            <tbody>
              {data.results.map((r, idx) => (
                <tr key={idx} className="border-b align-top">
                  <td className="py-2 pr-4 whitespace-nowrap">{r.query.item}</td>
                  <td className="py-2 pr-4 whitespace-nowrap">{r.query.color || "—"}</td>
                  <td className="py-2 pr-4 whitespace-nowrap">{r.query.size || "—"}</td>
                  <td className="py-2 pr-4">
                    <span
                      className={
                        r.status === "IN_STOCK"
                          ? "text-green-700"
                          : r.status === "OUT_OF_STOCK"
                          ? "text-yellow-700"
                          : "text-gray-600"
                      }
                    >
                      {r.status}
                    </span>
                  </td>
                  <td className="py-2 pr-4">
                    {r.matches.length === 0 ? (
                      <span className="text-gray-500">No matches</span>
                    ) : (
                      <ul className="space-y-1">
                        {r.matches.map((m, i) => (
                          <li key={i} className="text-gray-800">
                            {m.name} — {m.color} — {m.size} — {m.inStock ? "In stock" : "Out"}
                            {typeof m.sizeQty === "number" ? ` (qty: ${m.sizeQty})` : ""}
                            {m.productUrl ? (
                              <>
                                {" "}
                                <a href={m.productUrl} target="_blank" rel="noreferrer" className="text-blue-600 underline">
                                  link
                                </a>
                              </>
                            ) : null}
                          </li>
                        ))}
                      </ul>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </main>
  );
}
