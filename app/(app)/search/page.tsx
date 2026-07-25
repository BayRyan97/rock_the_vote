"use client";
import { useState, useCallback, useRef, useEffect } from "react";
import HouseholdCard, { HouseholdData } from "@/components/HouseholdCard";
import type { StatsPayload } from "@/app/api/search/stats/route";

export default function SearchPage() {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<HouseholdData[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [searched, setSearched] = useState(false);
  const [stats, setStats] = useState<StatsPayload | null>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    fetch("/api/search/stats")
      .then((r) => r.json())
      .then((d: StatsPayload) => setStats(d))
      .catch(() => {});
  }, []);

  const search = useCallback(async (q: string) => {
    if (!q.trim()) {
      setResults([]);
      setTotal(0);
      setSearched(false);
      return;
    }
    setLoading(true);
    try {
      const res = await fetch(`/api/households?q=${encodeURIComponent(q)}`);
      const data: HouseholdData[] = await res.json();
      setResults(Array.isArray(data) ? data : []);
      setTotal(Array.isArray(data) ? data.length : 0);
      setSearched(true);
    } catch {
      setResults([]);
      setSearched(true);
    } finally {
      setLoading(false);
    }
  }, []);

  function handleChange(e: React.ChangeEvent<HTMLInputElement>) {
    const q = e.target.value;
    setQuery(q);
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => search(q), 300);
  }

  const meta = loading
    ? "Searching…"
    : !query.trim()
    ? ""
    : total === 0
    ? `No matches for "${query}"`
    : `${total.toLocaleString()} match${total === 1 ? "" : "es"} for "${query}"${total === 60 ? " (showing first 60)" : ""}`;

  const partyBars = stats
    ? [
        { label: "DEM", count: stats.dem, color: "var(--dem-color, #3b82f6)" },
        { label: "REP", count: stats.rep, color: "var(--rep-color, #ef4444)" },
        { label: "Unaffiliated", count: stats.blk, color: "var(--blk-color, #6b7280)" },
        { label: "Other", count: stats.other, color: "var(--other-color, #a78bfa)" },
      ]
    : [];

  return (
    <div style={{ maxWidth: 1100, margin: "0 auto", padding: "0 20px 80px" }}>
      <input
        className="search-input"
        type="text"
        placeholder="Search an address or a name…"
        value={query}
        onChange={handleChange}
        autoComplete="off"
        autoFocus
      />

      <div className="meta-line">{meta}</div>

      {!query.trim() && (
        <div className="empty-state">
          <p className="stats-section-title">Nassau &amp; Suffolk Voter File</p>

          <div className="stats-headline">
            <div className="stat-hero">
              <div className="stat-num">
                {stats ? stats.households.toLocaleString() : "—"}
              </div>
              <div className="stat-lbl">Households</div>
            </div>
            <div className="stat-hero">
              <div className="stat-num">
                {stats ? stats.voters.toLocaleString() : "—"}
              </div>
              <div className="stat-lbl">Registered Voters</div>
            </div>
            <div className="stat-hero">
              <div className="stat-num">2</div>
              <div className="stat-lbl">Counties</div>
            </div>
          </div>

          {stats && (
            <div className="party-bars">
              {partyBars.map(({ label, count, color }) => {
                const pct = ((count / stats.voters) * 100).toFixed(1);
                return (
                  <div key={label} className="party-bar-row">
                    <div className="party-bar-label">{label}</div>
                    <div className="party-bar-track">
                      <div
                        className="party-bar-fill"
                        style={{ width: `${pct}%`, background: color }}
                      />
                    </div>
                    <div className="party-bar-total">
                      {count.toLocaleString()} <span style={{ opacity: 0.6 }}>({pct}%)</span>
                    </div>
                  </div>
                );
              })}
            </div>
          )}

          <div className="big" style={{ marginTop: 28 }}>
            Type an address or a name to pull a record.
          </div>
        </div>
      )}

      {searched && !loading && total === 0 && query.trim() && (
        <div className="empty-state">
          <div className="big">No matches.</div>
          <div className="stats">
            Try a partial street name, a house number, or a last name.<br />
            Note: the voter file covers Nassau and Suffolk counties only.
          </div>
        </div>
      )}

      {results.map((h) => (
        <HouseholdCard key={h.id} h={h} />
      ))}
    </div>
  );
}
