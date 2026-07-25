"use client";
import "leaflet/dist/leaflet.css";
import { useEffect, useRef, useState, useCallback } from "react";

type Race   = "assembly" | "senate" | "congressional";
type Metric = "dem_pct" | "margin" | "swing" | "reg_gap" | "dropoff";

const RACE_LABELS: Record<Race, string> = {
  assembly: "Assembly District",
  senate: "Senate District",
  congressional: "Congressional District",
};
const GEO_FILES: Record<Race, string> = {
  assembly:      "/geojson/li_assembly_districts.geojson",
  senate:        "/geojson/li_senate_districts.geojson",
  congressional: "/geojson/li_congressional_districts.geojson",
};
const YEARS = ["2024", "2022", "2020"] as const;

// HSL color interpolation
function lerp(a: number, b: number, t: number) { return a + (b - a) * t; }
function lerpColor(t: number, stops: {t:number;h:number;s:number;l:number}[]) {
  t = Math.max(0, Math.min(1, t));
  for (let i = 0; i < stops.length - 1; i++) {
    const a = stops[i], b = stops[i+1];
    if (t >= a.t && t <= b.t) {
      const f = (t - a.t) / (b.t - a.t);
      return `hsl(${Math.round(lerp(a.h,b.h,f))},${Math.round(lerp(a.s,b.s,f))}%,${Math.round(lerp(a.l,b.l,f))}%)`;
    }
  }
  return "#ccc";
}

const DEM_STOPS = [
  {t:0,   h:0,   s:65, l:35},
  {t:0.45,h:280, s:45, l:55},
  {t:0.65,h:0,   s:0,  l:55},
  {t:1,   h:213, s:70, l:30},
];
const SWING_STOPS = [
  {t:0,  h:0,  s:65, l:40},
  {t:0.5,h:0,  s:0,  l:65},
  {t:1,  h:145,s:55, l:30},
];
const DROPOFF_STOPS = [
  {t:0,  h:213,s:10,l:80},
  {t:0.5,h:35, s:80,l:55},
  {t:1,  h:0,  s:65,l:30},
];

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type ElecData = Record<string, Record<string, Record<string, any>>>;
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type MetricsData = Record<string, Record<string, any>>;

function computeColor(
  er: ElecData, dm: MetricsData,
  race: Race, district: string, year: string, metric: Metric
): string | null {
  const res = er[race]?.[year]?.[district];
  const met = dm[race]?.[district];

  if (metric === "dem_pct") {
    if (!res || res.total_votes === 0) {
      if (met) return lerpColor(Math.max(0,Math.min(1,(50 + met.registration_gap/2 - 30)/40)), DEM_STOPS);
      return null;
    }
    return lerpColor(Math.max(0,Math.min(1,(res.dem_pct - 30)/40)), DEM_STOPS);
  }
  if (metric === "margin") {
    if (!res || res.total_votes === 0) return null;
    return lerpColor(Math.max(0,Math.min(1,(res.margin_pct + 20)/40)), DEM_STOPS);
  }
  if (metric === "swing") {
    const priorYear = year === "2024" ? "2022" : year === "2022" ? "2020" : null;
    if (!priorYear) return "#bbb";
    const prior = er[race]?.[priorYear]?.[district];
    if (!res || !prior || res.total_votes === 0 || prior.total_votes === 0) return null;
    return lerpColor(Math.max(0,Math.min(1,(res.dem_pct - prior.dem_pct + 10)/20)), SWING_STOPS);
  }
  if (metric === "reg_gap") {
    if (!met) return null;
    return lerpColor(Math.max(0,Math.min(1,(met.registration_gap + 20)/40)), DEM_STOPS);
  }
  if (metric === "dropoff") {
    if (!met) return null;
    return lerpColor(Math.max(0,Math.min(1, met.dropoff_dem_count / 2000)), DROPOFF_STOPS);
  }
  return null;
}

function isTossup(er: ElecData, race: Race, district: string, year: string): boolean {
  const res = er[race]?.[year]?.[district];
  if (!res || res.total_votes === 0) return false;
  return Math.abs(res.margin_pct) <= 5;
}

