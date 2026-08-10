import { NextRequest, NextResponse } from "next/server";
import pool from "@/lib/db";

interface DonationRow {
  donor_key: string;
  source: string;
  donation_date: string | null;
  amount: number | null;
  committee: string | null;
  confirmed: boolean;
  employer: string | null;
  occupation: string | null;
}

export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ turfId: string }> }
) {
  const { turfId } = await params;
  const id = Number(turfId);
  if (!Number.isFinite(id)) {
    return NextResponse.json({ error: "Invalid turf id" }, { status: 400 });
  }

  const [turfRes, rosterRes] = await Promise.all([
    pool.query(
      `SELECT turf_id, n_doors, n_targets,
              value_net_margin::float8      AS value_net_margin,
              value_dem_ballots::float8     AS value_dem_ballots,
              hours_per_net_margin::float8  AS hours_per_net_margin,
              hours_per_ballot::float8      AS hours_per_ballot,
              targets_per_door::float8      AS targets_per_door,
              n_facilities_nearby, county, arm, canvasser_hours, computed_at
       FROM turfs WHERE turf_id = $1`,
      [id]
    ),
    // turf_assignment.hh_id is a per-run ordinal, not a usable join key (same
    // hazard class as the addr_id column migration 019 dropped) -- household
    // data comes through the real FK chain instead: person_id -> people.id
    // -> people.household_id -> households.id. turf_id set (vs. facility_id)
    // already excludes facility residents per the turf_assignment_one_track
    // CHECK in migration 021, so no extra filtering is needed for that.
    pool.query(
      `SELECT
         p.id AS person_id, p.household_id, p.donor_key,
         p.name, p.age, p.party, p.tier_letter, p.tier_count, p.elections,
         p.turnout_prob::float8  AS turnout_prob,
         p.dem_lean_prob::float8 AS dem_lean_prob,
         ta.m_net_i::float8 AS m_net_i, ta.m_i::float8 AS m_i,
         MAX(ta.m_net_i) OVER (PARTITION BY p.household_id)::float8 AS hh_max_m_net_i,
         h.address_num, h.street, h.city, h.zip,
         COALESCE(dn.donation_count, 0) AS donation_count,
         COALESCE(dn.donation_total, 0)::float8 AS donation_total,
         bc.email, bc.phone
       FROM turf_assignment ta
       JOIN people p     ON p.id = ta.person_id
       JOIN households h ON h.id = p.household_id
       LEFT JOIN LATERAL (
         SELECT COUNT(*)::int AS donation_count, SUM(amount) AS donation_total
         FROM donations d
         WHERE d.donor_key = p.donor_key AND d.confirmed = true
       ) dn ON true
       LEFT JOIN LATERAL (
         SELECT email, phone FROM boe_contacts bc2
         WHERE bc2.full_name = p.name AND bc2.res_zip = p.zip
         LIMIT 1
       ) bc ON true
       WHERE ta.turf_id = $1
       ORDER BY ta.m_net_i DESC NULLS LAST
       LIMIT 500`,
      [id]
    ),
  ]);

  if (!turfRes.rows.length) {
    return NextResponse.json({ error: "Not found" }, { status: 404 });
  }

  // Second, narrower query: itemized donation line items for anyone with a
  // match, batched by distinct donor_key -- same two-step shape as the party
  // lookup follow-up in app/api/donations/route.ts.
  const donorKeys = [
    ...new Set(
      rosterRes.rows.filter((r) => r.donation_count > 0).map((r) => r.donor_key)
    ),
  ];
  const donationsByKey = new Map<string, DonationRow[]>();
  if (donorKeys.length) {
    const { rows } = await pool.query<DonationRow>(
      `SELECT donor_key, source, donation_date::text AS donation_date,
              amount::float8 AS amount, committee, confirmed, employer, occupation
       FROM donations
       WHERE donor_key = ANY($1) AND confirmed = true
       ORDER BY donation_date DESC`,
      [donorKeys]
    );
    for (const row of rows) {
      if (!donationsByKey.has(row.donor_key)) donationsByKey.set(row.donor_key, []);
      donationsByKey.get(row.donor_key)!.push(row);
    }
  }

  const people = rosterRes.rows.map((row) => {
    const { donor_key, hh_max_m_net_i, m_net_i, elections, ...rest } = row;
    return {
      ...rest,
      m_net_i,
      is_ask: m_net_i != null && m_net_i === hh_max_m_net_i,
      elections: Array.isArray(elections)
        ? (elections as [number, string][]).map(([year, ballot]) => ({ year, ballot }))
        : [],
      donations: donationsByKey.get(donor_key) ?? [],
    };
  });

  return NextResponse.json({ turf: turfRes.rows[0], people });
}
