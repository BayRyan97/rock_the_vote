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

  const { rows } = await pool.query(
    `SELECT id, lat::float8 AS lat, lon::float8 AS lon, score_total,
            address_num, street, city, zip,
            score_wake_ups, score_unaffiliated, score_dropoff,
            COALESCE(people_count, 0) AS people_count
     FROM households
     WHERE lat >= $1 AND lat <= $2 AND lon >= $3 AND lon <= $4
       AND lat IS NOT NULL
     ORDER BY score_total DESC
     LIMIT 2000`,
    [south, north, west, east]
  );

  return NextResponse.json(rows);
}
