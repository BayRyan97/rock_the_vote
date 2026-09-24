import { NextRequest, NextResponse } from "next/server";
import pool from "@/lib/db";
import { buildGeoWhereSql, parseGeoFilters } from "@/lib/geoFilters";
import { requireUser } from "@/lib/supabase/authz";

export async function GET(req: NextRequest) {
  const { user } = await requireUser();
  if (!user) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });

  const p = req.nextUrl.searchParams;
  const south = parseFloat(p.get("s") ?? "");
  const north = parseFloat(p.get("n") ?? "");
  const west = parseFloat(p.get("w") ?? "");
  const east = parseFloat(p.get("e") ?? "");

  if ([south, north, west, east].some(isNaN)) {
    return NextResponse.json({ error: "Missing bounds s/n/w/e" }, { status: 400 });
  }

  const turfsParam  = p.get("turfs");
  const allMode     = p.get("all") === "1";
  const limit       = allMode ? 5000 : Math.min(Math.max(parseInt(p.get("limit") ?? "500"), 50), 800);

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const params: any[] = [south, north, west, east];
  let extra = "";

  // Up to 8 combinable geo dimensions (county/city/town/election_district/
  // legislative_district/congressional_district/senate_district/
  // assembly_district) — see lib/geoFilters.ts. This route selects directly
  // FROM households with no alias, so tableAlias is "".
  const geoFilters = parseGeoFilters(p);
  const { sql: geoSql, params: geoParams } = buildGeoWhereSql(geoFilters, {
    tableAlias: "",
    paramOffset: params.length,
  });
  extra += geoSql;
  params.push(...geoParams);

  if (turfsParam !== null) {
    const turfs = turfsParam ? turfsParam.split(",").map(Number).filter(n => Number.isFinite(n)) : [];
    extra += ` AND turf_id = ANY($${params.length + 1}::int[])`;
    params.push(turfs);
  }
  // arm=treatment is how the client says "canvassable only" without listing
  // 1,345 turf ids in the query string. Control and buffer turfs are the
  // randomized holdout — knocking them contaminates the experiment — so the
  // subquery against the 1,701-row turfs table is the cheap, honest filter.
  const armParam = p.get("arm");
  if (armParam && /^[a-z]+$/.test(armParam)) {
    extra += ` AND turf_id IN (SELECT turf_id FROM turfs WHERE arm = $${params.length + 1})`;
    params.push(armParam);
  }

  const { rows } = await pool.query(
    // is_facility: an apartment building, not a door. It carries a turf_id so it
    // still appears when its turf is selected, but it is deliberately NOT part of
    // that turf's n_doors or value — a canvasser can't knock a locked lobby. The
    // map marks it so nobody walks up expecting a door.
    `SELECT id, lat::float8 AS lat, lon::float8 AS lon, score_total,
            address_num, street, city, zip,
            score_wake_ups, score_unaffiliated, score_dropoff, is_facility,
            COALESCE(people_count, 0) AS people_count
     FROM households
     WHERE lat >= $1 AND lat <= $2 AND lon >= $3 AND lon <= $4
       AND lat IS NOT NULL
       ${allMode ? "" : "AND score_total > 0"}${extra}
     ORDER BY score_total DESC
     LIMIT ${limit}`,
    params
  );

  return NextResponse.json(rows);
}
