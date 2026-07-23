"use client";
import dynamic from "next/dynamic";

const GreenMapView = dynamic(() => import("@/components/GreenMapView"), {
  ssr: false,
  loading: () => (
    <div style={{ height: "calc(100vh - 160px)", display: "flex", alignItems: "center", justifyContent: "center", background: "#E8E6DF", fontFamily: "IBM Plex Mono, monospace", fontSize: "0.8rem", color: "#8A8377" }}>
      Loading map…
    </div>
  ),
});

export default function GreenMapPage() {
  return <GreenMapView />;
}
