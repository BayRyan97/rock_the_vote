"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { createClient } from "@/lib/supabase/client";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleLogin(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    const supabase = createClient();
    const { error } = await supabase.auth.signInWithPassword({ email, password });
    if (error) {
      setError(error.message);
      setLoading(false);
    } else {
      router.push("/search");
      router.refresh();
    }
  }

  return (
    <div style={{
      minHeight: "100vh",
      background: "var(--paper)",
      display: "flex",
      alignItems: "center",
      justifyContent: "center",
      padding: "24px",
    }}>
      <div style={{
        width: "100%",
        maxWidth: "360px",
      }}>
        {/* Header */}
        <div style={{ textAlign: "center", marginBottom: "32px" }}>
          <div style={{
            fontFamily: "'IBM Plex Mono', monospace",
            fontSize: "1.5rem",
            color: "var(--brass)",
            marginBottom: "12px",
            letterSpacing: "0.05em",
          }}>★</div>
          <h1 style={{
            fontFamily: "'Spectral', serif",
            fontWeight: 700,
            fontSize: "1.75rem",
            color: "var(--ink)",
            margin: "0 0 6px",
            letterSpacing: "0.01em",
          }}>Bellwether</h1>
          <p style={{
            fontFamily: "'IBM Plex Mono', monospace",
            fontSize: "0.72rem",
            color: "var(--ink-soft)",
            textTransform: "uppercase",
            letterSpacing: "0.1em",
            margin: 0,
          }}>Long Island Canvass Tool</p>
        </div>

        {/* Card */}
        <div style={{
          background: "var(--paper-raised)",
          border: "1px solid var(--rule-strong)",
          borderRadius: "6px",
          boxShadow: "var(--shadow)",
          padding: "28px 28px 24px",
        }}>
          <form onSubmit={handleLogin} style={{ display: "flex", flexDirection: "column", gap: "18px" }}>
            <div>
              <label style={{
                display: "block",
                fontFamily: "'IBM Plex Mono', monospace",
                fontSize: "0.68rem",
                fontWeight: 500,
                color: "var(--ink-soft)",
                textTransform: "uppercase",
                letterSpacing: "0.08em",
                marginBottom: "6px",
              }}>Email</label>
              <input
                type="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                style={{
                  width: "100%",
                  padding: "10px 12px",
                  fontFamily: "'IBM Plex Mono', monospace",
                  fontSize: "0.85rem",
                  border: "1.5px solid var(--rule-strong)",
                  borderRadius: "3px",
                  background: "var(--paper)",
                  color: "var(--ink)",
                  outline: "none",
                  boxSizing: "border-box",
                }}
                onFocus={e => (e.target.style.borderColor = "var(--brass)")}
                onBlur={e => (e.target.style.borderColor = "var(--rule-strong)")}
              />
            </div>

            <div>
              <label style={{
                display: "block",
                fontFamily: "'IBM Plex Mono', monospace",
                fontSize: "0.68rem",
                fontWeight: 500,
                color: "var(--ink-soft)",
                textTransform: "uppercase",
                letterSpacing: "0.08em",
                marginBottom: "6px",
              }}>Password</label>
              <input
                type="password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                style={{
                  width: "100%",
                  padding: "10px 12px",
                  fontFamily: "'IBM Plex Mono', monospace",
                  fontSize: "0.85rem",
                  border: "1.5px solid var(--rule-strong)",
                  borderRadius: "3px",
                  background: "var(--paper)",
                  color: "var(--ink)",
                  outline: "none",
                  boxSizing: "border-box",
                }}
                onFocus={e => (e.target.style.borderColor = "var(--brass)")}
                onBlur={e => (e.target.style.borderColor = "var(--rule-strong)")}
              />
            </div>

            {error && (
              <div style={{
                fontFamily: "'IBM Plex Mono', monospace",
                fontSize: "0.75rem",
                color: "var(--seal-l)",
                background: "rgba(139,58,58,0.07)",
                border: "1px solid rgba(139,58,58,0.2)",
                borderRadius: "3px",
                padding: "8px 12px",
              }}>{error}</div>
            )}

            <button
              type="submit"
              disabled={loading}
              style={{
                width: "100%",
                padding: "11px",
                fontFamily: "'IBM Plex Mono', monospace",
                fontSize: "0.8rem",
                fontWeight: 500,
                letterSpacing: "0.06em",
                textTransform: "uppercase",
                background: loading ? "var(--ink-soft)" : "var(--ink)",
                color: "var(--paper)",
                border: "none",
                borderRadius: "3px",
                cursor: loading ? "not-allowed" : "pointer",
                transition: "background 0.15s",
                marginTop: "4px",
              }}
              onMouseEnter={e => { if (!loading) (e.currentTarget.style.background = "var(--seal-f)"); }}
              onMouseLeave={e => { if (!loading) (e.currentTarget.style.background = "var(--ink)"); }}
            >
              {loading ? "Signing in…" : "Sign in"}
            </button>
          </form>

          <div style={{ textAlign: "center", marginTop: "16px", display: "flex", justifyContent: "center", gap: "20px" }}>
            <a href="/forgot-password" style={{
              fontFamily: "'IBM Plex Mono', monospace",
              fontSize: "0.72rem",
              color: "var(--ink-soft)",
              textDecoration: "none",
              letterSpacing: "0.02em",
            }}
            onMouseEnter={e => (e.currentTarget.style.color = "var(--ink)")}
            onMouseLeave={e => (e.currentTarget.style.color = "var(--ink-soft)")}
            >Forgot password?</a>
            <span style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "0.72rem", color: "var(--rule-strong)" }}>·</span>
            <a href="/signup" style={{
              fontFamily: "'IBM Plex Mono', monospace",
              fontSize: "0.72rem",
              color: "var(--ink-soft)",
              textDecoration: "none",
              letterSpacing: "0.02em",
            }}
            onMouseEnter={e => (e.currentTarget.style.color = "var(--ink)")}
            onMouseLeave={e => (e.currentTarget.style.color = "var(--ink-soft)")}
            >Create account</a>
          </div>
        </div>

        <p style={{
          textAlign: "center",
          fontFamily: "'IBM Plex Mono', monospace",
          fontSize: "0.65rem",
          color: "var(--ink-faint)",
          marginTop: "20px",
          letterSpacing: "0.04em",
        }}>Know before you knock.</p>
      </div>
    </div>
  );
}
