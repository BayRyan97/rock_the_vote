import { NextRequest, NextResponse } from "next/server";
import pool from "@/lib/db";

interface DonationRow {
  donor_key: string;
  source: string;
  donation_date: string | null;
  amount: number | null;
  committee: string | null;
  confirmed: boolean;
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

async function buildResponse(rows: DonationRow[]) {
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

  const donors = [...map.values()]
    .filter((d) => d.confirmed.length > 0 || d.possible.length > 0)
    .sort((a, b) => b.total_confirmed - a.total_confirmed);

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

  const totalAmount = donors.reduce((s, d) => s + d.total_confirmed, 0);
  const meta = `${donors.length.toLocaleString()} donor${donors.length === 1 ? "" : "s"} · $${totalAmount.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} total`;
  return NextResponse.json({ donors: top200, meta });
}

export async function GET(req: NextRequest) {
  const q           = (req.nextUrl.searchParams.get("q") ?? "").trim().toUpperCase();
  const byCommittee = req.nextUrl.searchParams.get("committee") === "1";

  if (!q) return NextResponse.json({ donors: [], meta: "" });

  if (byCommittee) {
    const { rows } = await pool.query<DonationRow>(
      `SELECT donor_key, source, donation_date::text AS donation_date,
              amount::float8 AS amount, committee, confirmed
       FROM donations
       WHERE committee ILIKE $1 AND confirmed = TRUE
       ORDER BY donation_date DESC
       LIMIT 5000`,
      [`%${q}%`]
    );
    return buildResponse(rows);
  }

  const param = /^\d{4,5}$/.test(q) ? `%|${q}%` : `%${q}%`;
  const { rows } = await pool.query<DonationRow>(
    `SELECT donor_key, source, donation_date::text AS donation_date,
            amount::float8 AS amount, committee, confirmed
     FROM donations
     WHERE donor_key ILIKE $1
     ORDER BY donation_date DESC
     LIMIT 5000`,
    [param]
  );
  return buildResponse(rows);
}
