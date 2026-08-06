"use client";
import "leaflet/dist/leaflet.css";
import { useEffect, useRef, useState, useCallback } from "react";
import Link from "next/link";

interface HHPoint {
  id: string;
  lat: number;
  lon: number;
  score_total: number;
  score_wake_ups: number;
  score_unaffiliated: number;
  score_dropoff: number;
  people_count: number;
  address_num: string;
  street: string;
  city: string;
  zip: string;
  // An apartment building rather than a door: shown because it sits in the
  // selected turf, but excluded from that turf's door count and value.
  is_facility: boolean;
}

// Control and buffer turfs are the randomized experimental holdout: knocking
// them destroys the measurement the arms exist to produce. They are 21% of the
// file but 7 of the top 20 by net margin, so anyone scrolling from the top of
// the sort hits one roughly every third pick. Hidden by default, not merely
// flagged — flagging was losing to the sort order.
function visibleTurfsOf(turfs: TurfOption[], canvassableOnly: boolean): TurfOption[] {
  return canvassableOnly ? turfs.filter(t => t.arm === "treatment") : turfs;
}

// A building, not a door. Held out of the walk list on purpose (see
// /api/map/facilities), which is exactly why it needs its own surface.
interface Facility {
  facility_id: number;
  household_id: string;
  n_targets: number;
  household_size: number;
  value_net_margin: number;
  lat: number;
  lon: number;
  nearest_turf_id: number | null;
  address_num: string;
  street: string;
  city: string;
  zip: string;
}

type TurfSort = "margin" | "efficiency";

// Total margin and margin-per-hour answer different questions and genuinely
// disagree: Spearman 0.69, and only 11 of their top 20 overlap, because n_doors
// runs 1–200. "margin" is the biggest prize, right when you have the people to
// finish a turf; "hrs/vote" is the best use of a shift, right when volunteer
// hours are the scarce resource — which is most of the time. Sorted client-side:
// the whole scoped list is already in memory, so a re-sort shouldn't cost a
// round trip. Turfs with no finite hrs/vote (value <= 0) sort last either way.
function sortTurfs(turfs: TurfOption[], sort: TurfSort): TurfOption[] {
  const out = [...turfs];
  if (sort === "efficiency") {
    out.sort((a, b) => {
      const av = a.hours_per_net_margin, bv = b.hours_per_net_margin;
      if (av == null) return bv == null ? 0 : 1;
      if (bv == null) return -1;
      return av - bv;                       // fewer hours per vote first
    });
  } else {
    out.sort((a, b) => b.value_net_margin - a.value_net_margin);
  }
  return out;
}

interface Filters {
  showSF: boolean;
  showCX: boolean;
  blkOnly: boolean;
  showAll: boolean;
  cutoff: number;
}

interface TurfOption {
  turf_id: number;
  n_doors: number;
  // Two-party margin, the ranking key. Distinct unit from value_dem_ballots
  // (gross supporting ballots) — label them separately in the UI.
  value_net_margin: number;
  value_dem_ballots: number;
  hours_per_net_margin: number | null;
  n_facilities_nearby: number;
  arm: "treatment" | "control" | "buffer";
}

function householdDominant(r: HHPoint): "lev" | "so" | "re" {
  if (r.score_wake_ups >= r.score_unaffiliated && r.score_wake_ups >= r.score_dropoff) return "lev";
  if (r.score_unaffiliated >= r.score_dropoff) return "so";
  return "re";
}

function percentileCap(weights: number[], p: number, ceiling: number): number {
  if (!weights.length) return 1;
  const sorted = [...weights].sort((a, b) => a - b);
  const idx = Math.floor(sorted.length * p);
  return Math.min(sorted[idx] ?? sorted[sorted.length - 1], ceiling);
}

