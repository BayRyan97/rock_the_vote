import { redirect } from "next/navigation";
import { getSessionUser } from "@/lib/supabase/server";

export default async function TargetLayout({ children }: { children: React.ReactNode }) {
  const { user, profile } = await getSessionUser();
  if (!user) redirect("/login");
  if (profile?.role !== "admin") redirect("/search");

  return <>{children}</>;
}
