import { NextRequest, NextResponse } from "next/server";
import pool from "@/lib/db";
import { buildGeoWhereSql, parseGeoFilters } from "@/lib/geoFilters";
import { requireUser } from "@/lib/supabase/authz";

// Apartment buildings and facilities, ranked. These are deliberately absent
// from the walk list — a canvasser cannot knock a locked lobby, and counting a
// 100-voter tower as one 3-minute door is what made apartment-dense turfs look
// like the most efficient in the county. But 1,407 buildings hold 20,372
// targets, 5% of the whole pool, so "not walkable" must not become "invisible".
// This is the surface that lets someone actually work them: lobby access, a
// building contact, phone.
//
// No hours figure, on purpose. What it costs to reach a building is an
// organising question, not doors ÷ 20/hour.
export async function GET(req: NextRequest) {
  const { user } = await requireUser();
  if (!user) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });

  const p = req.nextUrl.searchParams;
  const armParam    = p.get("arm");
  const limit = Math.min(Math.max(parseInt(p.get("limit") ?? "150"), 10), 500);

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const params: any[] = [];
  let extra = "";

  // Up to 8 combinable geo dimensions — see lib/geoFilters.ts. Scoped by the
  // same state as the turf list, so this panel never silently desyncs from it.
  const geoFilters = parseGeoFilters(p);
  const { sql: geoSql, params: geoParams } = buildGeoWhereSql(geoFilters, {
    tableAlias: "h",
    paramOffset: params.length,
  });
  extra += geoSql;
  params.push(...geoParams);

  // Match the turf list's "Canvassable only": a building whose nearest turf is
  // a control or buffer sits inside the randomized holdout, and working it
  // contaminates the same experiment knocking that turf would.
  if (armParam && /^[a-z]+$/.test(armParam)) {
    extra += ` AND t.arm = $${params.length + 1}`;
    params.push(armParam);
  }

  const { rows } = await pool.query(
    `SELECT f.facility_id, f.household_id, f.n_targets, f.household_size,
            f.value_net_margin::float8 AS value_net_margin,
            f.lat::float8 AS lat, f.lon::float8 AS lon,
            f.nearest_turf_id,
            h.address_num, h.street, h.city, h.zip,
            COALESCE(h.people_count, 0) AS people_count
     FROM facilities f
     JOIN households h ON h.id = f.household_id
     LEFT JOIN turfs t ON t.turf_id = f.nearest_turf_id
     WHERE true${extra}
     ORDER BY f.value_net_margin DESC
     LIMIT ${limit}`,
    params
  );

  return NextResponse.json(rows);
}