function makePopup(
  er: ElecData, dm: MetricsData,
  race: Race, district: string, year: string
): string {
  const label = RACE_LABELS[race];
  const met = dm[race]?.[district];
  const tossupNow = isTossup(er, race, district, year);
  const fmtP = (v: number|null) => v != null ? v.toFixed(1) + "%" : "—";
  const fmtN = (v: number|null) => v != null ? v.toLocaleString() : "—";
  const fmtPts = (v: number|null, signed = false) => v == null ? "—" : (signed && v > 0 ? "+" : "") + v.toFixed(1) + " pt";

  let html = `<div style="font-family:'Inter',sans-serif;font-size:0.82rem;color:#1E2A3A;min-width:240px;max-width:300px">`;
  html += `<div style="font-family:'IBM Plex Mono',monospace;font-size:0.95rem;font-weight:600;margin-bottom:2px">${label} ${district}</div>`;
  html += `<div style="color:#5B6470;font-size:0.75rem;margin-bottom:8px">Long Island</div>`;

  if (tossupNow) {
    html += `<span style="display:inline-block;font-family:'IBM Plex Mono',monospace;font-size:0.65rem;color:#6b3a8c;border:1px dashed #6b3a8c;border-radius:3px;padding:2px 6px;margin-bottom:8px">⚡ Toss-up ≤5 pt</span>`;
  }

  // Election table
  let hasResults = false;
  let table = `<div style="font-family:'IBM Plex Mono',monospace;font-size:0.65rem;color:#8B8F94;text-transform:uppercase;letter-spacing:.04em;margin-bottom:4px">Election Results</div>`;
  table += `<table style="width:100%;border-collapse:collapse;margin-bottom:10px"><thead><tr>
    <th style="font-family:'IBM Plex Mono',monospace;font-size:0.65rem;color:#8B8F94;padding:2px 4px;text-align:left">Year</th>
    <th style="font-family:'IBM Plex Mono',monospace;font-size:0.65rem;color:#8B8F94;padding:2px 4px;text-align:left">Dem</th>
    <th style="font-family:'IBM Plex Mono',monospace;font-size:0.65rem;color:#8B8F94;padding:2px 4px;text-align:left">Rep</th>
    <th style="font-family:'IBM Plex Mono',monospace;font-size:0.65rem;color:#8B8F94;padding:2px 4px;text-align:left">Margin</th>
  </tr></thead><tbody>`;

  for (const yr of YEARS) {
    const r = er[race]?.[yr]?.[district];
    if (!r || r.total_votes === 0) {
      table += `<tr style="border-bottom:1px solid #e8e4da"><td style="padding:3px 4px;font-family:'IBM Plex Mono',monospace;font-size:0.68rem;color:#5B6470;width:36px">${yr}</td>
        <td colspan="3" style="padding:3px 4px;font-size:0.70rem;color:#8B8F94">no data</td></tr>`;
    } else {
      hasResults = true;
      const repPct = r.total_votes > 0 ? ((r.rep_votes / r.total_votes) * 100).toFixed(1) : "—";
      const mSign = r.margin_pct >= 0 ? "+" : "";
      const winDem = r.winner === "DEM";
      table += `<tr style="border-bottom:1px solid #e8e4da">
        <td style="padding:3px 4px;font-family:'IBM Plex Mono',monospace;font-size:0.68rem;color:#5B6470;width:36px">${yr}</td>
        <td style="padding:3px 4px;font-size:0.78rem;font-weight:600;color:${winDem ? "#1a3a8c" : "inherit"}">${fmtP(r.dem_pct)}</td>
        <td style="padding:3px 4px;font-size:0.78rem;font-weight:600;color:${!winDem ? "#8c1a1a" : "inherit"}">${repPct}%</td>
        <td style="padding:3px 4px;font-size:0.70rem;color:${winDem ? "#1a3a8c" : "#8c1a1a"}">${winDem ? "▲" : "▽"} ${mSign}${r.margin_pct.toFixed(1)} pt</td>
      </tr>`;
      if (r.dem_candidate || r.rep_candidate) {
        table += `<tr><td></td><td colspan="3" style="padding:1px 4px 5px;font-size:0.68rem;color:#5B6470">${r.dem_candidate||"—"} vs ${r.rep_candidate||"—"}</td></tr>`;
      }
    }
  }
  table += `</tbody></table>`;
  if (hasResults) html += table;

  // Swing
  const r24 = er[race]?.[year]?.[district];
  const priorYear = year === "2024" ? "2022" : year === "2022" ? "2020" : null;
  const rPrior = priorYear ? er[race]?.[priorYear]?.[district] : null;
  if (r24 && rPrior && r24.total_votes > 0 && rPrior.total_votes > 0) {
    const swing = r24.dem_pct - rPrior.dem_pct;
    const swingStr = (swing >= 0 ? "▲ +" : "▽ ") + swing.toFixed(1) + " pt Dem";
    html += `<div style="display:flex;justify-content:space-between;font-size:0.76rem;margin-bottom:3px;margin-top:4px">
      <span style="color:#5B6470">Swing ${priorYear}→${year}</span>
      <span style="font-weight:600;color:${swing > 0 ? "#1a3a8c" : "#8c1a1a"}">${swingStr}</span>
    </div>`;
  }

  if (met) {
    html += `<hr style="border:none;border-top:1px solid #D9D0BC;margin:8px 0">`;
    html += `<div style="font-family:'IBM Plex Mono',monospace;font-size:0.65rem;color:#8B8F94;text-transform:uppercase;letter-spacing:.04em;margin-bottom:4px">Party Registration</div>`;
    const statRow = (label: string, val: string, color?: string) =>
      `<div style="display:flex;justify-content:space-between;font-size:0.76rem;margin-bottom:3px">
        <span style="color:#5B6470">${label}</span>
        <span style="font-weight:600${color ? ";color:"+color : ""}">${val}</span>
      </div>`;
    html += statRow("Democratic", fmtP(met.dem_pct), "#1a3a8c");
    html += statRow("Republican", fmtP(met.rep_pct), "#8c1a1a");
    html += statRow("Unaffiliated/Blank", fmtP(met.blk_pct));
    html += statRow("Registration lean",
      `${met.registration_gap >= 0 ? "DEM" : "REP"} ${fmtPts(Math.abs(met.registration_gap))}`,
      met.registration_gap >= 0 ? "#1a3a8c" : "#8c1a1a"
    );
    html += `<hr style="border:none;border-top:1px solid #D9D0BC;margin:8px 0">`;
    html += `<div style="font-family:'IBM Plex Mono',monospace;font-size:0.65rem;color:#8B8F94;text-transform:uppercase;letter-spacing:.04em;margin-bottom:4px">Voter Engagement</div>`;
    html += statRow("Total voters", fmtN(met.total_voters));
    html += statRow("Reliable voters", fmtP(met.reliable_pct));
    html += statRow("Drop-off Dems", fmtN(met.dropoff_dem_count), "#8c1a1a");
    html += statRow("Avg engagement gap", met.avg_engagement_gap?.toFixed(3) ?? "—");
  }

  html += `</div>`;
  return html;
}

