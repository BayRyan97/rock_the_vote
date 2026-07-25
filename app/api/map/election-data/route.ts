import { NextResponse } from "next/server";
import pool from "@/lib/db";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
let cached: any = null;
let cachedAt = 0;
const TTL_MS = 60_000 * 15;

// eslint-disable-next-line @typescript-eslint/no-explicit-any
let metricsCached: any = null;
let metricsCachedAt = 0;

export async function GET() {
  const now = Date.now();

  // Fast path: election results only (small table, always fast)
  if (cached && now - cachedAt < TTL_MS) {
    // Attach metrics if already computed
    if (metricsCached) return NextResponse.json({ ...cached, district_metrics: metricsCached });
    return NextResponse.json(cached);
  }

  const { rows } = await pool.query(
    `SELECT chamber, year, district, dem_votes, rep_votes, other_votes, total_votes,
            dem_candidate, rep_candidate, dem_pct, margin_pct, winner
     FROM election_results ORDER BY chamber, year, district`
  );

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const election_results: Record<string, Record<string, Record<string, any>>> = {};
  for (const r of rows) {
    const ch = r.chamber as string;
    const yr = String(r.year);
    const dist = String(r.district);
    if (!election_results[ch]) election_results[ch] = {};
    if (!election_results[ch][yr]) election_results[ch][yr] = {};
    election_results[ch][yr][dist] = {
      dem_pct: Number(r.dem_pct),
      margin_pct: Number(r.margin_pct),
      total_votes: r.total_votes,
      dem_votes: r.dem_votes,
      rep_votes: r.rep_votes,
      other_votes: r.other_votes,
      dem_candidate: r.dem_candidate,
      rep_candidate: r.rep_candidate,
      winner: r.winner,
    };
  }

  cached = { election_results };
  cachedAt = now;

  return NextResponse.json({
    election_results,
    district_metrics: metricsCached ?? {},
  });
}

// Separate slow endpoint for district metrics (people × households JOIN)
export async function POST() {
  const now = Date.now();
  if (metricsCached && now - metricsCachedAt < TTL_MS) {
    return NextResponse.json(metricsCached);
  }

  async function metricsForCol(districtCol: string) {
    const { rows } = await pool.query(
      `SELECT
         h.${districtCol}::text AS district,
         COUNT(p.id)::int AS total_voters,
         ROUND(COUNT(CASE WHEN p.party = 'DEM' THEN 1 END)::numeric / NULLIF(COUNT(p.id),0) * 100, 1) AS dem_pct,
         ROUND(COUNT(CASE WHEN p.party = 'REP' THEN 1 END)::numeric / NULLIF(COUNT(p.id),0) * 100, 1) AS rep_pct,
         ROUND(COUNT(CASE WHEN p.party NOT IN ('DEM','REP') THEN 1 END)::numeric / NULLIF(COUNT(p.id),0) * 100, 1) AS blk_pct,
         ROUND((COUNT(CASE WHEN p.party='DEM' THEN 1 END)::numeric - COUNT(CASE WHEN p.party='REP' THEN 1 END)::numeric)
               / NULLIF(COUNT(p.id),0) * 100, 1) AS registration_gap,
         ROUND(COUNT(CASE WHEN p.tier_letter = 'X' THEN 1 END)::numeric / NULLIF(COUNT(p.id),0) * 100, 1) AS reliable_pct,
         COUNT(CASE WHEN p.party = 'DEM' AND p.tier_letter IN ('F','L') THEN 1 END)::int AS dropoff_dem_count,
         ROUND(AVG(1.0 - p.turnout_prob::float), 3) AS avg_engagement_gap
       FROM people p
       JOIN households h ON p.household_id = h.id
       WHERE h.${districtCol} IS NOT NULL
       GROUP BY h.${districtCol}`,
      [],
    );
    const out: Record<string, object> = {};
    for (const r of rows) {
      if (r.district) out[r.district] = {
        total_voters: r.total_voters,
        dem_pct: Number(r.dem_pct),
        rep_pct: Number(r.rep_pct),
        blk_pct: Number(r.blk_pct),
        registration_gap: Number(r.registration_gap),
        reliable_pct: Number(r.reliable_pct),
        dropoff_dem_count: r.dropoff_dem_count,
        avg_engagement_gap: Number(r.avg_engagement_gap),
      };
    }
    return out;
  }

  const [assembly, senate, congressional] = await Promise.all([
    metricsForCol("assembly_district"),
    metricsForCol("senate_district"),
    metricsForCol("congressional_district"),
  ]);

  metricsCached = { assembly, senate, congressional };
  metricsCachedAt = now;
  return NextResponse.json(metricsCached);
}
