import { redirect } from "next/navigation";
import { getSessionUser } from "@/lib/supabase/server";
import AppNav from "@/components/AppNav";

export default async function AppLayout({ children }: { children: React.ReactNode }) {
  const { user, profile } = await getSessionUser();

  if (!user) redirect("/login");

  const isAdmin = profile?.role === "admin";
  const userLabel = profile?.name || user.email || "";

  return (
    <>
      <div className="app-chrome-wrap">
        <AppNav userLabel={userLabel} isAdmin={isAdmin} />
      </div>
      <main>{children}</main>
    </>
  );
}
