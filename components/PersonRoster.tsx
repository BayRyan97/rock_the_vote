"use client";
import { useState } from "react";

export interface Donation {
  donor_key: string;
  source: string;
  donation_date: string | null;
  amount: number | null;
  committee: string | null;
  confirmed: boolean;
}
export interface Election { year: number; ballot: string }

export interface Person {
  person_id: string;
  household_id: string;
  name: string;
  age: number | null;
  party: string;
  tier_letter: string;
  tier_count: number;
  elections: Election[];
  turnout_prob: number | null;
  dem_lean_prob: number | null;
  m_net_i: number | null;
  address_num: string;
  street: string;
  city: string;
  zip: string;
  donation_count: number;
  donation_total: number;
  donations: Donation[];
  email: string | null;
  phone: string | null;
  is_ask: boolean;
  // Present when a row was fetched across multiple turfs (Turf Search), so
  // the grid can show/sort by which turf each person belongs to.
  turf_id?: number;
}

export function fmtDollars(n: number) {
  return "$" + n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
export const pct = (v: number | null) => (v == null ? "—" : `${Math.round(v * 100)}%`);
// m_net_i is normalised to mean 1 over the target pool -- a multiple of an
// average target ("×2.4"), not a probability. Matches LeafletMap.tsx's popup.
export const mult = (v: number | null) => (v == null ? "—" : `×${v.toFixed(1)}`);

export function csvCell(v: unknown) {
  const s = String(v ?? "");
  return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

// One row per person, with an expandable donation-history sub-row. Shared
// between the single-turf detail page and the multi-turf Turf Search grid --
// `colSpan` must match however many <th>s the caller's <thead> renders.
export function PersonRow({
  p,
  showTurf,
  colSpan,
}: {
  p: Person;
  showTurf?: boolean;
  colSpan: number;
}) {
  const [donationsOpen, setDonationsOpen] = useState(false);
  const lastVoted = p.elections.length ? Math.max(...p.elections.map((e) => e.year)) : null;

  return (
    <>
      <tr className={p.is_ask ? "roll-ask" : ""}>
        {showTurf && <td>{p.turf_id}</td>}
        <td className="turf-roll-addr">
          {p.address_num} {p.street}, {p.city} {p.zip}
        </td>
        <td>
          {p.name}
          {p.is_ask && <span className="ask-badge">ASK FOR</span>}
        </td>
        <td>{p.age ?? "—"}</td>
        <td>{p.party}</td>
        <td>
          <span className={`badge ${p.tier_letter}`}>{p.tier_letter}{p.tier_count}</span>
        </td>
        <td>{pct(p.turnout_prob)}</td>
        <td>{pct(p.dem_lean_prob)}</td>
        <td>{mult(p.m_net_i)}</td>
        <td>
          {p.donation_count > 0 ? (
            <button className="elec-toggle" onClick={() => setDonationsOpen((o) => !o)}>
              {fmtDollars(p.donation_total)} · {p.donation_count} gift{p.donation_count === 1 ? "" : "s"}
            </button>
          ) : (
            <span className="roll-nontarget">—</span>
          )}
        </td>
        <td>
          {p.email ? (
            <a href={`mailto:${p.email}`} className="contact-email">{p.email}</a>
          ) : (
            <span className="roll-nontarget">—</span>
          )}
        </td>
        <td>
          {p.phone ? (
            <a href={`tel:${p.phone.replace(/\D/g, "")}`} className="contact-phone">{p.phone}</a>
          ) : (
            <span className="roll-nontarget">—</span>
          )}
        </td>
        <td>{lastVoted ?? "—"}</td>
      </tr>
      {donationsOpen && p.donation_count > 0 && (
        <tr className="elec-row open">
          <td colSpan={colSpan}>
            <table className="donation-roll">
              <thead>
                <tr><th>Year</th><th>Amount</th><th>Committee</th></tr>
              </thead>
              <tbody>
                {[...p.donations]
                  .sort((a, b) => (b.donation_date ?? "").localeCompare(a.donation_date ?? ""))
                  .map((d, i) => (
                    <tr key={i}>
                      <td className="yr">{d.donation_date ? d.donation_date.substring(0, 4) : "—"}</td>
                      <td className="amt">{fmtDollars(d.amount ?? 0)}</td>
                      <td>{d.committee ?? ""}</td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </td>
        </tr>
      )}
    </>
  );
}
