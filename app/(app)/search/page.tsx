"use client";
import { useState, useCallback, useRef, useEffect } from "react";
import HouseholdCard, { HouseholdData } from "@/components/HouseholdCard";
import type { StatsPayload } from "@/app/api/search/stats/route";

const PARTIES = [
  { key: "dem",   label: "Dem",          color: "#4A7CC7" },
  { key: "rep",   label: "Rep",          color: "#B94040" },
  { key: "blk",   label: "Unaffiliated", color: "#8A8377" },
  { key: "other", label: "Other",        color: "#9E90B0" },
] as const;

export default function SearchPage() {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<HouseholdData[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [searched, setSearched] = useState(false);
  const [stats, setStats] = useState<StatsPayload | null>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const abortRef = useRef<AbortController | null>(null);

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
    abortRef.current?.abort();
    abortRef.current = new AbortController();
    setLoading(true);
    try {
      const res = await fetch(`/api/households?q=${encodeURIComponent(q)}`, {
        signal: abortRef.current.signal,
      });
      const data: HouseholdData[] = await res.json();
      setResults(Array.isArray(data) ? data : []);
      setTotal(Array.isArray(data) ? data.length : 0);
      setSearched(true);
    } catch (e) {
      if ((e as Error).name === "AbortError") return;
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

  const meta = !query.trim()
    ? ""
    : total === 0
    ? `No matches for "${query}"`
    : `${total.toLocaleString()} match${total === 1 ? "" : "es"} for "${query}"${total === 60 ? " (showing first 60)" : ""}`;

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

      <div className="meta-line">
        {loading ? <span className="search-throbber" /> : meta}
      </div>

      {!query.trim() && (
        <div className="empty-state">
          <p className="stats-section-title">Nassau &amp; Suffolk Voter File</p>

          <div
            className="stats-headline"
            style={{ gridTemplateColumns: "repeat(3, minmax(0, 260px))", justifyContent: "center" }}
          >
            <div className="stat-hero">
              <div className="stat-num">{stats ? stats.households.toLocaleString() : "—"}</div>
              <div className="stat-lbl">Households</div>
            </div>
            <div className="stat-hero">
              <div className="stat-num">{stats ? stats.voters.toLocaleString() : "—"}</div>
              <div className="stat-lbl">Registered Voters</div>
            </div>
            <div className="stat-hero">
              <div className="stat-num">2</div>
              <div className="stat-lbl">Counties</div>
            </div>
          </div>

          {stats && (
            <div className="party-breakdown">
              <div className="party-stack">
                {PARTIES.map(({ key, label, color }) => {
                  const count = stats[key as keyof StatsPayload] as number;
                  const pct = (count / stats.voters) * 100;
                  return (
                    <div
                      key={key}
                      className="party-stack-seg"
                      style={{ width: `${pct}%`, background: color }}
                      title={`${label}: ${pct.toFixed(1)}%`}
                    />
                  );
                })}
              </div>
              <div className="party-stack-legend">
                {PARTIES.map(({ key, label, color }) => {
                  const count = stats[key as keyof StatsPayload] as number;
                  const pct = ((count / stats.voters) * 100).toFixed(1);
                  return (
                    <div key={key} className="party-stack-item">
                      <span className="party-stack-swatch" style={{ background: color }} />
                      <span className="party-stack-label">{label}</span>
                      <span className="party-stack-pct">{pct}%</span>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          <div className="big" style={{ marginTop: 24 }}>
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
