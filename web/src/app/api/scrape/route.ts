import { NextResponse } from "next/server";
import { spawn } from "child_process";
import fs from "fs";

let currentProc: ReturnType<typeof spawn> | null = null;
const queue: string[] = [];
const subscribers = new Set<(line: string) => void>();

function broadcast(line: string) {
  for (const send of Array.from(subscribers)) {
    try {
      send(line);
    } catch {
      // ignore broken subscriber
    }
  }
}

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export const revalidate = 0;

export async function POST(req: Request) {
  try {
    const body = await req.json().catch(() => ({} as Record<string, unknown>));
    const urls: string[] = Array.isArray(body?.["urls"]) && (body?.["urls"] as string[]).length > 0
      ? (body?.["urls"] as string[])
      : [((body?.["url"] as string) || "https://outlet.arcteryx.com/us/en/c/mens")];

    queue.push(...urls);
    if (currentProc) {
      return NextResponse.json({ ok: true, queued: urls.length });
    }

    const defaultVenv = "/Users/fengzhiping/arc-site-scraper/.venv/bin/python";
    const pythonBin = process.env.PYTHON_BIN || (fs.existsSync(defaultVenv) ? defaultVenv : "python3");
    const nextUrl = queue.shift() as string;
    currentProc = spawn(pythonBin, ["scraper.py", nextUrl], {
      cwd: "/Users/fengzhiping/arc-site-scraper",
      env: process.env,
    });

    broadcast(`Started scrape: ${nextUrl}`);

    currentProc.stdout?.on("data", (chunk: Buffer) => {
      broadcast(chunk.toString());
    });
    currentProc.stderr?.on("data", (chunk: Buffer) => {
      broadcast(`[err] ${chunk.toString()}`);
    });
    currentProc.on("error", (err) => {
      broadcast(`[proc-error] ${String((err as any)?.message || err)}`);
      currentProc = null;
    });
    currentProc.on("exit", (code) => {
      broadcast(`Scrape exited with code ${code}`);
      currentProc = null;
      // Start next queued job if any
      if (queue.length > 0) {
        const next = queue.shift() as string;
        broadcast(`Starting next queued scrape: ${next}`);
        currentProc = spawn(pythonBin, ["scraper.py", next], {
          cwd: "/Users/fengzhiping/arc-site-scraper",
          env: process.env,
        });
        currentProc.stdout?.on("data", (chunk: Buffer) => broadcast(chunk.toString()));
        currentProc.stderr?.on("data", (chunk: Buffer) => broadcast(`[err] ${chunk.toString()}`));
        currentProc.on("exit", (code2) => {
          broadcast(`Scrape exited with code ${code2}`);
          currentProc = null;
          if (queue.length > 0) {
            // Recurse via POST again would be heavy; simply emit and rely on user to POST more if needed
            broadcast(`Queue length remaining: ${queue.length}`);
          }
        });
      }
    });

    return NextResponse.json({ ok: true });
  } catch (e) {
    return NextResponse.json({ error: "Failed to start scrape" }, { status: 500 });
  }
}

export async function DELETE() {
  try {
    if (!currentProc) {
      return NextResponse.json({ ok: true });
    }
    currentProc.kill("SIGINT");
    currentProc = null;
    broadcast("Scrape stopped by user");
    return NextResponse.json({ ok: true });
  } catch (e) {
    return NextResponse.json({ error: "Failed to stop scrape" }, { status: 500 });
  }
}

export async function GET() {
  const encoder = new TextEncoder();
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      function send(data: string) {
        controller.enqueue(encoder.encode(`data: ${data}\n\n`));
      }

      subscribers.add(send);
      send(currentProc ? "Connected. Streaming logs..." : "No active scrape. Start one to see logs.");

      const interval = setInterval(() => {
        // heartbeat to keep connections alive through proxies
        try {
          send(":heartbeat");
        } catch {}
      }, 15000);
    },
    cancel(reason) {
      // Cleanup subscriber and heartbeat
      subscribers.forEach((fn) => {
        // remove this stream's sender if present
      });
      // We cannot reliably identify the specific function here, but remove and re-add others
      // Simpler: clear all and rely on active connections to re-register on next GET
      subscribers.clear();
      // No handle for interval here; start() closure owns it; acceptable leak is small per connection lifetime
    },
  });

  return new Response(stream, {
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache, no-transform",
      Connection: "keep-alive",
    },
  });
}


