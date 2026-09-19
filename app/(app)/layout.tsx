import { redirect } from "next/navigation";
import { getSessionUser } from "@/lib/supabase/server";
import AppNav from "@/components/AppNav";
import { NOTES_MANAGER_ROLES } from "@/lib/canvassNotes";

export default async function AppLayout({ children }: { children: React.ReactNode }) {
  const { user, profile } = await getSessionUser();

  if (!user) redirect("/login");

  const isAdmin = profile?.role === "admin";
  const canViewNotes = !!profile && NOTES_MANAGER_ROLES.includes(profile.role);
  const userLabel = profile?.name || user.email || "";

  return (
    <>
      <div className="app-chrome-wrap">
        <AppNav userLabel={userLabel} isAdmin={isAdmin} canViewNotes={canViewNotes} />
      </div>
      <main>{children}</main>
    </>
  );
}
