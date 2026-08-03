import Link from "next/link";

export default function LandingPage() {
  return (
    <div className="lp">
      <header className="lp-mast">
        <p className="lp-mast-line">Long Island Campaign Platform</p>
        <h1 className="lp-mast-name">
          <span className="lp-sheep" aria-hidden="true">🐑</span> Bellwether
        </h1>
        <nav className="lp-mast-actions" aria-label="Primary">
          <Link href="/login" className="lp-btn">Sign in</Link>
          <Link href="/signup" className="lp-link">Create account</Link>
        </nav>
        <hr className="lp-mast-rule" aria-hidden="true" />
      </header>

      <main>
        <article className="lp-article">
          <p className="lp-lede">
            A bellwether is the sheep that wears the bell — the one the flock follows,{" "}
            <em>without quite knowing why</em>. This is the field office&rsquo;s copy of that
            idea: one login that knows who&rsquo;s registered, who&rsquo;s given, who&rsquo;s
            already been knocked on, and who&rsquo;s worth the walk tonight.
          </p>

          <div className="lp-seal" aria-hidden="true">
            <span>🐑</span>
          </div>

          <p>
            <strong className="lp-head">Search.</strong> Type a name or an address and pull up
            the household — registration, party, past turnout, everyone else on the deed. It&rsquo;s
            the Nassau &amp; Suffolk voter file, indexed for typing fast at a door.
          </p>

          <p>
            <strong className="lp-head">The map.</strong> A live canvass heatmap of both
            counties, scored and layered by district, so a volunteer can see which blocks are
            worth the gas money before they leave the office.
          </p>

          <p>
            <strong className="lp-head">Giving.</strong> Search a name and see what they&rsquo;ve
            given, when, and to whom — pulled from FEC and state board of elections filings,
            matched to the household you&rsquo;re about to call.
          </p>

          <p>
            <strong className="lp-head">The record.</strong> The 2024 general, mapped ward by
            ward, so &ldquo;how did we do here&rdquo; has an answer instead of a guess.
          </p>

          <p>
            <strong className="lp-head">Targeting.</strong> Ask a question in plain English —
            something like <em>&ldquo;Suffolk Democrats under 40 who haven&rsquo;t voted since
            2022&rdquo;</em> — and the model builds the list. Admin only, for now.
          </p>

          <p className="lp-closing">
            None of this is public data made friendly to look at. It&rsquo;s the county file, the
            FEC and state filings, and a season of results, kept behind a login because the
            people knocking on doors deserve a tool that works, not one more account to remember.{" "}
            <Link href="/login" className="lp-cta">
              Sign in <span aria-hidden="true">→</span>
            </Link>
          </p>
        </article>
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
