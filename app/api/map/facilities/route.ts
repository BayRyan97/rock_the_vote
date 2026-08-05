import { NextRequest, NextResponse } from "next/server";
import pool from "@/lib/db";

// Apartment buildings and facilities, ranked. These are deliberately absent
// from the walk list — a canvasser cannot knock a locked lobby, and counting a
// 100-voter tower as one 3-minute door is what made apartment-dense turfs look
// like the most efficient in the county. But 1,407 buildings hold 20,372
// targets, 5% of the whole pool, so "not walkable" must not become "invisible".
// This is the surface that lets someone actually work them: lobby access, a
// building contact, phone.
//
// No hours figure, on purpose. What it costs to reach a building is an
// organising question, not doors ÷ 20/hour.
export async function GET(req: NextRequest) {
  const p = req.nextUrl.searchParams;
  const adsParam    = p.get("ads");
  const citiesParam = p.get("cities");
  const armParam    = p.get("arm");
  const limit = Math.min(Math.max(parseInt(p.get("limit") ?? "150"), 10), 500);

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const params: any[] = [];
  let extra = "";

  if (adsParam !== null) {
    const ads = adsParam ? adsParam.split(",").map(Number).filter(n => Number.isFinite(n)) : [];
    extra += ` AND h.assembly_district = ANY($${params.length + 1}::int[])`;
    params.push(ads);
  }
  if (citiesParam !== null) {
    const cities = citiesParam
      ? citiesParam.split(",").map(c => decodeURIComponent(c).toUpperCase()).filter(Boolean)
      : [];
    extra += ` AND upper(h.city) = ANY($${params.length + 1}::text[])`;
    params.push(cities);
  }
  // Match the turf list's "Canvassable only": a building whose nearest turf is
  // a control or buffer sits inside the randomized holdout, and working it
  // contaminates the same experiment knocking that turf would.
  if (armParam && /^[a-z]+$/.test(armParam)) {
    extra += ` AND t.arm = $${params.length + 1}`;
    params.push(armParam);
  }

  const { rows } = await pool.query(
    `SELECT f.facility_id, f.household_id, f.n_targets, f.household_size,
            f.value_net_margin::float8 AS value_net_margin,
            f.lat::float8 AS lat, f.lon::float8 AS lon,
            f.nearest_turf_id,
            h.address_num, h.street, h.city, h.zip,
            COALESCE(h.people_count, 0) AS people_count
     FROM facilities f
     JOIN households h ON h.id = f.household_id
     LEFT JOIN turfs t ON t.turf_id = f.nearest_turf_id
     WHERE true${extra}
     ORDER BY f.value_net_margin DESC
     LIMIT ${limit}`,
    params
  );

  return NextResponse.json(rows);
}
