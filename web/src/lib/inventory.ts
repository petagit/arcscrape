import fs from "fs";
import path from "path";
import { parse } from "csv-parse/sync";
import type {
  OutletCsvRow,
  NormalizedInventoryRecord,
  UploadRow,
  MatchResult,
  MatchStatus,
} from "@/types/inventory";

export const OUTLET_CSV_PATH: string = "/Users/fengzhiping/arc-site-scraper/arcteryx_outlet.csv";

function toBoolean(value: string | boolean | null | undefined): boolean {
  if (typeof value === "boolean") return value;
  if (value == null) return false;
  const v = String(value).trim().toLowerCase();
  return v === "true" || v === "1" || v === "yes";
}

function normalize(text: string | null | undefined): string {
  return (text ?? "").trim().toLowerCase();
}

export function readLatestInventory(): NormalizedInventoryRecord[] {
  const csvPath = OUTLET_CSV_PATH;
  if (!fs.existsSync(csvPath)) {
    throw new Error(`CSV not found at ${csvPath}`);
  }
  const content = fs.readFileSync(csvPath, "utf8");
  const rows = parse(content, {
    columns: true,
    skip_empty_lines: true,
  }) as OutletCsvRow[];

  const records: NormalizedInventoryRecord[] = [];
  for (const r of rows) {
    const allSizes = (r.sizes_all ?? "").split(",").map((s) => s.trim()).filter(Boolean);
    const inSizes = (r.sizes_in_stock ?? "").split(",").map((s) => s.trim()).filter(Boolean);
    const inSet = new Set(inSizes.map((s) => normalize(s)));
    const invAmount = r.inventory_amount == null || r.inventory_amount === ""
      ? undefined
      : Number(r.inventory_amount);
    let sizeQtyMap: Record<string, number> | undefined;
    if (r.size_quantities) {
      try {
        const parsed = JSON.parse(String(r.size_quantities));
        if (parsed && typeof parsed === "object") {
          sizeQtyMap = parsed as Record<string, number>;
        }
      } catch {}
    }
    for (const size of allSizes.length ? allSizes : [""]) {
      const inStock = size ? inSet.has(normalize(size)) : (Number(r.num_sizes_in_stock ?? 0) > 0);
      records.push({
        name: (r.name ?? "").trim(),
        color: (r.color ?? "").trim(),
        size: size,
        inStock,
        productUrl: r.product_url || undefined,
        imageUrl: r.image_url || undefined,
        inventoryAmount: Number.isFinite(invAmount) ? (invAmount as number) : undefined,
        sizeQty: size && sizeQtyMap ? sizeQtyMap[size] : undefined,
      });
    }
  }
  return records;
}

export function readRawOutletRows(): OutletCsvRow[] {
  const csvPath = OUTLET_CSV_PATH;
  if (!fs.existsSync(csvPath)) {
    return [];
  }
  const content = fs.readFileSync(csvPath, "utf8");
  const rows = parse(content, {
    columns: true,
    skip_empty_lines: true,
  }) as OutletCsvRow[];
  return rows;
}

export function matchUploadRows(
  uploadRows: UploadRow[],
  inventory: NormalizedInventoryRecord[],
): MatchResult[] {
  const results: MatchResult[] = [];
  for (const row of uploadRows) {
    const queryName = normalize(row.item);
    const queryColor = normalize(row.color ?? "");
    const querySize = normalize(row.size ?? "");

    const byItem = inventory.filter((i) => normalize(i.name).includes(queryName));
    if (byItem.length === 0) {
      results.push({ query: row, status: "ITEM_NOT_FOUND", matches: [] });
      continue;
    }

    const byVariant = byItem.filter((i) => {
      const colorOk = queryColor ? normalize(i.color) === queryColor : true;
      const sizeOk = querySize ? normalize(i.size) === querySize : true;
      return colorOk && sizeOk;
    });

    if (byVariant.length === 0) {
      results.push({ query: row, status: "VARIANT_NOT_FOUND", matches: byItem });
      continue;
    }

    const anyInStock = byVariant.some((m) => m.inStock);
    results.push({
      query: row,
      status: anyInStock ? "IN_STOCK" : "OUT_OF_STOCK",
      matches: byVariant,
    });
  }
  return results;
}



