import { NextResponse } from "next/server";
import { readRawOutletRows } from "@/lib/inventory";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export const revalidate = 0;

export async function GET() {
  try {
    const rows = readRawOutletRows();
    // Return most recent N (by file order which is append-only)
    const limit = 200;
    const tail = rows.slice(-limit);
    return NextResponse.json({ rows: tail });
  } catch (e) {
    return NextResponse.json({ rows: [] });
  }
}


