import { redirect } from "next/navigation";
import { getSessionUser } from "@/lib/supabase/server";
import LandingPage from "@/components/LandingPage";

export default async function Home() {
  const { user } = await getSessionUser();
  if (user) redirect("/search");

  return <LandingPage />;
}
