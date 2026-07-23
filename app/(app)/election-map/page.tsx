"use client";
import dynamic from "next/dynamic";

const ElectionMapView = dynamic(() => import("@/components/ElectionMapView"), {
  ssr: false,
  loading: () => (
    <div style={{ height: "calc(100vh - 160px)", display: "flex", alignItems: "center", justifyContent: "center", background: "#E8E6DF", fontFamily: "IBM Plex Mono, monospace", fontSize: "0.8rem", color: "#8A8377" }}>
      Loading map…
    </div>
  ),
});

export default function ElectionMapPage() {
  return <ElectionMapView />;
}
