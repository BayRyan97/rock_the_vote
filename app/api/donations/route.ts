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

interface DonorCard {
  donor_key: string;
  name: string;
  city: string;
  zip: string;
  party: string | null;
  total_confirmed: number;
  confirmed: DonationRow[];
  possible: DonationRow[];
}

// rankTotals, when given (committee search), holds each donor's total given
// to the SEARCHED committee specifically -- used for sort order and the meta
// total. `total_confirmed` on each card stays the donor's grand total across
// every committee in `rows`, so it agrees with the confirmed/possible lists
// rendered in the expanded card (which now include their other committees).
async function buildResponse(rows: DonationRow[], rankTotals?: Map<string, number>) {
  const map = new Map<string, DonorCard>();
  for (const row of rows) {
    const key = row.donor_key;
    if (!map.has(key)) {
      const parts = key.split("|");
      map.set(key, {
        donor_key: key,
        name:  parts[0] ?? "",
        city:  parts[1] ?? "",
        zip:   parts[2] ?? "",
        party: null,
        total_confirmed: 0,
        confirmed: [],
        possible:  [],
      });
    }
    const donor = map.get(key)!;
    if (row.confirmed) {
      donor.confirmed.push(row);
      donor.total_confirmed += row.amount ?? 0;
    } else {
      donor.possible.push(row);
    }
  }

  const donors = [...map.values()].filter((d) => d.confirmed.length > 0 || d.possible.length > 0);
  donors.sort((a, b) =>
    rankTotals
      ? (rankTotals.get(b.donor_key) ?? 0) - (rankTotals.get(a.donor_key) ?? 0)
      : b.total_confirmed - a.total_confirmed
  );

  const top200 = donors.slice(0, 200);

  if (top200.length > 0) {
    const keys = top200.map((d) => d.donor_key);
    const { rows: partyRows } = await pool.query<{ donor_key: string; party: string }>(
      `SELECT DISTINCT ON (donor_key) donor_key, party
       FROM people WHERE donor_key = ANY($1)`,
      [keys]
    );
    const partyMap = new Map(partyRows.map((r) => [r.donor_key, r.party]));
    for (const d of top200) d.party = partyMap.get(d.donor_key) ?? null;
  }

  const totalAmount = rankTotals
    ? top200.reduce((s, d) => s + (rankTotals.get(d.donor_key) ?? 0), 0)
    : donors.reduce((s, d) => s + d.total_confirmed, 0);
  const meta = `${donors.length.toLocaleString()} donor${donors.length === 1 ? "" : "s"} · $${totalAmount.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} total`;
  return NextResponse.json({ donors: top200, meta });
}

export async function GET(req: NextRequest) {
  const q           = (req.nextUrl.searchParams.get("q") ?? "").trim().toUpperCase();
  const byCommittee = req.nextUrl.searchParams.get("committee") === "1";

  if (!q) return NextResponse.json({ donors: [], meta: "" });

  if (byCommittee) {
    // Rank donors by what they gave to the SEARCHED committee, then pull
    // every one of those donors' donations (any committee, confirmed or
    // possible) so the expanded card shows their other committees too.
    const { rows: totalsRows } = await pool.query<{ donor_key: string; committee_total: number }>(
      `SELECT donor_key, SUM(amount)::float8 AS committee_total
       FROM donations
       WHERE committee ILIKE $1 AND confirmed = TRUE
       GROUP BY donor_key
       ORDER BY committee_total DESC
       LIMIT 200`,
      [`%${q}%`]
    );
    if (totalsRows.length === 0) return buildResponse([]);

    const rankTotals = new Map(totalsRows.map((r) => [r.donor_key, r.committee_total]));
    const keys = totalsRows.map((r) => r.donor_key);

    const { rows } = await pool.query<DonationRow>(
      `SELECT donor_key, source, donation_date::text AS donation_date,
              amount::float8 AS amount, committee, confirmed, employer, occupation
       FROM donations
       WHERE donor_key = ANY($1)
       ORDER BY donation_date DESC
       LIMIT 20000`,
      [keys]
    );
    return buildResponse(rows, rankTotals);
  }

  const param = /^\d{4,5}$/.test(q) ? `%|${q}%` : `%${q}%`;
  const { rows } = await pool.query<DonationRow>(
    `SELECT donor_key, source, donation_date::text AS donation_date,
            amount::float8 AS amount, committee, confirmed, employer, occupation
     FROM donations
     WHERE donor_key ILIKE $1
     ORDER BY donation_date DESC
     LIMIT 5000`,
    [param]
  );
  return buildResponse(rows);
}
