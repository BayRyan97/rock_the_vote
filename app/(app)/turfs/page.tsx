"use client";
import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { Person, PersonRow, csvCell, formatElections, formatDonations } from "@/components/PersonRoster";
import {
  GEO_DIMENSION_CONFIGS,
  GeoDimensionKey,
  appendGeoParams,
  emptyOptions,
  emptySelection,
  hasAnySelected,
} from "@/lib/geoDimensions";

interface TurfOption {
  turf_id: number;
  n_doors: number;
  value_net_margin: number;
  value_dem_ballots: number;
  hours_per_net_margin: number | null;
  n_facilities_nearby: number;
  arm: "treatment" | "control" | "buffer";
}

type TurfSortKey =
  | "turf_id"
  | "n_doors"
  | "value_net_margin"
  | "value_dem_ballots"
  | "hours_per_net_margin"
  | "n_facilities_nearby"
  | "arm";
interface TurfSortState { key: TurfSortKey; dir: "asc" | "desc" }

type PersonSortKey =
  | "turf_id"
  | "name"
  | "age"
  | "party"
  | "tier_letter"
  | "turnout_prob"
  | "dem_lean_prob"
  | "m_net_i"
  | "donation_total"
  | "last_voted";
interface PersonSortState { key: PersonSortKey; dir: "asc" | "desc" }

// Control/buffer turfs are the randomized experimental holdout and must not be
// walked — same rule and rationale as the Canvass Map (see LeafletMap.tsx).
function visibleTurfsOf(turfs: TurfOption[], canvassableOnly: boolean): TurfOption[] {
  return canvassableOnly ? turfs.filter((t) => t.arm === "treatment") : turfs;
}

// Nulls (e.g. no finite hrs/vote) always sort last, in either direction.
function compareTurfs(a: TurfOption, b: TurfOption, sort: TurfSortState): number {
  const av = a[sort.key];
  const bv = b[sort.key];
  if (av == null && bv == null) return 0;
  if (av == null) return 1;
  if (bv == null) return -1;
  const cmp =
    typeof av === "string" && typeof bv === "string"
      ? av.localeCompare(bv)
      : (av as number) - (bv as number);
  return sort.dir === "asc" ? cmp : -cmp;
}

const TURF_COLUMNS: { key: TurfSortKey; label: string }[] = [
  { key: "turf_id", label: "Turf" },
  { key: "n_doors", label: "Doors" },
  { key: "value_net_margin", label: "Net margin" },
  { key: "value_dem_ballots", label: "Dem ballots" },
  { key: "hours_per_net_margin", label: "Hrs/vote" },
  { key: "n_facilities_nearby", label: "Buildings" },
];
const ARM_COLUMN: { key: TurfSortKey; label: string } = { key: "arm", label: "Arm" };

function fmtMargin(n: number) {
  return n.toFixed(1);
}
function fmtHrsPerVote(n: number | null) {
  return n == null ? "—" : n.toFixed(2);
}

function lastVotedYear(p: Person): number | null {
  return p.elections.length ? Math.max(...p.elections.map((e) => e.year)) : null;
}
function personValue(p: Person, key: PersonSortKey): number | string | null {
  if (key === "last_voted") return lastVotedYear(p);
  return p[key] ?? null;
}
function comparePeople(a: Person, b: Person, sort: PersonSortState): number {
  const av = personValue(a, sort.key);
  const bv = personValue(b, sort.key);
  if (av == null && bv == null) return 0;
  if (av == null) return 1;
  if (bv == null) return -1;
  const cmp =
    typeof av === "string" && typeof bv === "string"
      ? av.localeCompare(bv)
      : (av as number) - (bv as number);
  return sort.dir === "asc" ? cmp : -cmp;
}

const PERSON_STRING_KEYS = new Set<PersonSortKey>(["name", "party", "tier_letter"]);
const PERSON_COLUMNS: { key: PersonSortKey; label: string }[] = [
  { key: "name", label: "Name" },
  { key: "age", label: "Age" },
  { key: "party", label: "Party" },
  { key: "tier_letter", label: "Tier" },
  { key: "turnout_prob", label: "Turnout" },
  { key: "dem_lean_prob", label: "Lean" },
  { key: "m_net_i", label: "Value" },
  { key: "donation_total", label: "Donations" },
];

