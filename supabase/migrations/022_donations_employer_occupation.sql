-- FEC's bulk Schedule A files and live API both return contributor employer
-- and occupation; the fetch scripts parsed them off every record and threw
-- them away. NY BOE's Schedule A dataset has no equivalent donor-level field
-- (only treasurer/independent-expenditure variants, which are different
-- people), so these stay NULL for source='nyboe' rows.

ALTER TABLE donations
  ADD COLUMN employer   text,
  ADD COLUMN occupation text;
