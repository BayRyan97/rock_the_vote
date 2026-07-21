-- Remove duplicate (household_id, name) rows, keeping the earliest inserted one
DELETE FROM people
WHERE id NOT IN (
  SELECT DISTINCT ON (household_id, name) id
  FROM people
  ORDER BY household_id, name, created_at
);

ALTER TABLE people
  ADD CONSTRAINT people_household_name_key
  UNIQUE (household_id, name);
