import { NextResponse } from "next/server";
import pool from "@/lib/db";

interface PartyYearCache { data: PartyYearRow[]; at: number }
let cache: PartyYearCache | null = null;
const CACHE_TTL = 60 * 60 * 1000;

export interface PartyYearRow {
  year: number;
  party: string;
  donors: number;
  total: number;
}

export async function GET() {
  if (cache && Date.now() - cache.at < CACHE_TTL) {
    return NextResponse.json(cache.data, {
      headers: { "Cache-Control": "s-maxage=3600, stale-while-revalidate=86400" },
    });
  }

  const { rows } = await pool.query<{
    year: number; party: string; donors: string; total: string;
  }>(`
    SELECT year, party, donors::text, total::text
    FROM donations_by_party_year_mv
    WHERE year BETWEEN 2000 AND EXTRACT(YEAR FROM CURRENT_DATE)::int
    ORDER BY year, party
  `);

  const data: PartyYearRow[] = rows.map(r => ({
    year:   r.year,
    party:  r.party,
    donors: parseInt(r.donors),
    total:  parseFloat(r.total),
  }));

  cache = { data, at: Date.now() };
  return NextResponse.json(data, {
    headers: { "Cache-Control": "s-maxage=3600, stale-while-revalidate=86400" },
  });
}
