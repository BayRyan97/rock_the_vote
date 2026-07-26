# Data Schema

```mermaid
erDiagram
    households {
        uuid id PK
        text county
        text address_num
        text street
        text city
        text zip
        text town
        smallint election_district
        smallint assembly_district
        smallint senate_district
        smallint congressional_district
        numeric lon
        numeric lat
        smallint score_total
        smallint score_wake_ups
        smallint score_unaffiliated
        smallint score_dropoff
        smallint ev_score
        text acs_tract_geoid
        integer acs_median_income
        numeric acs_pct_college
        numeric acs_pct_owner_occ
        numeric acs_pct_hispanic
        numeric acs_pct_black
        timestamptz created_at
        timestamptz updated_at
    }

    people {
        uuid id PK
        uuid household_id FK
        text name
        smallint age
        text party
        char tier_letter
        smallint tier_count
        jsonb elections
        text city
        text zip
        numeric turnout_prob
        numeric dem_lean_prob
        numeric rep_lean_prob
        text donor_key
        text voter_id
        timestamptz created_at
    }

    boe_contacts {
        text voter_id PK
        text state_voter_id
        text voter_status
        text voter_status_reason
        text reg_source
        text name_title
        text name_first
        text name_middle
        text name_last
        text name_suffix
        text full_name
        date dob
        text gender
        text email
        text phone
        text res_house_num
        text res_street_name
        text res_city
        text res_zip
        text mail_address1
        text mail_city
        text mail_zip
        boolean perm_absentee
        boolean res_military
        boolean election_worker
        date registration_date
        date registration_change
        date res_addr_change
        text party_desc
        text precinct_name
        text poll_place_name
        text poll_place_address
        text district_ct
        text district_ed
        jsonb voting_methods
        timestamptz updated_at
    }

    donations {
        uuid id PK
        text donor_key
        text source
        date donation_date
        numeric amount
        text committee
        boolean confirmed
        timestamptz created_at
    }

    donation_summaries {
        text donor_key PK
        numeric total_donated
        integer donation_count
    }

    donations_meta {
        integer id PK
        integer confirmed_count
        integer possible_count
        numeric confirmed_total
        integer confirmed_donors
    }

    ev_scores {
        text zip PK
        smallint score
        int count
        timestamptz updated_at
    }

    election_results {
        uuid id PK
        text chamber
        smallint year
        text district
        int dem_votes
        int rep_votes
        int other_votes
        int total_votes
        text dem_candidate
        text rep_candidate
        numeric dem_pct
        numeric margin_pct
        text winner
    }

    profiles {
        uuid id PK
        text role
        text name
        text email
        timestamptz created_at
    }

    door_knocks {
        uuid id PK
        uuid household_id FK
        uuid canvasser_id FK
        timestamptz knocked_at
        text outcome
        text notes
    }

    households ||--o{ people : "has"
    households ||--o{ door_knocks : "household_id"
    profiles ||--o{ door_knocks : "canvasser_id"
    people }o--o| boe_contacts : "voter_id"
    people }o--o| donation_summaries : "donor_key"
    donations }o--|| donation_summaries : "donor_key"
    households }o--o| ev_scores : "zip"
```

## Notes

- **Hard FK constraints**: `people.household_id → households`, `door_knocks.household_id → households`, `door_knocks.canvasser_id → profiles`, `profiles.id → auth.users`.
- **Soft joins (no FK constraint)**: `people.voter_id → boe_contacts.voter_id`, `people.donor_key / donations.donor_key → donation_summaries.donor_key`.
- `people.donor_key` is a generated column computed as `NAME|CITY|ZIP5`.
- `boe_contacts.full_name` is a generated column computed as `name_first || ' ' || name_last`.
- `donation_summaries` is a pre-aggregated cache of `donations` — queried by the AI targeting endpoint instead of hitting `donations` directly.
- `donations_meta` is a singleton row (`id = 1`) caching fleet-wide donation totals for the stats dashboard.
- `ev_score` on `households` is denormalized from `ev_scores` by zip — a log-normalized electric vehicle registration density score used as an environmental signal for targeting.
- `election_results` is a standalone reference table (chamber/year/district results) with no FK joins to voter data.
- `door_knocks.outcome` values: `contact`, `no_answer`, `moved`, `refused`, `not_home`.
- `profiles.role` values: `admin`, `canvasser`, `dfli`, `running`.
- `people.party` values: `DEM`, `REP`, `BLK` (blank/unaffiliated), `WOR` (Working Families), `CON` (Conservative), `IND`, `OTH`.
