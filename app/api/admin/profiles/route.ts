import { NextRequest, NextResponse } from "next/server";
import { createClient } from "@/lib/supabase/server";
import { UserRole } from "@/lib/supabase/types";

const VALID_ROLES: UserRole[] = ["admin", "canvasser", "dfli", "running"];

async function getAdminUser() {
  const supabase = await createClient();
  const { data: { user } } = await supabase.auth.getUser();
  if (!user) return { supabase, user: null, profile: null, error: "Unauthorized" };

  const { data: profile } = await supabase
    .from("profiles")
    .select("role")
    .eq("id", user.id)
    .single<{ role: string }>();

  if (profile?.role !== "admin") return { supabase, user, profile, error: "Forbidden" };
  return { supabase, user, profile, error: null };
}

export async function GET() {
  const { supabase, error } = await getAdminUser();
  if (error) return NextResponse.json({ error }, { status: error === "Unauthorized" ? 401 : 403 });

  const { data, error: dbError } = await supabase
    .from("profiles")
    .select("id, name, email, role, created_at")
    .order("created_at", { ascending: true });

  if (dbError) return NextResponse.json({ error: dbError.message }, { status: 500 });
  return NextResponse.json(data);
}

export async function PATCH(req: NextRequest) {
  const { supabase, user, error } = await getAdminUser();
  if (error) return NextResponse.json({ error }, { status: error === "Unauthorized" ? 401 : 403 });

  const body = await req.json();
  const { id, role } = body as { id: string; role: UserRole };

  if (!id || !VALID_ROLES.includes(role)) {
    return NextResponse.json({ error: "Invalid id or role." }, { status: 400 });
  }

  if (id === user!.id) {
    return NextResponse.json({ error: "Cannot change your own role." }, { status: 400 });
  }

  const { error: dbError } = await supabase
    .from("profiles")
    .update({ role })
    .eq("id", id);

  if (dbError) return NextResponse.json({ error: dbError.message }, { status: 500 });
  return NextResponse.json({ ok: true });
}
