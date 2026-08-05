import { NextRequest, NextResponse } from "next/server";
import pool from "@/lib/db";

export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params;

  const [hhRes, peopleRes, evRes] = await Promise.all([
    pool.query(
      // people_count and is_facility make this response self-sufficient for
      // rendering a popup: the Buildings panel opens one from a facility row,
      // which has no HHPoint behind it the way a map marker does.
      `SELECT id, county, address_num, street, city, zip, town,
              election_district, assembly_district, senate_district, congressional_district,
              lon::float8 AS lon, lat::float8 AS lat,
              score_total, score_wake_ups, score_unaffiliated, score_dropoff,
              is_facility, COALESCE(people_count, 0) AS people_count
       FROM households WHERE id = $1`,
      [id]
    ),
    // m_net_i is the turf model's own per-voter targeting weight, and its
    // argmax within a household is A(h) — the person the canvasser should ask
    // for, and the person the spillover term in value_households assumes was
    // reached. Ordering by tier instead named a different person in 58% of
    // multi-target households, so the model's answer has to lead here.
    // LEFT JOIN because not everyone is a target: the pool is filtered on
    // turnout 0.20–0.80 and dem_lean >= 0.55, so housemates outside it come
    // back NULL and sort below, ordered among themselves by tier as before.
    pool.query(
      `SELECT p.household_id, p.name, p.age, p.party, p.tier_letter, p.tier_count,
              p.elections,
              p.turnout_prob::float8 AS turnout_prob,
              p.dem_lean_prob::float8 AS dem_lean_prob,
              ta.m_net_i::float8 AS m_net_i
       FROM people p
       LEFT JOIN turf_assignment ta ON ta.person_id = p.id
       WHERE p.household_id = $1
       ORDER BY ta.m_net_i DESC NULLS LAST,
                CASE p.tier_letter WHEN 'X' THEN 0 WHEN 'F' THEN 1 WHEN 'L' THEN 2 ELSE 3 END,
                p.tier_count DESC
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
