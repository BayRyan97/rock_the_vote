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
  const allMode     = p.get("all") === "1";

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

  const { rows } = await pool.query(
    `SELECT id, lat::float8 AS lat, lon::float8 AS lon, score_total,
            address_num, street, city, zip,
            score_wake_ups, score_unaffiliated, score_dropoff,
            COALESCE(people_count, 0) AS people_count
     FROM households
     WHERE lat >= $1 AND lat <= $2 AND lon >= $3 AND lon <= $4
       AND lat IS NOT NULL${extra}
     ORDER BY score_total DESC
     LIMIT ${allMode ? 5000 : 2000}`,
    params
  );

  return NextResponse.json(rows);
}
