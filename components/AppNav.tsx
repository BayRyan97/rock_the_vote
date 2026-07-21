"use client";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { createClient } from "@/lib/supabase/client";

function SignOutButton() {
  const router = useRouter();
  async function handleLogout() {
    const supabase = createClient();
    await supabase.auth.signOut();
    router.push("/login");
    router.refresh();
  }
  return <button onClick={handleLogout} className="signout-btn">Sign out</button>;
}

const TABS = [
  { href: "/search", label: "Search" },
  { href: "/map", label: "Canvass Map" },
  { href: "/donations", label: "Donations" },
  { href: "/target", label: "AI Target" },
];

export default function AppNav({
  userLabel,
  isAdmin,
}: {
  userLabel: string;
  isAdmin: boolean;
}) {
  const path = usePathname();

  return (
    <div className="app-chrome">
      <header className="app-header">
        <div className="title-block">
          <h1>Nassau &amp; Suffolk County Voter File</h1>
        </div>
        <div className="header-right">
          <span className="user-label">{userLabel}</span>
          <SignOutButton />
        </div>
      </header>
      <nav className="view-tabs">
        {TABS.map((t) => (
          <Link
            key={t.href}
            href={t.href}
            className={`view-tab${path.startsWith(t.href) ? " active" : ""}`}
          >
            {t.label}
          </Link>
        ))}
        {isAdmin && (
          <Link
            href="/admin"
            className={`view-tab${path.startsWith("/admin") ? " active" : ""}`}
          >
            Admin
          </Link>
        )}
      </nav>
    </div>
  );
}