// Inline canvas heat layer — ported from original template.html
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function buildHeatLayerClass(L: any) {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  return L.Layer.extend({
    options: {
      radius: 25, blur: 15, max: 1.0, minOpacity: 0.05, maxZoom: 18,
      gradient: { 0.4: "blue", 0.65: "lime", 1: "red" },
    },
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    initialize(latlngs: any[], options: any) {
      this._latlngs = latlngs || [];
      L.setOptions(this, options);
    },
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    setLatLngs(latlngs: any[]) { this._latlngs = latlngs; return this.redraw(); },
    redraw() {
      if (this._canvas && !this._frame && this._map && !this._map._animating)
        this._frame = L.Util.requestAnimFrame(this._redraw, this);
      return this;
    },
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    onAdd(map: any) {
      this._map = map;
      if (!this._canvas) this._initCanvas();
      map._panes.overlayPane.appendChild(this._canvas);
      map.on("moveend", this._reset, this);
      if (map.options.zoomAnimation && L.Browser.any3d)
        map.on("zoomanim", this._animateZoom, this);
      this._reset();
    },
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    onRemove(map: any) {
      map._panes.overlayPane.removeChild(this._canvas);
      map.off("moveend", this._reset, this);
      if (map.options.zoomAnimation) map.off("zoomanim", this._animateZoom, this);
    },
    _initCanvas() {
      const canvas = (this._canvas = document.createElement("canvas"));
      const animated = this._map.options.zoomAnimation && L.Browser.any3d;
      L.DomUtil.addClass(
        canvas,
        "leaflet-layer " + (animated ? "leaflet-zoom-animated" : "leaflet-zoom-hide")
      );
    },
    _reset() {
      const topLeft = this._map.containerPointToLayerPoint([0, 0]);
      L.DomUtil.setPosition(this._canvas, topLeft);
      const size = this._map.getSize();
      this._canvas.width = size.x;
      this._canvas.height = size.y;
      this._redraw();
    },
    _redraw() {
      this._frame = null;
      const r = Math.floor(this.options.radius);
      const d = r * 2;
      const size = this._map.getSize();
      const bounds = new L.Bounds(L.point([-d, -d]), size.add(L.point(d, d)));
      const max = Math.max(this.options.max, 1);
      const maxZoom = this.options.maxZoom || this._map.options.maxZoom;
      const v = Math.max(
        1 / Math.pow(2, Math.max(0, Math.min(maxZoom - this._map.getZoom(), 12))),
        0.01
      );
      const cellSize = r / 2;
      const offscreen = document.createElement("canvas");
      offscreen.width = offscreen.height = d;
      const ctx = offscreen.getContext("2d")!;
      ctx.shadowOffsetX = ctx.shadowOffsetY = d * 2;
      ctx.shadowBlur = this.options.blur;
      ctx.shadowColor = "black";
      ctx.beginPath();
      ctx.arc(-d, -d, r, 0, Math.PI * 2, true);
      ctx.closePath();
      ctx.fill();
      const wCells = Math.ceil((size.x + d * 2) / cellSize + 4);
      const cells: number[] = [];
      for (const latlng of this._latlngs) {
        const p = this._map.latLngToContainerPoint(latlng);
        if (!bounds.contains(p)) continue;
        const alt = latlng[2] ?? 1;
        const x = Math.floor((p.x - bounds.min!.x) / cellSize) + 2;
        const y = Math.floor((p.y - bounds.min!.y) / cellSize) + 2;
        const i = y * wCells + x;
        cells[i] = (cells[i] ?? 0) + alt;
      }
      const canvas = this._canvas;
      const ctx2 = canvas.getContext("2d")!;
      ctx2.clearRect(0, 0, canvas.width, canvas.height);
      for (let i = 0; i < cells.length; i++) {
        if (cells[i] === undefined) continue;
        const cx2 = bounds.min!.x + ((i % wCells) - 2) * cellSize;
        const cy2 = bounds.min!.y + (Math.floor(i / wCells) - 2) * cellSize;
        ctx2.globalAlpha = Math.min(
          Math.max((cells[i] * v) / max, this.options.minOpacity),
          1
        );
        ctx2.drawImage(offscreen, cx2 - r, cy2 - r);
      }
      const colored = ctx2.getImageData(0, 0, canvas.width, canvas.height);
      this._colorize(colored.data, this._grad);
      ctx2.putImageData(colored, 0, 0);
    },
    _colorize(pixels: Uint8ClampedArray, grad: Uint8ClampedArray) {
      if (!grad) return;
      for (let i = 0, len = pixels.length; i < len; i += 4) {
        const j = pixels[i + 3] * 4;
        if (j) {
          pixels[i]     = grad[j];
          pixels[i + 1] = grad[j + 1];
          pixels[i + 2] = grad[j + 2];
          pixels[i + 3] = grad[j + 3];
        }
      }
    },
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    _animateZoom(e: any) {
      const scale = this._map.getZoomScale(e.zoom);
      const offset = this._map
        ._latLngBoundsToNewLayerBounds(this._map.getBounds(), e.zoom, e.center)
        .min;
      L.DomUtil.setTransform(this._canvas, offset, scale);
    },
    createGradient(grad: Record<string, string>): Uint8ClampedArray {
      const canvas = document.createElement("canvas");
      canvas.width = 1;
      canvas.height = 256;
      const ctx = canvas.getContext("2d")!;
      const gradient = ctx.createLinearGradient(0, 0, 0, 256);
      for (const [stop, color] of Object.entries(grad))
        gradient.addColorStop(+stop, color);
      ctx.fillStyle = gradient;
      ctx.fillRect(0, 0, 1, 256);
      return ctx.getImageData(0, 0, 1, 256).data as unknown as Uint8ClampedArray;
    },
  });
}

const CX_MARKER_MIN_ZOOM = 15;

const HEAT_CFG = {
  lev: {
    radius: 18, blur: 22, minOpacity: 0.12, maxZoom: 17,
    gradient: { "0": "#FFF3E0", "0.25": "#FFB74D", "0.6": "#F57C00", "1": "#E65100" },
  },
  so: {
    radius: 18, blur: 22, minOpacity: 0.12, maxZoom: 17,
    gradient: { "0": "#F3E5F5", "0.25": "#CE93D8", "0.6": "#8E24AA", "1": "#4A148C" },
  },
  re: {
    radius: 18, blur: 22, minOpacity: 0.12, maxZoom: 17,
    gradient: { "0": "#E3F2FD", "0.25": "#90CAF9", "0.6": "#1E88E5", "1": "#0D47A1" },
  },
  cx: {
    radius: 22, blur: 26, minOpacity: 0.15, maxZoom: 17,
    gradient: { "0": "#E0F2F1", "0.25": "#80CBC4", "0.55": "#00897B", "0.8": "#00695C", "1": "#004D40" },
  },
};

const COMP_COLOR: Record<string, string> = { lev: "#F57C00", so: "#8E24AA", re: "#1565C0" };

const LEGEND = [
  { label: "Wake-up calls",       color: "#F57C00" },
  { label: "Unaffiliated voters", color: "#8E24AA" },
  { label: "Drop-off Democrats",  color: "#1565C0" },
  { label: "Apartment complexes", color: "#00695C" },
];

