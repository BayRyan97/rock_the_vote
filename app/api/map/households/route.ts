import { NextRequest, NextResponse } from "next/server";
import pool from "@/lib/db";

export async function GET(req: NextRequest) {
  const p = req.nextUrl.searchParams;
  const south = parseFloat(p.get("s") ?? "");
  const north = parseFloat(p.get("n") ?? "");
  const west = parseFloat(p.get("w") ?? "");
  const east = parseFloat(p.get("e") ?? "");

  if ([south, north, west, east].some(isNaN)) {
    return NextResponse.json({ error: "Missing bounds s/n/w/e" }, { status: 400 });
  }

  const adsParam    = p.get("ads");
  const citiesParam = p.get("cities");
  const turfsParam  = p.get("turfs");
  const allMode     = p.get("all") === "1";
  const limit       = allMode ? 5000 : Math.min(Math.max(parseInt(p.get("limit") ?? "500"), 50), 800);

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const params: any[] = [south, north, west, east];
  let extra = "";

  if (adsParam !== null) {
    const ads = adsParam ? adsParam.split(",").map(Number).filter(n => Number.isFinite(n)) : [];
    extra += ` AND assembly_district = ANY($${params.length + 1}::int[])`;
    params.push(ads);
  }
  if (citiesParam !== null) {
    const cities = citiesParam
      ? citiesParam.split(",").map(c => decodeURIComponent(c).toUpperCase()).filter(Boolean)
      : [];
    extra += ` AND upper(city) = ANY($${params.length + 1}::text[])`;
    params.push(cities);
  }
  if (turfsParam !== null) {
    const turfs = turfsParam ? turfsParam.split(",").map(Number).filter(n => Number.isFinite(n)) : [];
    extra += ` AND turf_id = ANY($${params.length + 1}::int[])`;
    params.push(turfs);
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
