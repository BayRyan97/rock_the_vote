#!/bin/bash
# refresh_and_deploy.sh
# Runs weekly by launchd (Sunday midnight). Pipeline:
#   1. Re-query stale FEC entries (confirmed donors every 30d, no-match every 90d)
#   2. Re-query stale NYBOE (state-level) donation entries
#   3. Sync donations to Supabase
#   4. Rebuild dist/voter_lookup.html with updated data
#   5. Commit + push to GitHub
#   6. Redeploy to Cloudflare Pages
#
# Designed to be fast on subsequent runs: only queries people whose
# cached donation data is older than the staleness thresholds.
# A fresh run typically takes ~65 minutes (re-checking ~780 confirmed donors).

set -euo pipefail
cd "$(dirname "$0")/.."  # repo root

LOGFILE="/tmp/rtv_refresh_$(date +%Y%m%d_%H%M%S).log"
exec >> "$LOGFILE" 2>&1

echo "=== Rock the Vote weekly refresh ==="
echo "Started: $(date)"
echo ""

# 1. Refresh FEC donation data (honors staleness thresholds, lock file prevents overlap)
echo "--- Step 1: FEC data refresh ---"
python3 build/fetch_fec.py --all-parties
echo ""

# 2. Refresh NYBOE (state-level) donation data
echo "--- Step 2: NYBOE data refresh ---"
python3 build/fetch_nyboe.py --all-parties
echo ""

# 3. Sync both caches to Supabase donations table
echo "--- Step 3: Sync donations to Supabase ---"
python3 build/migrate_donations_psycopg2.py
echo ""

# 4. Rebuild the site
echo "--- Step 4: Rebuild dist/voter_lookup.html ---"
python3 build/build.py
echo ""

# 5. Git commit + push (no-op if nothing changed)
echo "--- Step 5: Git commit + push ---"
git add data/fec_cache.json data/nyboe_cache.json dist/voter_lookup.html
if git diff --cached --quiet; then
  echo "Nothing changed — skipping commit"
else
  git commit -m "Weekly donation data refresh $(date +%Y-%m-%d)"
  git push
fi
echo ""

# 6. Deploy to Cloudflare Pages
# Deploy dist/ directly so _redirects (which routes "/" to voter_lookup.html) is included.
echo "--- Step 6: Deploy to Cloudflare Pages ---"
npx --yes wrangler@3 pages deploy dist --project-name=rock-the-vote-canvass --branch=main
echo ""

echo "=== Done: $(date) ==="
