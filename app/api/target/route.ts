import { NextRequest, NextResponse } from "next/server";
import Anthropic from "@anthropic-ai/sdk";
import pool from "@/lib/db";

const anthropic = new Anthropic({ apiKey: process.env.ANTHROPIC_API_KEY });

const SYSTEM_PROMPT = `You are a voter targeting assistant for a Democratic campaign on Long Island, NY (AD-12, Suffolk County).

The voter database has these fields:

people:
- name (text)
- age (smallint)
- party: DEM, REP, BLK (blank/unaffiliated), WOR (Working Families), CON (Conservative), IND, OTH
- tier_letter — voting frequency tier:
    X = super-voter (votes in nearly every election, highest priority)
    F = frequent voter (votes in most elections)
    L = low propensity voter (rarely votes)
    I = inactive voter (little to no participation)
- elections: JSON array [[year, ballot_type], ...] — records of elections the person voted in

households:
- city, zip, county (NASSAU or SUFFOLK)
- assembly_district, senate_district, congressional_district (integers)
- score_total (integer, higher = more high-priority voters at that address)

donations (aggregated per person):
- total_donated (numeric, sum of all donations)
- donation_count (integer)
- has_donated (boolean)

Tier guidance:
- "high turnout", "reliable voters", "likely voters" → tier_letters: ["X", "F"]
- "super voters", "definite voters" → tier_letters: ["X"]
- "drop-off voters", "inconsistent voters" → tier_letters: ["F", "L"]
- "low propensity", "hard to reach", "infrequent" → tier_letters: ["L", "I"]
- "persuadable", "unaffiliated" → party: ["BLK", "IND"] with tier_letters: ["X", "F"]

Given a targeting description, return ONLY valid JSON (no markdown, no explanation outside the JSON):
{
  "filters": {
    "party": ["DEM"] or null,
    "age_min": integer or null,
    "age_max": integer or null,
    "cities": ["BRENTWOOD"] or null (uppercase city names),
    "county": "NASSAU" or "SUFFOLK" or null,
    "assembly_district": integer or null,
    "senate_district": integer or null,
    "tier_letters": ["X","F","L","I"] or null,
    "has_donated": true or null,
    "min_total_donated": number or null,
    "voted_in_years": [2024, 2022] or null (only include people who voted in ALL these years),
    "skipped_years": [2024] or null (only include people who did NOT vote in these years)
  },
  "sort_by": "score_total" | "total_donated" | "age",
  "sort_desc": true or false,
  "explanation": "One sentence describing the targeting strategy."
}`;

interface TargetFilters {
  party?: string[] | null;
  age_min?: number | null;
  age_max?: number | null;
  cities?: string[] | null;
  county?: string | null;
  assembly_district?: number | null;
  senate_district?: number | null;
  tier_letters?: string[] | null;
  has_donated?: boolean | null;
  min_total_donated?: number | null;
  voted_in_years?: number[] | null;
  skipped_years?: number[] | null;
}

interface ClaudeResponse {
  filters: TargetFilters;
  sort_by: string;
  sort_desc: boolean;
  explanation: string;
}

const ALLOWED_SORT = new Set(["score_total", "total_donated", "age"]);

export async function POST(req: NextRequest) {
  const { prompt } = await req.json();
  if (!prompt?.trim()) return NextResponse.json({ error: "No prompt provided." }, { status: 400 });

  // Ask Claude to parse the prompt into structured filters
  const msg = await anthropic.messages.create({
    model: "claude-sonnet-4-6",
    max_tokens: 1024,
    system: SYSTEM_PROMPT,
    messages: [{ role: "user", content: prompt }],
  });

  let parsed: ClaudeResponse;
  try {
    let raw = (msg.content[0] as { text: string }).text.trim();
    // Strip markdown code fences if Claude wrapped the JSON
    raw = raw.replace(/^```(?:json)?\s*/i, "").replace(/\s*```$/, "").trim();
    // Extract first {...} block in case there's surrounding text
    const match = raw.match(/\{[\s\S]*\}/);
    if (match) raw = match[0];
    parsed = JSON.parse(raw);
  } catch {
    return NextResponse.json({ error: "Could not parse targeting criteria. Try rephrasing." }, { status: 422 });
  }

  const { filters, sort_by, sort_desc, explanation } = parsed;
  const safeSort = ALLOWED_SORT.has(sort_by) ? sort_by : "score_total";
  const sortDir = sort_desc === false ? "ASC" : "DESC";

  // Build parameterized WHERE clauses
  const params: unknown[] = [];
  const where: string[] = ["1=1"];

  function p(val: unknown): string {
    params.push(val);
    return `$${params.length}`;
  }

  if (filters.party?.length) {
    where.push(`p.party = ANY(${p(filters.party)})`);
  }
  if (filters.age_min != null) where.push(`p.age >= ${p(filters.age_min)}`);
  if (filters.age_max != null) where.push(`p.age <= ${p(filters.age_max)}`);
  if (filters.cities?.length) {
    where.push(`UPPER(h.city) = ANY(${p(filters.cities.map((c) => c.toUpperCase()))})`);
  }
  if (filters.county) where.push(`h.county = ${p(filters.county.toUpperCase())}`);
  if (filters.assembly_district != null) where.push(`h.assembly_district = ${p(filters.assembly_district)}`);
  if (filters.senate_district != null) where.push(`h.senate_district = ${p(filters.senate_district)}`);
  if (filters.tier_letters?.length) {
    where.push(`p.tier_letter = ANY(${p(filters.tier_letters)})`);
  }
  if (filters.has_donated) where.push(`d.donation_count > 0`);
  if (filters.min_total_donated != null) where.push(`COALESCE(d.total_donated, 0) >= ${p(filters.min_total_donated)}`);

  // Election participation filters
  for (const year of filters.voted_in_years ?? []) {
    where.push(`EXISTS (SELECT 1 FROM jsonb_array_elements(p.elections) e WHERE (e->>0)::int = ${p(year)})`);
  }
  for (const year of filters.skipped_years ?? []) {
    where.push(`NOT EXISTS (SELECT 1 FROM jsonb_array_elements(p.elections) e WHERE (e->>0)::int = ${p(year)})`);
  }

  // Sort column mapping
  const sortCol =
    safeSort === "total_donated" ? "COALESCE(d.total_donated, 0)"
    : safeSort === "age" ? "p.age"
    : "h.score_total";

  const sql = `
    WITH donation_agg AS (
      SELECT donor_key,
             SUM(amount)  AS total_donated,
             COUNT(*)     AS donation_count
      FROM donations
      GROUP BY donor_key
    )
    SELECT
      p.id, p.name, p.age, p.party, p.tier_letter, p.elections,
      h.address_num, h.street, h.city, h.zip, h.county,
      h.assembly_district, h.senate_district, h.score_total,
      COALESCE(d.total_donated, 0)  AS total_donated,
      COALESCE(d.donation_count, 0) AS donation_count
    FROM people p
    JOIN households h ON p.household_id = h.id
    LEFT JOIN donation_agg d ON p.donor_key = d.donor_key
    WHERE ${where.join(" AND ")}
    ORDER BY
      CASE p.tier_letter WHEN 'X' THEN 0 WHEN 'F' THEN 1 WHEN 'L' THEN 2 ELSE 3 END,
      ${sortCol} ${sortDir} NULLS LAST
    LIMIT 500
  `;

  const result = await pool.query(sql, params);

  return NextResponse.json({
    explanation,
    filters,
    sort_by: safeSort,
    count: result.rowCount,
    results: result.rows,
  });
}
