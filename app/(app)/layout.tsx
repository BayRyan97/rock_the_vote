import { redirect } from "next/navigation";
import { createClient } from "@/lib/supabase/server";
import AppNav from "@/components/AppNav";

export default async function AppLayout({ children }: { children: React.ReactNode }) {
  const supabase = await createClient();
  const { data: { user } } = await supabase.auth.getUser();

  if (!user) redirect("/login");

  const { data: profile } = await supabase
    .from("profiles")
    .select("role, name")
    .eq("id", user.id)
    .single<{ role: "admin" | "canvasser"; name: string | null }>();

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
