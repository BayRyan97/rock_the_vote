import { redirect } from "next/navigation";
import { getSessionUser } from "@/lib/supabase/server";
import { NOTES_MANAGER_ROLES } from "@/lib/canvassNotes";

export default async function NotesLayout({ children }: { children: React.ReactNode }) {
  const { user, profile } = await getSessionUser();
  if (!user) redirect("/login");
  if (!profile || !NOTES_MANAGER_ROLES.includes(profile.role)) redirect("/search");

  return <>{children}</>;
}
