"use client";
import "leaflet/dist/leaflet.css";
import { useEffect, useRef, useState } from "react";

const ZIP_CITIES: Record<string, string> = {
  "11001":"Floral Park","11003":"Elmont","11004":"Glen Oaks","11010":"Franklin Square",
  "11020":"Great Neck","11021":"Great Neck","11030":"Manhasset","11040":"New Hyde Park",
  "11050":"Port Washington","11096":"Inwood","11501":"Mineola","11507":"Albertson",
  "11509":"Atlantic Beach","11510":"Baldwin","11514":"Carle Place","11516":"Cedarhurst",
  "11518":"East Rockaway","11520":"Freeport","11530":"Garden City","11542":"Glen Cove",
  "11545":"Glen Head","11547":"Glenwood Landing","11548":"Greenvale","11549":"Hempstead",
  "11550":"Hempstead","11552":"West Hempstead","11553":"Uniondale","11554":"East Meadow",
  "11557":"Hewlett","11558":"Island Park","11559":"Lawrence","11560":"Locust Valley",
  "11561":"Long Beach","11562":"Lynbrook","11565":"Malverne","11566":"Merrick",
  "11568":"Old Westbury","11569":"Point Lookout","11570":"Rockville Centre","11572":"Oceanside",
  "11575":"Roosevelt","11576":"Roslyn","11577":"Roslyn Heights","11579":"Sea Cliff",
  "11580":"Valley Stream","11590":"Westbury","11596":"Williston Park","11598":"Woodmere",
  "11710":"Bellmore","11714":"Bethpage","11716":"Bohemia","11717":"Brentwood",
  "11718":"Brightwaters","11720":"Centereach","11721":"Centerport","11722":"Central Islip",
  "11724":"Cold Spring Harbor","11725":"Commack","11726":"Copiague","11727":"Coram",
  "11729":"Deer Park","11730":"East Islip","11731":"East Northport","11733":"East Setauket",
  "11735":"Farmingdale","11738":"Farmingville","11740":"Greenlawn","11741":"Holbrook",
  "11742":"Holtsville","11743":"Huntington","11746":"Huntington Station","11747":"Melville",
  "11749":"Islandia","11751":"Islip","11753":"Jericho","11754":"Kings Park",
  "11755":"Lake Grove","11756":"Levittown","11757":"Lindenhurst","11758":"Massapequa",
  "11762":"Massapequa Park","11763":"Medford","11764":"Miller Place","11766":"Mount Sinai",
  "11767":"Nesconset","11768":"Northport","11772":"Patchogue","11776":"Port Jefferson Station",
  "11777":"Port Jefferson","11778":"Rocky Point","11779":"Ronkonkoma","11780":"Saint James",
  "11782":"Sayville","11783":"Seaford","11784":"Selden","11787":"Smithtown",
  "11789":"Sound Beach","11790":"Stony Brook","11791":"Syosset","11793":"Wantagh",
  "11795":"West Islip","11797":"Woodbury","11798":"Wyandanch","11801":"Hicksville",
  "11803":"Plainview","11804":"Old Bethpage","11901":"Riverhead","11930":"Amagansett",
  "11933":"Calverton","11934":"Center Moriches","11936":"East Hampton","11940":"East Moriches",
  "11941":"Eastport","11946":"Hampton Bays","11949":"Manorville","11950":"Mastic",
  "11951":"Mastic Beach","11953":"Middle Island","11954":"Montauk","11963":"Sag Harbor",
  "11967":"Shirley","11968":"Southampton","11971":"Southold","11978":"Westhampton Beach",
  "11980":"Yaphank",
};

function scoreColor(score: number | null): string {
  if (score == null || score === 0) return "hsl(145,15%,88%)";
  const t = score / 100;
  const l = Math.round(80 - t * 58);
  const s = Math.round(40 + t * 30);
  return `hsl(145,${s}%,${l}%)`;
}

