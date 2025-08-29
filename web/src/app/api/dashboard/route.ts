import { NextResponse } from "next/server";
import { execFile as _execFile } from "child_process";
import { promisify } from "util";

const execFile = promisify(_execFile);
const DB_PATH = "/Users/fengzhiping/arc-site-scraper/arcteryx_outlet.sqlite";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export const revalidate = 0;

export async function GET() {
  try {
    function parseTSV(tsv: string): any[] {
      const lines = tsv.split(/\r?\n/).filter(Boolean);
      if (lines.length === 0) return [];
      const headers = lines[0].split("\t");
      return lines.slice(1).map((line) => {
        const cols = line.split("\t");
        const obj: Record<string, any> = {};
        headers.forEach((h, i) => (obj[h] = cols[i] ?? ""));
        return obj;
      });
    }

    const variantsSQL = `SELECT hash_key, product_url, color, name, image_url, first_seen_at, last_seen_at, ever_in_stock FROM variants ORDER BY last_seen_at DESC LIMIT 500;`;
    const obsSQL = `SELECT hash_key, crawl_ts, num_sizes_in_stock, sizes_in_stock, sizes_all, size_quantities, list_price, sale_price, discount FROM observations ORDER BY crawl_ts DESC LIMIT 2000;`;

    const vCmd = await execFile("sqlite3", [DB_PATH, "-cmd", ".headers on", ".mode tabs", variantsSQL]);
    const oCmd = await execFile("sqlite3", [DB_PATH, "-cmd", ".headers on", ".mode tabs", obsSQL]);
    const variants = parseTSV(vCmd.stdout || "");
    const recentObs = parseTSV(oCmd.stdout || "");
    return NextResponse.json({ variants, recentObs });
  } catch (e) {
    return NextResponse.json({ variants: [], recentObs: [] });
  }
}


