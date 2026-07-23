"use client";

import { useState } from "react";
import { createClient } from "@/lib/supabase/client";

export default function SignupPage() {
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (password !== confirm) {
      setError("Passwords do not match.");
      return;
    }
    if (password.length < 8) {
      setError("Password must be at least 8 characters.");
      return;
    }
    setLoading(true);
    setError(null);
    const supabase = createClient();
    const { error } = await supabase.auth.signUp({
      email,
      password,
      options: {
        data: { name },
        emailRedirectTo: `${window.location.origin}/auth/callback`,
      },
    });
    if (error) {
      setError(error.message);
      setLoading(false);
    } else {
      setDone(true);
    }
  }

  const inputStyle = {
    width: "100%",
    padding: "10px 12px",
    fontFamily: "'IBM Plex Mono', monospace",
    fontSize: "0.85rem",
    border: "1.5px solid var(--rule-strong)",
    borderRadius: "3px",
    background: "var(--paper)",
    color: "var(--ink)",
    outline: "none",
    boxSizing: "border-box" as const,
  };

  const labelStyle = {
    display: "block",
    fontFamily: "'IBM Plex Mono', monospace",
    fontSize: "0.68rem",
    fontWeight: 500,
    color: "var(--ink-soft)",
    textTransform: "uppercase" as const,
    letterSpacing: "0.08em",
    marginBottom: "6px",
  };

  return (
    <div style={{
      minHeight: "100vh",
      background: "var(--paper)",
      display: "flex",
      alignItems: "center",
      justifyContent: "center",
      padding: "24px",
    }}>
      <div style={{ width: "100%", maxWidth: "360px" }}>
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
          }}>Create Account</h1>
          <p style={{
            fontFamily: "'IBM Plex Mono', monospace",
            fontSize: "0.72rem",
            color: "var(--ink-soft)",
            textTransform: "uppercase",
            letterSpacing: "0.1em",
            margin: 0,
          }}>Long Island Canvass Tool</p>
        </div>

        <div style={{
          background: "var(--paper-raised)",
          border: "1px solid var(--rule-strong)",
          borderRadius: "6px",
          boxShadow: "var(--shadow)",
          padding: "28px 28px 24px",
        }}>
          {done ? (
            <div style={{ textAlign: "center" }}>
              <div style={{
                fontFamily: "'IBM Plex Mono', monospace",
                fontSize: "1.2rem",
                color: "var(--seal-x)",
                marginBottom: "12px",
              }}>✓</div>
              <p style={{
                fontFamily: "'IBM Plex Mono', monospace",
                fontSize: "0.8rem",
                color: "var(--ink)",
                lineHeight: 1.6,
                margin: "0 0 6px",
              }}>Check your email to confirm your account.</p>
              <p style={{
                fontFamily: "'IBM Plex Mono', monospace",
                fontSize: "0.72rem",
                color: "var(--ink-soft)",
                margin: 0,
              }}>You&apos;ll be signed in automatically after confirming.</p>
            </div>
          ) : (
            <form onSubmit={handleSubmit} style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
              <div>
                <label style={labelStyle}>Full Name</label>
                <input
                  type="text"
                  required
                  value={name}
                  onChange={e => setName(e.target.value)}
                  style={inputStyle}
                  onFocus={e => (e.target.style.borderColor = "var(--brass)")}
                  onBlur={e => (e.target.style.borderColor = "var(--rule-strong)")}
                />
              </div>

              <div>
                <label style={labelStyle}>Email</label>
                <input
                  type="email"
                  required
                  value={email}
                  onChange={e => setEmail(e.target.value)}
                  style={inputStyle}
                  onFocus={e => (e.target.style.borderColor = "var(--brass)")}
                  onBlur={e => (e.target.style.borderColor = "var(--rule-strong)")}
                />
              </div>

              <div>
                <label style={labelStyle}>Password</label>
                <input
                  type="password"
                  required
                  value={password}
                  onChange={e => setPassword(e.target.value)}
                  style={inputStyle}
                  onFocus={e => (e.target.style.borderColor = "var(--brass)")}
                  onBlur={e => (e.target.style.borderColor = "var(--rule-strong)")}
                />
              </div>

              <div>
                <label style={labelStyle}>Confirm Password</label>
                <input
                  type="password"
                  required
                  value={confirm}
                  onChange={e => setConfirm(e.target.value)}
                  style={inputStyle}
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
                {loading ? "Creating account…" : "Create account"}
              </button>
            </form>
          )}
        </div>

        <div style={{ textAlign: "center", marginTop: "16px" }}>
          <a href="/login" style={{
            fontFamily: "'IBM Plex Mono', monospace",
            fontSize: "0.72rem",
            color: "var(--ink-soft)",
            textDecoration: "none",
          }}
          onMouseEnter={e => (e.currentTarget.style.color = "var(--ink)")}
          onMouseLeave={e => (e.currentTarget.style.color = "var(--ink-soft)")}
          >Already have an account? Sign in</a>
        </div>
      </div>
    </div>
  );
}
