import { createClient, getSessionUser } from "@/lib/supabase/server";
import { CANDIDATE_ROLES, NOTES_FULL_ACCESS_ROLES } from "@/lib/canvassNotes";
import NotesBrowser from "@/components/NotesBrowser";

export default async function NotesPage() {
  const supabase = await createClient();
  const { profile } = await getSessionUser();

  const { data: canvassers } = await supabase
    .from("profiles")
    .select("id, name")
    .order("name", { ascending: true }) as { data: { id: string; name: string | null }[] | null };

  const { data: candidates } = await supabase
    .from("profiles")
    .select("id, name")
    .in("role", CANDIDATE_ROLES)
    .order("name", { ascending: true }) as { data: { id: string; name: string | null }[] | null };

  const hasFullAccess = !!profile && NOTES_FULL_ACCESS_ROLES.includes(profile.role);
  const scopeName = profile?.role === "running" ? profile.name
    : profile?.role === "campaign_manager" ? profile.campaign_name
    : null;

  return (
    <div style={{ maxWidth: 1200, margin: "0 auto", padding: "32px 20px" }}>
      <h1 style={{ fontFamily: "'Spectral', serif", fontSize: 22, fontWeight: 600, marginBottom: 4 }}>
        Canvass Notes
      </h1>
      <p style={{ color: "var(--ink-soft)", fontSize: 13, marginBottom: 24 }}>
        {hasFullAccess
          ? "Notes logged by canvassers, across every household and turf."
          : scopeName
          ? `Notes logged by canvassers canvassing for ${scopeName}.`
          : "You're not yet assigned to a campaign — ask an admin to set it in User Management."}
      </p>
      <NotesBrowser
        canvassers={(canvassers ?? []).filter((c) => c.name)}
        candidates={(candidates ?? []).filter((c) => c.name)}
        hasFullAccess={hasFullAccess}
      />
    </div>
  );
}
