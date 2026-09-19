"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef } from "react";
import { createClient } from "@/lib/supabase/client";

function useSheepFollow() {
  const headerRef = useRef<HTMLElement>(null);
  const sheepRef = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    const header = headerRef.current;
    const sheep = sheepRef.current;
    if (!header || !sheep) return;

    if (!window.matchMedia("(hover: hover) and (pointer: fine)").matches) return;

    const MAX_X = 22;
    const MAX_Y = 8;

    function handleMove(e: MouseEvent) {
      const rect = header!.getBoundingClientRect();
      const relX = (e.clientX - rect.left) / rect.width;
      const relY = (e.clientY - rect.top) / rect.height;
      const tx = (relX - 0.5) * 2 * MAX_X;
      const ty = (relY - 0.5) * 2 * MAX_Y;
      const rotate = (tx / MAX_X) * 10;
      sheep!.style.transform = `translate(${tx}px, ${ty}px) rotate(${rotate}deg) scaleX(-1)`;
    }

    function handleLeave() {
      sheep!.style.transform = "translate(0, 0) rotate(0deg) scaleX(-1)";
    }

    header.addEventListener("mousemove", handleMove);
    header.addEventListener("mouseleave", handleLeave);
    return () => {
      header.removeEventListener("mousemove", handleMove);
      header.removeEventListener("mouseleave", handleLeave);
    };
  }, []);

  return { headerRef, sheepRef };
}

function SignOutButton() {
  async function handleLogout() {
    const supabase = createClient();
    await supabase.auth.signOut();
    // Hard navigation, not router.push — a client-side transition can still
    // serve the App Router's cached "logged in" result for "/" (redirect to
    // /search) even after the cookie clears. A full page load re-runs
    // app/page.tsx's session check against the fresh, cookie-cleared request.
    window.location.href = "/";
  }
  return <button onClick={handleLogout} className="signout-btn">Sign out</button>;
}

const TABS = [
  { href: "/search", label: "Search" },
  { href: "/turfs", label: "Turf Search" },
  { href: "/map", label: "Canvass Map" },
  { href: "/election-map", label: "Election Map" },
  { href: "/green-map", label: "Green Map" },
  { href: "/donations", label: "Donations" },
];

export default function AppNav({
  userLabel,
  isAdmin,
}: {
  userLabel: string;
  isAdmin: boolean;
}) {
  const path = usePathname();
  const { headerRef, sheepRef } = useSheepFollow();

  return (
    <div className="app-chrome">
      <header className="app-header" ref={headerRef}>
        <div className="title-block">
          <h1><span ref={sheepRef} className="sheep-emoji">🐑</span> Bellwether</h1>
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
          <>
            <Link
              href="/target"
              className={`view-tab${path.startsWith("/target") ? " active" : ""}`}
            >
              AI Target
            </Link>
            <Link
              href="/admin"
              className={`view-tab${path.startsWith("/admin") ? " active" : ""}`}
            >
              Admin
            </Link>
          </>
        )}
      </nav>
    </div>
  );
}
