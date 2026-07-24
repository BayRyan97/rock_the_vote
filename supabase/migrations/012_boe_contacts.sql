DROP TABLE IF EXISTS boe_contacts;

CREATE TABLE boe_contacts (
  voter_id              text PRIMARY KEY,
  state_voter_id        text,

  -- Status
  voter_status          text,
  voter_status_reason   text,
  reg_source            text,

  -- Name
  name_title            text,
  name_first            text,
  name_middle           text,
  name_last             text,
  name_suffix           text,

  -- Personal
  dob                   date,
  gender                text,

  -- Contact
  email                 text,
  phone                 text,

  -- Residential address
  res_house_num         text,
  res_street_dir_pre    text,
  res_street_name       text,
  res_street_type       text,
  res_street_dir_suf    text,
  res_unit_type         text,
  res_unit_num          text,
  res_city              text,
  res_zip               text,

  -- Mailing address (when different from residential)
  mail_address1         text,
  mail_address2         text,
  mail_city             text,
  mail_zip              text,
  mail_military         boolean,
  mail_foreign          boolean,

  -- Flags
  perm_absentee         boolean,
  res_military          boolean,
  election_worker       boolean,

  -- Registration
  registration_date     date,
  registration_change   date,
  res_addr_change       date,

  -- Party (raw BOE description)
  party_desc            text,

  -- Precinct
  precinct_name         text,

  -- Poll location
  poll_place_name       text,
  poll_place_address    text,
  poll_place_city       text,
  poll_place_zip        text,

  -- Districts
  district_ct           text,
  district_ed           text,
  district_vi           text,

  -- Per-election voting methods (E=Election Day, V=Early Vote, A=Absentee, P=Perm Absentee)
  voting_methods        jsonb,

  updated_at            timestamptz DEFAULT now()
);

CREATE INDEX idx_boe_contacts_name_city_zip
  ON boe_contacts(name_first, name_last, res_city, res_zip);

CREATE INDEX idx_boe_contacts_state_voter_id
  ON boe_contacts(state_voter_id);
