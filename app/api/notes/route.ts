import { NextRequest, NextResponse } from "next/server";
import { requireRole } from "@/lib/supabase/authz";
import { NOTES_MANAGER_ROLES, NOTES_FULL_ACCESS_ROLES, OUTCOMES, SUPPORT_LEVELS } from "@/lib/canvassNotes";

const SORT_COLUMNS = ["created_at", "turf_id"] as const;
type SortColumn = (typeof SORT_COLUMNS)[number];

const DEFAULT_LIMIT = 50;
const MAX_LIMIT = 200;

function parseListParam<T extends string>(value: string | null, allowed: readonly T[]): T[] {
  if (!value) return [];
  return value.split(",").filter((v): v is T => (allowed as readonly string[]).includes(v));
}

// Cross-household notes browser for admin/running/campaign_manager -- unlike
// /api/households/[id]/notes (open to any authenticated user, unchanged),
// this route rolls up notes across every household so managers can see who
// logged what, in which turf, and filter/sort/page across the whole table.
export async function GET(req: NextRequest) {
  const { supabase, profile, error } = await requireRole(NOTES_MANAGER_ROLES);
  if (error) return NextResponse.json({ error }, { status: error === "Unauthorized" ? 401 : 403 });

  const params = req.nextUrl.searchParams;

  // "running" is scoped to notes canvassed for them (their own name);
  // "campaign_manager" is scoped to notes for the candidate an admin
  // assigned them to (profiles.campaign_name). Everyone else in
  // NOTES_MANAGER_ROLES (admin, running_admin, campaign_manager_admin)
  // sees everything and may optionally filter by candidate via
  // ?canvassing_for=. This is enforced here regardless of any
  // canvassing_for value the client sends -- a scoped viewer can't widen
  // their own access by editing the query string.
  let canvassingFor: string | null = null;
  if (!NOTES_FULL_ACCESS_ROLES.includes(profile!.role)) {
    const scopeName = profile!.role === "running" ? profile!.name : profile!.campaign_name;
    if (!scopeName) return NextResponse.json({ notes: [], total: 0, limit: DEFAULT_LIMIT, offset: 0 });
    canvassingFor = scopeName;
  } else {
    canvassingFor = params.get("canvassing_for");
  }

  const turfIdParam = params.get("turf_id");
  const turfId = turfIdParam !== null && Number.isFinite(Number(turfIdParam)) ? Number(turfIdParam) : null;
  const canvasserId = params.get("canvasser_id");
  const outcomes = parseListParam(params.get("outcome"), OUTCOMES);
  const supportLevels = parseListParam(params.get("support_level"), SUPPORT_LEVELS);
  const from = params.get("from");
  const to = params.get("to");

  const sortParam = params.get("sort");
  const sort: SortColumn = (SORT_COLUMNS as readonly string[]).includes(sortParam ?? "")
    ? (sortParam as SortColumn)
    : "created_at";
  const ascending = params.get("dir") === "asc";

  const limit = Math.min(MAX_LIMIT, Math.max(1, Number(params.get("limit")) || DEFAULT_LIMIT));
  const offset = Math.max(0, Number(params.get("offset")) || 0);

  let query = supabase
    .from("canvass_notes")
    .select(
      "*, profiles(name), households(address_num, street, city, zip, town)",
      { count: "exact" }
    );

  if (canvassingFor) query = query.eq("canvassing_for", canvassingFor);
  if (turfId !== null) query = query.eq("turf_id", turfId);
  if (canvasserId) query = query.eq("canvasser_id", canvasserId);
  if (outcomes.length) query = query.in("outcome", outcomes);
  if (supportLevels.length) query = query.in("support_level", supportLevels);
  if (from) query = query.gte("created_at", from);
  if (to) query = query.lte("created_at", to);

  query = query.order(sort, { ascending }).range(offset, offset + limit - 1);

  const { data, error: dbError, count } = await query;
  if (dbError) return NextResponse.json({ error: dbError.message }, { status: 500 });

  return NextResponse.json({ notes: data, total: count ?? 0, limit, offset });
}
