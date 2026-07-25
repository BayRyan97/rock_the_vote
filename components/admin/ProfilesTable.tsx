"use client";
import { useState } from "react";
import { UserRole } from "@/lib/supabase/types";

export interface ProfileRow {
  id: string;
  name: string | null;
  email: string | null;
  role: UserRole;
  created_at: string;
}

const ROLES: UserRole[] = ["admin", "canvasser", "dfli", "running"];

const ROLE_LABELS: Record<UserRole, string> = {
  admin: "Admin",
  canvasser: "Canvasser",
  dfli: "DFLI",
  running: "Running",
};

export default function ProfilesTable({ initialProfiles, currentUserId }: {
  initialProfiles: ProfileRow[];
  currentUserId: string;
}) {
  const [profiles, setProfiles] = useState(initialProfiles);
  const [saving, setSaving] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function handleRoleChange(id: string, role: UserRole) {
    setSaving(id);
    setError(null);
    setProfiles(prev => prev.map(p => p.id === id ? { ...p, role } : p));

    const res = await fetch("/api/admin/profiles", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id, role }),
    });

    if (!res.ok) {
      const { error: msg } = await res.json();
      setError(msg ?? "Failed to update role.");
      const orig = initialProfiles.find(p => p.id === id);
      if (orig) setProfiles(prev => prev.map(p => p.id === id ? orig : p));
    }
    setSaving(null);
  }

  return (
    <div>
      {error && (
        <p style={{ color: "var(--seal-l)", fontSize: 13, marginBottom: 12 }}>{error}</p>
      )}
      <table className="roll" style={{ tableLayout: "fixed" }}>
        <colgroup>
          <col style={{ width: "22%" }} />
          <col style={{ width: "30%" }} />
          <col style={{ width: "20%" }} />
          <col style={{ width: "16%" }} />
        </colgroup>
        <thead>
          <tr>
            <th>Name</th>
            <th>Email</th>
            <th>Role</th>
            <th>Joined</th>
          </tr>
        </thead>
        <tbody>
          {profiles.map((p) => (
            <tr key={p.id}>
              <td style={{ color: "var(--ink)" }}>{p.name ?? <span style={{ color: "var(--ink-faint)" }}>—</span>}</td>
              <td style={{ color: "var(--ink-soft)", fontSize: 13 }}>{p.email ?? <span style={{ color: "var(--ink-faint)" }}>—</span>}</td>
              <td>
                {p.id === currentUserId ? (
                  <span style={{ color: "var(--ink-soft)", fontSize: 13 }}>{ROLE_LABELS[p.role]} (you)</span>
                ) : (
                  <select
                    value={p.role}
                    disabled={saving === p.id}
                    onChange={(e) => handleRoleChange(p.id, e.target.value as UserRole)}
                    style={{
                      fontSize: 13,
                      padding: "2px 6px",
                      border: "1px solid var(--rule-strong)",
                      borderRadius: 4,
                      background: "var(--paper-raised)",
                      color: "var(--ink)",
                      opacity: saving === p.id ? 0.5 : 1,
                    }}
                  >
                    {ROLES.map(r => (
                      <option key={r} value={r}>{ROLE_LABELS[r]}</option>
                    ))}
                  </select>
                )}
              </td>
              <td style={{ color: "var(--ink-faint)", fontSize: 12 }}>
                {new Date(p.created_at).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" })}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