const PERSON_CSV_HEADERS = [
  "Turf", "Address", "Name", "Age", "Party", "Tier", "Turnout", "Lean", "Value",
  "Ask for", "Donation total", "Donation count", "Donation detail", "Email", "Phone",
  "Elections voted", "Last voted", "Voting history",
];

function downloadRosterCsv(people: Person[], showTurf: boolean) {
  const headers = showTurf ? PERSON_CSV_HEADERS : PERSON_CSV_HEADERS.filter((h) => h !== "Turf");
  const rows = people.map((p) => {
    const row = [
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
      formatDonations(p.donations),
      p.email ?? "",
      p.phone ?? "",
      p.elections.length,
      lastVotedYear(p) ?? "",
      formatElections(p.elections),
    ];
    return showTurf ? [p.turf_id ?? "", ...row] : row;
  });
  const csv =
    "﻿" + [headers, ...rows].map((r) => r.map(csvCell).join(",")).join("\r\n");
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "turf-search-roster.csv";
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

function clampPct(v: string) {
  const n = Number(v);
  return Number.isFinite(n) ? Math.min(100, Math.max(0, n)) : 0;
}
function clampDollar(v: string) {
  const n = Number(v);
  return Number.isFinite(n) ? Math.max(0, n) : 0;
}

export default function TurfSearchPage() {
  // Up to 8 combinable geo dimensions (county/city/town/election_district/
  // legislative_district/congressional_district/senate_district/
  // assembly_district) — each independently toggleable and ANDed together
  // server-side (lib/geoFilters.ts). `openDims` is PURELY which accordion
  // sections are visually expanded — it has no effect on filtering.
  // Filtering is driven only by `selected` (see hasAnySelected/
  // appendGeoParams): expanding a section to browse its options must never
  // by itself narrow the turf list or fire a request.
  const [selected, setSelected] = useState<Record<GeoDimensionKey, Set<string | number>>>(emptySelection);
  const [available, setAvailable] = useState<Record<GeoDimensionKey, (string | number)[]>>(emptyOptions);
  const [openDims, setOpenDims] = useState<Set<GeoDimensionKey>>(new Set());
  const [dimSearch, setDimSearch] = useState<Partial<Record<GeoDimensionKey, string>>>({});
  const [canvassableOnly, setCanvassableOnly] = useState(true);
  const [allTurfs, setAllTurfs] = useState<TurfOption[]>([]);
  const [scopedTurfs, setScopedTurfs] = useState<TurfOption[]>([]);
  const [loading, setLoading] = useState(true);
  const [turfSort, setTurfSort] = useState<TurfSortState>({ key: "value_net_margin", dir: "desc" });

  const [selectedTurfIds, setSelectedTurfIds] = useState<Set<number>>(new Set());
  const [people, setPeople] = useState<Person[]>([]);
  const [peopleLoading, setPeopleLoading] = useState(false);
  const [personSort, setPersonSort] = useState<PersonSortState>({ key: "m_net_i", dir: "desc" });
  const [minTurnout, setMinTurnout] = useState(0);
  const [minLean, setMinLean] = useState(0);
  const [minDonation, setMinDonation] = useState(0);

  // Load the full geo-option lists and the full unscoped turf list once on mount.
  useEffect(() => {
    fetch("/api/map/filters")
      .then((r) => r.json())
      .then(
        ({
          options,
          turfs,
        }: {
          options: Record<GeoDimensionKey, (string | number)[]>;
          turfs: TurfOption[];
        }) => {
          setAvailable(options);
          setAllTurfs(turfs);
          setScopedTurfs(turfs);
          setLoading(false);
        }
      )
      .catch(() => setLoading(false));
  }, []);

  // Rescope whenever any dimension's CHECKED values change — not when a
  // section is merely opened/closed for browsing. /api/map/filters also
  // returns freshly-cascaded `options` on every call (not just the unscoped
  // one), so checking a County immediately narrows what Town/ED/etc. offer —
  // this is what makes the panel a funnel rather than 8 independent lists.
  useEffect(() => {
    if (!hasAnySelected(selected)) {
      setScopedTurfs(allTurfs);
      return;
    }
    const controller = new AbortController();
    const timer = setTimeout(() => {
      const qs = new URLSearchParams();
      appendGeoParams(qs, selected);
      fetch(`/api/map/filters?${qs}`, { signal: controller.signal })
        .then((r) => r.json())
        .then(
          ({
            options,
            turfs,
          }: {
            options: Record<GeoDimensionKey, (string | number)[]>;
            turfs: TurfOption[];
          }) => {
            setAvailable(options);
            setScopedTurfs(turfs);
          }
        )
        .catch(() => {});
    }, 300);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [selected, allTurfs]);

  const visible = useMemo(
    () => visibleTurfsOf(scopedTurfs, canvassableOnly),
    [scopedTurfs, canvassableOnly]
  );
  const hiddenCount = scopedTurfs.length - visible.length;
  const sortedTurfs = useMemo(
    () => [...visible].sort((a, b) => compareTurfs(a, b, turfSort)),
    [visible, turfSort]
  );

  // Drop any selected turf that's fallen out of the visible set (area
  // re-scoped, or "Canvassable only" hid it) so the roster below never
  // silently keeps fetching a turf the user can no longer see or pick.
  useEffect(() => {
    setSelectedTurfIds((prev) => {
      const validIds = new Set(visible.map((t) => t.turf_id));
      const next = new Set([...prev].filter((id) => validIds.has(id)));
      return next.size === prev.size ? prev : next;
    });
  }, [visible]);

  // Fetch everyone in the selected turf(s), debounced against rapid clicks.
  // The area scope goes along too: a turf is offered under "AD 15" as soon as
  // ONE of its doors is in AD 15 (see /api/map/filters), so without this a
  // turf that's mostly a neighboring district would still hand back every
  // household in it. Passing the same scope here keeps what's shown lined up
  // with what was searched for.
  useEffect(() => {
    const ids = [...selectedTurfIds];
    if (ids.length === 0) {
      setPeople([]);
      return;
    }
    const controller = new AbortController();
    setPeopleLoading(true);
    const timer = setTimeout(() => {
      const qs = new URLSearchParams();
      qs.set("turfs", ids.join(","));
      appendGeoParams(qs, selected);
      fetch(`/api/turfs/roster?${qs}`, { signal: controller.signal })
        .then((r) => r.json())
        .then(({ people }: { people: Person[] }) => setPeople(people))
        .catch(() => {})
        .finally(() => setPeopleLoading(false));
    }, 300);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [selectedTurfIds, selected]);

  // Always replace the top-level `selected` object (not just mutate a nested
  // Set) so its reference changes and the rescoping/roster effects above,
  // which depend on it, actually re-run.
  const toggleValue = (dim: GeoDimensionKey, value: string | number) => {
    setSelected((prev) => {
      const set = new Set(prev[dim]);
      if (set.has(value)) set.delete(value);
      else set.add(value);
      return { ...prev, [dim]: set };
    });
  };
  const dimSelectAll = (dim: GeoDimensionKey) => {
    setSelected((prev) => ({ ...prev, [dim]: new Set(available[dim]) }));
  };
  const dimClearAll = (dim: GeoDimensionKey) => {
    setSelected((prev) => ({ ...prev, [dim]: new Set() }));
  };
  const clearAllFilters = () => setSelected(emptySelection());
  const toggleOpenDim = (dim: GeoDimensionKey) => {
    setOpenDims((prev) => {
      const next = new Set(prev);
      if (next.has(dim)) next.delete(dim);
      else next.add(dim);
      return next;
    });
  };

  const toggleTurfSelect = (id: number) => {
    setSelectedTurfIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };
  const selectAllVisibleTurfs = () => setSelectedTurfIds(new Set(visible.map((t) => t.turf_id)));
  const clearSelectedTurfs = () => setSelectedTurfIds(new Set());

  function handleTurfSort(key: TurfSortKey) {
    setTurfSort((s) =>
      s.key === key
        ? { key, dir: s.dir === "asc" ? "desc" : "asc" }
        : { key, dir: key === "turf_id" || key === "arm" ? "asc" : "desc" }
    );
  }
  function handlePersonSort(key: PersonSortKey) {
    setPersonSort((s) =>
      s.key === key
        ? { key, dir: s.dir === "asc" ? "desc" : "asc" }
        : { key, dir: PERSON_STRING_KEYS.has(key) ? "asc" : "desc" }
    );
  }

  const turfColumns = canvassableOnly ? TURF_COLUMNS : [...TURF_COLUMNS, ARM_COLUMN];

  const filteredPeople = useMemo(
    () =>
      people.filter(
        (p) =>
          (p.turnout_prob ?? 0) * 100 >= minTurnout &&
          (p.dem_lean_prob ?? 0) * 100 >= minLean &&
          p.donation_total >= minDonation
      ),
    [people, minTurnout, minLean, minDonation]
  );
  const sortedPeople = useMemo(
    () => [...filteredPeople].sort((a, b) => comparePeople(a, b, personSort)),
    [filteredPeople, personSort]
  );
  const showTurfColumn = selectedTurfIds.size > 1;
  const personColSpan = showTurfColumn ? 13 : 12;
  const filtersActive = minTurnout > 0 || minLean > 0 || minDonation > 0;

  return (
    <div className="app-chrome-wrap turf-page">
      <p className="stats-section-title">Turf Search</p>

      <div className="turf-search-grid">
        <div className="panel">
          <div className="geo-panel-head">
            <h3>Narrow by area</h3>
            {hasAnySelected(selected) && (
              <button type="button" className="geo-clear-all" onClick={clearAllFilters}>
                Clear all
              </button>
            )}
          </div>
          {hasAnySelected(selected) && (
            <div className="geo-chip-row">
              {GEO_DIMENSION_CONFIGS.filter((d) => selected[d.key].size > 0).map((d) => (
                <button
                  key={d.key}
                  type="button"
                  className="geo-chip"
                  onClick={() => dimClearAll(d.key)}
                  title={`Clear ${d.label}`}
                >
                  {d.label} · {selected[d.key].size}
                  <span className="geo-chip-x">×</span>
                </button>
              ))}
            </div>
          )}
          <div className="geo-dim-list-scroll">
            <div className="geo-dim-list">
              {GEO_DIMENSION_CONFIGS.map((dim, i) => {
                const isOpen = openDims.has(dim.key);
                const count = selected[dim.key].size;
                const opts = available[dim.key];
                const search = dimSearch[dim.key] ?? "";
                const shownOpts =
                  dim.searchable && search
                    ? opts.filter((v) => String(v).toLowerCase().includes(search.toLowerCase()))
                    : opts;
                const showGroupLabel = i === 0 || GEO_DIMENSION_CONFIGS[i - 1].group !== dim.group;
                return (
                  <div key={dim.key}>
                    {showGroupLabel && <div className="geo-dim-group-label">{dim.group}</div>}
                    <div className="geo-dim-section">
                      <button
                        type="button"
                        className={`geo-dim-header${isOpen ? " open" : ""}`}
                        onClick={() => toggleOpenDim(dim.key)}
                      >
                        <span className="geo-dim-caret">{isOpen ? "▾" : "▸"}</span>
                        <span className="geo-dim-label">{dim.label}</span>
                        {count > 0 && <span className="geo-dim-badge">{count}</span>}
                      </button>
                      {isOpen && (
                        <div className="geo-dim-body">
                          <div className="geo-ctrl-row">
                            <button className="geo-btn" onClick={() => dimSelectAll(dim.key)}>All</button>
                            <button className="geo-btn" onClick={() => dimClearAll(dim.key)}>None</button>
                            <span className="geo-sel-count">
                              {count === opts.length ? `${opts.length} shown` : `${count} / ${opts.length}`}
                            </span>
                          </div>
                          {dim.searchable && (
                            <input
                              className="geo-search"
                              type="text"
                              placeholder={`Search ${dim.label.toLowerCase()}…`}
                              value={search}
                              onChange={(e) => setDimSearch((s) => ({ ...s, [dim.key]: e.target.value }))}
                            />
                          )}
                          <div className="geo-checklist">
                            {shownOpts.map((v) => (
                              <label key={v} className="geo-check-row">
                                <input
                                  type="checkbox"
                                  checked={selected[dim.key].has(v)}
                                  onChange={() => toggleValue(dim.key, v)}
                                />
                                <span>{dim.formatValue ? dim.formatValue(v) : v}</span>
                              </label>
                            ))}
                          </div>
                        </div>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        </div>

        <div className="panel">
          <h3>Turfs{hasAnySelected(selected) ? " in selected area" : ""}</h3>
          <label className="geo-check-row turf-arm-toggle">
            <input
              type="checkbox"
              checked={canvassableOnly}
              onChange={() => setCanvassableOnly((v) => !v)}
            />
            <span>
              Canvassable only
              {hiddenCount > 0 && (
                <span className="turf-arm-hint">
                  {" "}
                  · {hiddenCount} holdout turf{hiddenCount === 1 ? "" : "s"} hidden
                </span>
              )}
            </span>
          </label>

          <div className="geo-ctrl-row">
            <button className="geo-btn" onClick={selectAllVisibleTurfs}>Select all</button>
            <button className="geo-btn" onClick={clearSelectedTurfs}>Clear</button>
            <span className="geo-sel-count">
              {selectedTurfIds.size > 0
                ? `${selectedTurfIds.size} selected · ${sortedTurfs.length} shown`
                : `${sortedTurfs.length.toLocaleString()} turf${sortedTurfs.length === 1 ? "" : "s"}`}
            </span>
          </div>

          {loading ? (
            <div className="stats-loading">
              <span className="search-throbber" /> Loading turfs…
            </div>
          ) : sortedTurfs.length === 0 ? (
            <div className="empty-state">
              <div className="big">
                {scopedTurfs.length > 0
                  ? "Every turf here is a control or buffer holdout — untick “Canvassable only” to see them."
                  : !hasAnySelected(selected)
                  ? "No turfs found."
                  : "No turfs in the selected area."}
              </div>
            </div>
          ) : (
            <div className="turf-roll-wrap">
              <table className="roll turf-roll">
                <thead>
                  <tr>
                    <th />
                    {turfColumns.map((col) => (
                      <th
                        key={col.key}
                        className={`sortable${turfSort.key === col.key ? " sort-active" : ""}`}
                        onClick={() => handleTurfSort(col.key)}
                      >
                        {col.label}
                        {turfSort.key === col.key && (
                          <span className="sort-caret">{turfSort.dir === "asc" ? "▲" : "▼"}</span>
                        )}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {sortedTurfs.map((t) => (
                    <tr
                      key={t.turf_id}
                      className={`stats-row-link${selectedTurfIds.has(t.turf_id) ? " match" : ""}`}
                      onClick={() => toggleTurfSelect(t.turf_id)}
                    >
                      <td onClick={(e) => e.stopPropagation()}>
                        <input
                          type="checkbox"
                          checked={selectedTurfIds.has(t.turf_id)}
                          onChange={() => toggleTurfSelect(t.turf_id)}
                        />
                      </td>
                      <td>
                        <Link
                          href={`/turfs/${t.turf_id}`}
                          className="turf-id-link"
                          onClick={(e) => e.stopPropagation()}
                        >
                          Turf {t.turf_id}
                        </Link>
                      </td>
                      <td>{t.n_doors.toLocaleString()}</td>
                      <td>{fmtMargin(t.value_net_margin)}</td>
                      <td>{fmtMargin(t.value_dem_ballots)}</td>
                      <td>{fmtHrsPerVote(t.hours_per_net_margin)}</td>
                      <td>{t.n_facilities_nearby > 0 ? `+${t.n_facilities_nearby} bldgs` : "—"}</td>
                      {!canvassableOnly && (
                        <td>
                          {t.arm !== "treatment" ? (
                            <span className={`geo-arm-badge geo-arm-${t.arm}`}>
                              {t.arm.toUpperCase()}
                            </span>
                          ) : (
                            "—"
                          )}
                        </td>
                      )}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>

      <div className="panel turf-search-people">
        <h3>People{selectedTurfIds.size > 0 ? ` in selected turf${selectedTurfIds.size === 1 ? "" : "s"}` : ""}</h3>

        {selectedTurfIds.size === 0 ? (
          <div className="empty-state">
            <div className="big">Select one or more turfs above to see who lives there.</div>
          </div>
        ) : (
          <>
            <div className="turf-filter-row">
              <label className="turf-filter-label">
                Min turnout
                <input
                  type="number"
                  className="turf-filter-input"
                  min={0}
                  max={100}
                  value={minTurnout}
                  onChange={(e) => setMinTurnout(clampPct(e.target.value))}
                />
                %
              </label>
              <label className="turf-filter-label">
                Min lean
                <input
                  type="number"
                  className="turf-filter-input"
                  min={0}
                  max={100}
                  value={minLean}
                  onChange={(e) => setMinLean(clampPct(e.target.value))}
                />
                %
              </label>
              <label className="turf-filter-label">
                Min donations $
                <input
                  type="number"
                  className="turf-filter-input"
                  min={0}
                  value={minDonation}
                  onChange={(e) => setMinDonation(clampDollar(e.target.value))}
                />
              </label>
              {filtersActive && (
                <button
                  className="geo-btn"
                  onClick={() => {
                    setMinTurnout(0);
                    setMinLean(0);
                    setMinDonation(0);
                  }}
                >
                  Reset filters
                </button>
              )}
              <button
                className="geo-btn turf-export-btn"
                onClick={() => downloadRosterCsv(sortedPeople, showTurfColumn)}
              >
                Export CSV
              </button>
            </div>

            <div className="meta-line">
              {peopleLoading ? (
                <span className="search-throbber" />
              ) : (
                `${sortedPeople.length.toLocaleString()} of ${people.length.toLocaleString()} people${
                  people.length >= 3000 ? " (capped at 3,000 by model value)" : ""
                }`
              )}
            </div>

            {peopleLoading ? (
              <div className="stats-loading">
                <span className="search-throbber" /> Loading people…
              </div>
            ) : sortedPeople.length === 0 ? (
              <div className="empty-state">
                <div className="big">
                  {people.length === 0
                    ? "No one modeled in the selected turf(s)."
                    : "No one matches the current filters."}
                </div>
              </div>
            ) : (
              <div className="turf-roll-wrap">
                <table className="roll turf-roll">
                  <thead>
                    <tr>
                      {showTurfColumn && (
                        <th
                          className={`sortable${personSort.key === "turf_id" ? " sort-active" : ""}`}
                          onClick={() => handlePersonSort("turf_id")}
                        >
                          Turf
                          {personSort.key === "turf_id" && (
                            <span className="sort-caret">{personSort.dir === "asc" ? "▲" : "▼"}</span>
                          )}
                        </th>
                      )}
                      <th>Address</th>
                      {PERSON_COLUMNS.map((col) => (
                        <th
                          key={col.key}
                          className={`sortable${personSort.key === col.key ? " sort-active" : ""}`}
                          onClick={() => handlePersonSort(col.key)}
                        >
                          {col.label}
                          {personSort.key === col.key && (
                            <span className="sort-caret">{personSort.dir === "asc" ? "▲" : "▼"}</span>
                          )}
                        </th>
                      ))}
                      <th>Email</th>
                      <th>Phone</th>
                      <th
                        className={`sortable${personSort.key === "last_voted" ? " sort-active" : ""}`}
                        onClick={() => handlePersonSort("last_voted")}
                      >
                        Last voted
                        {personSort.key === "last_voted" && (
                          <span className="sort-caret">{personSort.dir === "asc" ? "▲" : "▼"}</span>
                        )}
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {sortedPeople.map((p) => (
                      <PersonRow
                        key={p.person_id}
                        p={p}
                        showTurf={showTurfColumn}
                        colSpan={personColSpan}
                      />
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
