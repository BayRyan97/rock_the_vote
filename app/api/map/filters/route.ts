import { NextResponse } from "next/server";
import pool from "@/lib/db";

let cached: { assembly_districts: number[]; cities: string[] } | null = null;
let cachedAt = 0;
const TTL_MS = 60_000 * 10;

export async function GET() {
  const now = Date.now();
  if (cached && now - cachedAt < TTL_MS) {
    return NextResponse.json(cached);
  }

  const [adsRes, citiesRes] = await Promise.all([
    pool.query(
      `SELECT DISTINCT assembly_district
       FROM households
       WHERE assembly_district IS NOT NULL AND score_total > 0
       ORDER BY 1`
    ),
    pool.query(
      `SELECT DISTINCT city
       FROM households
       WHERE city IS NOT NULL AND score_total > 0
       ORDER BY 1`
    ),
  ]);

  cached = {
    assembly_districts: adsRes.rows.map((r) => Number(r.assembly_district)),
    cities: citiesRes.rows.map((r) => r.city as string),
  };
  cachedAt = now;
  return NextResponse.json(cached);
}
