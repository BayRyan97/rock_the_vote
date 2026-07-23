import { ImageResponse } from "next/og";

export const runtime = "edge";
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

export default function Image() {
  return new ImageResponse(
    (
      <div
        style={{
          display: "flex",
          width: "100%",
          height: "100%",
          backgroundColor: "#1d2840",
          alignItems: "center",
          justifyContent: "center",
          flexDirection: "column",
          fontFamily: "system-ui, -apple-system, sans-serif",
        }}
      >
        {/* Logo mark */}
        <div
          style={{
            display: "flex",
            width: 110,
            height: 110,
            borderRadius: 24,
            backgroundColor: "#253354",
            alignItems: "center",
            justifyContent: "center",
            marginBottom: 36,
          }}
        >
          <div
            style={{
              display: "flex",
              width: 80,
              height: 80,
              borderRadius: 40,
              border: "4px solid #b8882a",
              alignItems: "center",
              justifyContent: "center",
            }}
          >
            <span style={{ color: "#f0e8d0", fontSize: 44, lineHeight: 1, marginTop: -2 }}>
              ✓
            </span>
          </div>
        </div>

        {/* Title */}
        <div
          style={{
            color: "#ffffff",
            fontSize: 68,
            fontWeight: 700,
            letterSpacing: "-1px",
            marginBottom: 14,
          }}
        >
          Rock the Vote
        </div>

        {/* Gold divider */}
        <div
          style={{
            width: 60,
            height: 3,
            backgroundColor: "#b8882a",
            marginBottom: 24,
          }}
        />

        {/* Subtitle */}
        <div
          style={{
            color: "#b8c8e0",
            fontSize: 26,
            fontWeight: 400,
            letterSpacing: "3px",
            textTransform: "uppercase",
            marginBottom: 28,
          }}
        >
          Long Island Campaign Platform
        </div>

        {/* Feature tags */}
        <div
          style={{
            display: "flex",
            gap: 16,
          }}
        >
          {["Voter Outreach", "Election Maps", "Donor Analytics", "Smart Targeting"].map(
            (label) => (
              <div
                key={label}
                style={{
                  display: "flex",
                  backgroundColor: "#253354",
                  color: "#8899bb",
                  fontSize: 18,
                  paddingTop: 8,
                  paddingBottom: 8,
                  paddingLeft: 16,
                  paddingRight: 16,
                  borderRadius: 6,
                  letterSpacing: "0.5px",
                }}
              >
                {label}
              </div>
            )
          )}
        </div>
      </div>
    ),
    { width: 1200, height: 630 }
  );
}
