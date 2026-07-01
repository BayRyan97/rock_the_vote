#!/bin/bash
# refresh_and_deploy.sh
# Runs monthly by launchd. Pipeline:
#   1. Re-query stale FEC entries (confirmed donors every 30d, no-match every 90d)
#   2. Rebuild dist/voter_lookup.html with updated data
#   3. Commit + push to GitHub
#   4. Redeploy to Cloudflare Pages
#
# Designed to be fast on subsequent runs: only queries people whose
# cached donation data is older than the staleness thresholds.
# A fresh run typically takes ~65 minutes (re-checking ~780 confirmed donors).

set -euo pipefail
cd "$(dirname "$0")/.."  # repo root

LOGFILE="/tmp/rtv_refresh_$(date +%Y%m%d_%H%M%S).log"
exec >> "$LOGFILE" 2>&1

echo "=== Rock the Vote monthly refresh ==="
echo "Started: $(date)"
echo ""

# 1. Refresh FEC donation data (honors staleness thresholds, lock file prevents overlap)
echo "--- Step 1: FEC data refresh ---"
python3 build/fetch_fec.py --all-parties
echo ""

# 2. Rebuild the site
echo "--- Step 2: Rebuild dist/voter_lookup.html ---"
python3 build/build.py
echo ""

# 3. Git commit + push (no-op if nothing changed)
echo "--- Step 3: Git commit + push ---"
git add data/fec_cache.json dist/voter_lookup.html
if git diff --cached --quiet; then
  echo "Nothing changed — skipping commit"
else
  git commit -m "Monthly FEC donation data refresh $(date +%Y-%m-%d)"
  git push
fi
echo ""

# 4. Deploy to Cloudflare Pages
echo "--- Step 4: Deploy to Cloudflare Pages ---"
cp dist/voter_lookup.html build/cf-deploy/index.html
cd build/cf-deploy
npx --yes wrangler@3 pages deploy . --project-name=rock-the-vote-canvass
echo ""

echo "=== Done: $(date) ==="