function popupHtml(r: HHPoint) {
  return (
    `<div class="popup-addr">${r.address_num} ${r.street}</div>` +
    `<div class="popup-sub">${r.city} ${r.zip} · ${r.people_count} voter${r.people_count === 1 ? "" : "s"}</div>` +
    // Say so on the marker itself. This is the address a canvasser walks up to,
    // and it is the one place they'd otherwise discover the lobby is locked.
    (r.is_facility
      ? `<div class="popup-facility">Building — not a door. Excluded from this turf's ` +
        `door count; needs lobby access, phone, or a building contact.</div>`
      : "") +
    (r.score_total > 0
      ? `<div class="popup-score">score <b>${r.score_total}</b></div>` +
        `<div class="popup-breakdown">wake-ups +${r.score_wake_ups} · unaffiliated +${r.score_unaffiliated} · drop-off Dems +${r.score_dropoff}</div>`
      : "")
  );
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function voterTableHtml(hh: any): string {
  const people = hh?.people ?? [];
  if (!people.length) return '<div style="margin-top:8px;font-family:\'IBM Plex Mono\',monospace;font-size:0.7rem;color:var(--ink-faint);">No voters on file.</div>';
  const ages = people.map((p: any) => p.age).filter((a: any) => a != null);
  const youngest = ages.length ? Math.min(...ages) : null;
  const oldest   = ages.length ? Math.max(...ages) : null;
  const stats = [
    `Voters: <b>${people.length}</b>`,
    youngest != null ? `Ages: <b>${youngest}&ndash;${oldest}</b>` : '',
  ].filter(Boolean).join(' · ');
  const pct = (v: number | null | undefined) => v == null ? '—' : `${Math.round(v * 100)}%`;
  // m_net_i is normalised to mean 1 over the target pool, so it reads honestly
  // as a multiple of an average target ("x2.4") and NOT as a probability —
  // showing the bare 0.83 invites reading it as 83%.
  const mult = (v: number | null | undefined) =>
    v == null ? '<span class="roll-nontarget">not a target</span>' : `&times;${v.toFixed(1)}`;
  // The API orders by m_net_i desc, so the first row with a mass IS A(h).
  const askIdx = people.findIndex((p: any) => p.m_net_i != null);
  const rows = people.map((p: any, i: number) =>
    `<tr class="${i === askIdx ? 'roll-ask' : ''}"><td>${p.name}` +
    (i === askIdx ? ' <span class="ask-badge">ASK FOR</span>' : '') +
    `</td><td>${p.age ?? '—'}</td><td>${p.party}</td>` +
    `<td>${pct(p.turnout_prob)}</td><td>${pct(p.dem_lean_prob)}</td><td>${mult(p.m_net_i)}</td>` +
    `<td><span class="badge ${p.tier_letter}">${p.tier_letter}${p.tier_count}</span></td></tr>`
  ).join('');
  // Only worth explaining when there is more than one target to choose between.
  const nTargets = people.filter((p: any) => p.m_net_i != null).length;
  const askNote = nTargets > 1
    ? `<div class="popup-ask-note">Ask for <b>${people[askIdx].name}</b> &mdash; highest expected ` +
      `net margin here. Reaching them is what carries the rest of the household.</div>`
    : '';
  return (
    `<div class="stat-strip popup-stats" style="margin-top:10px;gap:8px;">${stats}</div>` +
    askNote +
    `<table class="roll popup-roll"><thead><tr><th>Name</th><th>Age</th><th>Party</th><th>Turnout</th><th>Lean</th><th>Value</th><th>Tier</th></tr></thead>` +
    `<tbody>${rows}</tbody></table>`
  );
}

export default function LeafletMap() {
  const mapRef    = useRef<HTMLDivElement>(null);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const Lref      = useRef<any>(null);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const mapObj    = useRef<any>(null);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const HeatClass = useRef<any>(null);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const layersMap = useRef<Record<string, any>>({});
  const pointsRef  = useRef<HHPoint[]>([]);
  const fetchTimer    = useRef<ReturnType<typeof setTimeout> | null>(null);
  const fetchAbort    = useRef<AbortController | null>(null);
  const clickTicketRef = useRef(0);

  // All filter values live in a ref so map event callbacks always read current values
  const filtersRef = useRef<Filters>({ showSF: true, showCX: true, blkOnly: false, showAll: false, cutoff: 6 });

  // Geo filter refs (read by loadViewport; must not be in its dep array)
  // geoScope narrows which turfs are on offer ("all" / by AD / by city) —
  // it no longer picks between AD-vs-city-vs-turf as alternative household
  // filters. Turf selection is the only thing that actually drives what
  // households load; AD/City just help find turfs worth picking.
  const geoScopeRef       = useRef<"all" | "ad" | "city">("all");
  const selectedADsRef    = useRef<Set<number>>(new Set());
  const selectedCitiesRef = useRef<Set<string>>(new Set());
  const selectedTurfsRef  = useRef<Set<number>>(new Set());
  const availableADsRef   = useRef<number[]>([]);
  const availableCitiesRef = useRef<string[]>([]);
  const availableTurfsRef  = useRef<TurfOption[]>([]);
  // Default ON: the common case is a volunteer picking a turf to walk, and the
  // holdout arms must not be walkable by accident. Admins can turn it off.
  const canvassableOnlyRef = useRef(true);
  // Full unscoped turf list, fetched once — restored when geoScope returns
  // to "all" without needing a re-fetch.
  const allTurfsRef        = useRef<TurfOption[]>([]);

  const [fetching, setFetching] = useState(false);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const [showSF, _setShowSF]   = useState(true);
  const [showCX, _setShowCX]   = useState(true);
  const [blkOnly, _setBlkOnly] = useState(false);
  const [showAll, _setShowAll] = useState(false);
  const [cutoff, _setCutoff]   = useState(6);
  const [topList, setTopList]  = useState<HHPoint[]>([]);
  const [counts, setCounts]    = useState({ sf: 0, cx: 0 });
  const [canvassableOnly, _setCanvassableOnly] = useState(true);
  const [turfSort, setTurfSort] = useState<TurfSort>("margin");
  const [facilities, setFacilities] = useState<Facility[]>([]);

  // Geo filter state (drives UI rendering; refs drive API calls)
  const [geoScope, _setGeoScope]           = useState<"all" | "ad" | "city">("all");
  const [selectedADs, _setSelectedADs]     = useState<Set<number>>(new Set());
  const [selectedCities, _setSelectedCities] = useState<Set<string>>(new Set());
  const [selectedTurfs, _setSelectedTurfs] = useState<Set<number>>(new Set());
  const [availableADs, _setAvailableADs]   = useState<number[]>([]);
  const [availableCities, _setAvailableCities] = useState<string[]>([]);
  const [availableTurfs, _setAvailableTurfs] = useState<TurfOption[]>([]);
  const [citySearch, setCitySearch]        = useState("");
  const [geoFilterVer, setGeoFilterVer]    = useState(0);

  const setShowSF  = (v: boolean) => { filtersRef.current.showSF  = v; _setShowSF(v); };
  const setShowCX  = (v: boolean) => { filtersRef.current.showCX  = v; _setShowCX(v); };
  const setBlkOnly = (v: boolean) => { filtersRef.current.blkOnly = v; _setBlkOnly(v); };
  const setShowAll = (v: boolean) => { filtersRef.current.showAll = v; _setShowAll(v); };
  const setCutoff  = (v: number)  => { filtersRef.current.cutoff  = v; _setCutoff(v); };

  // geoScope/AD/city setters intentionally do NOT bump geoFilterVer — the
  // scope-narrowing effect below reacts to them, refetches the matching
  // turf list, auto-selects it, and bumps geoFilterVer itself once that
  // resolves. Bumping it here too would fire a household fetch against the
  // stale turf selection first.
  const setGeoScope = (v: "all" | "ad" | "city") => {
    geoScopeRef.current = v;
    _setGeoScope(v);
  };
  const _applyADs = (s: Set<number>) => {
    selectedADsRef.current = s;
    _setSelectedADs(new Set(s));
  };
  const _applyCities = (s: Set<string>) => {
    selectedCitiesRef.current = s;
    _setSelectedCities(new Set(s));
  };
  // Turf selection is the one thing that directly drives the household
  // fetch, so it still bumps geoFilterVer itself — no round-trip needed.
  const _applyTurfs = (s: Set<number>) => {
    selectedTurfsRef.current = s;
    _setSelectedTurfs(new Set(s));
    setGeoFilterVer(n => n + 1);
  };
  const toggleAD = (ad: number) => {
    const next = new Set(selectedADsRef.current);
    if (next.has(ad)) next.delete(ad); else next.add(ad);
    _applyADs(next);
  };
  const toggleCity = (city: string) => {
    const next = new Set(selectedCitiesRef.current);
    if (next.has(city)) next.delete(city); else next.add(city);
    _applyCities(next);
  };
  const toggleTurf = (turfId: number) => {
    const next = new Set(selectedTurfsRef.current);
    if (next.has(turfId)) next.delete(turfId); else next.add(turfId);
    _applyTurfs(next);
  };
  // Bulk select/clear for the AD/City scope checklist.
  const scopeSelectAll = () => {
    if (geoScopeRef.current === "ad") _applyADs(new Set(availableADsRef.current));
    else if (geoScopeRef.current === "city") _applyCities(new Set(availableCitiesRef.current));
  };
  const scopeClearAll = () => {
    if (geoScopeRef.current === "ad") _applyADs(new Set());
    else if (geoScopeRef.current === "city") _applyCities(new Set());
  };
  // Bulk select/clear for the turf checklist (always the currently-VISIBLE list,
  // so "All" can never hand someone a holdout turf they aren't allowed to walk).
  const turfSelectAll = () =>
    _applyTurfs(new Set(visibleTurfsOf(availableTurfsRef.current,
                                       canvassableOnlyRef.current).map(t => t.turf_id)));
  const turfClearAll  = () => _applyTurfs(new Set());
  // Sets the turf list on offer (scoped or not) and auto-selects all of
  // it — turf is the primary, "math forward" filter, so narrowing to a new
  // area should show its households immediately rather than requiring a
  // second manual pick.
  const setScopedTurfs = useCallback((turfs: TurfOption[]) => {
    availableTurfsRef.current = turfs;
    _setAvailableTurfs(turfs);
    const all = new Set(visibleTurfsOf(turfs, canvassableOnlyRef.current).map(t => t.turf_id));
    selectedTurfsRef.current = all;
    _setSelectedTurfs(all);
    setGeoFilterVer(n => n + 1);
  }, []);

  // Flipping the toggle re-derives the selection so what's checked always
  // matches what's listed — otherwise turning the filter on would leave
  // control/buffer turfs silently selected and still driving the map.
  const toggleCanvassableOnly = () => {
    const next = !canvassableOnlyRef.current;
    canvassableOnlyRef.current = next;
    _setCanvassableOnly(next);
    _applyTurfs(new Set(visibleTurfsOf(availableTurfsRef.current, next).map(t => t.turf_id)));
  };

  const updateTopList = useCallback((pts: HHPoint[]) => {
    if (!mapObj.current) return;
    const f = filtersRef.current;
    const b = mapObj.current.getBounds();
    const visible = pts.filter(r => {
      if (!r.lat || !r.lon || r.score_total === 0) return false;
      if (!b.contains([r.lat, r.lon])) return false;
      if (f.blkOnly && r.score_unaffiliated === 0) return false;
      return r.people_count >= f.cutoff ? f.showCX : f.showSF;
    });
    visible.sort((a, b) => b.score_total - a.score_total);
    setTopList(visible.slice(0, 10));
  }, []);

  const updateSFMarkers = useCallback((pts: HHPoint[]) => {
    const L2 = Lref.current;
    const m  = mapObj.current;
    if (!m || !L2) return;
    if (layersMap.current.sfMarkers) {
      m.removeLayer(layersMap.current.sfMarkers);
      delete layersMap.current.sfMarkers;
    }
    const f = filtersRef.current;
    if (m.getZoom() < CX_MARKER_MIN_ZOOM || !f.showSF) return;
    const b = m.getBounds();
    const markers = [];
    for (const r of pts) {
      if (!r.lat || !r.lon) continue;
      if (r.people_count >= f.cutoff) continue;
      if (!b.contains([r.lat, r.lon])) continue;
      if (r.score_total === 0) {
        if (!f.showAll) continue;
        markers.push(
          L2.circleMarker([r.lat, r.lon], {
            radius: 3, weight: 1, color: "#aaa", fillColor: "#ccc",
            fillOpacity: 0.6, opacity: 0.6, interactive: false,
          })
        );
        continue;
      }
      if (f.blkOnly && r.score_unaffiliated === 0) continue;
      const color = COMP_COLOR[householdDominant(r)] ?? "#888";
      markers.push(
        L2.circleMarker([r.lat, r.lon], {
          radius: 5, weight: 1, color, fillColor: color,
          fillOpacity: 0.8, opacity: 0.8, interactive: false,
        })
      );
    }
    if (markers.length)
      layersMap.current.sfMarkers = L2.layerGroup(markers).addTo(m);
  }, []);

  const renderHeat = useCallback(
    (pts: HHPoint[]) => {
      const L2 = Lref.current;
      const m  = mapObj.current;
      const HC = HeatClass.current;
      if (!m || !L2 || !HC) return;
      const f = filtersRef.current;

      for (const key of ["lev", "so", "re", "cx", "cxMarkers", "sfMarkers", "unscored"]) {
        if (layersMap.current[key]) {
          m.removeLayer(layersMap.current[key]);
          delete layersMap.current[key];
        }
      }

      const buckets = {
        lev: { pts: [] as [number, number, number][], weights: [] as number[] },
        so:  { pts: [] as [number, number, number][], weights: [] as number[] },
        re:  { pts: [] as [number, number, number][], weights: [] as number[] },
        cx:  { pts: [] as [number, number, number][], weights: [] as number[] },
      };
      let ctSF = 0, ctCX = 0;

      for (const r of pts) {
        if (!r.lat || !r.lon || r.score_total === 0) continue;
        if (f.blkOnly && r.score_unaffiliated === 0) continue;
        const w = f.blkOnly ? r.score_unaffiliated : r.score_total;
        if (w <= 0) continue;
        const hw = Math.sqrt(w);
        if (r.people_count >= f.cutoff) {
          ctCX++;
          buckets.cx.pts.push([r.lat, r.lon, hw]);
          buckets.cx.weights.push(w);
        } else {
          ctSF++;
          const d = householdDominant(r);
          buckets[d].pts.push([r.lat, r.lon, hw]);
          buckets[d].weights.push(w);
        }
      }
      setCounts({ sf: ctSF, cx: ctCX });

      for (const key of ["lev", "so", "re"] as const) {
        if (!f.showSF || !buckets[key].pts.length) continue;
        const maxW = Math.sqrt(percentileCap(buckets[key].weights, 0.95, f.blkOnly ? 6 : 60));
        const cfg  = HEAT_CFG[key];
        const hl   = new HC(buckets[key].pts, { ...cfg, max: maxW });
        hl._grad   = hl.createGradient(cfg.gradient);
        hl.addTo(m);
        layersMap.current[key] = hl;
      }

      if (f.showCX && buckets.cx.pts.length) {
        const maxW = Math.sqrt(percentileCap(buckets.cx.weights, 0.95, f.blkOnly ? 10 : 400));
        const cfg  = HEAT_CFG.cx;
        const hl   = new HC(buckets.cx.pts, { ...cfg, max: maxW });
        hl._grad   = hl.createGradient(cfg.gradient);
        hl.addTo(m);
        layersMap.current.cx = hl;

        const cxMs = buckets.cx.pts.map(p =>
          L2.circleMarker([p[0], p[1]], {
            pane: "cxMarkerPane", radius: 3, weight: 1,
            color: "#00695C", fillColor: "#00695C", fillOpacity: 0.85, opacity: 0.85, interactive: false,
          })
        );
        layersMap.current.cxMarkers = L2.layerGroup(cxMs).addTo(m);
        const pane = m.getPane("cxMarkerPane");
        if (pane) pane.style.display = m.getZoom() >= CX_MARKER_MIN_ZOOM ? "" : "none";
      }

      if (f.showAll) {
        const grayMarkers = pts
          .filter(r => r.lat && r.lon && r.score_total === 0)
          .map(r => L2.circleMarker([r.lat, r.lon], {
            radius: 3, weight: 1, color: "#aaa", fillColor: "#ccc",
            fillOpacity: 0.6, opacity: 0.6, interactive: false,
          }));
        if (grayMarkers.length)
          layersMap.current.unscored = L2.layerGroup(grayMarkers).addTo(m);
      }

      updateSFMarkers(pts);
      updateTopList(pts);
    },
    [updateSFMarkers, updateTopList]
  );

  const loadViewport = useCallback(async () => {
    const m = mapObj.current;
    if (!m) return;

    // Households are driven solely by turf selection — AD/City only narrow
    // which turfs are on offer (see the scope-narrowing effect). Don't
    // fetch until a turf selection exists, including on the very first
    // call before /api/map/filters has resolved.
    if (selectedTurfsRef.current.size === 0) {
      // A blocked fetch must also clear whatever was already drawn —
      // otherwise the previous selection's layers (esp. the always-visible
      // complex markers) just sit there looking like "nothing happened".
      if (fetchTimer.current) clearTimeout(fetchTimer.current);
      fetchAbort.current?.abort();
      pointsRef.current = [];
      renderHeat([]);
      return;
    }

    if (fetchTimer.current) clearTimeout(fetchTimer.current);
    fetchTimer.current = setTimeout(async () => {
      // Cancel any in-flight request from a previous viewport
      fetchAbort.current?.abort();
      fetchAbort.current = new AbortController();
      const signal = fetchAbort.current.signal;

      const b    = m.getBounds();
      const zoom = m.getZoom();
      const s = b.getSouth().toFixed(5), n = b.getNorth().toFixed(5);
      const w = b.getWest().toFixed(5),  e = b.getEast().toFixed(5);

      const limit = zoom >= 14 ? 800 : zoom >= 12 ? 500 : 300;
      let url = `/api/map/households?s=${s}&n=${n}&w=${w}&e=${e}&limit=${limit}`;
      if (filtersRef.current.showAll) url += "&all=1";

      // Append the turf filter. Skipping it is only safe when truly
      // unrestricted — geoScope "all" with every turf selected. Once
      // scoped to an AD/City, "every (narrower) turf selected" is NOT the
      // same as "no filter": the household query has no other way to know
      // about that scoping, since only turf IDs get sent here — so the
      // explicit list must go through even when 100% of it is checked.
      const sel = selectedTurfsRef.current;
      const avail = availableTurfsRef.current;
      const vis = visibleTurfsOf(avail, canvassableOnlyRef.current);
      // "Everything currently on offer is checked" — the default state. Send the
      // cheap server-side predicate instead of enumerating it: at geoScope "all"
      // with canvassable-only on, the explicit list would be 1,345 ids (~7KB of
      // query string) that the arm filter expresses in one word.
      const allVisibleSelected = geoScopeRef.current === "all" && sel.size === vis.length;
      if (allVisibleSelected) {
        if (canvassableOnlyRef.current) url += `&arm=treatment`;
      } else {
        url += `&turfs=${[...sel].join(",")}`;
      }

      setFetching(true);
      setFetchError(null);
      try {
        const res  = await fetch(url, { signal });
        if (!res.ok) {
          const body = await res.text();
          setFetchError(`API error ${res.status}: ${body.slice(0, 200)}`);
          return;
        }
        const data: HHPoint[] = await res.json();
        pointsRef.current = data;
        renderHeat(data);
      } catch (err) {
        if ((err as Error).name === "AbortError") return;
        setFetchError((err as Error).message);
      } finally {
        setFetching(false);
      }
    }, 400);
  }, [renderHeat]);

  useEffect(() => {
    if (mapObj.current || !mapRef.current) return;
    import("leaflet").then((Lmod) => {
      Lref.current      = Lmod;
      HeatClass.current = buildHeatLayerClass(Lmod);

      const m = Lmod.map(mapRef.current!, { preferCanvas: true, minZoom: 10, maxZoom: 18 })
        .setView([40.79, -73.55], 11);

      m.createPane("cxMarkerPane");
      // eslint-disable-next-line @typescript-eslint/no-non-null-assertion
      m.getPane("cxMarkerPane")!.style.zIndex = "450";

      Lmod.tileLayer("https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png", {
        attribution: "© OpenStreetMap © CARTO", subdomains: "abcd", maxZoom: 20,
      }).addTo(m);

      mapObj.current = m;

      m.on("moveend", () => { loadViewport(); updateTopList(pointsRef.current); });
      m.on("zoomend", () => {
        const pane = m.getPane("cxMarkerPane");
        if (pane) pane.style.display = m.getZoom() >= CX_MARKER_MIN_ZOOM ? "" : "none";
        updateSFMarkers(pointsRef.current);
        updateTopList(pointsRef.current);
      });

      // Proximity click: find nearest scored household within ~200m and show popup
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      m.on("click", (e: any) => {
        const pts = pointsRef.current;
        if (!pts.length) return;
        const { lat, lng } = e.latlng;
        let best: HHPoint | null = null, bestD = Infinity;
        for (const r of pts) {
          if (!r.lat || !r.lon || (!filtersRef.current.showAll && r.score_total === 0)) continue;
          const dlat = r.lat - lat;
          const dlon = (r.lon - lng) * Math.cos(lat * Math.PI / 180);
          const d = dlat * dlat + dlon * dlon;
          if (d < bestD) { bestD = d; best = r; }
        }
        if (!best || bestD >= 0.0000035) return;
        const ticket = ++clickTicketRef.current;
        const popup = Lmod.popup({ closeButton: true, maxWidth: 400, maxHeight: 420 })
          .setLatLng([best.lat, best.lon])
          .setContent(popupHtml(best) + "<div style=\"margin-top:8px;font-family:'IBM Plex Mono',monospace;font-size:0.7rem;color:var(--ink-faint);\">Loading voters…</div>")
          .openOn(m);
        const bestId = best.id;
        fetch(`/api/households/${bestId}`)
          .then(res => res.ok ? res.json() : null)
          .then(hh => { if (ticket === clickTicketRef.current) popup.setContent(popupHtml(best!) + voterTableHtml(hh)); })
          .catch(() => {});
      });

      loadViewport();
    });
    return () => {
      if (mapObj.current) { mapObj.current.remove(); mapObj.current = null; }
    };
  }, [loadViewport, updateSFMarkers, updateTopList]);

  // Re-render heat when display filters change (no API refetch needed)
  useEffect(() => {
    if (pointsRef.current.length) {
      try { renderHeat(pointsRef.current); }
      catch (err) { setFetchError((err as Error).message); }
    }
  }, [showSF, showCX, blkOnly, showAll, cutoff, renderHeat]);

  // Load available assembly districts, cities and the full turf list once
  // on mount. geoScope starts "all", so the full turf list is what's on
  // offer immediately — auto-selected, same "math forward" default as before.
  useEffect(() => {
    fetch("/api/map/filters")
      .then(r => r.json())
      .then(({ assembly_districts, cities, turfs }:
        { assembly_districts: number[]; cities: string[]; turfs: TurfOption[] }) => {
        availableADsRef.current = assembly_districts;
        _setAvailableADs(assembly_districts);
        // Start with nothing selected — user must pick from the dropdown
        selectedADsRef.current = new Set();
        _setSelectedADs(new Set());

        availableCitiesRef.current = cities;
        _setAvailableCities(cities);
        selectedCitiesRef.current = new Set();
        _setSelectedCities(new Set());

        allTurfsRef.current = turfs;
        setScopedTurfs(turfs);
      })
      .catch(() => {});
  }, [setScopedTurfs]);

  // Scope-narrowing: whenever geoScope or the selected AD(s)/city(ies)
  // change, refetch (or restore) the matching turf list and auto-select
  // it. Debounced so rapid-fire checkbox clicks don't fire a request per
  // click. Also fires once on mount (geoScope is "all", allTurfsRef is
  // still empty at that instant) — a harmless no-op, since the mount
  // effect above calls setScopedTurfs again once its own fetch resolves.
  useEffect(() => {
    if (geoScope === "all") {
      setScopedTurfs(allTurfsRef.current);
      return;
    }
    const ids = geoScope === "ad" ? [...selectedADs] : [...selectedCities];
    if (ids.length === 0) {
      setScopedTurfs([]);
      return;
    }
    const controller = new AbortController();
    const timer = setTimeout(() => {
      const qs = geoScope === "ad"
        ? `ads=${ids.join(",")}`
        : `cities=${(ids as string[]).map(c => encodeURIComponent(c)).join(",")}`;
      fetch(`/api/map/filters?${qs}`, { signal: controller.signal })
        .then(r => r.json())
        .then(({ turfs }: { turfs: TurfOption[] }) => setScopedTurfs(turfs))
        .catch(() => {});
    }, 300);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [geoScope, selectedADs, selectedCities, setScopedTurfs]);

  // Re-fetch households when the turf selection changes.
  useEffect(() => {
    if (mapObj.current) loadViewport();
  }, [geoFilterVer, loadViewport]);

  // Re-fetch when showAll changes (affects row limit)
  useEffect(() => {
    if (mapObj.current) loadViewport();
  }, [showAll, loadViewport]);

  // Buildings track the same area scoping and the same canvassable filter as
  // the turf list, so the two panels always describe one plan rather than two.
  useEffect(() => {
    const controller = new AbortController();
    const qs = new URLSearchParams();
    if (geoScope === "ad" && selectedADs.size) qs.set("ads", [...selectedADs].join(","));
    if (geoScope === "city" && selectedCities.size) {
      qs.set("cities", [...selectedCities].map(encodeURIComponent).join(","));
    }
    if (canvassableOnly) qs.set("arm", "treatment");
    fetch(`/api/map/facilities?${qs}`, { signal: controller.signal })
      .then(res => (res.ok ? res.json() : []))
      .then((rows: Facility[]) => setFacilities(rows))
      .catch(() => {});
    return () => controller.abort();
  }, [geoScope, selectedADs, selectedCities, canvassableOnly]);

  // Open a building's popup from the Buildings list. Unlike flyTo there is no
  // HHPoint behind the row, so the whole popup is rendered from the detail
  // response — which is why that route now returns people_count/is_facility.
  function flyToFacility(f: Facility) {
    const m  = mapObj.current;
    const L2 = Lref.current;
    if (!m || !L2) return;
    m.setView([f.lat, f.lon], 17);
    const ticket = ++clickTicketRef.current;
    const head =
      `<div class="popup-addr">${f.address_num} ${f.street}</div>` +
      `<div class="popup-sub">${f.city} ${f.zip} · ${f.household_size} voters</div>`;
    const popup = L2.popup({ closeButton: true, maxWidth: 400, maxHeight: 420 })
      .setLatLng([f.lat, f.lon])
      .setContent(head + "<div style=\"margin-top:8px;font-family:'IBM Plex Mono',monospace;font-size:0.7rem;color:var(--ink-faint);\">Loading voters…</div>")
      .openOn(m);
    fetch(`/api/households/${f.household_id}`)
      .then(res => (res.ok ? res.json() : null))
      .then(hh => {
        if (ticket !== clickTicketRef.current) return;
        popup.setContent(hh ? popupHtml(hh as HHPoint) + voterTableHtml(hh) : head);
      })
      .catch(() => {});
  }

  function flyTo(r: HHPoint) {
    const m  = mapObj.current;
    const L2 = Lref.current;
    if (!m || !L2) return;
    m.setView([r.lat, r.lon], 17);
    const ticket = ++clickTicketRef.current;
    const popup = L2.popup({ closeButton: true, maxWidth: 400, maxHeight: 420 })
      .setLatLng([r.lat, r.lon])
      .setContent(popupHtml(r) + "<div style=\"margin-top:8px;font-family:'IBM Plex Mono',monospace;font-size:0.7rem;color:var(--ink-faint);\">Loading voters…</div>")
      .openOn(m);
    fetch(`/api/households/${r.id}`)
      .then(res => res.ok ? res.json() : null)
      .then(hh => { if (ticket === clickTicketRef.current) popup.setContent(popupHtml(r) + voterTableHtml(hh)); })
      .catch(() => {});
  }

  // Derived from state (not the refs) so the list re-renders when the toggle flips.
  const shownTurfs = sortTurfs(visibleTurfsOf(availableTurfs, canvassableOnly), turfSort);
  const hiddenTurfCount = availableTurfs.length - shownTurfs.length;

  return (
    <div className="map-grid">
      {/* Map */}
      <div className="map-wrap">
        <div ref={mapRef} id="map" style={{ width: "100%", height: "100%" }} />

        <div className="map-legend">
          <div style={{ fontWeight: 600, fontSize: "0.68rem", marginBottom: 6 }}>Canvass priority</div>
          {LEGEND.map(({ label, color }) => (
            <div key={label} style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 3 }}>
              <span style={{ width: 10, height: 10, borderRadius: "50%", background: color, flexShrink: 0, display: "inline-block" }} />
              <span>{label}</span>
            </div>
          ))}
        </div>

        {fetching && (
          <div className="map-throbber">
            <span className="map-spinner" />
            Loading…
          </div>
        )}
        {fetchError && (
          <div className="map-throbber map-throbber-error">{fetchError}</div>
        )}
      </div>

      {/* Side panel */}
      <div className="map-side">
        <div className="panel">
          <h3>Show on map</h3>
          <label className="layer-row">
            <input type="checkbox" checked={showSF} onChange={e => setShowSF(e.target.checked)} />
            <span className="swatch sf" />
            <span className="lbl">Single-family homes</span>
            <span className="ct">{counts.sf.toLocaleString()}</span>
          </label>
          <label className="layer-row">
            <input type="checkbox" checked={showCX} onChange={e => setShowCX(e.target.checked)} />
            <span className="swatch cx" />
            <span className="lbl">Apartment complexes</span>
            <span className="ct">{counts.cx.toLocaleString()}</span>
          </label>
          <div className="slider-row">
            <div className="label-line">
              <span>Complex min size</span>
              <b>{cutoff}+ voters</b>
            </div>
            <input type="range" min={2} max={30} value={cutoff} step={1}
              onChange={e => setCutoff(parseInt(e.target.value))} />
          </div>
          <details style={{ marginTop: 8 }}>
            <summary className="filter-summary">More filters</summary>
            <label className="layer-row" style={{ marginTop: 6 }}>
              <input type="checkbox" checked={blkOnly} onChange={e => setBlkOnly(e.target.checked)} />
              <span className="swatch blk" />
              <span className="lbl">Unaffiliated (BLK) only</span>
            </label>
            <div className="layer-hint">Shows only households with at least one unaffiliated registered voter.</div>
            <label className="layer-row" style={{ marginTop: 6 }}>
              <input type="checkbox" checked={showAll} onChange={e => setShowAll(e.target.checked)} />
              <span className="lbl">Show all voter addresses</span>
            </label>
            <div className="layer-hint">Includes addresses with no canvassing score, shown as gray dots.</div>
          </details>
        </div>

        {/* Narrow by area — optional, only affects which turfs are on offer
            below. Doesn't filter households directly anymore: turf
            selection is the one thing that does that. */}
        <div className="panel">
          <h3>Narrow by area</h3>
          <div className="geo-mode-toggle">
            <button
              className={geoScope === "all" ? "active" : ""}
              onClick={() => setGeoScope("all")}
            >All</button>
            <button
              className={geoScope === "ad" ? "active" : ""}
              onClick={() => setGeoScope("ad")}
            >Assembly District</button>
            <button
              className={geoScope === "city" ? "active" : ""}
              onClick={() => setGeoScope("city")}
            >City</button>
          </div>

          {geoScope !== "all" && (
            <>
              <div className="geo-ctrl-row">
                <button className="geo-btn" onClick={scopeSelectAll}>All</button>
                <button className="geo-btn" onClick={scopeClearAll}>None</button>
                <span className="geo-sel-count">
                  {geoScope === "ad"
                    ? (selectedADs.size === availableADs.length ? `${availableADs.length} ADs` : `${selectedADs.size} / ${availableADs.length}`)
                    : (selectedCities.size === availableCities.length ? `${availableCities.length} cities` : `${selectedCities.size} / ${availableCities.length}`)
                  }
                </span>
              </div>

              {geoScope === "city" && (
                <input
                  className="geo-search"
                  type="text"
                  placeholder="Search cities…"
                  value={citySearch}
                  onChange={e => setCitySearch(e.target.value)}
                />
              )}

              <div className="geo-checklist">
                {geoScope === "ad"
                  ? availableADs.map(ad => (
                      <label key={ad} className="geo-check-row">
                        <input
                          type="checkbox"
                          checked={selectedADs.has(ad)}
                          onChange={() => toggleAD(ad)}
                        />
                        <span>AD {ad}</span>
                      </label>
                    ))
                  : availableCities
                      .filter(c => !citySearch || c.toLowerCase().includes(citySearch.toLowerCase()))
                      .map(city => (
                        <label key={city} className="geo-check-row">
                          <input
                            type="checkbox"
                            checked={selectedCities.has(city)}
                            onChange={() => toggleCity(city)}
                          />
                          <span>{city}</span>
                        </label>
                      ))
                }
              </div>
            </>
          )}
        </div>

        {/* Turfs — always shown, scoped to whatever area is narrowed above.
            The server returns them by value_net_margin; the Rank-by control
            re-sorts in place (see sortTurfs for why both orders are needed).

            Control/buffer turfs are HIDDEN by default rather than merely
            flagged. They were flagged before, but flagging lost to the sort
            order: they are 21% of the file and 7 of the top 20 by net margin,
            so a volunteer scrolling from the top hit an unwalkable holdout
            roughly every third pick. The toggle restores them for anyone who
            needs to see the whole experiment.

            "margin" not "ballots": mobilisation turns out whoever answers the
            door, so the number shown is expected NET two-party gain. n_doors
            excludes apartment buildings — they can't be knocked, and are
            counted separately as "+N bldgs" needing a phone/lobby plan. */}
        <div className="panel">
          <h3>Turfs{geoScope !== "all" ? " in selected area" : ""}</h3>
          <label className="geo-check-row turf-arm-toggle">
            <input
              type="checkbox"
              checked={canvassableOnly}
              onChange={toggleCanvassableOnly}
            />
            <span>
              Canvassable only
              {hiddenTurfCount > 0 && (
                <span className="turf-arm-hint"> · {hiddenTurfCount} holdout turf{hiddenTurfCount === 1 ? "" : "s"} hidden</span>
              )}
            </span>
          </label>
          <div className="turf-sort-row">
            <span className="turf-sort-label">Rank by</span>
            <button
              className={`geo-btn${turfSort === "margin" ? " geo-btn-on" : ""}`}
              onClick={() => setTurfSort("margin")}
              title="Total expected net two-party margin — the biggest prize"
            >net margin</button>
            <button
              className={`geo-btn${turfSort === "efficiency" ? " geo-btn-on" : ""}`}
              onClick={() => setTurfSort("efficiency")}
              title="Canvasser-hours per net vote — the best use of a shift"
            >hrs/vote</button>
          </div>
          <div className="geo-ctrl-row">
            <button className="geo-btn" onClick={turfSelectAll}>All</button>
            <button className="geo-btn" onClick={turfClearAll}>None</button>
            <span className="geo-sel-count">
              {selectedTurfs.size === shownTurfs.length ? `${shownTurfs.length} turfs` : `${selectedTurfs.size} / ${shownTurfs.length}`}
            </span>
          </div>
          <div className="geo-checklist">
            {shownTurfs.length === 0 ? (
              <div className="top-empty">
                {availableTurfs.length > 0
                  ? "Every turf here is a control or buffer holdout — untick “Canvassable only” to see them."
                  : geoScope === "all" ? "Loading turfs…" : "No turfs in the selected area."}
              </div>
            ) : shownTurfs.map(t => (
              <label
                key={t.turf_id}
                className={`geo-check-row${t.arm !== "treatment" ? " geo-check-row-flagged" : ""}`}
              >
                <input
                  type="checkbox"
                  checked={selectedTurfs.has(t.turf_id)}
                  onChange={() => toggleTurf(t.turf_id)}
                />
                <span>
                  <Link
                    href={`/turfs/${t.turf_id}`}
                    className="turf-id-link"
                    onClick={(e) => e.stopPropagation()}
                  >
                    Turf {t.turf_id}
                  </Link>
                  {" "}· {t.value_net_margin.toFixed(1)} margin
                  {t.hours_per_net_margin != null && <> · {t.hours_per_net_margin.toFixed(2)} hrs/vote</>}
                  {" "}· {t.n_doors} doors
                  {t.n_facilities_nearby > 0 && <> · +{t.n_facilities_nearby} bldgs</>}
                </span>
                {t.arm !== "treatment" && (
                  <span className={`geo-arm-badge geo-arm-${t.arm}`}>{t.arm.toUpperCase()}</span>
                )}
              </label>
            ))}
          </div>
        </div>

        {/* Buildings — the other half of the plan. These are held OUT of the
            turf list on purpose (a locked lobby is not a door), so without this
            panel 1,407 buildings holding 20,372 targets — 5% of the whole pool
            — would be invisible rather than merely non-walkable. Ranked by the
            same net-margin figure, but no hours estimate: what it costs to get
            into a building is an organising question, not doors ÷ 20/hour.
            Clicking a row flies to it and opens the same popup a marker does. */}
        <details className="panel">
          <summary className="panel-summary">
            Buildings{facilities.length > 0 && ` · ${facilities.length}`}
          </summary>
          <div className="bldg-intro">
            Not doors — a canvasser can&rsquo;t knock a locked lobby. Worth a
            building contact, lobby access, or a phone shift.
          </div>
          <div className="top-list">
            {facilities.length === 0 ? (
              <div className="top-empty">No buildings in the selected area.</div>
            ) : facilities.map(f => (
              <button key={f.facility_id} className="top-row bldg-row" onClick={() => flyToFacility(f)}>
                <span className="top-addr">{f.address_num} {f.street}, {f.city}</span>
                <span className="bldg-meta">
                  {f.n_targets} target{f.n_targets === 1 ? "" : "s"} of {f.household_size} voters
                  {f.nearest_turf_id != null && <> · nearest turf {f.nearest_turf_id}</>}
                </span>
              </button>
            ))}
          </div>
        </details>

        <details className="panel" open>
          <summary className="panel-summary">How scoring works</summary>
          <div className="scoring-explainer">
            <p><b>Wake-up calls.</b> A reliable voter living with non-voters — pulling them along is the easiest add.</p>
            <p><b>Unaffiliated voters.</b> Registered "blank" — no party — the most persuadable people in the file.</p>
            <p><b>Drop-off Democrats.</b> Registered Dems who stopped showing up. Friendly, just need a nudge.</p>
          </div>
        </details>

        <details className="panel">
          <summary className="panel-summary">Top targets in view</summary>
          <div className="top-list">
            {topList.length === 0 ? (
              <div className="top-empty">
                {selectedTurfs.size === 0
                  ? "Select turfs above to load households."
                  : "No scored households in current view."}
              </div>
            ) : topList.map(r => (
              <div key={r.id} className="top-row" onClick={() => flyTo(r)}>
                <span className="top-addr">{r.address_num} {r.street}, {r.city}</span>
                <span className="top-sc">{r.score_total}</span>
              </div>
            ))}
          </div>
        </details>

      </div>
    </div>
  );
}
