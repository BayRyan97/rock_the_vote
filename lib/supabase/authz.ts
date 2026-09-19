import { createClient } from "@/lib/supabase/server";
import type { UserRole } from "@/lib/supabase/types";

// Generalizes the getAdminUser()-style check in app/api/admin/profiles/route.ts
// for routes that need to allow more than one role.
export async function requireRole(allowed: UserRole[]) {
  const supabase = await createClient();
  const { data: { user } } = await supabase.auth.getUser();
  if (!user) return { supabase, user: null, profile: null, error: "Unauthorized" as const };

  const { data: profile } = await supabase
    .from("profiles")
    .select("role, name, campaign_name")
    .eq("id", user.id)
    .single<{ role: UserRole; name: string | null; campaign_name: string | null }>();

  if (!profile || !allowed.includes(profile.role)) {
    return { supabase, user, profile, error: "Forbidden" as const };
  }
  return { supabase, user, profile, error: null };
}