export default function GreenMapView() {
  const mapRef = useRef<HTMLDivElement>(null);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const mapObj = useRef<any>(null);
  const [minScore, setMinScore] = useState(0);
  const minScoreRef = useRef(0);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const geoLayerRef = useRef<any>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const scoresRef = useRef<Record<string, number>>({});
  const countsRef = useRef<Record<string, number>>({});

  function zipStyle(zip: string, min: number) {
    const score = scoresRef.current[zip] ?? null;
    const hidden = score === null || score < min;
    return {
      fillColor: hidden ? "#e0ddd8" : scoreColor(score),
      fillOpacity: hidden ? 0.2 : 0.72,
      color: "#9aaa99",
      weight: 0.8,
      opacity: hidden ? 0.25 : 0.7,
    };
  }

  function refreshStyles(min: number) {
    if (!geoLayerRef.current) return;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    geoLayerRef.current.eachLayer((layer: any) => {
      const zip = layer.feature?.properties?.zip;
      if (zip) layer.setStyle(zipStyle(zip, min));
    });
  }

  useEffect(() => {
    if (mapObj.current || !mapRef.current) return;
    let cancelled = false;

    import("leaflet").then(async (Lmod) => {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const L = Lmod as any;
      if (cancelled || !mapRef.current) return;

      const m = L.map(mapRef.current, {
        preferCanvas: true, minZoom: 9, maxZoom: 17,
      }).setView([40.79, -73.55], 11);

      L.tileLayer("https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png", {
        attribution: "© OpenStreetMap © CARTO", subdomains: "abcd", maxZoom: 20,
      }).addTo(m);

      // Legend
      const legend = L.control({ position: "bottomright" });
      legend.onAdd = () => {
        const div = L.DomUtil.create("div");
        div.style.cssText = "background:rgba(245,241,230,0.96);border:1px solid #ccc;border-radius:6px;padding:10px 14px;font-family:'IBM Plex Mono',monospace;font-size:0.70rem;color:#5B6470;min-width:150px;box-shadow:0 2px 8px rgba(0,0,0,.12)";
        div.innerHTML = `
          <div style="font-weight:600;color:#1E2A3A;margin-bottom:7px">Env Zone Score</div>
          <div style="height:10px;border-radius:3px;background:linear-gradient(to right,#d8f0e6,#1a5c35);margin-bottom:4px"></div>
          <div style="display:flex;justify-content:space-between;font-size:0.63rem;color:#8B8F94"><span>0</span><span>50</span><span>100</span></div>
          <div style="margin-top:8px;display:flex;align-items:center;gap:6px"><div style="width:12px;height:12px;background:#ccc;border-radius:2px;flex-shrink:0"></div><span>Below min / no data</span></div>
          <div style="margin-top:8px;font-size:0.63rem;color:#8B8F94;line-height:1.4">Based on EV registrations<br>per zip (NY Open Data)</div>
        `;
        return div;
      };
      legend.addTo(m);

      mapObj.current = m;

      try {
        const [evRes, geoRes] = await Promise.all([
          fetch("/api/map/ev-scores"),
          fetch("/geojson/nassau_suffolk_zips.geojson"),
        ]);
        if (!evRes.ok || !geoRes.ok) throw new Error("Failed to load data");
        const evData = await evRes.json();
        const geo = await geoRes.json();

        if (cancelled) return;
        scoresRef.current = evData.scores ?? {};
        countsRef.current = evData.counts ?? {};

        const min = minScoreRef.current;
        const geoLayer = L.geoJSON(geo as GeoJSON.FeatureCollection, {
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          style: (feat: any) => zipStyle(feat?.properties?.zip, min),
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          onEachFeature: (feat: any, layer: any) => {
            const zip = feat?.properties?.zip;
            layer.on("click", () => {
              const score = scoresRef.current[zip] ?? null;
              const count = countsRef.current[zip] ?? null;
              const city = ZIP_CITIES[zip] || "";
              const scoreBadge = score !== null
                ? `<div style="display:inline-block;font-family:'IBM Plex Mono',monospace;font-size:0.75rem;font-weight:600;background:#e8f5ee;color:#1a5c35;border:1px solid #aad9be;border-radius:4px;padding:2px 8px;margin-bottom:6px">Env Zone ${score}/100</div>`
                : `<div style="display:inline-block;font-family:'IBM Plex Mono',monospace;font-size:0.75rem;background:#f5f5f5;color:#888;border:1px solid #ddd;border-radius:4px;padding:2px 8px;margin-bottom:6px">No EV data</div>`;
              layer.bindPopup(`
                <div style="font-family:'Inter',sans-serif;font-size:0.82rem;min-width:200px">
                  <div style="font-family:'IBM Plex Mono',monospace;font-size:1.1rem;font-weight:600;margin-bottom:2px">${zip}</div>
                  <div style="color:#5B6470;font-size:0.78rem;margin-bottom:10px">${city}</div>
                  ${scoreBadge}
                  ${count !== null ? `<div style="color:#5B6470;font-size:0.77rem;margin-bottom:10px">${count.toLocaleString()} EV${count === 1 ? "" : "s"} registered</div>` : ""}
                </div>
              `, { maxWidth: 280 }).openPopup();
            });
            layer.on("mouseover", function(this: typeof layer) {
              const s = scoresRef.current[zip] ?? null;
              if (s !== null && s >= minScoreRef.current) {
                this.setStyle({ weight: 2, color: "#1a5c35", fillOpacity: 0.88 });
              }
            });
            layer.on("mouseout", function(this: typeof layer) {
              geoLayer.resetStyle(this);
              this.setStyle(zipStyle(zip, minScoreRef.current));
            });
          },
        }).addTo(m);

        geoLayerRef.current = geoLayer;
        setLoading(false);
      } catch (err) {
        if (!cancelled) setError((err as Error).message);
        setLoading(false);
      }
    });

    return () => {
      cancelled = true;
      if (mapObj.current) { mapObj.current.remove(); mapObj.current = null; }
    };
  }, []);

  function handleSlider(v: number) {
    minScoreRef.current = v;
    setMinScore(v);
    refreshStyles(v);
  }

  const NAV_H = 120;
  const CTRL_H = 44;

  return (
    <div style={{ display: "flex", flexDirection: "column", height: `calc(100vh - ${NAV_H}px)` }}>
      {/* Controls bar */}
      <div style={{
        flexShrink: 0, height: CTRL_H, background: "#F5F1E6", borderBottom: "1px solid #D9D0BC",
        display: "flex", alignItems: "center", gap: 18, padding: "0 20px", flexWrap: "wrap",
      }}>
        <label style={{ fontFamily: "'IBM Plex Mono',monospace", fontSize: "0.73rem", color: "#5B6470", display: "flex", alignItems: "center", gap: 8, whiteSpace: "nowrap" }}>
          Min env zone
          <input type="range" min={0} max={100} value={minScore} step={5}
            style={{ width: 100, accentColor: "#1a5c35" }}
            onChange={e => handleSlider(parseInt(e.target.value))} />
          <span style={{ fontFamily: "'IBM Plex Mono',monospace", fontSize: "0.73rem", color: "#1E2A3A", fontWeight: 600, minWidth: 30 }}>
            {minScore === 0 ? "any" : `${minScore}+`}
          </span>
        </label>
        <span style={{ fontFamily: "'IBM Plex Mono',monospace", fontSize: "0.68rem", color: "#8B8F94", marginLeft: "auto" }}>
          EV registrations per zip (NY Open Data) · higher = stronger environmental lean
        </span>
      </div>

      {/* Map */}
      <div style={{ position: "relative", flex: 1, minHeight: 0 }}>
        <div ref={mapRef} style={{ width: "100%", height: "100%" }} />
        {loading && (
          <div style={{
            position: "absolute", inset: 0, background: "rgba(245,241,230,0.85)",
            display: "flex", alignItems: "center", justifyContent: "center",
            zIndex: 2000, fontFamily: "'IBM Plex Mono',monospace", fontSize: "0.85rem", color: "#5B6470", gap: 10,
          }}>
            <span style={{ width: 18, height: 18, border: "2px solid #D9D0BC", borderTopColor: "#1a5c35", borderRadius: "50%", animation: "spin 0.7s linear infinite", display: "inline-block" }} />
            Loading zip boundaries…
          </div>
        )}
        {error && (
          <div style={{ position: "absolute", inset: 0, background: "rgba(245,241,230,0.9)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 2000, color: "#c0392b", fontFamily: "'IBM Plex Mono',monospace" }}>
            Error: {error}
          </div>
        )}
      </div>
    </div>
  );
}
