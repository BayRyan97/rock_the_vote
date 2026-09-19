// Shared AND-filter builder for the "Narrow by area" geo scope used by Turf
// Search and the Canvass Map. Both pages let a user combine up to 8
// independent geography/district dimensions at once (County AND Town AND
// Senate District, etc. — OR within a dimension's own multi-select, AND
// across dimensions). Every route that scopes turfs/households/facilities by
// this state (app/api/map/filters, app/api/turfs/roster,
// app/api/map/households, app/api/map/facilities) builds its WHERE clause
// through this module instead of hand-rolling its own branch per dimension —
// with 3 dimensions that branching was tolerable duplicated four times over;
// with 8 it isn't.
//
// `city` isn't one of the "funnel" dimensions (county/town/ED/LD/CD/SD/AD)
// but is kept as an 8th: it's an existing filter (Turf Search's "City", the
// Canvass Map's "City") and a distinct, overlapping unit from Town on Long
// Island — a Town can contain multiple incorporated villages/cities — so
// dropping it would remove functionality without being asked to.

export type GeoDimensionKey =
  | "county"
  | "city"
  | "town"
  | "election_district"
  | "legislative_district"
  | "congressional_district"
  | "senate_district"
  | "assembly_district";

interface DimensionSpec {
  /** households.<column>, always unqualified — callers supply the table alias. */
  column: string;
  /** "int": households.<column> = ANY($n::int[]).
   *  "text_upper": upper(households.<column>) = ANY($n::text[]) — the
   *  existing case-insensitive convention for free-text county/city/town. */
  valueType: "int" | "text_upper";
}

export const GEO_DIMENSIONS: Record<GeoDimensionKey, DimensionSpec> = {
  county: { column: "county", valueType: "text_upper" },
  city: { column: "city", valueType: "text_upper" },
  town: { column: "town", valueType: "text_upper" },
  election_district: { column: "election_district", valueType: "int" },
  legislative_district: { column: "legislative_district", valueType: "int" },
  congressional_district: { column: "congressional_district", valueType: "int" },
  senate_district: { column: "senate_district", valueType: "int" },
  assembly_district: { column: "assembly_district", valueType: "int" },
};

export const ALL_GEO_DIMENSIONS = Object.keys(GEO_DIMENSIONS) as GeoDimensionKey[];

export interface ParsedGeoFilters {
  // Only dimensions actually present in the query string end up here — a
  // present-but-empty param (e.g. `town=`) is kept as an empty array,
  // distinct from an absent param, matching the existing `ads !== null`
  // convention: an opened-but-nothing-checked dimension means "match
  // nothing" (ANY() against an empty array), not "ignore this dimension".
  active: Partial<Record<GeoDimensionKey, (string | number)[]>>;
}

/** Parses every GEO_DIMENSIONS param off a URLSearchParams into typed,
 *  comma-separated value arrays. Numeric dimensions drop non-finite values;
 *  text dimensions are decoded and upper-cased (matching the existing
 *  `upper(h.city) = ANY(...)` convention for county/city/town). */
export function parseGeoFilters(p: URLSearchParams): ParsedGeoFilters {
  const active: Partial<Record<GeoDimensionKey, (string | number)[]>> = {};
  for (const key of ALL_GEO_DIMENSIONS) {
    const raw = p.get(key);
    if (raw === null) continue;
    const spec = GEO_DIMENSIONS[key];
    if (raw === "") {
      active[key] = [];
      continue;
    }
    if (spec.valueType === "int") {
      active[key] = raw
        .split(",")
        .map(Number)
        .filter((n) => Number.isFinite(n));
    } else {
      active[key] = raw
        .split(",")
        .map((v) => decodeURIComponent(v).toUpperCase())
        .filter(Boolean);
    }
  }
  return { active };
}

export function hasActiveGeoFilters(filters: ParsedGeoFilters): boolean {
  return Object.keys(filters.active).length > 0;
}

/** Builds one AND-of-predicates SQL fragment (pre-fixed with " AND ", or ""
 *  if nothing is active) plus the params to append, numbered starting at
 *  paramOffset+1 so callers can prepend their own $1..$N (e.g. a turf_ids
 *  array, or a bbox). `exclude` skips one dimension — this is what makes the
 *  cascading "available options" queries possible: the values offered for
 *  Town are computed from every OTHER active dimension, never Town's own
 *  current selection (else unchecking the last box would empty the list). */
export function buildGeoWhereSql(
  filters: ParsedGeoFilters,
  opts: { tableAlias: string; paramOffset: number; exclude?: GeoDimensionKey }
): { sql: string; params: unknown[] } {
  const prefix = opts.tableAlias ? `${opts.tableAlias}.` : "";
  const params: unknown[] = [];
  const clauses: string[] = [];
  let n = opts.paramOffset;

  for (const key of ALL_GEO_DIMENSIONS) {
    if (key === opts.exclude) continue;
    const values = filters.active[key];
    if (values === undefined) continue;
    const spec = GEO_DIMENSIONS[key];
    n += 1;
    if (spec.valueType === "int") {
      clauses.push(`${prefix}${spec.column} = ANY($${n}::int[])`);
    } else {
      clauses.push(`upper(${prefix}${spec.column}) = ANY($${n}::text[])`);
    }
    params.push(values);
  }

  return { sql: clauses.length ? " AND " + clauses.join(" AND ") : "", params };
}
