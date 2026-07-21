ALTER TABLE households
  ADD CONSTRAINT households_county_address_street_zip_key
  UNIQUE (county, address_num, street, zip);
