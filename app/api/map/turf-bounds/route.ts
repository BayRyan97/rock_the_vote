import { NextResponse } from "next/server";
import pool from "@/lib/db";

// Bounding box per turf, so the map can frame a selection without a round trip
// per checkbox click.
//
// This has to be derived rather than read: `turfs` carries diameter_m and
// doors_per_km but no lat/lon, centroid, or box of its own (migration 017), and
// turf_id is reassigned by Hilbert order on every model run, so caching boxes in
// that table would need write_supabase.py to repopulate them in the same
// transaction. households.turf_id + households.lat/lon is the authoritative
// pair already kept in lockstep, so aggregate from there.
//
// Measured 2026-08-07 against production: 1,701 turfs in ~490ms, 75KB of JSON.
// That is too slow to run per click and cheap enough to run once, which is why
// this is its own cached route rather than extra columns on /api/map/filters —
// the scoped (per-click) branch of that route would otherwise pay for it too,
// and the map can render while this loads in parallel.

// [turf_id, minLat, minLon, maxLat, maxLon] — positional, not an object per
// turf: at 1,700 rows the key names are most of the payload.
type TurfBox = [number, number, number, number, number];

let cached: TurfBox[] | null = null;
let cachedAt = 0;
const TTL_MS = 60_000 * 10;

export async function GET() {
  const now = Date.now();
  if (cached && now - cachedAt < TTL_MS) {
    return NextResponse.json({ bounds: cached });
  }

  const res = await pool.query<{
    turf_id: string;
    min_lat: string;
    min_lon: string;
    max_lat: string;
    max_lon: string;
  }>(
    `SELECT turf_id,
            MIN(lat) AS min_lat, MIN(lon) AS min_lon,
            MAX(lat) AS max_lat, MAX(lon) AS max_lon
     FROM households
     WHERE turf_id IS NOT NULL AND lat IS NOT NULL AND lon IS NOT NULL
     GROUP BY turf_id`
  );

  // 5 decimals is ~1m at this latitude — well past what fitBounds can express
  // on screen, and it roughly halves the payload versus full float precision.
  const round = (v: string) => Math.round(Number(v) * 1e5) / 1e5;
  cached = res.rows.map((r): TurfBox => [
    Number(r.turf_id),
    round(r.min_lat),
    round(r.min_lon),
    round(r.max_lat),
    round(r.max_lon),
  ]);
  cachedAt = now;
  return NextResponse.json({ bounds: cached });
}
