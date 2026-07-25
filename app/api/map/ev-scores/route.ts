import { NextResponse } from "next/server";
import pool from "@/lib/db";

let cached: { scores: Record<string, number>; counts: Record<string, number> } | null = null;
let cachedAt = 0;
const TTL_MS = 60_000 * 15;

export async function GET() {
  const now = Date.now();
  if (cached && now - cachedAt < TTL_MS) return NextResponse.json(cached);

  const { rows } = await pool.query(`SELECT zip, score, count FROM ev_scores`);
  const scores: Record<string, number> = {};
  const counts: Record<string, number> = {};
  for (const r of rows) {
    scores[r.zip] = Number(r.score);
    counts[r.zip] = Number(r.count);
  }
  cached = { scores, counts };
  cachedAt = now;
  return NextResponse.json(cached);
}
