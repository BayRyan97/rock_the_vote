import { NextRequest, NextResponse } from "next/server";
import pool from "@/lib/db";
import {
  ALL_GEO_DIMENSIONS,
  GEO_DIMENSIONS,
  GeoDimensionKey,
  ParsedGeoFilters,
  buildGeoWhereSql,
  hasActiveGeoFilters,
  parseGeoFilters,
} from "@/lib/geoFilters";

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

type GeoOptions = Record<GeoDimensionKey, (string | number)[]>;

// The 10-minute cache only ever applies to the true zero-selection request
// (first page load, before anyone has touched a filter) — every request with
// >=1 active dimension now depends on the caller's own selections, so there's
// nothing stable left to key it on. Bypassing the cache there is the same
// tradeoff this route already made for the old ads/cities/towns scoping: at
// ~1,652 turfs and indexed households columns, a fresh scan is cheap.
let cached: { options: GeoOptions; turfs: TurfOption[] } | null = null;
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

// Turfs has no county/city/town/*_district column of its own — every geo
// dimension lives on households, linked via households.turf_id (no FK: turfs
// gets truncated + reloaded on every model run, see migration 018). Scoping
// is therefore always "does this turf have at least one household matching
// ALL currently active dimensions" — one EXISTS with every predicate ANDed
// in, not one EXISTS per dimension.
async function fetchTurfs(filters: ParsedGeoFilters): Promise<TurfOption[]> {
  const { sql: whereSql, params } = buildGeoWhereSql(filters, { tableAlias: "h", paramOffset: 0 });
  if (!whereSql) {
    const res = await pool.query(`SELECT ${TURF_COLS} FROM turfs t ORDER BY t.value_net_margin DESC`);
    return mapTurfRows(res.rows);
  }
  const res = await pool.query(
    `SELECT ${TURF_COLS}
     FROM turfs t
     WHERE EXISTS (
       SELECT 1 FROM households h
       WHERE h.turf_id = t.turf_id${whereSql}
     )
     ORDER BY t.value_net_margin DESC`,
    params
  );
  return mapTurfRows(res.rows);
}

// Cascading available-options: for each of the 8 dimensions, the values
// offered are those that actually occur among households matching every
// OTHER currently active dimension (never the dimension's own selection —
// otherwise unchecking the last box in a dimension would make its own list
// disappear). This is what makes picking County=SUFFOLK immediately narrow
// the Town/ED/etc. checklists to what exists in Suffolk.
async function fetchOptions(filters: ParsedGeoFilters): Promise<GeoOptions> {
  const entries = await Promise.all(
    ALL_GEO_DIMENSIONS.map(async (key) => {
      const spec = GEO_DIMENSIONS[key];
      const { sql: whereSql, params } = buildGeoWhereSql(filters, {
        tableAlias: "h",
        paramOffset: 0,
        exclude: key,
      });
      const res = await pool.query(
        `SELECT DISTINCT h.${spec.column} AS v
         FROM households h
         WHERE h.${spec.column} IS NOT NULL AND h.score_total > 0${whereSql}
         ORDER BY 1`,
        params
      );
      const values = res.rows.map((r) =>
        spec.valueType === "int" ? Number(r.v) : (r.v as string)
      );
      return [key, values] as const;
    })
  );
  return Object.fromEntries(entries) as GeoOptions;
}

export async function GET(req: NextRequest) {
  const filters = parseGeoFilters(req.nextUrl.searchParams);

  if (!hasActiveGeoFilters(filters)) {
    const now = Date.now();
    if (cached && now - cachedAt < TTL_MS) {
      return NextResponse.json(cached);
    }
    const [options, turfs] = await Promise.all([fetchOptions(filters), fetchTurfs(filters)]);
    cached = { options, turfs };
    cachedAt = now;
    return NextResponse.json(cached);
  }

  const [options, turfs] = await Promise.all([fetchOptions(filters), fetchTurfs(filters)]);
  return NextResponse.json({ options, turfs });
}
