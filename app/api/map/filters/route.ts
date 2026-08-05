import { NextRequest, NextResponse } from "next/server";
import pool from "@/lib/db";

interface TurfOption {
  turf_id: number;
  n_doors: number;
  // Expected two-party MARGIN, which is what the list is ranked on. Not the same
  // unit as value_dem_ballots (gross supporting ballots) — don't relabel one as
  // the other. Mobilisation turns out whoever answers, so a 0.55-lean voter is
  // worth +0.10 net rather than +0.55.
  value_net_margin: number;
  value_dem_ballots: number;
  hours_per_net_margin: number | null;
  // Apartment buildings near this turf. They are deliberately NOT in n_doors —
  // a canvasser can't knock a locked lobby — so a high count here means real
  // opportunity that needs a phone/lobby/relational plan instead.
  n_facilities_nearby: number;
  arm: "treatment" | "control" | "buffer";
}

let cached: { assembly_districts: number[]; cities: string[]; turfs: TurfOption[] } | null = null;
let cachedAt = 0;
const TTL_MS = 60_000 * 10;

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function mapTurfRows(rows: any[]): TurfOption[] {
  return rows.map((r) => ({
    turf_id: Number(r.turf_id),
    n_doors: Number(r.n_doors),
    value_net_margin: Number(r.value_net_margin),
    value_dem_ballots: Number(r.value_dem_ballots),
    hours_per_net_margin:
      r.hours_per_net_margin == null ? null : Number(r.hours_per_net_margin),
    n_facilities_nearby: Number(r.n_facilities_nearby),
    arm: r.arm as TurfOption["arm"],
  }));
}

const TURF_COLS =
  "t.turf_id, t.n_doors, t.value_net_margin, t.value_dem_ballots, " +
  "t.hours_per_net_margin, t.n_facilities_nearby, t.arm";

export async function GET(req: NextRequest) {
  const p = req.nextUrl.searchParams;
  const adsParam = p.get("ads");
  const citiesParam = p.get("cities");

  // AD/City narrows which turfs are offered to pick from — turfs has no
  // assembly_district/city column of its own, so scoping goes through
  // households.turf_id. This varies per click, so it bypasses the cache
  // below (which only serves the unscoped base response); at ~1,652 turfs
  // and an indexed households.assembly_district/city, the EXISTS scan is
  // cheap enough to run fresh every time.
  if (adsParam !== null || citiesParam !== null) {
    const turfsRes = adsParam !== null
      ? await pool.query(
          `SELECT ${TURF_COLS}
           FROM turfs t
           WHERE EXISTS (
             SELECT 1 FROM households h
             WHERE h.turf_id = t.turf_id AND h.assembly_district = ANY($1::int[])
           )
           ORDER BY t.value_net_margin DESC`,
          [adsParam ? adsParam.split(",").map(Number).filter(Number.isFinite) : []]
        )
      : await pool.query(
          `SELECT ${TURF_COLS}
           FROM turfs t
           WHERE EXISTS (
             SELECT 1 FROM households h
             WHERE h.turf_id = t.turf_id AND upper(h.city) = ANY($1::text[])
           )
           ORDER BY t.value_net_margin DESC`,
          [citiesParam
            ? citiesParam.split(",").map((c) => decodeURIComponent(c).toUpperCase()).filter(Boolean)
            : []]
        );
    return NextResponse.json({ turfs: mapTurfRows(turfsRes.rows) });
  }

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
      `SELECT turf_id, n_doors, value_net_margin, value_dem_ballots,
              hours_per_net_margin, n_facilities_nearby, arm
       FROM turfs
       ORDER BY value_net_margin DESC`
    ),
  ]);

  cached = {
    assembly_districts: adsRes.rows.map((r) => Number(r.assembly_district)),
    cities: citiesRes.rows.map((r) => r.city as string),
    turfs: mapTurfRows(turfsRes.rows),
  };
  cachedAt = now;
  return NextResponse.json(cached);
}
