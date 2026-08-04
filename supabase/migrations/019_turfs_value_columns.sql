-- Turf valuation: rank on net two-party margin, and stop shipping a dead flag.
--
-- WHY value_net_margin exists. value_dem_ballots credits a voter's full
-- dem_lean probability, so a 0.55-lean voter counted as 0.55 ballots and the
-- 0.45 chance of turning out an opponent was never subtracted. Canvassing is
-- blunt -- you mobilise whoever answers the door -- so the walk list is now
-- ranked on expected two-party MARGIN. Computed as p_support - p_oppose, not
-- 2p-1: the party head is 3-class (dem_lean/rep_lean/other), and 2p-1 would
-- charge the campaign for minor-party registrants as though they voted the
-- other way. value_dem_ballots stays for comparison against the prior ranking.
--
-- The two are NOT the same unit. value_dem_ballots counts ballots produced;
-- value_net_margin counts margin. hours_per_ballot and hours_per_net_margin
-- are likewise not comparable numbers -- do not present them interchangeably.
--
-- WHY is_facility_share goes. It was computed from a rule that could never
-- fire: it counted distinct households sharing a street address, but the ETL
-- keys households on household_uuid and already collapses a whole building
-- into one row (10 WELWYN RD GREAT NECK = 1 household, 30 voters). The column
-- was therefore exactly 0.0 on all 1,652 rows ever written, and nothing read
-- it. Replaced by n_facilities_nearby, which is actionable: it tells an
-- organiser how many buildings near this turf need a non-door tactic.

ALTER TABLE turfs
  ADD COLUMN value_net_margin     double precision NOT NULL DEFAULT 0,
  ADD COLUMN hours_per_net_margin double precision,
  ADD COLUMN targets_per_door     double precision,
  ADD COLUMN n_facilities_nearby  integer NOT NULL DEFAULT 0,
  DROP COLUMN is_facility_share;

CREATE INDEX idx_turfs_net_value ON turfs (value_net_margin DESC);

-- addr_id was a per-run pd.factorize ordinal with no stable meaning across
-- reruns (same hazard as turf_id, but without turf_id's reason to exist), it
-- was ~1:1 with hh_id because of the household collapse described above, and
-- nothing ever read it.
ALTER TABLE turf_assignment DROP COLUMN addr_id;

-- m_net_i alongside m_i, so a per-voter list can be sorted the same way the
-- turf ranking is.
ALTER TABLE turf_assignment ADD COLUMN m_net_i double precision NOT NULL DEFAULT 0;
