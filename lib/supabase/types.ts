// Database type definitions — run `supabase gen types typescript` to regenerate
// after schema changes, or update manually.

export type Json =
  | string
  | number
  | boolean
  | null
  | { [key: string]: Json | undefined }
  | Json[];

export type UserRole = "admin" | "canvasser";

export interface Database {
  public: {
    Tables: {
      households: {
        Row: {
          id: string;
          county: "NASSAU" | "SUFFOLK";
          address_num: string;
          street: string;
          city: string;
          zip: string;
          town: string | null;
          election_district: number | null;
          assembly_district: number | null;
          senate_district: number | null;
          congressional_district: number | null;
          lon: number | null;
          lat: number | null;
          score_total: number;
          score_wake_ups: number;
          score_unaffiliated: number;
          score_dropoff: number;
          ev_score: number | null;
          created_at: string;
          updated_at: string;
        };
        Insert: Omit<
          Database["public"]["Tables"]["households"]["Row"],
          "created_at" | "updated_at"
        >;
        Update: Partial<Database["public"]["Tables"]["households"]["Insert"]>;
      };
      people: {
        Row: {
          id: string;
          household_id: string;
          name: string;
          age: number | null;
          party: "DEM" | "REP" | "BLK" | "WOR" | "CON" | "IND" | "OTH" | null;
          tier_letter: "X" | "F" | "L" | "I" | null;
          tier_count: number;
          elections: Json | null;
          city: string;
          zip: string;
          turnout_prob: number | null;
          dem_lean_prob: number | null;
          rep_lean_prob: number | null;
          donor_key: string;
          created_at: string;
        };
        Insert: Omit<
          Database["public"]["Tables"]["people"]["Row"],
          "donor_key" | "created_at"
        >;
        Update: Partial<Database["public"]["Tables"]["people"]["Insert"]>;
      };
      donations: {
        Row: {
          id: string;
          donor_key: string;
          source: "fec" | "nyboe";
          donation_date: string | null;
          amount: number | null;
          committee: string | null;
          confirmed: boolean;
          created_at: string;
        };
        Insert: Omit<Database["public"]["Tables"]["donations"]["Row"], "id" | "created_at">;
        Update: Partial<Database["public"]["Tables"]["donations"]["Insert"]>;
      };
      ev_scores: {
        Row: { zip: string; score: number; count: number; updated_at: string };
        Insert: Omit<Database["public"]["Tables"]["ev_scores"]["Row"], "updated_at">;
        Update: Partial<Database["public"]["Tables"]["ev_scores"]["Insert"]>;
      };
      profiles: {
        Row: {
          id: string;
          role: UserRole;
          name: string | null;
          created_at: string;
        };
        Insert: Omit<Database["public"]["Tables"]["profiles"]["Row"], "created_at">;
        Update: Partial<Database["public"]["Tables"]["profiles"]["Insert"]>;
      };
      door_knocks: {
        Row: {
          id: string;
          household_id: string;
          canvasser_id: string;
          knocked_at: string;
          outcome: "contact" | "no_answer" | "moved" | "refused" | "not_home" | null;
          notes: string | null;
        };
        Insert: Omit<Database["public"]["Tables"]["door_knocks"]["Row"], "id" | "knocked_at">;
        Update: Partial<Database["public"]["Tables"]["door_knocks"]["Insert"]>;
      };
    };
    Functions: {
      get_user_role: { Args: Record<string, never>; Returns: UserRole };
    };
  };
}

export type Household = Database["public"]["Tables"]["households"]["Row"];
export type Person = Database["public"]["Tables"]["people"]["Row"];
export type Donation = Database["public"]["Tables"]["donations"]["Row"];
export type Profile = Database["public"]["Tables"]["profiles"]["Row"];
export type DoorKnock = Database["public"]["Tables"]["door_knocks"]["Row"];

export type HouseholdWithPeople = Household & { people: Person[] };
