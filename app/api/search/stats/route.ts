import { NextResponse } from "next/server";
import pool from "@/lib/db";

interface Cache { data: StatsPayload; at: number }
let cache: Cache | null = null;
const TTL = 60 * 60 * 1000; // 1 hour

export interface StatsPayload {
  households: number;
  voters: number;
  dem: number;
  rep: number;
  blk: number;
  other: number;
}

export async function GET() {
  if (cache && Date.now() - cache.at < TTL) {
    return NextResponse.json(cache.data, {
      headers: { "Cache-Control": "s-maxage=3600, stale-while-revalidate=86400" },
    });
  }

  const [hhRes, partyRes] = await Promise.all([
    pool.query<{ cnt: string }>("SELECT COUNT(*)::text AS cnt FROM households"),
    pool.query<{ dem: string; rep: string; blk: string; other: string; voters: string }>(`
      SELECT
        COUNT(*) FILTER (WHERE party = 'DEM')::text                        AS dem,
        COUNT(*) FILTER (WHERE party = 'REP')::text                        AS rep,
        COUNT(*) FILTER (WHERE party = 'BLK')::text                        AS blk,
        COUNT(*) FILTER (WHERE party NOT IN ('DEM','REP','BLK'))::text     AS other,
        COUNT(*)::text                                                       AS voters
      FROM people
    `),
  ]);

  const p = partyRes.rows[0];
  const data: StatsPayload = {
    households: parseInt(hhRes.rows[0].cnt),
    voters:     parseInt(p.voters),
    dem:        parseInt(p.dem),
    rep:        parseInt(p.rep),
    blk:        parseInt(p.blk),
    other:      parseInt(p.other),
  };

  cache = { data, at: Date.now() };
  return NextResponse.json(data, {
    headers: { "Cache-Control": "s-maxage=3600, stale-while-revalidate=86400" },
  });
}
