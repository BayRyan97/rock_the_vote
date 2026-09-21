-- donation-cache bucket had no per-bucket file_size_limit, so uploads fell
-- back to the project-wide default (50 MB). Adding the 2026 FEC cycle and
-- raising POSSIBLE_CAP grew the gzipped fec_cache.json past that (65.5 MB),
-- so build/cache_storage.py's upload started failing with a 400.
UPDATE storage.buckets
SET file_size_limit = 209715200  -- 200 MB
WHERE id = 'donation-cache';
