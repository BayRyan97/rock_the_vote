"use client";
import { useMemo, useState } from "react";

export interface PartyYearRow {
  year: number;
  party: string;
  donors: number;
  total: number;
}

// Fixed render order (also the stack order, bottom -> top). Not sorted by
// value per run -- a party's position/color must stay put as years change.
const PARTY_ORDER = ["DEM", "REP", "BLK", "WOR", "CON", "IND", "OTH"] as const;

export const PARTY_LABEL: Record<string, string> = {
  DEM: "Democrat", REP: "Republican", BLK: "Unaffiliated",
  WOR: "Working Families", CON: "Conservative", IND: "Independence", OTH: "Other",
};
export const PARTY_COLOR: Record<string, string> = {
  DEM: "var(--seal-f)", REP: "var(--seal-l)", BLK: "var(--ink-soft)",
  WOR: "#5b21b6", CON: "var(--seal-x)", IND: "#92400e", OTH: "var(--ink-faint)",
};

function fmtFull$(n: number) {
  return "$" + Math.round(n).toLocaleString("en-US");
}
function fmtAxis$(n: number) {
  if (n >= 1_000_000) return "$" + (n / 1_000_000).toFixed(1) + "M";
  if (n >= 1_000) return "$" + Math.round(n / 1_000) + "K";
  return "$" + Math.round(n);
}

const GRID_FRACTIONS = [0, 0.25, 0.5, 0.75, 1];

export default function PartyYearChart({ data }: { data: PartyYearRow[] }) {
  const [hoverYear, setHoverYear] = useState<number | null>(null);

  const { years, maxTotal, byYear } = useMemo(() => {
    const byYear = new Map<number, Map<string, number>>();
    for (const row of data) {
      if (!byYear.has(row.year)) byYear.set(row.year, new Map());
      byYear.get(row.year)!.set(row.party, row.total);
    }
    const years = [...byYear.keys()].sort((a, b) => a - b);
    let maxTotal = 0;
    for (const partyMap of byYear.values()) {
      const yearTotal = [...partyMap.values()].reduce((s, v) => s + v, 0);
      if (yearTotal > maxTotal) maxTotal = yearTotal;
    }
    return { years, maxTotal, byYear };
  }, [data]);

  if (years.length === 0 || maxTotal === 0) return null;

  return (
    <div className="party-year-chart-wrap">
      <div className="party-year-chart-row">
        <div className="party-year-axis-labels">
          {GRID_FRACTIONS.map(f => (
            <span key={f} style={{ bottom: `${f * 100}%` }}>{fmtAxis$(maxTotal * f)}</span>
          ))}
        </div>
        <div className="party-year-plot">
          <div className="party-year-gridlines">
            {GRID_FRACTIONS.map(f => (
              <div key={f} className="party-year-gridline" style={{ bottom: `${f * 100}%` }} />
            ))}
          </div>
          <div className="party-year-bars">
            {years.map((year) => {
              const partyMap = byYear.get(year)!;
              const yearTotal = [...partyMap.values()].reduce((s, v) => s + v, 0);
              const isHover = hoverYear === year;
              return (
                <div
                  key={year}
                  className={`party-year-col${isHover ? " hover" : ""}`}
                  onMouseEnter={() => setHoverYear(year)}
                  onMouseLeave={() => setHoverYear(null)}
                >
                  <div className="party-year-bar" style={{ height: `${(yearTotal / maxTotal) * 100}%` }}>
                    {PARTY_ORDER.map((p, i) => {
                      const v = partyMap.get(p) ?? 0;
                      if (v <= 0) return null;
                      return (
                        <div
                          key={p}
                          className="party-year-seg"
                          style={{
                            height: `${(v / yearTotal) * 100}%`,
                            background: PARTY_COLOR[p],
                            borderTop: i > 0 ? "1px solid var(--paper-raised)" : "none",
                          }}
                        />
                      );
                    })}
                  </div>
                </div>
              );
            })}
          </div>
          {/* Anchored near the top of the fixed-height plot (not the hovered
              bar's own top) so a near-100%-tall bar's tooltip can't overflow
              above the card -- bar height is data-driven and unbounded. */}
          {hoverYear !== null && (() => {
            const partyMap = byYear.get(hoverYear)!;
            const yearTotal = [...partyMap.values()].reduce((s, v) => s + v, 0);
            const idx = years.indexOf(hoverYear);
            const leftPct = Math.min(90, Math.max(10, ((idx + 0.5) / years.length) * 100));
            return (
              <div className="party-year-tooltip" style={{ left: `${leftPct}%` }}>
                <div className="party-year-tooltip-total">{hoverYear} &middot; {fmtFull$(yearTotal)}</div>
                {PARTY_ORDER.filter(p => (partyMap.get(p) ?? 0) > 0).map(p => (
                  <div key={p} className="party-year-tooltip-row">
                    <span className="swatch" style={{ background: PARTY_COLOR[p] }} />
                    <span className="lbl">{PARTY_LABEL[p]}</span>
                    <span className="val">{fmtFull$(partyMap.get(p) ?? 0)}</span>
                  </div>
                ))}
              </div>
            );
          })()}
        </div>
      </div>
      <div className="party-year-axis-x">
        {years.map(year => <span key={year}>{String(year).slice(2)}</span>)}
      </div>
      <div className="party-year-legend">
        {PARTY_ORDER.map(p => (
          <div key={p} className="party-year-legend-item">
            <span className="swatch" style={{ background: PARTY_COLOR[p] }} />
            {PARTY_LABEL[p]}
          </div>
        ))}
      </div>
    </div>
  );
}
