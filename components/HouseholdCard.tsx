"use client";
import { useState } from "react";

const ETYPE: Record<string, string> = { G: "General", P: "Primary" };
const EMETHOD: Record<string, string> = {
  E: "Election Day", V: "Early Voting", A: "Absentee",
  F: "Federal", D: "Affidavit", M: "Mail", O: "Other",
};

interface Election { year: number; ballot: string }
interface Person {
  name: string;
  age: number | null;
  party: string;
  tier_letter: string;
  tier_count: number;
  elections: Election[] | null;
}
export interface HouseholdData {
  id: string;
  address_num: string;
  street: string;
  city: string;
  zip: string;
  town: string | null;
  election_district: number | null;
  score_total: number;
  ev_score: number;
  ev_count: number;
  people: Person[];
  matched_idx: number;
}

function tierLabel(letter: string, count: number) { return `${letter}${count}`; }

function dominantTier(people: Person[]): string {
  for (const t of ["X", "F", "L", "I"]) {
    if (people.some((p) => p.tier_letter === t)) return t;
  }
  return "I";
}

function deriveStats(people: Person[]) {
  if (!people.length) return { gap: 0, reliable: 0, low: 0, oldest: -1, youngest: 999, count: 0 };
  let maxV = -1, minV = 99, reliable = 0, low = 0, oldest = -1, youngest = 999;
  const lowSet = new Set(["I0", "F1", "L1", "F2", "L2"]);
  people.forEach((p) => {
    const v = p.tier_count;
    if (v > maxV) maxV = v;
    if (v < minV) minV = v;
    if (p.tier_letter === "X" && p.tier_count >= 4) reliable++;
    if (lowSet.has(tierLabel(p.tier_letter, p.tier_count))) low++;
    if (p.age && p.age > oldest) oldest = p.age;
    if (p.age && p.age < youngest) youngest = p.age;
  });
  return { gap: maxV - minV, reliable, low, oldest, youngest, count: people.length };
}

function PersonRows({ p, rowId, isMatch }: { p: Person; rowId: string; isMatch: boolean }) {
  const [elecOpen, setElecOpen] = useState(false);
  const elecCount = p.elections?.length ?? 0;
  return (
    <>
      <tr className={isMatch ? "match" : ""}>
        <td>{p.name}</td>
        <td>{p.age ?? "—"}</td>
        <td>{p.party}</td>
        <td>
          <span className={`badge ${p.tier_letter}`}>
            {tierLabel(p.tier_letter, p.tier_count)}
          </span>
        </td>
        <td>
          {elecCount > 0 && (
            <button className="elec-toggle" onClick={() => setElecOpen((o) => !o)}>
              {elecCount} election{elecCount === 1 ? "" : "s"}
            </button>
          )}
        </td>
      </tr>
      {elecOpen && elecCount > 0 && (
        <tr className="elec-row open">
          <td colSpan={5}>
            <table className="elec-table">
              <thead><tr><th>Year</th><th>Election</th><th>How</th></tr></thead>
              <tbody>
                {p.elections!.map((e, i) => (
                  <tr key={`${rowId}-e${i}`}>
                    <td>{e.year}</td>
                    <td>{ETYPE[e.ballot?.[0]] ?? e.ballot?.[0] ?? "—"}</td>
                    <td>{EMETHOD[e.ballot?.[1]] ?? e.ballot?.[1] ?? "—"}</td>
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

export default function HouseholdCard({ h }: { h: HouseholdData }) {
  const [open, setOpen] = useState(false);
  const dom = dominantTier(h.people);
  const stats = deriveStats(h.people);

  return (
    <div className={`card${open ? " open" : ""}`}>
      <div className="card-head" onClick={() => setOpen((o) => !o)}>
        <div className={`tier-tab ${dom}`} />
        <div className="card-main">
          <div>
            <div className="card-addr">
              {h.address_num} {h.street},{" "}
              <span className="city">{h.city} {h.zip}</span>
            </div>
            <div className="card-sub">
              {[h.town, h.election_district ? `ED ${h.election_district}` : null]
                .filter(Boolean)
                .join(" · ")}
            </div>
          </div>
          <div className="card-count">
            <span><span className="n">{h.people.length}</span> voter{h.people.length === 1 ? "" : "s"}</span>
            {h.score_total > 0 && (
              <span className="card-score">canvass score {h.score_total}</span>
            )}
            {h.ev_score > 0 && (
              <span className="card-ev-score">env zone {h.ev_score}/100</span>
            )}
          </div>
        </div>
      </div>

      {open && (
        <div className="card-body">
          {!h.people.length ? (
            <div className="no-detail">No household detail on file for this address.</div>
          ) : (
            <>
              <div className="stat-strip">
                <span>Voters: <b>{stats.count}</b></span>
                {stats.oldest !== -1 && (
                  <span>Ages: <b>{stats.youngest}–{stats.oldest}</b></span>
                )}
                <span>Engagement gap: <b>{stats.gap}</b></span>
                <span>Reliable: <b>{stats.reliable}</b></span>
                <span>Low-engagement: <b>{stats.low}</b></span>
                <span>Canvass score: <b>{h.score_total}</b></span>
                {h.ev_score > 0 && (
                  <span style={{ color: "var(--seal-x)" }}>
                    Env zone: <b>{h.ev_score}/100</b>
                  </span>
                )}
              </div>
              <table className="roll">
                <thead>
                  <tr><th>Name</th><th>Age</th><th>Party</th><th>Tier</th><th></th></tr>
                </thead>
                <tbody>
                  {h.people.map((p, i) => (
                    <PersonRows
                      key={`${h.id}-${i}`}
                      p={p}
                      rowId={`${h.id}-${i}`}
                      isMatch={i === h.matched_idx}
                    />
                  ))}
                </tbody>
              </table>
            </>
          )}
        </div>
      )}
    </div>
  );
}
