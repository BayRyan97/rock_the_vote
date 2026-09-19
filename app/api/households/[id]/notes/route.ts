import { NextRequest, NextResponse } from "next/server";
import { createClient } from "@/lib/supabase/server";
import { OUTCOMES, SUPPORT_LEVELS, type Outcome, type SupportLevel } from "@/lib/canvassNotes";

type NotePayload = {
  canvassing_for?: string | null;
  outcome?: Outcome | null;
  support_level?: SupportLevel | null;
  issues?: string | null;
  follow_up_needed?: boolean | null;
  mail_ballot_assistance?: boolean | null;
  contact_name?: string | null;
  language_spoken?: string | null;
  left_pamphlet?: boolean | null;
  donation_amount?: number | null;
  donor_name?: string | null;
  donor_phone?: string | null;
  donor_email?: string | null;
  notes?: string | null;
};

function cleanStr(v: unknown): string | null {
  if (typeof v !== "string") return null;
  const t = v.trim();
  return t ? t : null;
}

export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params;
  const supabase = await createClient();

  const { data: { user } } = await supabase.auth.getUser();
  if (!user) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });

  const { data, error } = await supabase
    .from("canvass_notes")
    .select("*, profiles(name)")
    .eq("household_id", id)
    .order("created_at", { ascending: false });

  if (error) return NextResponse.json({ error: error.message }, { status: 500 });
  return NextResponse.json(data);
}

export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params;
  const supabase = await createClient();

  const { data: { user } } = await supabase.auth.getUser();
  if (!user) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });

  const body = (await req.json()) as NotePayload;

  const canvassing_for = cleanStr(body.canvassing_for);
  if (!canvassing_for) {
    return NextResponse.json({ error: "Who you're canvassing for is required." }, { status: 400 });
  }

  const outcome = (OUTCOMES as string[]).includes(body.outcome ?? "") ? body.outcome! : null;
  const support_level = (SUPPORT_LEVELS as string[]).includes(body.support_level ?? "")
    ? body.support_level!
    : null;
  const issues = cleanStr(body.issues);
  const follow_up_needed = typeof body.follow_up_needed === "boolean" ? body.follow_up_needed : null;
  const mail_ballot_assistance =
    typeof body.mail_ballot_assistance === "boolean" ? body.mail_ballot_assistance : null;
  const contact_name = cleanStr(body.contact_name);
  const language_spoken = cleanStr(body.language_spoken);
  const left_pamphlet =
    typeof body.left_pamphlet === "boolean" ? body.left_pamphlet : null;
  const donation_amount =
    typeof body.donation_amount === "number" && body.donation_amount > 0
      ? body.donation_amount
      : null;
  const donor_name = cleanStr(body.donor_name);
  const donor_phone = cleanStr(body.donor_phone);
  const donor_email = cleanStr(body.donor_email);
  const notes = cleanStr(body.notes);

  const hasAnyField =
    canvassing_for || outcome || support_level || issues || follow_up_needed !== null ||
    mail_ballot_assistance !== null || contact_name || language_spoken ||
    left_pamphlet !== null || donation_amount || notes;
  if (!hasAnyField) {
    return NextResponse.json({ error: "Add at least one field before saving." }, { status: 400 });
  }
  if (donation_amount && (!donor_name || (!donor_phone && !donor_email))) {
    return NextResponse.json(
      { error: "Donor name and a phone or email are required when an amount is entered." },
      { status: 400 }
    );
  }

  const { data: household } = await supabase
    .from("households")
    .select("turf_id")
    .eq("id", id)
    .single<{ turf_id: number | null }>();

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const { data, error } = await (supabase.from("canvass_notes") as any)
    .insert({
      household_id: id,
      canvasser_id: user.id,
      turf_id: household?.turf_id ?? null,
      canvassing_for,
      outcome,
      support_level,
      issues,
      follow_up_needed,
      mail_ballot_assistance,
      contact_name,
      language_spoken,
      left_pamphlet,
      donation_amount,
      donor_name,
      donor_phone,
      donor_email,
      notes,
    })
    .select("*, profiles(name)")
    .single();

  if (error) return NextResponse.json({ error: error.message }, { status: 400 });
  return NextResponse.json(data, { status: 201 });
}
