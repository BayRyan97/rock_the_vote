"use client";
import { useState, useCallback, useRef, useEffect } from "react";
import DonorCard, { DonorData } from "@/components/DonorCard";

interface FastStats {
  totals: {
    confirmed_count: number;
    possible_count: number;
    confirmed_total: number;
    confirmed_donors: number;
  };
  committees: { committee: string; cnt: number; total: number }[];
  zips: { zip: string; donors: number; total: number }[];
}
interface PartyRow { party: string; donors: number; total: number; avg: number }

const PARTY_LABEL: Record<string, string> = {
  DEM: "Democrat", REP: "Republican", BLK: "Unaffiliated",
  WOR: "Working Families", CON: "Conservative", IND: "Independence", OTH: "Other",
};
const PARTY_COLOR: Record<string, string> = {
  DEM: "var(--seal-f)", REP: "var(--seal-l)", BLK: "var(--ink-soft)",
  WOR: "#5b21b6", CON: "var(--seal-x)", IND: "#92400e", OTH: "var(--ink-faint)",
};

function fmt$(n: number) {
  if (n >= 1_000_000) return "$" + (n / 1_000_000).toFixed(1) + "M";
  if (n >= 1_000)     return "$" + Math.round(n / 1_000) + "K";
  return "$" + Math.round(n).toLocaleString();
}
function fmtFull$(n: number) {
  return "$" + n.toLocaleString("en-US", { minimumFractionDigits: 0, maximumFractionDigits: 0 });
}

