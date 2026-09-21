import {
  ALL_GEO_DIMENSIONS,
  buildGeoWhereSql,
  hasActiveGeoFilters,
  parseGeoFilters,
} from "@/lib/geoFilters";

const qs = (s: string) => new URLSearchParams(s);

describe("parseGeoFilters", () => {
  it("ignores dimensions that are absent from the query string", () => {
    const { active } = parseGeoFilters(qs("county=NASSAU"));
    expect(Object.keys(active)).toEqual(["county"]);
  });

  it("keeps a present-but-empty param as an empty array, not as absent", () => {
    // The distinction that matters: an opened-but-nothing-checked dimension
    // means "match nothing", not "ignore this dimension".
    const { active } = parseGeoFilters(qs("town="));
    expect(active.town).toEqual([]);
    expect("town" in active).toBe(true);
  });

  it("upper-cases and URI-decodes text dimensions", () => {
    // Double-encoded on purpose. URLSearchParams already decodes one layer, so
    // a singly-encoded "%20" would pass this test with the decodeURIComponent
    // call deleted. Encoding twice means only an explicit decode produces the
    // expected value -- and the escaped comma pins that splitting happens
    // BEFORE decoding, so a comma inside a city name survives.
    const { active } = parseGeoFilters(qs("city=GREAT%2520NECK%252C%2520NY,Oyster%2520Bay"));
    expect(active.city).toEqual(["GREAT NECK, NY", "OYSTER BAY"]);
  });

  it("drops non-finite values from int dimensions", () => {
    const { active } = parseGeoFilters(qs("senate_district=5,abc,7,,9"));
    expect(active.senate_district).toEqual([5, 7, 9]);
  });

  it("drops whitespace-only segments, which Number() would turn into 0", () => {
    // Pins the .map(v => v.trim()) half of the fix. Without the trim, " "
    // survives .filter(Boolean) and Number(" ") is 0, so a phantom district 0
    // lands in the ANY() array -- the same defect as the empty segment above,
    // by a different route.
    const { active } = parseGeoFilters(qs("senate_district=5,%20,7"));
    expect(active.senate_district).toEqual([5, 7]);
  });

  it("keeps a literal zero, which is a real value and not an empty segment", () => {
    // .filter(Boolean) runs on the STRING "0", which is truthy, so this must
    // survive. Guards against "fixing" the blank-segment bug by filtering
    // after Number(), which would silently drop district 0.
    const { active } = parseGeoFilters(qs("election_district=0,1"));
    expect(active.election_district).toEqual([0, 1]);
  });

  it("parses all eight dimensions independently", () => {
    const { active } = parseGeoFilters(
      qs(
        "county=NASSAU&city=HEMPSTEAD&town=OYSTER+BAY&election_district=1" +
          "&legislative_district=2&congressional_district=3" +
          "&senate_district=4&assembly_district=5"
      )
    );
    expect(Object.keys(active).sort()).toEqual([...ALL_GEO_DIMENSIONS].sort());
  });
});

describe("hasActiveGeoFilters", () => {
  it("is false when nothing was supplied", () => {
    expect(hasActiveGeoFilters(parseGeoFilters(qs("")))).toBe(false);
  });

  it("is true for a present-but-empty dimension", () => {
    expect(hasActiveGeoFilters(parseGeoFilters(qs("town=")))).toBe(true);
  });
});

describe("buildGeoWhereSql", () => {
  it("returns an empty fragment when nothing is active", () => {
    const out = buildGeoWhereSql(parseGeoFilters(qs("")), {
      tableAlias: "h",
      paramOffset: 0,
    });
    expect(out).toEqual({ sql: "", params: [] });
  });

  it("emits int and text dimensions with their own casts", () => {
    const out = buildGeoWhereSql(parseGeoFilters(qs("county=NASSAU&senate_district=5")), {
      tableAlias: "h",
      paramOffset: 0,
    });
    expect(out.sql).toBe(
      " AND upper(h.county) = ANY($1::text[]) AND h.senate_district = ANY($2::int[])"
    );
    expect(out.params).toEqual([["NASSAU"], [5]]);
  });

  it("omits the table prefix when the alias is empty", () => {
    // app/api/map/households selects FROM households with no alias.
    const out = buildGeoWhereSql(parseGeoFilters(qs("county=NASSAU")), {
      tableAlias: "",
      paramOffset: 0,
    });
    expect(out.sql).toBe(" AND upper(county) = ANY($1::text[])");
  });

  it("numbers placeholders from paramOffset + 1 so callers can prepend their own", () => {
    // e.g. a bbox already occupying $1..$4.
    const out = buildGeoWhereSql(parseGeoFilters(qs("county=NASSAU&town=ISLIP")), {
      tableAlias: "h",
      paramOffset: 4,
    });
    expect(out.sql).toContain("$5::text[]");
    expect(out.sql).toContain("$6::text[]");
    expect(out.params).toHaveLength(2);
  });

  it("excludes one dimension without leaving a gap in the placeholder numbering", () => {
    // This powers the cascading option lists. A gap here would be a runtime
    // SQL error, not a wrong result.
    const filters = parseGeoFilters(qs("county=NASSAU&town=ISLIP&city=BABYLON"));
    const out = buildGeoWhereSql(filters, {
      tableAlias: "h",
      paramOffset: 0,
      exclude: "city",
    });
    expect(out.sql).not.toContain("h.city");
    expect(out.params).toHaveLength(2);
    expect(placeholderNumbers(out.sql)).toEqual([1, 2]);
  });

  it("keeps placeholder count and param count in lockstep across all dimensions", () => {
    const filters = parseGeoFilters(
      qs(
        "county=NASSAU&city=HEMPSTEAD&town=OYSTER+BAY&election_district=1" +
          "&legislative_district=2&congressional_district=3" +
          "&senate_district=4&assembly_district=5"
      )
    );
    const out = buildGeoWhereSql(filters, { tableAlias: "h", paramOffset: 0 });
    const nums = placeholderNumbers(out.sql);
    expect(nums).toEqual([1, 2, 3, 4, 5, 6, 7, 8]);
    expect(out.params).toHaveLength(nums.length);
  });

  it("still binds an empty array for a present-but-empty dimension", () => {
    // "match nothing" has to reach the database as an empty array, not vanish.
    const out = buildGeoWhereSql(parseGeoFilters(qs("town=")), {
      tableAlias: "h",
      paramOffset: 0,
    });
    expect(out.sql).toBe(" AND upper(h.town) = ANY($1::text[])");
    expect(out.params).toEqual([[]]);
  });
});

/** Placeholder numbers in the order they appear, e.g. " AND x = ANY($2…)" -> [2]. */
function placeholderNumbers(sql: string): number[] {
  return [...sql.matchAll(/\$(\d+)/g)].map((m) => Number(m[1]));
}
