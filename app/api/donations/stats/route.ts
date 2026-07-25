import { NextResponse } from "next/server";
import pool from "@/lib/db";

interface FastCache { data: FastPayload; at: number }
let fastCache: FastCache | null = null;
const CACHE_TTL = 60 * 60 * 1000; // 1 hour

export interface FastPayload {
  totals: {
    confirmed_count: number;
    possible_count: number;
    confirmed_total: number;
    confirmed_donors: number;
  };
  committees: { committee: string; cnt: number; total: number }[];
  zips: { zip: string; donors: number; total: number }[];
}

export async function GET() {
  if (fastCache && Date.now() - fastCache.at < CACHE_TTL) {
    return NextResponse.json(fastCache.data, {
      headers: { "Cache-Control": "s-maxage=3600, stale-while-revalidate=86400" },
    });
  }

  // Totals from pre-computed table (instant)
  // Committees + zips run in parallel alongside it
  const [metaRes, committeesRes, zipsRes] = await Promise.all([
    pool.query<{
      confirmed_count: string; possible_count: string;
      confirmed_total: string; confirmed_donors: string;
    }>(`
      SELECT confirmed_count::text, possible_count::text,
             confirmed_total::text, confirmed_donors::text
      FROM donations_meta WHERE id = 1
    `),

    pool.query<{ committee: string; cnt: string; total: string }>(`
      SELECT committee,
             COUNT(*)::text                           AS cnt,
             COALESCE(SUM(amount::float8), 0)::text  AS total
      FROM donations
      WHERE confirmed = TRUE AND committee IS NOT NULL AND committee <> ''
      GROUP BY committee
      ORDER BY COALESCE(SUM(amount::float8), 0) DESC
      LIMIT 12
    `),

    pool.query<{ zip: string; donors: string; total: string }>(`
      SELECT split_part(donor_key, chr(124), 3)            AS zip,
             COUNT(DISTINCT donor_key)::text               AS donors,
             COALESCE(SUM(amount::float8), 0)::text        AS total
      FROM donations
      WHERE confirmed = TRUE
      GROUP BY 1
      HAVING split_part(donor_key, chr(124), 3) ~ E'^[0-9]{4,5}$'
      ORDER BY COALESCE(SUM(amount::float8), 0) DESC
      LIMIT 10
    `),
  ]);

  const m = metaRes.rows[0] ?? {
    confirmed_count: "0", possible_count: "0",
    confirmed_total: "0", confirmed_donors: "0",
  };

  const data: FastPayload = {
    totals: {
      confirmed_count:  parseInt(m.confirmed_count),
      possible_count:   parseInt(m.possible_count),
      confirmed_total:  parseFloat(m.confirmed_total),
      confirmed_donors: parseInt(m.confirmed_donors),
    },
    committees: committeesRes.rows.map(r => ({
      committee: r.committee,
      cnt:       parseInt(r.cnt),
      total:     parseFloat(r.total),
    })),
    zips: zipsRes.rows.map(r => ({
      zip:    r.zip,
      donors: parseInt(r.donors),
      total:  parseFloat(r.total),
    })),
  };

  fastCache = { data, at: Date.now() };
  return NextResponse.json(data, {
    headers: { "Cache-Control": "s-maxage=3600, stale-while-revalidate=86400" },
  });
}
