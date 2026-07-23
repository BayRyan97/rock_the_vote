"use client";
import "leaflet/dist/leaflet.css";
import { useEffect, useRef, useState, useCallback } from "react";

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
}

interface Filters {
  showSF: boolean;
  showCX: boolean;
  blkOnly: boolean;
  cutoff: number;
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
  const rows = people.map((p: any) =>
    `<tr><td>${p.name}</td><td>${p.age ?? '—'}</td><td>${p.party}</td>` +
    `<td><span class="badge ${p.tier_letter}">${p.tier_letter}${p.tier_count}</span></td></tr>`
  ).join('');
  return (
    `<div class="stat-strip popup-stats" style="margin-top:10px;gap:8px;">${stats}</div>` +
    `<table class="roll popup-roll"><thead><tr><th>Name</th><th>Age</th><th>Party</th><th>Tier</th></tr></thead>` +
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
  const clickTicketRef = useRef(0);

  // All filter values live in a ref so map event callbacks always read current values
  const filtersRef = useRef<Filters>({ showSF: true, showCX: true, blkOnly: false, cutoff: 6 });

  const [fetching, setFetching] = useState(false);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const [showSF, _setShowSF]   = useState(true);
  const [showCX, _setShowCX]   = useState(true);
  const [blkOnly, _setBlkOnly] = useState(false);
  const [cutoff, _setCutoff]   = useState(6);
  const [topList, setTopList]  = useState<HHPoint[]>([]);
  const [counts, setCounts]    = useState({ sf: 0, cx: 0 });

  const setShowSF  = (v: boolean) => { filtersRef.current.showSF  = v; _setShowSF(v); };
  const setShowCX  = (v: boolean) => { filtersRef.current.showCX  = v; _setShowCX(v); };
  const setBlkOnly = (v: boolean) => { filtersRef.current.blkOnly = v; _setBlkOnly(v); };
  const setCutoff  = (v: number)  => { filtersRef.current.cutoff  = v; _setCutoff(v); };

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
      if (!r.lat || !r.lon || r.score_total === 0) continue;
      if (r.people_count >= f.cutoff) continue;
      if (!b.contains([r.lat, r.lon])) continue;
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

      for (const key of ["lev", "so", "re", "cx", "cxMarkers", "sfMarkers"]) {
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

      updateSFMarkers(pts);
      updateTopList(pts);
    },
    [updateSFMarkers, updateTopList]
  );

  const loadViewport = useCallback(async () => {
    const m = mapObj.current;
    if (!m) return;
    if (fetchTimer.current) clearTimeout(fetchTimer.current);
    fetchTimer.current = setTimeout(async () => {
      const b = m.getBounds();
      const s = b.getSouth().toFixed(5), n = b.getNorth().toFixed(5);
      const w = b.getWest().toFixed(5),  e = b.getEast().toFixed(5);
      setFetching(true);
      setFetchError(null);
      try {
        const res  = await fetch(`/api/map/households?s=${s}&n=${n}&w=${w}&e=${e}`);
        if (!res.ok) {
          const body = await res.text();
          setFetchError(`API error ${res.status}: ${body.slice(0, 200)}`);
          return;
        }
        const data: HHPoint[] = await res.json();
        pointsRef.current = data;
        renderHeat(data);
      } catch (err) {
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
          if (!r.lat || !r.lon || r.score_total === 0) continue;
          const dlat = r.lat - lat;
          const dlon = (r.lon - lng) * Math.cos(lat * Math.PI / 180);
          const d = dlat * dlat + dlon * dlon;
          if (d < bestD) { bestD = d; best = r; }
        }
        if (!best || bestD >= 0.0000035) return;
        const ticket = ++clickTicketRef.current;
        const popup = Lmod.popup({ closeButton: true, maxWidth: 340, maxHeight: 420 })
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

  // Re-render when any filter changes
  useEffect(() => {
    if (pointsRef.current.length) {
      try { renderHeat(pointsRef.current); }
      catch (err) { setFetchError((err as Error).message); }
    }
  }, [showSF, showCX, blkOnly, cutoff, renderHeat]);

  function flyTo(r: HHPoint) {
    const m  = mapObj.current;
    const L2 = Lref.current;
    if (!m || !L2) return;
    m.setView([r.lat, r.lon], 17);
    const ticket = ++clickTicketRef.current;
    const popup = L2.popup({ closeButton: true, maxWidth: 340, maxHeight: 420 })
      .setLatLng([r.lat, r.lon])
      .setContent(popupHtml(r) + "<div style=\"margin-top:8px;font-family:'IBM Plex Mono',monospace;font-size:0.7rem;color:var(--ink-faint);\">Loading voters…</div>")
      .openOn(m);
    fetch(`/api/households/${r.id}`)
      .then(res => res.ok ? res.json() : null)
      .then(hh => { if (ticket === clickTicketRef.current) popup.setContent(popupHtml(r) + voterTableHtml(hh)); })
      .catch(() => {});
  }

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

        {fetching && <div className="map-loading">Loading…</div>}
        {fetchError && (
          <div className="map-loading" style={{ background: "rgba(139,58,58,0.9)", color: "#fff" }}>
            {fetchError}
          </div>
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
          </details>
        </div>

        <details className="panel" open>
          <summary className="panel-summary">How scoring works</summary>
          <div className="scoring-explainer">
            <p><b>Wake-up calls.</b> A reliable voter living with non-voters — pulling them along is the easiest add.</p>
            <p><b>Unaffiliated voters.</b> Registered "blank" — no party — the most persuadable people in the file.</p>
            <p><b>Drop-off Democrats.</b> Registered Dems who stopped showing up. Friendly, just need a nudge.</p>
          </div>
        </details>

        <div className="panel">
          <h3>Top targets in view</h3>
          <div className="top-list">
            {topList.length === 0 ? (
              <div className="top-empty">No scored households in current view.</div>
            ) : topList.map(r => (
              <div key={r.id} className="top-row" onClick={() => flyTo(r)}>
                <span className="top-addr">{r.address_num} {r.street}, {r.city}</span>
                <span className="top-sc">{r.score_total}</span>
              </div>
            ))}
          </div>
        </div>

      </div>
    </div>
  );
}
