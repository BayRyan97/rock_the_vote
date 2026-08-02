import { NextResponse } from "next/server";
import pool from "@/lib/db";

interface TurfOption {
  turf_id: number;
  n_doors: number;
  value_dem_ballots: number;
  arm: "treatment" | "control" | "buffer";
}

let cached: { assembly_districts: number[]; cities: string[]; turfs: TurfOption[] } | null = null;
let cachedAt = 0;
const TTL_MS = 60_000 * 10;

export async function GET() {
  const now = Date.now();
  if (cached && now - cachedAt < TTL_MS) {
    return NextResponse.json(cached);
  }

  const [adsRes, citiesRes, turfsRes] = await Promise.all([
    pool.query(
      `SELECT DISTINCT assembly_district
       FROM households
       WHERE assembly_district IS NOT NULL AND score_total > 0
       ORDER BY 1`
    ),
    pool.query(
      `SELECT DISTINCT city
       FROM households
       WHERE city IS NOT NULL AND score_total > 0
       ORDER BY 1`
    ),
    // Sourced from `turfs` directly, not `DISTINCT turf_id FROM households`:
    // turfs already carries the value/door-count/arm metadata the checklist
    // needs, and at ~1,652 rows a full scan is trivially cheap.
    pool.query(
      `SELECT turf_id, n_doors, value_dem_ballots, arm
       FROM turfs
       ORDER BY value_dem_ballots DESC`
    ),
  ]);

  cached = {
    assembly_districts: adsRes.rows.map((r) => Number(r.assembly_district)),
    cities: citiesRes.rows.map((r) => r.city as string),
    turfs: turfsRes.rows.map((r) => ({
      turf_id: Number(r.turf_id),
      n_doors: Number(r.n_doors),
      value_dem_ballots: Number(r.value_dem_ballots),
      arm: r.arm as TurfOption["arm"],
    })),
  };
  cachedAt = now;
  return NextResponse.json(cached);
}
