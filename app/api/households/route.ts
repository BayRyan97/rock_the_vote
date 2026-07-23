import { NextRequest, NextResponse } from "next/server";
import pool from "@/lib/db";

export async function GET(req: NextRequest) {
  const q = (req.nextUrl.searchParams.get("q") ?? "").trim();
  if (!q) return NextResponse.json([]);

  const upper = q.toUpperCase();

  // Detect "NUMBER TEXT" pattern — e.g. "43 Bayville", "12 Main St", "43B Oak Ave"
  // address_num is stored separately from street/city, so a combined ILIKE on either
  // column can never match. Split and search each column independently instead.
  const houseMatch = upper.match(/^(\d+[A-Z]?)\s+(.+)$/);

  const [addrRes, nameRes] = await Promise.all([
    houseMatch
      ? pool.query<{ id: string; score_total: number }>(
          `SELECT id, score_total FROM households
           WHERE address_num = $1 AND (street ILIKE $2 OR city ILIKE $2)
           ORDER BY score_total DESC LIMIT 60`,
          [houseMatch[1], `%${houseMatch[2]}%`]
        )
      : pool.query<{ id: string; score_total: number }>(
          // UNION (not OR) lets each column use its own GIN trigram index independently
          `(SELECT id, score_total FROM households WHERE street ILIKE $1 ORDER BY score_total DESC LIMIT 50)
           UNION
           (SELECT id, score_total FROM households WHERE city ILIKE $1 ORDER BY score_total DESC LIMIT 50)
           ORDER BY score_total DESC LIMIT 50`,
          [`%${upper}%`]
        ),
    pool.query<{ household_id: string; score_total: number }>(
      `SELECT DISTINCT p.household_id, h.score_total
       FROM people p
       JOIN households h ON h.id = p.household_id
       WHERE p.name ILIKE $1
       ORDER BY h.score_total DESC LIMIT 100`,
      [`%${upper}%`]
    ),
  ]);

  // Merge and deduplicate by score
  const seen = new Map<string, number>();
  for (const r of addrRes.rows) seen.set(r.id, r.score_total);
  for (const r of nameRes.rows) {
    if (!seen.has(r.household_id)) seen.set(r.household_id, r.score_total);
  }
  const ids = [...seen.entries()]
    .sort((a, b) => b[1] - a[1])
    .slice(0, 60)
    .map(([id]) => id);

  if (!ids.length) return NextResponse.json([]);

  const placeholders = ids.map((_, i) => `$${i + 1}`).join(",");

  // Three parallel fetches for the 60 matched households
  const [hhRes, peopleRes, evRes] = await Promise.all([
    pool.query(
      `SELECT id, county, address_num, street, city, zip, town,
              election_district, assembly_district, senate_district, congressional_district,
              lon::float8 AS lon, lat::float8 AS lat,
              score_total, score_wake_ups, score_unaffiliated, score_dropoff
       FROM households WHERE id IN (${placeholders})`,
      ids
    ),
    pool.query(
      `SELECT household_id, name, age, party, tier_letter, tier_count, elections
       FROM (
         SELECT *,
           ROW_NUMBER() OVER (
             PARTITION BY household_id
             ORDER BY CASE tier_letter WHEN 'X' THEN 0 WHEN 'F' THEN 1 WHEN 'L' THEN 2 ELSE 3 END,
                      tier_count DESC
           ) AS rn
         FROM people WHERE household_id IN (${placeholders})
       ) t
       WHERE rn <= 30`,
      ids
    ),
    pool.query(`SELECT zip, score, count FROM ev_scores`),
  ]);

  const evMap = new Map(
    (evRes.rows as { zip: string; score: number; count: number }[]).map((e) => [e.zip, e])
  );
  // Cap at 30 people per household — apartments can have 400+ voters,
  // no need to ship them all to the browser. People are pre-sorted by tier.
  const MAX_PEOPLE = 30;
  const peopleByHH = new Map<string, unknown[]>();
  for (const p of peopleRes.rows) {
    if (!peopleByHH.has(p.household_id)) peopleByHH.set(p.household_id, []);
    const arr = peopleByHH.get(p.household_id)!;
    if (arr.length < MAX_PEOPLE) {
      // DB stores elections as [[year, ballot], ...] — normalize to {year, ballot} for the client
      const elections = Array.isArray(p.elections)
        ? (p.elections as [number, string][]).map(([year, ballot]) => ({ year, ballot }))
        : [];
      arr.push({ ...p, elections });
    }
  }

  const hhById = new Map(hhRes.rows.map((h) => [h.id as string, h]));
  const result = ids.map((id) => {
    const h = hhById.get(id)!;
    const people = peopleByHH.get(id) ?? [];
    const ev = evMap.get(h.zip);
    const matchedIdx = (people as { name: string }[]).findIndex((p) =>
      p.name?.toUpperCase().includes(upper)
    );
    return {
      ...h,
      people,
      ev_score: ev?.score ?? 0,
      ev_count: ev?.count ?? 0,
      matched_idx: matchedIdx,
    };
  });

  return NextResponse.json(result);
}
