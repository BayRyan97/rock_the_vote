import type { UserRole } from "@/lib/supabase/types";

export type Outcome = "contact" | "not_home" | "refused" | "moved";
export type SupportLevel =
  | "strong_support"
  | "lean_support"
  | "undecided"
  | "lean_oppose"
  | "strong_oppose";

export const OUTCOMES: Outcome[] = ["contact", "not_home", "refused", "moved"];
export const SUPPORT_LEVELS: SupportLevel[] = [
  "strong_support",
  "lean_support",
  "undecided",
  "lean_oppose",
  "strong_oppose",
];

export const OUTCOME_LABELS: Record<Outcome, string> = {
  contact: "Contact",
  not_home: "Not home",
  refused: "Refused",
  moved: "Moved",
};

export const SUPPORT_LABELS: Record<SupportLevel, string> = {
  strong_support: "Strong support",
  lean_support: "Lean support",
  undecided: "Undecided",
  lean_oppose: "Lean oppose",
  strong_oppose: "Strong oppose",
};

// Roles allowed into the cross-household "browse all notes" manager view --
// kept in one place so the layout guard, the API route's role check, and
// AppNav's link visibility can't drift out of sync.
export const NOTES_MANAGER_ROLES: UserRole[] = [
  "admin",
  "running",
  "campaign_manager",
  "running_admin",
  "campaign_manager_admin",
];

// Of those, which see every note vs. only notes scoped to one candidate.
// "running" is scoped to their own profiles.name; "campaign_manager" is
// scoped to their own profiles.campaign_name (see app/api/notes/route.ts).
export const NOTES_FULL_ACCESS_ROLES: UserRole[] = ["admin", "running_admin", "campaign_manager_admin"];

// Roles that represent an actual candidate -- these are the names offered
// as suggestions for "who are you canvassing for" on the note form, and are
// visible to every authenticated user for that reason (migration 041).
export const CANDIDATE_ROLES: UserRole[] = ["running", "running_admin"];
