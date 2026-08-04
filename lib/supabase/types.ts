// Database type definitions — run `supabase gen types typescript` to regenerate
// after schema changes, or update manually.

export type Json =
  | string
  | number
  | boolean
  | null
  | { [key: string]: Json | undefined }
  | Json[];

export type UserRole = "admin" | "canvasser" | "dfli" | "running";

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
          turf_id: number | null;
          /** True for apartment buildings / facilities. They keep a turf_id (their
           *  nearest walk turf) so they still render on the map, but they are NOT
           *  part of that turf's door count or value -- they need a different tactic. */
          is_facility: boolean;
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
          email: string | null;
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
      // Written by model/turfs/write_supabase.py, a full-snapshot TRUNCATE +
      // reload on every run -- turf_id is a dense integer reassigned each
      // time, not a stable identity, so these rows are not meant to be
      // referenced by id across runs the way the tables above are.
      turfs: {
        Row: {
          turf_id: number;
          n_doors: number;
          n_targets: number;
          /** n_targets / n_doors. Above ~2 means multi-unit density; the
           *  facility split should have caught the buildings already. */
          targets_per_door: number | null;
          /** Gross supporting ballots. Kept for comparison with the pre-2026-08-03
           *  ranking; value_net_margin is what the list is ordered by. */
          value_dem_ballots: number;
          /** Expected two-party MARGIN, not ballots. Not interchangeable with
           *  value_dem_ballots -- different units. */
          value_net_margin: number;
          diameter_m: number | null;
          doors_per_km: number | null;
          canvasser_hours: number;
          hours_per_ballot: number | null;
          hours_per_net_margin: number | null;
          county: string | null;
          ed_keys_touched: string[];
          /** Buildings near this turf that need a non-door tactic (see facilities). */
          n_facilities_nearby: number;
          arm: "treatment" | "control" | "buffer";
          model: string;
          computed_at: string;
        };
        Insert: Database["public"]["Tables"]["turfs"]["Row"];
        Update: Partial<Database["public"]["Tables"]["turfs"]["Row"]>;
      };
      turf_assignment: {
        Row: {
          person_id: string;
          hh_id: number;
          turf_id: number;
          m_i: number;
          m_net_i: number;
        };
        Insert: Database["public"]["Tables"]["turf_assignment"]["Row"];
        Update: Partial<Database["public"]["Tables"]["turf_assignment"]["Row"]>;
      };
      // Apartment buildings and facilities, held OUT of the walk list: a
      // canvasser cannot knock a locked lobby, so these are ranked on their own
      // for lobby access / phone / relational organising. No hours figure on
      // purpose -- reaching a building is an organising cost, not doors/hour.
      facilities: {
        Row: {
          facility_id: number;
          household_id: string;
          n_targets: number;
          household_size: number;
          value_dem_ballots: number;
          value_net_margin: number;
          lat: number | null;
          lon: number | null;
          county: string | null;
          ed_key: string | null;
          /** The walk turf whose canvassers are closest, for hand-off. */
          nearest_turf_id: number | null;
          model: string;
          computed_at: string;
        };
        Insert: Database["public"]["Tables"]["facilities"]["Row"];
        Update: Partial<Database["public"]["Tables"]["facilities"]["Row"]>;
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