export default function ElectionMapView() {
  const mapRef = useRef<HTMLDivElement>(null);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const mapObj = useRef<any>(null);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const Lref   = useRef<any>(null);
  const [race, setRace_]     = useState<Race>("assembly");
  const [year, setYear_]     = useState("2024");
  const [metric, setMetric_] = useState<Metric>("dem_pct");
  const raceRef   = useRef<Race>("assembly");
  const yearRef   = useRef("2024");
  const metricRef = useRef<Metric>("dem_pct");
  const erRef  = useRef<ElecData>({});
  const dmRef  = useRef<MetricsData>({});
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const geoDataRef   = useRef<Record<string, any>>({});
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const geoLayersRef = useRef<Record<string, any>>({});
  const [loading, setLoading] = useState(true);
  const [loadMsg, setLoadMsg] = useState("Loading…");
  const [tossupCount, setTossupCount] = useState(0);
  const legendRef = useRef<HTMLDivElement | null>(null);

  const updateTossupCount = useCallback(() => {
    const er = erRef.current;
    const race = raceRef.current;
    const year = yearRef.current;
    const districts = er[race]?.[year] ?? {};
    const n = Object.values(districts).filter((r: { total_votes: number; margin_pct: number }) => r.total_votes > 0 && Math.abs(r.margin_pct) <= 5).length;
    setTossupCount(n);
  }, []);

  const updateLegend = useCallback(() => {
    const div = legendRef.current;
    if (!div) return;
    const metric = metricRef.current;
    const year = yearRef.current;
    let grad: string, left: string, mid: string, right: string, title: string;
    if (metric === "dem_pct" || metric === "margin") {
      title = metric === "dem_pct" ? "Dem % of vote" : "Margin (Dem−Rep)";
      grad  = "linear-gradient(to right,hsl(0,65%,35%),hsl(280,45%,55%),hsl(213,70%,30%))";
      left  = metric === "dem_pct" ? "≤35% Dem" : "−20 pt";
      mid   = metric === "dem_pct" ? "50%" : "0";
      right = metric === "dem_pct" ? "≥65% Dem" : "+20 pt";
    } else if (metric === "swing") {
      const priorY = year === "2024" ? "2022" : year === "2022" ? "2020" : "—";
      title = `Swing ${priorY}→${year}`;
      grad  = "linear-gradient(to right,hsl(0,65%,40%),hsl(0,0%,65%),hsl(145,55%,30%))";
      left = "Rep +10"; mid = "Flat"; right = "Dem +10";
    } else if (metric === "reg_gap") {
      title = "Registration lean";
      grad  = "linear-gradient(to right,hsl(0,65%,35%),hsl(280,45%,55%),hsl(213,70%,30%))";
      left = "REP+20"; mid = "Even"; right = "DEM+20";
    } else {
      title = "Drop-off Dems";
      grad  = "linear-gradient(to right,hsl(213,10%,80%),hsl(35,80%,55%),hsl(0,65%,30%))";
      left = "Low"; mid = "1,000"; right = "2,000+";
    }
    div.innerHTML = `
      <div style="font-weight:600;color:#1E2A3A;margin-bottom:7px;font-size:0.72rem">${title}</div>
      <div style="height:10px;border-radius:3px;background:${grad};margin-bottom:4px"></div>
      <div style="display:flex;justify-content:space-between;font-size:0.63rem;color:#8B8F94;margin-bottom:8px"><span>${left}</span><span>${mid}</span><span>${right}</span></div>
      <div style="display:flex;align-items:center;gap:6px;margin-top:5px;font-size:0.65rem">
        <div style="width:20px;height:12px;border:2px dashed #6b3a8c;background:transparent;border-radius:2px;flex-shrink:0"></div>
        <span>Toss-up (≤5 pt)</span>
      </div>
      <div style="display:flex;align-items:center;gap:6px;margin-top:5px;font-size:0.65rem">
        <div style="width:20px;height:10px;background:#ccc;border-radius:2px;flex-shrink:0"></div>
        <span>No data</span>
      </div>
    `;
  }, []);

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  function districtStyle(feature: any) {
    const district = String(feature.properties.district);
    const color = computeColor(erRef.current, dmRef.current, raceRef.current, district, yearRef.current, metricRef.current);
    const tossup = isTossup(erRef.current, raceRef.current, district, yearRef.current);
    return {
      fillColor: color ?? "#d0ccc4",
      fillOpacity: color ? 0.72 : 0.25,
      color: tossup ? "#6b3a8c" : "#aaa8a0",
      weight: tossup ? 2.5 : 0.9,
      dashArray: tossup ? "6 3" : undefined,
      opacity: tossup ? 0.9 : 0.6,
    };
  }

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  function refreshStyles(layer: any) {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    layer.eachLayer((sub: any) => sub.setStyle(districtStyle(sub.feature)));
    updateLegend();
    updateTossupCount();
  }

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  function buildLayer(L: any, race: Race, geo: any) {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const layer = L.geoJSON(geo, {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      style: (f: any) => districtStyle(f),
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      onEachFeature: (feat: any, sub: any) => {
        const district = String(feat.properties.district);
        sub.on("click", () => {
          sub.bindPopup(makePopup(erRef.current, dmRef.current, race, district, yearRef.current), { maxWidth: 320 }).openPopup();
        });
        sub.on("mouseover", function(this: typeof sub) {
          this.setStyle({ weight: 2.5, color: "#333", fillOpacity: 0.88 });
          this.bringToFront();
        });
        sub.on("mouseout", function(this: typeof sub) {
          layer.resetStyle(this);
          this.setStyle(districtStyle(feat));
        });
      },
    });
    return layer;
  }

  const switchRace = useCallback(async (newRace: Race) => {
    const L = Lref.current;
    const m = mapObj.current;
    if (!L || !m) return;

    setLoading(true);
    setLoadMsg(`Loading ${RACE_LABELS[newRace]} boundaries…`);

    if (!geoDataRef.current[newRace]) {
      try {
        const res = await fetch(GEO_FILES[newRace]);
        geoDataRef.current[newRace] = await res.json();
      } catch {
        setLoadMsg(`Failed to load ${newRace} boundaries`);
        setLoading(false);
        return;
      }
    }

    // Remove old active layer
    Object.values(geoLayersRef.current).forEach((l: unknown) => { try { m.removeLayer(l); } catch { /**/ } });

    const newLayer = buildLayer(L, newRace, geoDataRef.current[newRace]);
    geoLayersRef.current[newRace] = newLayer;
    newLayer.addTo(m);

    // Fit bounds on first load for this race
    const bounds = newLayer.getBounds();
    if (bounds.isValid()) m.fitBounds(bounds, { padding: [20, 20] });

    setLoading(false);
    refreshStyles(newLayer);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Apply style change without reloading geo
  const applyStyleChange = useCallback(() => {
    const layer = geoLayersRef.current[raceRef.current];
    if (layer) refreshStyles(layer);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const setRace   = (v: Race)   => { raceRef.current   = v; setRace_(v);   switchRace(v); };
  const setYear   = (v: string) => { yearRef.current   = v; setYear_(v);   applyStyleChange(); };
  const setMetric = (v: Metric) => { metricRef.current = v; setMetric_(v); applyStyleChange(); };

  useEffect(() => {
    if (mapObj.current || !mapRef.current) return;
    let cancelled = false;

    import("leaflet").then(async (Lmod) => {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const L = Lmod as any;
      if (cancelled || !mapRef.current) return;
      Lref.current = L;

      const m = L.map(mapRef.current, { preferCanvas: true, minZoom: 8, maxZoom: 17 })
        .setView([40.82, -73.10], 10);

      L.tileLayer("https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png", {
        attribution: "© OpenStreetMap © CARTO", subdomains: "abcd", maxZoom: 20,
      }).addTo(m);

      const legendCtrl = L.control({ position: "bottomright" });
      legendCtrl.onAdd = () => {
        const div = L.DomUtil.create("div");
        div.style.cssText = "background:rgba(245,241,230,0.96);border:1px solid #ccc;border-radius:6px;padding:10px 14px;font-family:'IBM Plex Mono',monospace;font-size:0.70rem;color:#5B6470;min-width:160px;box-shadow:0 2px 8px rgba(0,0,0,.12)";
        legendRef.current = div;
        return div;
      };
      legendCtrl.addTo(m);
      mapObj.current = m;

      // Fetch election results first (fast — small table), then render the map
      try {
        const res = await fetch("/api/map/election-data");
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        if (!cancelled) {
          erRef.current = data.election_results ?? {};
          dmRef.current = data.district_metrics ?? {};
        }
      } catch { /* render map without data */ }

      if (!cancelled) {
        updateLegend();
        switchRace("assembly");
      }

      // Fetch district metrics in the background (slow JOIN — 20 s+)
      // When it arrives, refresh district styles and popup data
      fetch("/api/map/election-data", { method: "POST" })
        .then(r => r.ok ? r.json() : null)
        .then(metrics => {
          if (!cancelled && metrics) {
            dmRef.current = metrics;
            const layer = geoLayersRef.current[raceRef.current];
            if (layer) refreshStyles(layer);
          }
        })
        .catch(() => {});
    });

    return () => {
      cancelled = true;
      if (mapObj.current) { mapObj.current.remove(); mapObj.current = null; }
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const btnStyle = (active: boolean): React.CSSProperties => ({
    fontFamily: "'IBM Plex Mono',monospace", fontSize: "0.70rem",
    padding: "4px 10px", border: "none",
    background: active ? "#1E2A3A" : "#F5F1E6", color: active ? "#F5F1E6" : "#5B6470",
    cursor: "pointer", borderRight: "1px solid #D9D0BC", whiteSpace: "nowrap",
    transition: "background 0.12s, color 0.12s",
  });
  const labelStyle: React.CSSProperties = {
    fontFamily: "'IBM Plex Mono',monospace", fontSize: "0.70rem", color: "#5B6470", whiteSpace: "nowrap",
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "calc(100vh - 120px)" }}>
      {/* Controls bar */}
      <div style={{
        flexShrink: 0, background: "#F5F1E6", borderBottom: "1px solid #D9D0BC",
        display: "flex", alignItems: "center", gap: 16, padding: "7px 20px", flexWrap: "wrap", zIndex: 999,
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <span style={labelStyle}>Race</span>
          <div style={{ display: "flex", border: "1px solid #D9D0BC", borderRadius: 4, overflow: "hidden" }}>
            {(["assembly","senate","congressional"] as Race[]).map(r => (
              <button key={r} style={btnStyle(race === r)} onClick={() => setRace(r)}>
                {r.charAt(0).toUpperCase() + r.slice(1)}
              </button>
            ))}
          </div>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <span style={labelStyle}>Year</span>
          <div style={{ display: "flex", border: "1px solid #D9D0BC", borderRadius: 4, overflow: "hidden" }}>
            {YEARS.map(y => (
              <button key={y} style={btnStyle(year === y)} onClick={() => setYear(y)}>{y}</button>
            ))}
          </div>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <span style={labelStyle}>Show</span>
          <select
            value={metric}
            onChange={e => setMetric(e.target.value as Metric)}
            style={{ fontFamily: "'IBM Plex Mono',monospace", fontSize: "0.70rem", color: "#1E2A3A", background: "#F5F1E6", border: "1px solid #D9D0BC", borderRadius: 4, padding: "4px 8px", cursor: "pointer" }}
          >
            <option value="dem_pct">Dem % of vote</option>
            <option value="margin">Margin (Dem − Rep)</option>
            <option value="swing">Swing from prior cycle</option>
            <option value="reg_gap">Registration lean</option>
            <option value="dropoff">Drop-off Dem count</option>
          </select>
        </div>
        {tossupCount > 0 && (
          <span style={{ fontFamily: "'IBM Plex Mono',monospace", fontSize: "0.65rem", color: "#6b3a8c", border: "1px dashed #6b3a8c", borderRadius: 3, padding: "3px 8px", whiteSpace: "nowrap" }}>
            {tossupCount} toss-up{tossupCount !== 1 ? "s" : ""}
          </span>
        )}
      </div>

      {/* Map */}
      <div style={{ position: "relative", flex: 1, minHeight: 0 }}>
        <div ref={mapRef} style={{ width: "100%", height: "100%" }} />
        {loading && (
          <div style={{
            position: "absolute", inset: 0, background: "rgba(245,241,230,0.85)",
            display: "flex", alignItems: "center", justifyContent: "center",
            zIndex: 2000, fontFamily: "'IBM Plex Mono',monospace", fontSize: "0.85rem", color: "#5B6470", gap: 10,
            pointerEvents: "none",
          }}>
            <span style={{ width: 18, height: 18, border: "2px solid #D9D0BC", borderTopColor: "#1a3a8c", borderRadius: "50%", animation: "spin 0.7s linear infinite", display: "inline-block" }} />
            {loadMsg}
          </div>
        )}
      </div>
    </div>
  );
}
