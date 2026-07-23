"use client";

import { useState, useRef } from "react";

interface TargetPerson {
  id: string;
  name: string;
  age: number | null;
  party: string | null;
  tier_letter: string | null;
  elections: [number, string][] | null;
  address_num: string;
  street: string;
  city: string;
  zip: string;
  county: string;
  assembly_district: number | null;
  score_total: number;
  total_donated: number;
  donation_count: number;
}

interface TargetResult {
  explanation: string;
  count: number;
  sort_by: string;
  results: TargetPerson[];
}

const PARTY_LABEL: Record<string, string> = {
  DEM: "Dem", REP: "Rep", BLK: "Unaf", WOR: "WFP",
  CON: "Con", IND: "Ind", OTH: "Oth",
};

const TIER_COLOR: Record<string, string> = {
  X: "var(--seal-x)", F: "var(--seal-f)", L: "var(--seal-l)", I: "var(--seal-i)",
};

function pct(v: number | null) {
  if (v == null) return "—";
  return `${Math.round(v * 100)}%`;
}

function exportCSV(results: TargetPerson[], explanation: string) {
  const headers = [
    "Rank", "Name", "Address", "City", "Zip", "County",
    "Party", "Age", "Tier", "Elections Voted", "Total Donated",
  ];
  const rows = results.map((p, i) => [
    i + 1,
    p.name,
    `${p.address_num} ${p.street}`,
    p.city,
    p.zip,
    p.county,
    p.party ?? "",
    p.age ?? "",
    p.tier_letter ?? "",
    (p.elections ?? []).map(([y]) => y).join("; "),
    p.total_donated ?? 0,
  ]);

  const csv = [
    `# ${explanation}`,
    headers.join(","),
    ...rows.map((r) => r.map((v) => `"${v}"`).join(",")),
  ].join("\n");

  const blob = new Blob([csv], { type: "text/csv" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `target-list-${new Date().toISOString().slice(0, 10)}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}

export default function TargetPage() {
  const [prompt, setPrompt] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<TargetResult | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!prompt.trim()) return;
    setLoading(true);
    setError(null);
    setResult(null);

    try {
      const res = await fetch("/api/target", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ prompt }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error ?? "Something went wrong.");
      setResult(data);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div style={{ maxWidth: 1100, margin: "0 auto", padding: "0 20px 40px" }}>
      {/* Header */}
      <div style={{
        borderBottom: "2px solid var(--ink)",
        padding: "18px 0 14px",
        marginBottom: "24px",
        display: "flex",
        alignItems: "flex-end",
        justifyContent: "space-between",
        flexWrap: "wrap",
        gap: 12,
      }}>
        <div>
          <h1 style={{ fontFamily: "'Spectral', serif", fontWeight: 600, fontSize: "1.5rem", margin: 0 }}>
            AI Voter Targeting
          </h1>
          <p style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "0.73rem", color: "var(--ink-soft)", margin: "4px 0 0" }}>
            Describe who you want to reach — get a prioritized list
          </p>
        </div>
        {result && (
          <button
            onClick={() => exportCSV(result.results, result.explanation)}
            style={{
              fontFamily: "'IBM Plex Mono', monospace",
              fontSize: "0.73rem",
              background: "none",
              border: "1px solid var(--rule-strong)",
              borderRadius: 3,
              color: "var(--ink-soft)",
              cursor: "pointer",
              padding: "5px 12px",
            }}
            onMouseEnter={e => { e.currentTarget.style.color = "var(--ink)"; e.currentTarget.style.borderColor = "var(--ink)"; }}
            onMouseLeave={e => { e.currentTarget.style.color = "var(--ink-soft)"; e.currentTarget.style.borderColor = "var(--rule-strong)"; }}
          >
            Export CSV ({result.count})
          </button>
        )}
      </div>

      {/* Prompt form */}
      <form onSubmit={handleSubmit} style={{ marginBottom: 24 }}>
        <textarea
          ref={textareaRef}
          value={prompt}
          onChange={e => setPrompt(e.target.value)}
          placeholder="e.g. Registered Democrats in Brentwood between 30 and 55 who have donated and have high turnout probability"
          rows={3}
          style={{
            width: "100%",
            padding: "14px 16px",
            fontFamily: "'IBM Plex Mono', monospace",
            fontSize: "0.9rem",
            border: "1.5px solid var(--ink)",
            borderRadius: 3,
            background: "var(--paper-raised)",
            color: "var(--ink)",
            outline: "none",
            resize: "vertical",
            boxSizing: "border-box",
          }}
          onFocus={e => (e.target.style.borderColor = "var(--brass)")}
          onBlur={e => (e.target.style.borderColor = "var(--ink)")}
          onKeyDown={e => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) handleSubmit(e); }}
        />
        <div style={{ display: "flex", alignItems: "center", gap: 12, marginTop: 10 }}>
          <button
            type="submit"
            disabled={loading || !prompt.trim()}
            style={{
              fontFamily: "'IBM Plex Mono', monospace",
              fontSize: "0.8rem",
              fontWeight: 500,
              letterSpacing: "0.06em",
              textTransform: "uppercase",
              padding: "9px 20px",
              background: loading || !prompt.trim() ? "var(--ink-soft)" : "var(--ink)",
              color: "var(--paper)",
              border: "none",
              borderRadius: 3,
              cursor: loading || !prompt.trim() ? "not-allowed" : "pointer",
              transition: "background 0.15s",
            }}
            onMouseEnter={e => { if (!loading && prompt.trim()) e.currentTarget.style.background = "var(--seal-f)"; }}
            onMouseLeave={e => { if (!loading && prompt.trim()) e.currentTarget.style.background = "var(--ink)"; }}
          >
            {loading ? "Targeting…" : "Generate List"}
          </button>
          <span style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "0.7rem", color: "var(--ink-faint)" }}>
            ⌘ + Enter
          </span>
        </div>
      </form>

      {/* Error */}
      {error && (
        <div style={{
          fontFamily: "'IBM Plex Mono', monospace",
          fontSize: "0.8rem",
          color: "var(--seal-l)",
          background: "rgba(139,58,58,0.07)",
          border: "1px solid rgba(139,58,58,0.2)",
          borderRadius: 3,
          padding: "10px 14px",
          marginBottom: 20,
        }}>{error}</div>
      )}

      {/* Results */}
      {result && (
        <>
          {/* Explanation chip */}
          <div style={{
            fontFamily: "'IBM Plex Mono', monospace",
            fontSize: "0.75rem",
            color: "var(--ink-soft)",
            background: "var(--brass-soft)",
            border: "1px solid var(--rule-strong)",
            borderRadius: 3,
            padding: "8px 14px",
            marginBottom: 16,
            display: "flex",
            alignItems: "center",
            gap: 10,
          }}>
            <span style={{ color: "var(--brass)", fontWeight: 600 }}>★</span>
            <span>{result.explanation}</span>
            <span style={{ marginLeft: "auto", color: "var(--ink-faint)", whiteSpace: "nowrap" }}>
              {result.count} results · sorted by {result.sort_by.replace("_", " ")}
            </span>
          </div>

          {result.results.length === 0 ? (
            <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "0.85rem", color: "var(--ink-soft)", padding: "40px 0", textAlign: "center" }}>
              No voters matched these criteria.
            </div>
          ) : (
            <div style={{ overflowX: "auto" }}>
              <table style={{ width: "100%", borderCollapse: "collapse", fontFamily: "'IBM Plex Mono', monospace", fontSize: "0.75rem" }}>
                <thead>
                  <tr style={{ borderBottom: "2px solid var(--ink)" }}>
                    {["#", "Name", "Address", "City", "Party", "Age", "Tier", "Elections", "Donated"].map(h => (
                      <th key={h} style={{
                        padding: "8px 10px",
                        textAlign: h === "#" || h === "Age" || h === "Turnout" || h === "Dem Lean" || h === "Donated" ? "right" : "left",
                        color: "var(--ink-soft)",
                        fontWeight: 500,
                        letterSpacing: "0.05em",
                        textTransform: "uppercase",
                        whiteSpace: "nowrap",
                      }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {result.results.map((p, i) => (
                    <tr key={p.id} style={{ borderBottom: "1px solid var(--rule)", background: i % 2 === 0 ? "transparent" : "rgba(30,42,58,0.02)" }}>
                      <td style={{ padding: "7px 10px", textAlign: "right", color: "var(--ink-faint)" }}>{i + 1}</td>
                      <td style={{ padding: "7px 10px", color: "var(--ink)", fontWeight: 500, whiteSpace: "nowrap" }}>{p.name}</td>
                      <td style={{ padding: "7px 10px", color: "var(--ink-soft)", whiteSpace: "nowrap" }}>{p.address_num} {p.street}</td>
                      <td style={{ padding: "7px 10px", color: "var(--ink-soft)", whiteSpace: "nowrap" }}>{p.city}</td>
                      <td style={{ padding: "7px 10px" }}>
                        <span style={{
                          background: p.party === "DEM" ? "rgba(52,89,140,0.12)" : "transparent",
                          color: p.party === "DEM" ? "var(--seal-f)" : "var(--ink-soft)",
                          padding: "2px 6px",
                          borderRadius: 2,
                          fontWeight: p.party === "DEM" ? 600 : 400,
                        }}>{PARTY_LABEL[p.party ?? ""] ?? p.party ?? "—"}</span>
                      </td>
                      <td style={{ padding: "7px 10px", textAlign: "right", color: "var(--ink-soft)" }}>{p.age ?? "—"}</td>
                      <td style={{ padding: "7px 10px", textAlign: "center" }}>
                        {p.tier_letter ? (
                          <span style={{
                            fontWeight: 700,
                            color: TIER_COLOR[p.tier_letter] ?? "var(--ink-soft)",
                          }}>{p.tier_letter}</span>
                        ) : "—"}
                      </td>
                      <td style={{ padding: "7px 10px", color: "var(--ink-faint)", fontSize: "0.7rem" }}>
                        {(p.elections ?? []).map(([y]) => y).join(", ") || "—"}
                      </td>
                      <td style={{ padding: "7px 10px", textAlign: "right", color: p.total_donated > 0 ? "var(--brass)" : "var(--ink-faint)" }}>
                        {p.total_donated > 0 ? `$${p.total_donated.toLocaleString()}` : "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </div>
  );
}
