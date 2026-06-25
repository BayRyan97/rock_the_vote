# Nassau County Voter Canvass Tool (AD-13 / AD-15)

A self-contained address/name lookup + canvass heatmap built from a Nassau County
voter file export and the Census TIGER/Line address-range shapefile. Everything —
data, search index, and the offline map (roads + town labels) — is embedded in a
single static HTML file. No server, no API calls, no external tile dependency.

## What's in this folder

```
data/    Raw source files (voter file export + TIGER shapefile zip)
build/   The script that turns data/ into dist/voter_lookup.html
dist/    The finished tool — open this in a browser
```

## Opening the tool

Just open `dist/voter_lookup.html` in a browser. That's it — no install, no server.
It's ~3 MB because the entire dataset (66,833 households, both districts) is
compressed and embedded directly in the file.

If your browser blocks local file access for some reason, serve it instead:

```bash
cd dist
python3 -m http.server 8123
# then open http://localhost:8123/voter_lookup.html
```

## Rebuilding from source

If you get a new voter file export, or want to add more districts down the line:

```bash
cd build
pip install -r requirements.txt
python build.py
```

This re-runs the full pipeline:
1. Unzips the TIGER/Line address-range shapefile (`data/tl_2025_36059_addrfeat.zip`)
2. Reads the voter file (`data/Assembly_15_13.xlsx`)
3. Geocodes every household by matching its street address against TIGER's
   address-range segments and interpolating a lat/lon along the segment
   (~94% match rate — newer developments not yet in TIGER will miss)
4. Scores every household on the canvass formula (see below)
5. Dictionary-encodes repeated strings (street/city/town/party names) and
   gzip+base64-compresses the whole dataset
6. Injects it into `build/template.html` and writes `dist/voter_lookup.html`

Takes a couple of minutes, mostly spent geocoding ~67K addresses.

## The canvass score

Three scenarios, each worth knocking on a door for, added together:

- **Wake-up calls**: `engagement_gap × num_low_engagement_voters`. A reliable
  voter living with people who don't vote — pulling them along is the easiest
  kind of add.
- **Unaffiliated voters**: `count(BLK party) × 2`. "BLK" is the NY voter-file
  code for no party affiliation ("blank") — the most persuadable bucket in
  the file.
- **Drop-off Democrats**: `count(DEM with tier I0/F1/L1)`. Already friendly,
  just need a nudge to show up again.

Vote tiers are encoded as a letter + number, e.g. `X4`:
- `X` = cross-cutting (votes in both federal and local elections)
- `F` = federal-only, `L` = local-only, `I` = inactive (0 votes recorded)
- The number is total elections voted in over the lookback window

This is a **positives-only** formula by design — it doesn't penalize
reliable opposition voters, just surfaces where there's something to gain.

## The canvass map

The "Canvass map" tab renders one heat point per household (omitting any
household with a score of 0), colored by housing type. The Layers panel
controls what's shown:

- **Single-family homes / Complexes** — toggle each layer independently.
  A household counts as a "complex" once its registered-voter count meets
  the cutoff slider (default 6+).
- **Complex cutoff** — drag to change the single-family/complex threshold.
- **Unaffiliated (BLK) voters only** — when checked, filters the heatmap
  (and the "Top targets in view" list) down to households that have at
  least one BLK-party voter, and re-weights the heat intensity by BLK-voter
  count instead of total canvass score. Useful for visualizing where the
  unaffiliated population is concentrated independent of the other two
  scoring factors.

## Handling the data responsibly

`data/Assembly_15_13.xlsx` and `dist/voter_lookup.html` both contain real
personal information for ~67,000 registered voters — names, ages, home
addresses, party affiliation, and voting history. `dist/voter_lookup.html`
embeds the *entire* dataset (gzip+base64) directly in the page source, so
anyone who can load that file can extract the full dataset, regardless of
what the UI shows.

If this repo is hosted on GitHub: keep it **private**. A client-side
password prompt does not protect this data on a public repo or public
GitHub Pages site — the dataset is sitting in the page source and in the
git history either way, downloadable via `git clone` or "view source." If
you need to publish the tool for a team to use online, use a private repo
with GitHub Pages restricted to collaborators (requires GitHub Pro/Team/
Enterprise for private Pages), or host it behind an access-controlled
service rather than a public static-site host.

## Adding more districts later

The pipeline is generic — it doesn't hardcode AD-13/AD-15 anywhere except in
how the source query was originally filtered. To add a district:

1. Re-run the Databricks query with the new `assembly_district` values
   included, export to `data/` (replacing or alongside the existing file)
2. Update `VOTER_FILE` in `build/build.py` if the filename changes
3. Re-run `python build.py`

The frontend already keys data by district number dynamically (the `13`/`15`
keys in the embedded JSON), so a third district just needs a third key —
worth adding a UI tab for it in `build/template.html` when that happens
(search for `ad-pill` in the HTML).

## How the map works without internet

The artifact sandbox this was originally built in blocks all outbound
network calls except script tags from `cdnjs.cloudflare.com` at parse time —
no map tile servers, no fetch(), no XHR at runtime. So instead of real map
tiles, the tool draws its own minimal basemap from data already in the
dataset: major roads (TIGER `S1100`/`S1200` road classes) as parchment-colored
lines, and town name labels at the centroid of each town's households. It's
not pretty, but it's enough to orient a hot zone to a real place.

If you deploy this somewhere with normal internet access (a real web server,
not a sandboxed artifact), you could swap in real tile layers in
`build/template.html` — look for the `initMap()` function.
