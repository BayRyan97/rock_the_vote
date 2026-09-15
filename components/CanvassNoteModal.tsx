"use client";
import { useEffect, useState } from "react";

type Outcome = "contact" | "not_home" | "refused" | "moved";
type SupportLevel = "strong_support" | "lean_support" | "undecided" | "lean_oppose" | "strong_oppose";

interface CanvassNote {
  id: string;
  created_at: string;
  outcome: Outcome | null;
  support_level: SupportLevel | null;
  issues: string | null;
  follow_up_needed: boolean | null;
  donation_amount: number | null;
  donor_name: string | null;
  donor_phone: string | null;
  donor_email: string | null;
  notes: string | null;
  profiles: { name: string | null } | null;
}

const OUTCOME_LABELS: Record<Outcome, string> = {
  contact: "Contact",
  not_home: "Not home",
  refused: "Refused",
  moved: "Moved",
};

const SUPPORT_LABELS: Record<SupportLevel, string> = {
  strong_support: "Strong support",
  lean_support: "Lean support",
  undecided: "Undecided",
  lean_oppose: "Lean oppose",
  strong_oppose: "Strong oppose",
};

function fmtDollars(n: number) {
  return "$" + n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

export default function CanvassNoteModal({
  householdId,
  address,
  onClose,
  onNoteAdded,
}: {
  householdId: string;
  address: string;
  onClose: () => void;
  onNoteAdded?: () => void;
}) {
  const [history, setHistory] = useState<CanvassNote[]>([]);
  const [historyLoading, setHistoryLoading] = useState(true);

  const [outcome, setOutcome] = useState<Outcome | "">("");
  const [supportLevel, setSupportLevel] = useState<SupportLevel | "">("");
  const [issues, setIssues] = useState("");
  const [followUpNeeded, setFollowUpNeeded] = useState(false);
  const [donationAmount, setDonationAmount] = useState("");
  const [donorName, setDonorName] = useState("");
  const [donorPhone, setDonorPhone] = useState("");
  const [donorEmail, setDonorEmail] = useState("");
  const [notes, setNotes] = useState("");

  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);
  const [saving, setSaving] = useState(false);

  const amountEntered = parseFloat(donationAmount) > 0;

  useEffect(() => {
    let cancelled = false;
    fetch(`/api/households/${householdId}/notes`)
      .then((r) => r.json())
      .then((d: CanvassNote[]) => {
        if (!cancelled) setHistory(Array.isArray(d) ? d : []);
      })
      .catch(() => {})
      .finally(() => {
        if (!cancelled) setHistoryLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [householdId]);

  function resetForm() {
    setOutcome("");
    setSupportLevel("");
    setIssues("");
    setFollowUpNeeded(false);
    setDonationAmount("");
    setDonorName("");
    setDonorPhone("");
    setDonorEmail("");
    setNotes("");
  }

  async function handleSubmit() {
    setError(null);
    setSuccess(false);

    const amount = parseFloat(donationAmount);
    const hasAmount = donationAmount.trim() !== "" && amount > 0;

    const hasAnyField =
      outcome || supportLevel || issues.trim() || followUpNeeded || hasAmount || notes.trim();
    if (!hasAnyField) {
      setError("Add at least one field before saving.");
      return;
    }
    if (hasAmount && (!donorName.trim() || (!donorPhone.trim() && !donorEmail.trim()))) {
      setError("Donor name and a phone or email are required when an amount is entered.");
      return;
    }

    setSaving(true);
    try {
      const res = await fetch(`/api/households/${householdId}/notes`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          outcome: outcome || null,
          support_level: supportLevel || null,
          issues: issues.trim() || null,
          follow_up_needed: followUpNeeded || null,
          donation_amount: hasAmount ? amount : null,
          donor_name: hasAmount ? donorName.trim() : null,
          donor_phone: hasAmount ? donorPhone.trim() || null : null,
          donor_email: hasAmount ? donorEmail.trim() || null : null,
          notes: notes.trim() || null,
        }),
      });
      const data = await res.json();
      if (!res.ok) {
        setError(data.error ?? "Something went wrong. Try again.");
        return;
      }
      setHistory((h) => [data as CanvassNote, ...h]);
      resetForm();
      setSuccess(true);
      onNoteAdded?.();
    } catch {
      setError("Something went wrong. Try again.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="note-modal-overlay" onClick={onClose}>
      <div className="note-modal" onClick={(e) => e.stopPropagation()}>
        <div className="note-modal-head">
          <div className="note-modal-addr">{address}</div>
          <button className="note-modal-close" onClick={onClose} aria-label="Close">
            ×
          </button>
        </div>

        <div className="note-modal-body">
          <div className="note-history">
            <div className="note-history-title">Previous notes</div>
            {historyLoading ? (
              <div className="note-history-empty">Loading…</div>
            ) : history.length === 0 ? (
              <div className="note-history-empty">No notes yet for this household.</div>
            ) : (
              history.map((n) => (
                <div className="note-history-item" key={n.id}>
                  <div className="note-history-meta">
                    <span>{new Date(n.created_at).toLocaleString()}</span>
                    {n.profiles?.name && <span> · {n.profiles.name}</span>}
                  </div>
                  <div className="note-history-tags">
                    {n.outcome && <span className="note-tag">{OUTCOME_LABELS[n.outcome]}</span>}
                    {n.support_level && (
                      <span className="note-tag">{SUPPORT_LABELS[n.support_level]}</span>
                    )}
                    {n.follow_up_needed && <span className="note-tag">Follow-up</span>}
                    {n.donation_amount != null && (
                      <span className="note-tag note-tag-amount">
                        {fmtDollars(n.donation_amount)} — {n.donor_name}
                      </span>
                    )}
                  </div>
                  {n.issues && <div className="note-history-text">{n.issues}</div>}
                  {n.notes && <div className="note-history-text">{n.notes}</div>}
                </div>
              ))
            )}
          </div>

          <div className="note-form">
            <div className="note-form-title">Add a note</div>

            <label className="note-field">
              <span>Interaction outcome</span>
              <select value={outcome} onChange={(e) => setOutcome(e.target.value as Outcome | "")}>
                <option value="">—</option>
                {(Object.keys(OUTCOME_LABELS) as Outcome[]).map((k) => (
                  <option key={k} value={k}>
                    {OUTCOME_LABELS[k]}
                  </option>
                ))}
              </select>
            </label>

            <label className="note-field">
              <span>Voter support level</span>
              <select
                value={supportLevel}
                onChange={(e) => setSupportLevel(e.target.value as SupportLevel | "")}
              >
                <option value="">—</option>
                {(Object.keys(SUPPORT_LABELS) as SupportLevel[]).map((k) => (
                  <option key={k} value={k}>
                    {SUPPORT_LABELS[k]}
                  </option>
                ))}
              </select>
            </label>

            <label className="note-field">
              <span>Issues or concerns discussed</span>
              <textarea value={issues} onChange={(e) => setIssues(e.target.value)} rows={2} />
            </label>

            <label className="note-field note-field-checkbox">
              <input
                type="checkbox"
                checked={followUpNeeded}
                onChange={(e) => setFollowUpNeeded(e.target.checked)}
              />
              <span>Follow-up needed</span>
            </label>

            <label className="note-field">
              <span>Amount donated</span>
              <input
                type="number"
                step="0.01"
                min="0"
                placeholder="$0.00"
                value={donationAmount}
                onChange={(e) => setDonationAmount(e.target.value)}
              />
            </label>

            {amountEntered && (
              <div className="note-donor-fields">
                <label className="note-field">
                  <span>Donor name *</span>
                  <input type="text" value={donorName} onChange={(e) => setDonorName(e.target.value)} />
                </label>
                <label className="note-field">
                  <span>Donor phone</span>
                  <input type="text" value={donorPhone} onChange={(e) => setDonorPhone(e.target.value)} />
                </label>
                <label className="note-field">
                  <span>Donor email</span>
                  <input type="text" value={donorEmail} onChange={(e) => setDonorEmail(e.target.value)} />
                </label>
                <div className="note-donor-hint">* name and a phone or email are required</div>
              </div>
            )}

            <label className="note-field">
              <span>Notes</span>
              <textarea value={notes} onChange={(e) => setNotes(e.target.value)} rows={3} />
            </label>

            {error && <div className="note-form-error">{error}</div>}
            {success && <div className="note-form-success">Note saved.</div>}

            <button className="note-submit" onClick={handleSubmit} disabled={saving}>
              {saving ? "Saving…" : "Save note"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
