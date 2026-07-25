"use client";
import { useState } from "react";

interface Donation {
  donor_key: string;
  source: string;
  donation_date: string | null;
  amount: number | null;
  committee: string | null;
  confirmed: boolean;
}

export interface DonorData {
  donor_key: string;
  name: string;
  city: string;
  zip: string;
  party: string | null;
  total_confirmed: number;
  confirmed: Donation[];
  possible: Donation[];
}

function fmtDollars(n: number) {
  return "$" + n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function DonationTable({ rows }: { rows: Donation[] }) {
  const sorted = [...rows].sort((a, b) =>
    (b.donation_date ?? "").localeCompare(a.donation_date ?? "")
  );
  return (
    <table className="donation-roll">
      <thead>
        <tr><th>Year</th><th>Amount</th><th>Committee</th></tr>
      </thead>
      <tbody>
        {sorted.map((c, i) => (
          <tr key={i}>
            <td className="yr">{c.donation_date ? c.donation_date.substring(0, 4) : "—"}</td>
            <td className="amt">{fmtDollars(c.amount ?? 0)}</td>
            <td>{c.committee ?? ""}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export default function DonorCard({ d }: { d: DonorData }) {
  const [open, setOpen] = useState(false);
  const totalDonations = d.confirmed.length + d.possible.length;

  return (
    <div className={`donor-card${open ? " open" : ""}`}>
      <div className="donor-head" onClick={() => setOpen((o) => !o)}>
        <div className="donor-name-loc">
          <div className="donor-name">
            {d.name}
            {d.party && (
              <span className={`party-pill ${d.party}`}>{d.party}</span>
            )}
          </div>
          <div className="donor-loc">{d.city} &middot; {d.zip}</div>
        </div>
        <div className="donor-total">
          {d.total_confirmed > 0 ? (
            <span className="amount">{fmtDollars(d.total_confirmed)}</span>
          ) : (
            <span className="amount" style={{ color: "var(--ink-soft)" }}>—</span>
          )}
          <span className="count">
            {totalDonations} donation{totalDonations === 1 ? "" : "s"}
          </span>
        </div>
      </div>

      {open && (
        <div className="donor-body">
          {d.confirmed.length > 0 && (
            <>
              <div className="donor-section-label">Confirmed donations</div>
              <DonationTable rows={d.confirmed} />
            </>
          )}
          {d.possible.length > 0 && (
            <>
              <div className="donor-section-label possible">
                Possible matches{" "}
                <span style={{ fontWeight: 400, textTransform: "none" }}>
                  (address unverified — may be a different person)
                </span>
              </div>
              <DonationTable rows={d.possible} />
            </>
          )}
        </div>
      )}
    </div>
  );
}
