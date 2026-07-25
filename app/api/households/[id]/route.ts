import { NextRequest, NextResponse } from "next/server";
import pool from "@/lib/db";

export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params;

  const [hhRes, peopleRes, evRes] = await Promise.all([
    pool.query(
      `SELECT id, county, address_num, street, city, zip, town,
              election_district, assembly_district, senate_district, congressional_district,
              lon::float8 AS lon, lat::float8 AS lat,
              score_total, score_wake_ups, score_unaffiliated, score_dropoff
       FROM households WHERE id = $1`,
      [id]
    ),
    pool.query(
      `SELECT household_id, name, age, party, tier_letter, tier_count, elections
       FROM people WHERE household_id = $1
       ORDER BY CASE tier_letter WHEN 'X' THEN 0 WHEN 'F' THEN 1 WHEN 'L' THEN 2 ELSE 3 END,
                tier_count DESC
       LIMIT 30`,
      [id]
    ),
    pool.query(`SELECT zip, score, count FROM ev_scores`),
  ]);

  if (!hhRes.rows.length) {
    return NextResponse.json({ error: "Not found" }, { status: 404 });
  }

  const h = hhRes.rows[0];
  const evMap = new Map(
    (evRes.rows as { zip: string; score: number; count: number }[]).map((e) => [e.zip, e])
  );
  const ev = evMap.get(h.zip);

  const people = peopleRes.rows.map((p) => {
    const elections = Array.isArray(p.elections)
      ? (p.elections as [number, string][]).map(([year, ballot]) => ({ year, ballot }))
      : [];
    return { ...p, elections };
  });

  return NextResponse.json({
    ...h,
    people,
    ev_score: ev?.score ?? 0,
    ev_count: ev?.count ?? 0,
    matched_idx: -1,
  });
}
