// Shared UI config for the combinable "Narrow by area" geo filter, used by
// both the Turf Search page (app/(app)/turfs/page.tsx) and the Canvass Map
// (components/LeafletMap.tsx) so the two don't drift into two different
// dimension lists / labels / formatting rules. Query-string param names and
// SQL column names live in lib/geoFilters.ts; this module is presentation
// only (labels, display formatting, whether a dimension gets a search box).
import type { GeoDimensionKey } from "./geoFilters";

export type { GeoDimensionKey };

export interface GeoDimensionConfig {
  key: GeoDimensionKey;
  label: string;
  valueType: "string" | "number";
  // A search box only earns its place when a checklist can run into the
  // dozens+ of entries — matches today's city/town search boxes. Election
  // districts number in the dozens-to-hundreds per county, so they get one
  // too; the other numeric dimensions have few enough values (a couple dozen
  // districts each) that a plain scrollable checklist is enough, same as AD
  // today.
  searchable: boolean;
  formatValue?: (v: string | number) => string;
  // Visual grouping only — a small uppercase divider label rendered above
  // the first dimension of each group in the accordion, so the government-
  // level ordering reads as chunks rather than one undifferentiated list of
  // 8. Not a real containment hierarchy (see the ordering note below).
  group: "Federal & state" | "County" | "Town & local";
}

function titleCase(v: string | number): string {
  return String(v)
    .toLowerCase()
    .split(" ")
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}

const districtLabel = (prefix: string) => (v: string | number) => `${prefix} ${v}`;

// Ordered broadest jurisdiction to narrowest, matching how these offices
// actually nest for a Long Island voter: federal, then state, then county,
// then town, then the two units below any elected office (city is a postal/
// mailing designation from the voter file, not an incorporated government;
// election district is the precinct BOE administers, not an office). This is
// a display/ordering convention, not a strict geographic containment tree —
// Congressional/Senate/Assembly district lines don't nest inside each other
// or inside county lines the way county-legislature and town lines mostly do.
//
// "Town" here also covers what a Town Supervisor is elected over: NY town
// supervisors run at-large across the whole town, not from a sub-district,
// so there's no separate "supervisor district" underneath it. Town Council
// district (some towns, e.g. Brookhaven, elect council members from
// sub-town districts; others run at-large) is NOT included — that boundary
// data doesn't exist anywhere in this pipeline yet (not in the Nassau/
// Suffolk voter CSVs, not geocoded, no geojson) and would need a new source
// before it could be added as a real filter.
export const GEO_DIMENSION_CONFIGS: GeoDimensionConfig[] = [
  { key: "congressional_district", label: "Congressional District", valueType: "number", searchable: false, formatValue: districtLabel("CD"), group: "Federal & state" },
  { key: "senate_district", label: "State Senate District", valueType: "number", searchable: false, formatValue: districtLabel("SD"), group: "Federal & state" },
  { key: "assembly_district", label: "State Assembly District", valueType: "number", searchable: false, formatValue: districtLabel("AD"), group: "Federal & state" },
  { key: "county", label: "County", valueType: "string", searchable: false, formatValue: titleCase, group: "County" },
  { key: "legislative_district", label: "County Legislature", valueType: "number", searchable: false, formatValue: districtLabel("LD"), group: "County" },
  { key: "town", label: "Town", valueType: "string", searchable: true, formatValue: titleCase, group: "Town & local" },
  // City is left raw (uppercase), matching how it's always been shown here —
  // only Town gets title-cased, per the pre-existing convention.
  { key: "city", label: "City", valueType: "string", searchable: true, group: "Town & local" },
  { key: "election_district", label: "Election District", valueType: "number", searchable: true, formatValue: districtLabel("ED"), group: "Town & local" },
];

export function emptySelection(): Record<GeoDimensionKey, Set<string | number>> {
  return Object.fromEntries(
    GEO_DIMENSION_CONFIGS.map((d) => [d.key, new Set<string | number>()])
  ) as Record<GeoDimensionKey, Set<string | number>>;
}

export function emptyOptions(): Record<GeoDimensionKey, (string | number)[]> {
  return Object.fromEntries(
    GEO_DIMENSION_CONFIGS.map((d) => [d.key, [] as (string | number)[]])
  ) as Record<GeoDimensionKey, (string | number)[]>;
}

/** Whether ANY dimension has at least one checked value — the single source
 *  of truth for "is a filter actually active", independent of which
 *  accordion sections happen to be visually expanded. Expanding a section to
 *  browse its options must never by itself narrow anything. */
export function hasAnySelected(
  selected: Record<GeoDimensionKey, Set<string | number>>
): boolean {
  return GEO_DIMENSION_CONFIGS.some((d) => selected[d.key].size > 0);
}

/** Builds the query-string params for every dimension with at least one
 *  checked value (an empty dimension contributes nothing — unchecking every
 *  box in a facet means "stop filtering by it", not "match nothing").
 *  Numeric dimensions encode as bare numbers, string dimensions URI-encoded
 *  (matching the existing upper()-matched county/city/town convention
 *  server-side — case doesn't need to be forced here since
 *  lib/geoFilters.ts upper-cases on parse). */
export function appendGeoParams(
  qs: URLSearchParams,
  selected: Record<GeoDimensionKey, Set<string | number>>
) {
  for (const dim of GEO_DIMENSION_CONFIGS) {
    const vals = [...selected[dim.key]];
    if (vals.length === 0) continue;
    qs.set(
      dim.key,
      dim.valueType === "string"
        ? vals.map((v) => encodeURIComponent(String(v))).join(",")
        : vals.join(",")
    );
  }
}
