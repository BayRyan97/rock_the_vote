import { NextResponse } from "next/server";
import pool from "@/lib/db";

interface PartyCache { data: PartyRow[]; at: number }
let cache: PartyCache | null = null;
const CACHE_TTL = 60 * 60 * 1000;

export interface PartyRow {
  party: string;
  donors: number;
  total: number;
  avg: number;
}

export async function GET() {
  if (cache && Date.now() - cache.at < CACHE_TTL) {
    return NextResponse.json(cache.data, {
      headers: { "Cache-Control": "s-maxage=3600, stale-while-revalidate=86400" },
    });
  }

  const { rows } = await pool.query<{
    party: string; donors: string; total: string; avg: string;
  }>(`
    SELECT party, donors::text, total::text, avg::text
    FROM donations_by_party_mv
    ORDER BY total DESC
  `);

  const data: PartyRow[] = rows.map(r => ({
    party:  r.party,
    donors: parseInt(r.donors),
    total:  parseFloat(r.total),
    avg:    parseFloat(r.avg),
  }));

  cache = { data, at: Date.now() };
  return NextResponse.json(data, {
    headers: { "Cache-Control": "s-maxage=3600, stale-while-revalidate=86400" },
  });
}
