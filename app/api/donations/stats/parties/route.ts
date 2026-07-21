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
    SELECT p.party,
           COUNT(DISTINCT d.donor_key)::text        AS donors,
           COALESCE(SUM(d.amount::float8), 0)::text AS total,
           AVG(d.amount::float8)::text              AS avg
    FROM donations d
    JOIN people p USING (donor_key)
    WHERE d.confirmed = TRUE AND p.party IS NOT NULL
    GROUP BY p.party
    ORDER BY COALESCE(SUM(d.amount::float8), 0) DESC
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
