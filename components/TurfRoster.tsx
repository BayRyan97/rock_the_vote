"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { Person, PersonRow, fmtDollars } from "@/components/PersonRoster";

interface Turf {
  turf_id: number;
  n_doors: number;
  n_targets: number;
  value_net_margin: number;
  hours_per_net_margin: number | null;
  n_facilities_nearby: number;
  arm: "treatment" | "control" | "buffer";
}

type SortKey = "value" | "donations";

const CSV_HEADERS = [
  "Address", "Name", "Age", "Party", "Tier", "Turnout", "Lean", "Value",
  "Ask for", "Donation total", "Donation count", "Email", "Phone", "Last voted",
];

function csvCell(v: unknown) {
  const s = String(v ?? "");
  return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

function downloadTurfCsv(turfId: number, people: Person[]) {
  const rows = people.map((p) => [
    `${p.address_num} ${p.street}, ${p.city} ${p.zip}`,
    p.name,
    p.age ?? "",
    p.party,
    `${p.tier_letter}${p.tier_count}`,
    p.turnout_prob != null ? `${Math.round(p.turnout_prob * 100)}%` : "",
    p.dem_lean_prob != null ? `${Math.round(p.dem_lean_prob * 100)}%` : "",
    p.m_net_i != null ? p.m_net_i.toFixed(2) : "",
    p.is_ask ? "Yes" : "No",
    p.donation_total.toFixed(2),
    p.donation_count,
    p.email ?? "",
    p.phone ?? "",
    p.elections.length ? Math.max(...p.elections.map((e) => e.year)) : "",
  ]);
  // Leading ﻿ so Excel detects UTF-8 instead of mangling names with accents.
  const csv = "﻿" + [CSV_HEADERS, ...rows].map((r) => r.map(csvCell).join(",")).join("\r\n");
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `turf-${turfId}-roster.csv`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

export default function TurfRoster({ turfId }: { turfId: string }) {
  const [turf, setTurf] = useState<Turf | null>(null);
  const [people, setPeople] = useState<Person[]>([]);
  const [loading, setLoading] = useState(true);
  const [notFound, setNotFound] = useState(false);
  const [sortBy, setSortBy] = useState<SortKey>("value");

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setNotFound(false);
    fetch(`/api/turfs/${turfId}`)
      .then((res) => {
        if (!res.ok) throw new Error(String(res.status));
        return res.json();
      })
      .then((data: { turf: Turf; people: Person[] }) => {
        if (cancelled) return;
        setTurf(data.turf);
        setPeople(data.people);
      })
      .catch(() => {
        if (!cancelled) setNotFound(true);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [turfId]);

  const sorted = [...people].sort((a, b) =>
    sortBy === "donations"
      ? b.donation_total - a.donation_total
      : (b.m_net_i ?? -1) - (a.m_net_i ?? -1)
  );

  return (
    <div className="app-chrome-wrap turf-page">
      <Link href="/map" className="turf-back-link">← Back to Canvass Map</Link>

      {loading ? (
        <div className="stats-loading"><span className="search-throbber" /> Loading turf…</div>
      ) : notFound || !turf ? (
        <div className="empty-state">
          <div className="big">Turf not found.</div>
          <div className="stats">
            That turf id doesn’t exist, or the model run that produced it has since been replaced.
          </div>
        </div>
      ) : (
        <>
          <div className="turf-header">
            <h1>
              Turf {turf.turf_id}
              {turf.arm !== "treatment" && (
                <span className={`geo-arm-badge geo-arm-${turf.arm}`}>{turf.arm.toUpperCase()}</span>
              )}
            </h1>
            <div className="stat-strip">
              <span>Net margin: <b>{turf.value_net_margin.toFixed(1)}</b></span>
              {turf.hours_per_net_margin != null && (
                <span>Hrs/vote: <b>{turf.hours_per_net_margin.toFixed(2)}</b></span>
              )}
              <span>Doors: <b>{turf.n_doors}</b></span>
              <span>Targets: <b>{turf.n_targets}</b></span>
              {turf.n_facilities_nearby > 0 && (
                <span>Buildings nearby: <b>{turf.n_facilities_nearby}</b></span>
              )}
            </div>
          </div>

          {people.length === 0 ? (
            <div className="empty-state">
              <div className="big">No targets modeled for this turf.</div>
            </div>
          ) : (
            <>
              <div className="turf-sort-row">
                <span className="turf-sort-label">Rank by:</span>
                <button
                  className={`geo-btn${sortBy === "value" ? " geo-btn-on" : ""}`}
                  onClick={() => setSortBy("value")}
                >
                  Model value
                </button>
                <button
                  className={`geo-btn${sortBy === "donations" ? " geo-btn-on" : ""}`}
                  onClick={() => setSortBy("donations")}
                >
                  Donations
                </button>
                <button
                  className="geo-btn turf-export-btn"
                  onClick={() => downloadTurfCsv(turf.turf_id, sorted)}
                >
                  Export CSV
                </button>
              </div>

              <div className="turf-roll-wrap">
                <table className="roll turf-roll">
                  <thead>
                    <tr>
                      <th>Address</th>
                      <th>Name</th>
                      <th>Age</th>
                      <th>Party</th>
                      <th>Tier</th>
                      <th>Turnout</th>
                      <th>Lean</th>
                      <th>Value</th>
                      <th>Donations</th>
                      <th>Email</th>
                      <th>Phone</th>
                      <th>Last voted</th>
                    </tr>
                  </thead>
                  <tbody>
                    {sorted.map((p) => (
                      <PersonRow key={p.person_id} p={p} colSpan={12} />
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </>
      )}
    </div>
  );
}
