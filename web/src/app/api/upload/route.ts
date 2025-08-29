import { NextResponse } from "next/server";
import * as XLSX from "xlsx";
import { z } from "zod";
import {
  readLatestInventory,
  matchUploadRows,
} from "@/lib/inventory";
import type { UploadRow } from "@/types/inventory";

export const runtime = "nodejs";

const UploadSchema = z.array(
  z.object({
    item: z.string().min(1),
    color: z.string().optional().nullable(),
    size: z.string().optional().nullable(),
  }),
);

export async function POST(req: Request) {
  try {
    const formData = await req.formData();
    const file = formData.get("file");
    if (!(file instanceof File)) {
      return NextResponse.json({ error: "No file provided" }, { status: 400 });
    }

    const arrayBuffer = await file.arrayBuffer();
    const workbook = XLSX.read(arrayBuffer, { type: "array" });
    const sheetName = workbook.SheetNames[0];
    const worksheet = workbook.Sheets[sheetName];
    const json = XLSX.utils.sheet_to_json<Record<string, unknown>>(worksheet, {
      defval: "",
    });

    // Accept flexible headers: Item, Number, SKU, Color, Size
    const rows: UploadRow[] = json.map((r) => {
      const entries = Object.fromEntries(
        Object.entries(r).map(([k, v]) => [k.toString().trim().toLowerCase(), v]),
      ) as Record<string, unknown>;
      const item =
        (entries["item"] as string) ||
        (entries["number"] as string) ||
        (entries["sku"] as string) ||
        (entries["name"] as string) ||
        "";
      const color = (entries["color"] as string) ?? (entries["colour"] as string) ?? "";
      const size = (entries["size"] as string) ?? "";
      return { item: String(item), color: String(color), size: String(size) };
    });

    const parsed = UploadSchema.safeParse(rows);
    if (!parsed.success) {
      return NextResponse.json({ error: "Invalid sheet format" }, { status: 400 });
    }

    const inventory = readLatestInventory();
    const results = matchUploadRows(parsed.data, inventory);
    return NextResponse.json({ results });
  } catch (err) {
    console.error(err);
    return NextResponse.json({ error: "Failed to process file" }, { status: 500 });
  }
}



