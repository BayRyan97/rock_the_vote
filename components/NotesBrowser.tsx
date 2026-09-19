"use client";
import { useEffect, useState } from "react";
import {
  type Outcome,
  type SupportLevel,
  OUTCOMES,
  SUPPORT_LEVELS,
  OUTCOME_LABELS,
  SUPPORT_LABELS,
} from "@/lib/canvassNotes";
import { csvCell } from "@/components/PersonRoster";

interface Note {
  id: string;
  created_at: string;
  canvassing_for: string;
  outcome: Outcome | null;
  support_level: SupportLevel | null;
  issues: string | null;
  follow_up_needed: boolean | null;
  mail_ballot_assistance: boolean | null;
  contact_name: string | null;
  language_spoken: string | null;
  left_pamphlet: boolean | null;
  notes: string | null;
  turf_id: number | null;
  canvasser_id: string;
  profiles: { name: string | null } | null;
  households: {
    address_num: string;
    street: string;
    city: string;
    zip: string;
    town: string | null;
  } | null;
}

type SortKey = "created_at" | "turf_id";
interface SortState {
  key: SortKey;
  dir: "asc" | "desc";
}

const LIMIT = 50;

const CSV_HEADERS = [
  "Date", "Address", "Turf", "Canvasser", "Canvassing for", "Outcome", "Support level",
  "Follow-up", "Mail ballot assistance", "Left pamphlet", "Contact",
  "Language", "Issues", "Notes",
];

function formatAddress(h: Note["households"]) {
  if (!h) return "—";
  return `${h.address_num} ${h.street}, ${h.city} ${h.zip}`;
}

