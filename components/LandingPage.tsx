import Link from "next/link";

// First Tuesday after the first Monday in November 2026.
const ELECTION_DAY = new Date("2026-11-03T00:00:00-05:00");

function daysUntilElection(): number {
  const ms = ELECTION_DAY.getTime() - Date.now();
  return Math.max(0, Math.ceil(ms / (1000 * 60 * 60 * 24)));
}

const FEATURES: Array<{ k: string; d: string; href: string }> = [
  { k: "Search", d: "Every household on the file, typed fast at the door.", href: "/search" },
  { k: "The map", d: "Live canvass heatmap, scored block by block.", href: "/map" },
  { k: "Giving", d: "FEC & state filings, matched to the household.", href: "/donations" },
  { k: "The record", d: "2024 general, mapped ward by ward.", href: "/election-map" },
  { k: "Targeting", d: "Ask in plain English. The model builds the list.", href: "/target" },
];

export default function LandingPage() {
  const days = daysUntilElection();

  return (
    <div className="lp">
      <header className="lp-mast">
        <div className="lp-mast-top">
          <p className="lp-mast-name">
            <span className="lp-sheep" aria-hidden="true">🐑</span> Bellwether
          </p>
          <nav className="lp-mast-actions" aria-label="Primary">
            <Link href="/login" className="lp-btn">Sign in</Link>
            <Link href="/signup" className="lp-link">Create account</Link>
          </nav>
        </div>
      </header>

      <main>
        <section className="lp-hero">
          <p className="lp-eyebrow">Long Island campaign platform</p>
          <h1 className="lp-h1">Know before you knock.</h1>
          <p className="lp-sub">
            One login for the Nassau &amp; Suffolk voter file — who&rsquo;s registered, who&rsquo;s
            given, who&rsquo;s already been walked, and who&rsquo;s worth tonight&rsquo;s gas money.
          </p>

          <div className="lp-hero-actions">
            <Link href="/login" className="lp-btn lp-btn-lg">Sign in</Link>
            <Link href="/signup" className="lp-link lp-link-lg">Create an account →</Link>
          </div>

          <div className="lp-countdown" role="note">
            <span className="lp-countdown-num">{days}</span>
            <span className="lp-countdown-copy">
              days to Election Day
              <span className="lp-countdown-date">Tuesday, November 3, 2026</span>
            </span>
          </div>
        </section>

        <section className="lp-features" aria-label="What's inside">
          {FEATURES.map((f) => (
            <Link key={f.k} href={f.href} className="lp-feature">
              <span className="lp-feature-k">{f.k}</span>
              <span className="lp-feature-d">{f.d}</span>
            </Link>
          ))}
        </section>

        <section className="lp-fomo">
          <p className="lp-fomo-line">
            The list moves every night — new registrations, new turnout, new doors already
            knocked. Whoever&rsquo;s logged in sees it first.
          </p>
          <Link href="/login" className="lp-cta">
            Sign in <span aria-hidden="true">→</span>
          </Link>
        </section>
      </main>

      <footer className="lp-foot">
        <p className="lp-foot-close">
          Know before you knock.
          <br />
          <span className="lp-foot-sign">— Bellwether, Long Island</span>
        </p>
        <p className="lp-foot-links">
          <Link href="/login">Sign in</Link>
          <span aria-hidden="true">·</span>
          <Link href="/signup">Create account</Link>
        </p>
        <p className="lp-foot-copy">© {new Date().getFullYear()} Bellwether</p>
      </footer>
    </div>
  );
}
