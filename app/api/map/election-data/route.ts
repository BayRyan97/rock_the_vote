import { NextResponse } from "next/server";
import pool from "@/lib/db";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
let cached: any = null;
let cachedAt = 0;
const TTL_MS = 60_000 * 15;

export async function GET() {
  const now = Date.now();

  if (cached && now - cachedAt < TTL_MS) {
    return NextResponse.json(cached);
  }

  const { rows } = await pool.query(
    `SELECT chamber, year, district, dem_votes, rep_votes, other_votes, total_votes,
            dem_candidate, rep_candidate, dem_pct, margin_pct, winner
     FROM election_results ORDER BY chamber, year, district`
  );

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const election_results: Record<string, Record<string, Record<string, any>>> = {};
  for (const r of rows) {
    const ch = r.chamber as string;
    const yr = String(r.year);
    const dist = String(r.district);
    if (!election_results[ch]) election_results[ch] = {};
    if (!election_results[ch][yr]) election_results[ch][yr] = {};
    election_results[ch][yr][dist] = {
      dem_pct: Number(r.dem_pct),
      margin_pct: Number(r.margin_pct),
      total_votes: r.total_votes,
      dem_votes: r.dem_votes,
      rep_votes: r.rep_votes,
      other_votes: r.other_votes,
      dem_candidate: r.dem_candidate,
      rep_candidate: r.rep_candidate,
      winner: r.winner,
    };
  }

  cached = { election_results, district_metrics: {} };
  cachedAt = now;

  return NextResponse.json(cached);
}