function downloadNotesCsv(notes: Note[]) {
  const rows = notes.map((n) => [
    new Date(n.created_at).toLocaleString(),
    formatAddress(n.households),
    n.turf_id ?? "Unknown",
    n.profiles?.name ?? "",
    n.canvassing_for,
    n.outcome ? OUTCOME_LABELS[n.outcome] : "",
    n.support_level ? SUPPORT_LABELS[n.support_level] : "",
    n.follow_up_needed ? "Yes" : "",
    n.mail_ballot_assistance ? "Yes" : "",
    n.left_pamphlet ? "Yes" : "",
    n.contact_name ?? "",
    n.language_spoken ?? "",
    n.issues ?? "",
    n.notes ?? "",
  ]);
  const csv = "﻿" + [CSV_HEADERS, ...rows].map((r) => r.map(csvCell).join(",")).join("\r\n");
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "canvass-notes.csv";
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

function toggleInSet<T>(set: Set<T>, value: T): Set<T> {
  const next = new Set(set);
  if (next.has(value)) next.delete(value);
  else next.add(value);
  return next;
}

export default function NotesBrowser({
  canvassers,
  candidates,
  hasFullAccess,
}: {
  canvassers: { id: string; name: string | null }[];
  candidates: { id: string; name: string | null }[];
  hasFullAccess: boolean;
}) {
  const [notes, setNotes] = useState<Note[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [sort, setSort] = useState<SortState>({ key: "created_at", dir: "desc" });

  const [turfId, setTurfId] = useState("");
  const [canvasserId, setCanvasserId] = useState("");
  const [canvassingFor, setCanvassingFor] = useState("");
  const [outcomes, setOutcomes] = useState<Set<Outcome>>(new Set());
  const [supportLevels, setSupportLevels] = useState<Set<SupportLevel>>(new Set());
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");

  useEffect(() => {
    let cancelled = false;
    setLoading(true);

    const params = new URLSearchParams();
    if (turfId.trim()) params.set("turf_id", turfId.trim());
    if (canvasserId) params.set("canvasser_id", canvasserId);
    if (hasFullAccess && canvassingFor) params.set("canvassing_for", canvassingFor);
    if (outcomes.size) params.set("outcome", [...outcomes].join(","));
    if (supportLevels.size) params.set("support_level", [...supportLevels].join(","));
    if (from) params.set("from", from);
    if (to) params.set("to", to);
    params.set("sort", sort.key);
    params.set("dir", sort.dir);
    params.set("limit", String(LIMIT));
    params.set("offset", String(offset));

    fetch(`/api/notes?${params}`)
      .then((r) => r.json())
      .then((d: { notes: Note[]; total: number }) => {
        if (cancelled) return;
        setNotes(Array.isArray(d.notes) ? d.notes : []);
        setTotal(d.total ?? 0);
      })
      .catch(() => {
        if (!cancelled) {
          setNotes([]);
          setTotal(0);
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [turfId, canvasserId, canvassingFor, hasFullAccess, outcomes, supportLevels, from, to, sort, offset]);

  function handleSort(key: SortKey) {
    setOffset(0);
    setSort((s) => (s.key === key ? { key, dir: s.dir === "asc" ? "desc" : "asc" } : { key, dir: "desc" }));
  }

  function updateFilter<T>(setter: (v: T) => void) {
    return (v: T) => {
      setOffset(0);
      setter(v);
    };
  }

  function clearFilters() {
    setOffset(0);
    setTurfId("");
    setCanvasserId("");
    setCanvassingFor("");
    setOutcomes(new Set());
    setSupportLevels(new Set());
    setFrom("");
    setTo("");
  }

  const from_ = offset + 1;
  const to_ = Math.min(offset + LIMIT, total);

  return (
    <div>
      <div className="turf-filter-row">
        <label className="turf-filter-label">
          Turf
          <input
            type="number"
            className="turf-filter-input"
            value={turfId}
            onChange={(e) => updateFilter(setTurfId)(e.target.value)}
            placeholder="id"
          />
        </label>

        <label className="turf-filter-label">
          Canvasser
          <select
            value={canvasserId}
            onChange={(e) => updateFilter(setCanvasserId)(e.target.value)}
            style={{ fontFamily: "var(--font-mono), monospace", fontSize: "0.74rem" }}
          >
            <option value="">All</option>
            {canvassers.map((c) => (
              <option key={c.id} value={c.id}>{c.name}</option>
            ))}
          </select>
        </label>

        {hasFullAccess && (
          <label className="turf-filter-label">
            Canvassing for
            <select
              value={canvassingFor}
              onChange={(e) => updateFilter(setCanvassingFor)(e.target.value)}
              style={{ fontFamily: "var(--font-mono), monospace", fontSize: "0.74rem" }}
            >
              <option value="">All</option>
              {candidates.map((c) => (
                <option key={c.id} value={c.name!}>{c.name}</option>
              ))}
            </select>
          </label>
        )}

        <label className="turf-filter-label">
          From
          <input type="date" className="turf-filter-input" style={{ width: 130 }} value={from} onChange={(e) => updateFilter(setFrom)(e.target.value)} />
        </label>
        <label className="turf-filter-label">
          To
          <input type="date" className="turf-filter-input" style={{ width: 130 }} value={to} onChange={(e) => updateFilter(setTo)(e.target.value)} />
        </label>

        <button className="geo-btn" onClick={clearFilters}>Clear filters</button>
        <button className="geo-btn" onClick={() => downloadNotesCsv(notes)} disabled={!notes.length}>
          Export page as CSV
        </button>
      </div>

      <div className="turf-filter-row">
        <span className="turf-filter-label">Outcome</span>
        {OUTCOMES.map((o) => (
          <button
            key={o}
            className={`geo-btn${outcomes.has(o) ? " geo-btn-on" : ""}`}
            onClick={() => updateFilter(setOutcomes)(toggleInSet(outcomes, o))}
          >
            {OUTCOME_LABELS[o]}
          </button>
        ))}
      </div>

      <div className="turf-filter-row">
        <span className="turf-filter-label">Support</span>
        {SUPPORT_LEVELS.map((s) => (
          <button
            key={s}
            className={`geo-btn${supportLevels.has(s) ? " geo-btn-on" : ""}`}
            onClick={() => updateFilter(setSupportLevels)(toggleInSet(supportLevels, s))}
          >
            {SUPPORT_LABELS[s]}
          </button>
        ))}
      </div>

      {loading ? (
        <div className="stats-loading"><span className="search-throbber" /> Loading notes…</div>
      ) : notes.length === 0 ? (
        <div className="empty-state">
          <div className="big">No notes match these filters.</div>
        </div>
      ) : (
        <>
          <div className="turf-roll-wrap">
            <table className="roll turf-roll">
              <thead>
                <tr>
                  <th
                    className={`sortable${sort.key === "created_at" ? " sort-active" : ""}`}
                    onClick={() => handleSort("created_at")}
                  >
                    Date
                    {sort.key === "created_at" && (
                      <span className="sort-caret">{sort.dir === "asc" ? "▲" : "▼"}</span>
                    )}
                  </th>
                  <th>Address</th>
                  <th
                    className={`sortable${sort.key === "turf_id" ? " sort-active" : ""}`}
                    onClick={() => handleSort("turf_id")}
                  >
                    Turf
                    {sort.key === "turf_id" && (
                      <span className="sort-caret">{sort.dir === "asc" ? "▲" : "▼"}</span>
                    )}
                  </th>
                  <th>Canvasser</th>
                  <th>Canvassing for</th>
                  <th>Theme</th>
                  <th>Notes</th>
                </tr>
              </thead>
              <tbody>
                {notes.map((n) => (
                  <tr key={n.id}>
                    <td>{new Date(n.created_at).toLocaleString()}</td>
                    <td>{formatAddress(n.households)}</td>
                    <td>{n.turf_id ?? <span className="roll-nontarget">Unknown</span>}</td>
                    <td>{n.profiles?.name ?? <span className="roll-nontarget">Unknown</span>}</td>
                    <td>{n.canvassing_for}</td>
                    <td>
                      <div className="note-history-tags">
                        {n.outcome && <span className="note-tag">{OUTCOME_LABELS[n.outcome]}</span>}
                        {n.support_level && <span className="note-tag">{SUPPORT_LABELS[n.support_level]}</span>}
                        {n.follow_up_needed && <span className="note-tag">Follow-up</span>}
                        {n.mail_ballot_assistance && <span className="note-tag">Mail ballot assistance</span>}
                        {n.left_pamphlet && <span className="note-tag">Left pamphlet</span>}
                      </div>
                    </td>
                    <td>{n.issues || n.notes || ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="turf-sort-row" style={{ marginTop: 8 }}>
            <button className="geo-btn" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - LIMIT))}>
              Prev
            </button>
            <button className="geo-btn" disabled={offset + LIMIT >= total} onClick={() => setOffset(offset + LIMIT)}>
              Next
            </button>
            <span className="turf-filter-label">{total === 0 ? "0 of 0" : `${from_}–${to_} of ${total}`}</span>
          </div>
        </>
      )}
    </div>
  );
}