export default function DonationsPage() {
  const [query, setQuery]             = useState("");
  const [byCommittee, setByCommittee] = useState(false);
  const [donors, setDonors]           = useState<DonorData[]>([]);
  const [meta, setMeta]               = useState("");
  const [loading, setLoading]         = useState(false);

  const [stats, setStats]             = useState<FastStats | null>(null);
  const [statsLoading, setStatsLoading] = useState(true);
  const [parties, setParties]         = useState<PartyRow[]>([]);
  const [partiesLoading, setPartiesLoading] = useState(true);

  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    fetch("/api/donations/stats")
      .then(r => r.json())
      .then(setStats)
      .finally(() => setStatsLoading(false));

    fetch("/api/donations/stats/parties")
      .then(r => r.json())
      .then(setParties)
      .finally(() => setPartiesLoading(false));
  }, []);

  const search = useCallback(async (q: string, committee: boolean) => {
    if (!q.trim()) { setDonors([]); setMeta(""); return; }
    setLoading(true);
    try {
      const url  = `/api/donations?q=${encodeURIComponent(q)}${committee ? "&committee=1" : ""}`;
      const data = await fetch(url).then(r => r.json());
      setDonors(data.donors ?? []);
      setMeta(data.meta ?? "");
    } catch {
      setMeta("Error loading results.");
    } finally {
      setLoading(false);
    }
  }, []);

  function handleChange(e: React.ChangeEvent<HTMLInputElement>) {
    const q = e.target.value;
    setQuery(q);
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => search(q, byCommittee), 300);
  }

  function handleMode(committee: boolean) {
    setByCommittee(committee);
    if (query.trim()) search(query, committee);
  }

  const showResults = query.trim().length > 0;
  const maxPartyTotal = parties[0]?.total ?? 1;

  return (
    <div style={{ maxWidth: 1100, margin: "0 auto", padding: "0 20px 80px" }}>

      {/* Search + mode toggle */}
      <div className="donations-search-row">
        <input
          className="search-input"
          type="text"
          placeholder={byCommittee ? "Search a committee or campaign name…" : "Search a name, city, or ZIP…"}
          value={query}
          onChange={handleChange}
          autoComplete="off"
          autoFocus
        />
        <div className="mode-toggle">
          <button className={`mode-btn${!byCommittee ? " active" : ""}`} onClick={() => handleMode(false)}>
            Donors
          </button>
          <button className={`mode-btn${byCommittee ? " active" : ""}`} onClick={() => handleMode(true)}>
            Committees
          </button>
        </div>
      </div>

      <div className="meta-line">{loading ? "Searching…" : showResults ? meta : " "}</div>

      {/* ── Stats dashboard (shown when nothing typed) ── */}
      {!showResults && (
        <div className="stats-dashboard">

          {/* Headline numbers */}
          <div className="stats-headline">
            {statsLoading ? (
              <>
                <div className="stat-hero stat-skeleton" />
                <div className="stat-hero stat-skeleton" />
                <div className="stat-hero stat-skeleton" />
                <div className="stat-hero stat-skeleton" />
              </>
            ) : stats ? (
              <>
                <div className="stat-hero">
                  <span className="stat-num">{fmt$(stats.totals.confirmed_total)}</span>
                  <span className="stat-lbl">confirmed donations</span>
                </div>
                <div className="stat-hero">
                  <span className="stat-num">{stats.totals.confirmed_donors.toLocaleString()}</span>
                  <span className="stat-lbl">unique donors</span>
                </div>
                <div className="stat-hero">
                  <span className="stat-num">{stats.totals.confirmed_count.toLocaleString()}</span>
                  <span className="stat-lbl">transactions on file</span>
                </div>
                <div className="stat-hero">
                  <span className="stat-num">{stats.totals.possible_count.toLocaleString()}</span>
                  <span className="stat-lbl">possible matches</span>
                </div>
              </>
            ) : null}
          </div>

          {/* Party bars */}
          <div className="stats-section">
            <div className="stats-section-title">Donations by party registration</div>
            {partiesLoading ? (
              <div className="party-loading">Computing party breakdown…</div>
            ) : parties.length > 0 ? (
              <div className="party-bars">
                {parties.map(p => (
                  <div key={p.party} className="party-bar-row">
                    <span className="party-bar-label" style={{ color: PARTY_COLOR[p.party] ?? "var(--ink)" }}>
                      {PARTY_LABEL[p.party] ?? p.party}
                    </span>
                    <div className="party-bar-track">
                      <div
                        className="party-bar-fill"
                        style={{
                          width: `${(p.total / maxPartyTotal) * 100}%`,
                          background: PARTY_COLOR[p.party] ?? "var(--ink-soft)",
                        }}
                      />
                    </div>
                    <span className="party-bar-total">{fmtFull$(p.total)}</span>
                    <span className="party-bar-avg">avg {fmtFull$(p.avg)}</span>
                  </div>
                ))}
              </div>
            ) : null}
          </div>

          {/* Committees + ZIPs */}
          <div className="stats-cols">
            <div className="stats-section">
              <div className="stats-section-title">Top political committees</div>
              {statsLoading ? (
                <div className="party-loading">Loading…</div>
              ) : stats?.committees.length ? (
                <table className="stats-table">
                  <thead>
                    <tr><th>Committee</th><th className="r">Total</th><th className="r">Donations</th></tr>
                  </thead>
                  <tbody>
                    {stats.committees.map(c => (
                      <tr key={c.committee} className="stats-row-link"
                        onClick={() => { setQuery(c.committee); setByCommittee(true); search(c.committee, true); }}>
                        <td className="committee-cell">{c.committee}</td>
                        <td className="r">{fmtFull$(c.total)}</td>
                        <td className="r">{c.cnt.toLocaleString()}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : null}
            </div>

            <div className="stats-section">
              <div className="stats-section-title">Top zip codes by total donated</div>
              {statsLoading ? (
                <div className="party-loading">Loading…</div>
              ) : stats?.zips.length ? (
                <table className="stats-table">
                  <thead>
                    <tr><th>ZIP</th><th className="r">Total</th><th className="r">Donors</th></tr>
                  </thead>
                  <tbody>
                    {stats.zips.map(z => (
                      <tr key={z.zip} className="stats-row-link"
                        onClick={() => { setQuery(z.zip); setByCommittee(false); search(z.zip, false); }}>
                        <td>{z.zip}</td>
                        <td className="r">{fmtFull$(z.total)}</td>
                        <td className="r">{z.donors.toLocaleString()}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : null}
            </div>
          </div>

        </div>
      )}

      {/* ── Search results ── */}
      {showResults && donors.map((d, i) => (
        <DonorCard key={`${d.donor_key}-${i}`} d={d} />
      ))}
      {showResults && !loading && donors.length === 0 && (
        <div className="empty-state">
          <div className="big">No {byCommittee ? "committee" : "donation"} records found.</div>
          <div className="stats">
            {byCommittee
              ? `Try a partial name — e.g. "FRIENDS OF" or a candidate's last name.`
              : "Try a partial name, a city, or a 5-digit ZIP."}
          </div>
        </div>
      )}
    </div>
  );
}
