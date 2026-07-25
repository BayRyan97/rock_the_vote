import { createClient } from "@/lib/supabase/server";
import ProfilesTable, { ProfileRow } from "@/components/admin/ProfilesTable";

export default async function AdminPage() {
  const supabase = await createClient();
  const { data: { user } } = await supabase.auth.getUser();

  const { data: profiles } = await supabase
    .from("profiles")
    .select("id, name, email, role, created_at")
    .order("created_at", { ascending: true });

  return (
    <div style={{ maxWidth: 900, margin: "0 auto", padding: "32px 20px" }}>
      <h1 style={{ fontFamily: "'Spectral', serif", fontSize: 22, fontWeight: 600, marginBottom: 4 }}>
        User Management
      </h1>
      <p style={{ color: "var(--ink-soft)", fontSize: 13, marginBottom: 24 }}>
        {profiles?.length ?? 0} {profiles?.length === 1 ? "user" : "users"}
      </p>
      <ProfilesTable
        initialProfiles={(profiles ?? []) as ProfileRow[]}
        currentUserId={user!.id}
      />
    </div>
  );
}
