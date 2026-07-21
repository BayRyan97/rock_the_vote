"use client";
import { useState, useCallback, useRef } from "react";
import HouseholdCard, { HouseholdData } from "@/components/HouseholdCard";

export default function SearchPage() {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<HouseholdData[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [searched, setSearched] = useState(false);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

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
          <div className="big">Type an address or a name to pull a record.</div>
          <div className="stats">
            757,909 households · 1,854,934 registered voters on file
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
