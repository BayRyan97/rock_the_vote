"use client";
import { useState, useCallback, useRef, useEffect } from "react";
import HouseholdCard, { HouseholdData } from "@/components/HouseholdCard";
import type { StatsPayload } from "@/app/api/search/stats/route";

const PARTY_COLORS = {
  DEM: "#3b82f6",
  REP: "#ef4444",
  Unaffiliated: "#6b7280",
  Other: "#a78bfa",
};

function DonutChart({ data, total }: {
  data: { label: string; count: number; color: string }[];
  total: number;
}) {
  const SIZE = 150;
  const CX = SIZE / 2, CY = SIZE / 2;
  const R = SIZE / 2 - 6;
  const INNER = R * 0.58;
  const cos = Math.cos, sin = Math.sin;

  let angle = -Math.PI / 2;
  const paths = data.map(({ count, color }) => {
    const sweep = (count / total) * 2 * Math.PI;
    const end = angle + sweep;
    const large = sweep > Math.PI ? 1 : 0;
    const d = [
      `M ${CX + R * cos(angle)} ${CY + R * sin(angle)}`,
      `A ${R} ${R} 0 ${large} 1 ${CX + R * cos(end)} ${CY + R * sin(end)}`,
      `L ${CX + INNER * cos(end)} ${CY + INNER * sin(end)}`,
      `A ${INNER} ${INNER} 0 ${large} 0 ${CX + INNER * cos(angle)} ${CY + INNER * sin(angle)}`,
      "Z",
    ].join(" ");
    angle = end;
    return { d, color };
  });

  return (
    <svg width={SIZE} height={SIZE} viewBox={`0 0 ${SIZE} ${SIZE}`}>
      {paths.map((p, i) => (
        <path key={i} d={p.d} fill={p.color} opacity={0.82} />
      ))}
    </svg>
  );
}

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
    // Cancel any previous in-flight request so stale results can't overwrite newer ones
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

  const partySlices = stats
    ? [
        { label: "DEM",          count: stats.dem,   color: PARTY_COLORS.DEM },
        { label: "REP",          count: stats.rep,   color: PARTY_COLORS.REP },
        { label: "Unaffiliated", count: stats.blk,   color: PARTY_COLORS.Unaffiliated },
        { label: "Other",        count: stats.other, color: PARTY_COLORS.Other },
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
            <div className="party-pie-row">
              <DonutChart data={partySlices} total={stats.voters} />
              <div className="party-pie-legend">
                {partySlices.map(({ label, count, color }) => {
                  const pct = ((count / stats.voters) * 100).toFixed(1);
                  return (
                    <div key={label} className="pie-legend-row">
                      <span className="pie-swatch" style={{ background: color }} />
                      <span className="pie-legend-label">{label}</span>
                      <span className="pie-legend-nums">
                        {count.toLocaleString()}<span className="pie-pct">{pct}%</span>
                      </span>
                    </div>
                  );
                })}
              </div>
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
