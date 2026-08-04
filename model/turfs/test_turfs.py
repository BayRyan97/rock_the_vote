"""test_turfs.py — self-checks for turf construction and valuation.

No database, no built pipeline, no persons.parquet: every fixture here is
synthetic, same pattern as test_features_person.py.

Two checks matter more than the rest, and both pin bugs that shipped:

  test_apartment_building_is_not_one_household  -- see build_households'
      docstring. Households and addresses are different relations.
  test_one_row_building_is_a_facility           -- the same docstring, other
      half. The address-level rule could never fire against this ETL, so
      is_facility was False for every household ever built and
      is_facility_share was exactly 0.0 across all 1,652 production turfs.
      Any fixture here that only exercises the addr_size branch will keep
      passing while the real data shape goes undetected.

Run:  python model/turfs/test_turfs.py     (exit 0 = all checks pass)
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config as C  # noqa: E402
from turfs import (assert_serve_base_rate, assign_arms, build_households,  # noqa: E402
                   build_turfs, hilbert_index, movability_weight, planar_metres,
                   rank_facilities, split_track, value_households, value_turfs)

FAILURES = []


def ok(name, got, want):
    good = (got == want)
    if isinstance(good, np.ndarray):
        good = bool(good.all())
    if not good:
        FAILURES.append(f"{name}: got {got!r}, want {want!r}")
    print(f"  [{'OK' if good else 'FAIL'}] {name}" + ("" if good else f"  got={got!r}"))


def close(name, got, want, tol=1e-9):
    good = abs(float(got) - float(want)) <= tol
    if not good:
        FAILURES.append(f"{name}: got {got!r}, want {want!r} (tol {tol})")
    print(f"  [{'OK' if good else 'FAIL'}] {name}" + ("" if good else f"  got={got!r}"))


def raises(name, fn, exc=ValueError, mention=()):
    try:
        fn()
    except exc as e:
        miss = [m for m in mention if m not in str(e)]
        if miss:
            FAILURES.append(f"{name}: message lacks {miss}")
        print(f"  [{'OK' if not miss else 'FAIL'}] {name}: {str(e)[:70]}")
        return
    except Exception as e:                                    # noqa: BLE001
        FAILURES.append(f"{name}: raised {type(e).__name__}")
        print(f"  [FAIL] {name}: raised {type(e).__name__}")
        return
    FAILURES.append(f"{name}: did not raise")
    print(f"  [FAIL] {name} did not raise")


print("test_turfs")

# ---------------------------------------------------------------- A
print(" A. movability_weight — normalisation and symmetry (§2.4)")
p = np.array([0.0, 0.1, 0.3, 0.5, 0.7, 0.9, 1.0])
w = movability_weight(p, exponent=2)
close("mean(w) == 1.0 over the pool", w.mean(), 1.0)
close("w symmetric: w(0.3) == w(0.7)", w[2], w[4])
ok("w(0.5) is the max", bool(w[3] == w.max()), True)
close("w(0.0) == 0", w[0], 0.0)
close("w(1.0) == 0", w[-1], 0.0)

# ---------------------------------------------------------------- B
print(" B. build_households — household vs address, and facility detection")


def targets_at_one_address(n_households, addr="NASSAU|GC|MAIN ST|100",
                           household_size=1, persons_per_hh=1):
    county, city, street, num = addr.split("|")
    n = n_households * persons_per_hh
    return pd.DataFrame({
        "person_row": np.arange(n, dtype=np.int64),
        "household_row": np.repeat(np.arange(n_households, dtype=np.int64), persons_per_hh),
        "household_size": [household_size] * n,
        "county": [county] * n, "city": [city] * n,
        "street_name": [street] * n, "address_number": [num] * n,
        "ed_key": ["NASSAU|1|1"] * n,
        "lat": [40.8] * n, "lon": [-73.6] * n,
        "has_geo": [1] * n,
        "p_turnout": [0.5] * n, "p_support": [0.8] * n, "p_oppose": [0.15] * n,
        "m_i": [0.5] * n, "m_net_i": [0.4] * n,
    })


building = targets_at_one_address(12)   # 12 households, one 12-unit building
hh = build_households(building, facility_min_voters=C.TURF_FACILITY_MIN_VOTERS)
ok("12 distinct households, NOT one merged household", len(hh), 12)
ok("every household has exactly 1 target (not fanned out to the building)",
   hh["n_targets"].tolist(), [1] * 12)
ok("all 12 share one addr_id (it IS one building)", hh["addr_id"].nunique(), 1)
ok("facility flag trips at the ADDRESS level (12 units > cap 10)",
   hh["is_facility"].tolist(), [True] * 12)

print("    the real ETL shape: households are keyed on household_uuid, so a")
print("    30-voter building is ONE row with addr_size 1. The address-level")
print("    rule alone can never fire on it -- that is why every production")
print("    turf reported is_facility_share == 0.0")
one_row_building = targets_at_one_address(1, addr="NASSAU|GN|WELWYN RD|10",
                                          household_size=30, persons_per_hh=8)
hh_welwyn = build_households(one_row_building)
ok("one household, 30 registered voters, IS a facility",
   hh_welwyn["is_facility"].tolist(), [True])
ok("...even though only one household shares that address",
   int(hh_welwyn["addr_size"].iloc[0]), 1)
ok("household_size survives onto the frame for auditing",
   int(hh_welwyn["household_size"].iloc[0]), 30)
ok("the facility SHARE is non-zero (the production symptom)",
   bool(hh_welwyn["is_facility"].mean() > 0), True)

print("    a 30-voter building where only 4 voters survive the movable/Dem")
print("    filter must still read as a building, not a 4-person house --")
print("    which is why the signal is household_size and not n_targets")
sparse_building = targets_at_one_address(1, addr="NASSAU|GN|WELWYN RD|10",
                                         household_size=30, persons_per_hh=4)
hh_sparse = build_households(sparse_building)
ok("4 targets inside a 30-voter building still flags", hh_sparse["is_facility"].tolist(), [True])
ok("...and n_targets is genuinely only 4", int(hh_sparse["n_targets"].iloc[0]), 4)

small_building = targets_at_one_address(3, addr="NASSAU|GC|OAK ST|5")
hh_small = build_households(small_building)
ok("a 3-unit building is not a facility", hh_small["is_facility"].tolist(), [False] * 3)

family = targets_at_one_address(1, addr="NASSAU|GC|ELM ST|7",
                                household_size=4, persons_per_hh=3)
ok("a 4-voter family household is not a facility",
   build_households(family)["is_facility"].tolist(), [False])

raises("build_households without household_size is refused, not guessed",
       lambda: build_households(building.drop(columns=["household_size"])),
       mention=["household_size"])

two_addr = pd.concat([targets_at_one_address(2, "NASSAU|GC|MAIN ST|100"),
                      targets_at_one_address(2, "NASSAU|GC|OAK ST|5")], ignore_index=True)
two_addr["household_row"] = np.arange(4, dtype=np.int64)
two_addr["person_row"] = np.arange(4, dtype=np.int64)
hh2 = build_households(two_addr)
ok("two addresses stay two addr_ids", hh2["addr_id"].nunique(), 2)
ok("four distinct households across the two addresses", len(hh2), 4)

# ---------------------------------------------------------------- C
print(" C. value_households — the valuation formula and the spillover cap (§3.1)")


def household_targets(masses, hh_id=0, net_scale=0.5):
    n = len(masses)
    return pd.DataFrame({
        "person_row": np.arange(n), "household_row": [hh_id] * n,
        "m_i": list(masses), "m_net_i": [m * net_scale for m in masses],
    })


def one_household(n_targets=2, hh_id=0):
    return pd.DataFrame({"hh_id": [hh_id], "lat": [40.8], "lon": [-73.6],
                         "has_geo": [1], "n_targets": [n_targets],
                         "household_size": [n_targets], "is_facility": [False]})


targets2 = household_targets([0.8, 0.6])
valued = value_households(one_household(2), targets2, beta=0.60, delta=0.098, kappa=0.25)
want = 0.25 * 0.098 * (0.8 + 0.60 * 0.6)
close("hand-computed 2-person household value", valued["value"].iloc[0], want, tol=1e-9)

valued1 = value_households(one_household(1), household_targets([0.5]),
                           beta=0.60, delta=0.098, kappa=0.25)
close("1-person household: no spillover term at all",
     valued1["value"].iloc[0], 0.25 * 0.098 * 0.5, tol=1e-9)

print("    the cap must be INERT on ordinary households -- A(h) is the argmax,")
print("    so no 1- or 2-person household can ever reach it, and those are the")
print("    only sizes Nickerson's beta was measured on")
valued3 = value_households(one_household(3), household_targets([0.8, 0.6, 0.5]),
                           beta=0.60, delta=0.098, kappa=0.25)
close("3-person household is uncapped (0.6+0.5 < 3*0.8)",
     valued3["value"].iloc[0], 0.25 * 0.098 * (0.8 + 0.60 * 1.1), tol=1e-9)

print("    ...and must bite hard on a 275-target 'household', which is really")
print("    an apartment tower: one knock was worth 3.22 expected ballots")
big = household_targets([0.8] + [0.5] * 49)
valued_big = value_households(one_household(50), big, beta=0.60, delta=0.098, kappa=0.25)
capped_want = 0.25 * 0.098 * 0.8 * (1 + 3.0 * 0.60)
close("50-target household saturates at kappa*delta*m_A*(1+ratio*beta)",
     valued_big["value"].iloc[0], capped_want, tol=1e-9)
uncapped = 0.25 * 0.098 * (0.8 + 0.60 * 0.5 * 49)
ok("capped value is far below the linear value it replaces",
   bool(valued_big["value"].iloc[0] < uncapped / 4), True)

prev = 0.0
mono = True
for k in range(1, 12):
    v = value_households(one_household(k), household_targets([0.8] + [0.4] * (k - 1)),
                         beta=0.60, delta=0.098, kappa=0.25)["value"].iloc[0]
    mono = mono and (v >= prev - 1e-12)
    prev = v
ok("adding a housemate never lowers household value", mono, True)

facility_hh = one_household(2)
facility_hh["is_facility"] = [True]
valued_fac = value_households(facility_hh, targets2, beta=0.60, delta=0.098, kappa=0.25)
close("is_facility does not zero household value (the track split handles it)",
     valued_fac["value"].iloc[0], want, tol=1e-9)

valued_empty = value_households(one_household(0, hh_id=99), targets2,
                                beta=0.60, delta=0.098, kappa=0.25)
close("a household with no matching targets gets value 0", valued_empty["value"].iloc[0], 0.0)
close("...and net value 0 too", valued_empty["value_net"].iloc[0], 0.0)

# ---------------------------------------------------------------- C2
print(" C2. net margin — p_support - p_oppose, NOT 2p-1")
print("    the party head is 3-class (dem_lean/rep_lean/other), so 2p-1 would")
print("    charge the campaign for minor-party registrants as if they were Rep")
net_t = pd.DataFrame({"person_row": [0], "household_row": [0],
                      "m_i": [0.55], "m_net_i": [0.55 - 0.35]})
net_v = value_households(one_household(1), net_t, beta=0.60, delta=0.098, kappa=0.25)
close("value_net/value == (0.55-0.35)/0.55 for a 0.55/0.35/0.10 voter",
     net_v["value_net"].iloc[0] / net_v["value"].iloc[0], 0.20 / 0.55, tol=1e-9)
ok("...which is NOT what 2p-1 would give (0.10/0.55)",
   bool(abs(net_v["value_net"].iloc[0] / net_v["value"].iloc[0] - 0.10 / 0.55) > 1e-6), True)

mixed_net = value_households(one_household(3), household_targets([0.8, 0.6, 0.5]),
                             beta=0.60, delta=0.098, kappa=0.25)
ok("net value never exceeds gross value",
   bool((mixed_net["value_net"] <= mixed_net["value"] + 1e-12).all()), True)

# ---------------------------------------------------------------- D
print(" D. serve-base-rate guard (§2.2)")
raises("pre-shift / wrong-vintage scores are refused",
      lambda: assert_serve_base_rate(np.full(1000, 0.717)),
      mention=["SERVE_BASE_RATE"])
try:
    assert_serve_base_rate(np.full(1000, C.SERVE_BASE_RATE))
    print("  [OK] correctly-shifted scores pass silently")
except Exception as e:                                        # noqa: BLE001
    FAILURES.append(f"correctly-shifted scores should not raise: {e}")
    print(f"  [FAIL] correctly-shifted scores raised: {e}")

# ---------------------------------------------------------------- E
print(" E. hilbert_index — locality preservation on a synthetic grid")
GRID = 24
gx, gy = np.meshgrid(np.arange(GRID), np.arange(GRID))
gx, gy = gx.ravel().astype(float) * 100.0, gy.ravel().astype(float) * 100.0
hidx = hilbert_index(gx, gy, order=10)
order = np.argsort(hidx)


def mean_pairwise(xs, ys):
    d = np.hypot(xs[:, None] - xs[None, :], ys[:, None] - ys[None, :])
    iu = np.triu_indices(len(xs), k=1)
    return d[iu].mean()


CHUNK = 24
hilbert_dists, snake_dists = [], []
snake_key = gy + np.where((gy / 100).astype(int) % 2 == 0, gx / 1e6, -gx / 1e6)
snake_order = np.argsort(snake_key)
for i in range(0, len(gx) - CHUNK, CHUNK):
    hi = order[i:i + CHUNK]
    si = snake_order[i:i + CHUNK]
    hilbert_dists.append(mean_pairwise(gx[hi], gy[hi]))
    snake_dists.append(mean_pairwise(gx[si], gy[si]))
ok("hilbert chunks are at least as compact as snake-order chunks",
   bool(np.mean(hilbert_dists) <= np.mean(snake_dists)), True)
print(f"       mean intra-chunk pairwise distance: hilbert={np.mean(hilbert_dists):.1f}  "
     f"snake={np.mean(snake_dists):.1f}")

ok("hilbert_index is deterministic", bool((hilbert_index(gx, gy, 10) == hidx).all()), True)

# ---------------------------------------------------------------- F
print(" F. build_turfs — size bounds, fragment merge, break_metres")


def grid_households(nx, ny, spacing_m=50.0, x0=0.0, y0=0.0):
    xs, ys = np.meshgrid(np.arange(nx) * spacing_m + x0, np.arange(ny) * spacing_m + y0)
    lon0, lat0 = -73.6, 40.8
    lon = lon0 + xs.ravel() / (111_320.0 * np.cos(np.radians(lat0)))
    lat = lat0 + ys.ravel() / 110_540.0
    n = nx * ny
    return split_track(pd.DataFrame({
        "hh_id": np.arange(n), "lat": lat, "lon": lon, "has_geo": np.ones(n, int),
        "ed_key": ["NASSAU|1|1"] * n, "county": ["NASSAU"] * n,
        "n_targets": np.ones(n, int), "household_size": np.ones(n, int),
        "addr_id": np.arange(n), "is_facility": [False] * n,
    }))


hh_grid = grid_households(10, 10)   # 100 households, compact 450x450m block
built = build_turfs(hh_grid, target_doors=10, max_doors=16, min_doors=6,
                   break_metres=100_000, merge_metres=100_000)
sizes = built[built["turf_id"] >= 0]["turf_id"].value_counts()
ok("every household got a turf assignment", int((built["turf_id"] < 0).sum()), 0)
ok("no turf exceeds max_doors", bool((sizes <= 16).all()), True)
ok("total households preserved across turfs", int(sizes.sum()), 100)

# 97 (not a multiple of target_doors) with merge_metres BELOW grid spacing, so
# chunks close exactly at target_doors and the final 7-household remainder is
# genuinely undersized -- exercising _merge_undersized_turfs rather than
# max_doors overshoot tolerance, with enough max_doors headroom to succeed.
hh97 = grid_households(10, 10).iloc[:97].reset_index(drop=True)
hh97["hh_id"] = np.arange(len(hh97))
built97 = build_turfs(hh97, target_doors=10, max_doors=20, min_doors=8,
                      break_metres=100_000, merge_metres=40)
sizes97 = built97[built97["turf_id"] >= 0]["turf_id"].value_counts()
ok("undersized remainder gets merged into a neighbour", bool((sizes97 >= 8).all()), True)
ok("merge respects max_doors", bool((sizes97 <= 20).all()), True)
ok("no household lost in the merge", int(sizes97.sum()), 97)

hh_far = pd.concat([grid_households(5, 5, x0=0, y0=0),
                    grid_households(5, 5, x0=50_000, y0=0)], ignore_index=True)
hh_far["hh_id"] = np.arange(len(hh_far))
built_far = build_turfs(hh_far, target_doors=10, max_doors=40, min_doors=2,
                        break_metres=1000, merge_metres=10)
left = built_far[built_far["lon"] < -73.5]["turf_id"]
right = built_far[built_far["lon"] >= -73.5]["turf_id"]
ok("a 50km gap never shares a turf across it", bool(set(left) & set(right)), False)

print("    ...and the MERGE stage must not undo that break. min_doors is high")
print("    enough here that the 3-household island is undersized and its only")
print("    capacity-eligible neighbour is 50km away")
hh_island = pd.concat([grid_households(5, 5, x0=0, y0=0),
                       grid_households(3, 1, x0=50_000, y0=0)], ignore_index=True)
hh_island["hh_id"] = np.arange(len(hh_island))
built_island = build_turfs(hh_island, target_doors=10, max_doors=40, min_doors=8,
                           break_metres=1000, merge_metres=10)
isl_left = set(built_island[built_island["lon"] < -73.5]["turf_id"])
isl_right = set(built_island[built_island["lon"] >= -73.5]["turf_id"])
ok("an undersized island does NOT merge across the 50km break",
   bool(isl_left & isl_right), False)
ok("it stays its own undersized turf rather than being dropped",
   int((built_island["turf_id"] >= 0).sum()), len(hh_island))

hh_geo_gap = grid_households(6, 6)
hh_geo_gap.loc[0:3, "has_geo"] = 0
hh_geo_gap = split_track(hh_geo_gap.drop(columns=["track"]))
built_gap = build_turfs(hh_geo_gap, target_doors=10, max_doors=16, min_doors=4,
                        break_metres=1000, merge_metres=1000)
ok("ungeocoded households get turf_id -1, not silently dropped",
   int((built_gap.loc[built_gap["has_geo"] == 0, "turf_id"] == -1).all()), 1)
ok("ungeocoded households are still present in the output",
   len(built_gap), len(hh_geo_gap))

raises("build_turfs without a track column is refused",
       lambda: build_turfs(hh_grid.drop(columns=["track"])), mention=["track"])

# ---------------------------------------------------------------- F2
print(" F2. split_track — walk / facility / ungeocoded")
mix = grid_households(4, 4).drop(columns=["track"])
mix.loc[0:2, "is_facility"] = True
mix.loc[3:4, "has_geo"] = 0
tracked = split_track(mix)
ok("facilities are tracked separately", int((tracked["track"] == "facility").sum()), 3)
ok("ungeocoded wins over facility (a building nobody can find)",
   int((tracked["track"] == "ungeocoded").sum()), 2)
ok("the rest are walkable doors", int((tracked["track"] == "walk").sum()), 11)

# ---------------------------------------------------------------- G
print(" G. value_turfs — roll-up, and both sentinels excluded")
hh_grid_targets = pd.DataFrame({
    "person_row": np.arange(100), "household_row": np.arange(100),
    "m_i": np.full(100, 0.5), "m_net_i": np.full(100, 0.4),
})
valued_grid = value_households(built, hh_grid_targets, beta=0.6, delta=0.098, kappa=0.25)
turf_table = value_turfs(valued_grid, doors_per_hour=20)
ok("one row per turf", turf_table["turf_id"].nunique(), len(turf_table))
close("turf value sums household values",
     turf_table["value_dem_ballots"].sum(),
     valued_grid.loc[valued_grid["turf_id"] >= 0, "value"].sum(), tol=1e-6)
close("net margin sums household net values",
     turf_table["value_net_margin"].sum(),
     valued_grid.loc[valued_grid["turf_id"] >= 0, "value_net"].sum(), tol=1e-6)
ok("hours-per-ballot is positive and finite for a turf with value",
   bool((turf_table["hours_per_ballot"] > 0).all()
        & np.isfinite(turf_table["hours_per_ballot"]).all()), True)
ok("the ranked table is sorted by net margin, descending",
   turf_table["value_net_margin"].is_monotonic_decreasing, True)
close("targets_per_door is n_targets/n_doors",
     turf_table["targets_per_door"].iloc[0],
     turf_table["n_targets"].iloc[0] / turf_table["n_doors"].iloc[0])

print("    the ungeocoded bucket (turf_id -1) must never rank as a turf --")
print("    caught for real on production data: a huge scattered, un-walkable")
print("    aggregate summed to more value than any real turf and sorted first")


def sentinel_household(hh_id, turf_id, n_targets):
    return pd.DataFrame({
        "hh_id": [hh_id], "lat": [np.nan], "lon": [np.nan], "has_geo": [0],
        "ed_key": ["NASSAU|1|1"], "county": ["NASSAU"], "n_targets": [n_targets],
        "household_size": [n_targets], "addr_id": [hh_id], "is_facility": [False],
        "track": ["ungeocoded"], "turf_id": [turf_id],
    })


def sentinel_targets(hh_id, n, mass=5.0):
    return pd.DataFrame({"person_row": np.arange(n), "household_row": np.full(n, hh_id),
                         "m_i": np.full(n, mass), "m_net_i": np.full(n, mass)})


mixed_hh = pd.concat([built, sentinel_household(-100, -1, 1_000)], ignore_index=True)
mixed_targets = pd.concat([hh_grid_targets, sentinel_targets(-100, 1_000)], ignore_index=True)
valued_mixed = value_households(mixed_hh, mixed_targets, beta=0.6, delta=0.098, kappa=0.25)
turf_table_mixed = value_turfs(valued_mixed, doors_per_hour=20)
ok("turf_id -1 never appears in the ranked turf table",
   bool((turf_table_mixed["turf_id"] == -1).any()), False)
ok("excluding it doesn't change the count of REAL turfs",
   len(turf_table_mixed), len(turf_table))

print("    turf_id -2 (facilities) must be excluded for the same reason: a")
print("    58-voter tower counted as one 3-minute door made apartment-dense")
print("    turfs the most 'efficient' in the county")
fac_hh = sentinel_household(-200, -2, 300)
fac_hh["track"] = ["facility"]
fac_hh["lat"], fac_hh["lon"], fac_hh["has_geo"] = [40.8], [-73.6], [1]
mixed_hh2 = pd.concat([built, fac_hh], ignore_index=True)
mixed_targets2 = pd.concat([hh_grid_targets, sentinel_targets(-200, 300)], ignore_index=True)
valued_mixed2 = value_households(mixed_hh2, mixed_targets2, beta=0.6, delta=0.098, kappa=0.25)
tt2 = value_turfs(valued_mixed2, doors_per_hour=20)
ok("turf_id -2 never appears in the ranked turf table",
   bool((tt2["turf_id"] == -2).any()), False)
ok("facilities do not inflate any turf's n_doors",
   int(tt2["n_doors"].sum()), int(turf_table["n_doors"].sum()))
close("facilities do not inflate the walk list's value",
     tt2["value_net_margin"].sum(), turf_table["value_net_margin"].sum(), tol=1e-9)

# ---------------------------------------------------------------- G2
print(" G2. rank_facilities — the separate tactic track")
facs = rank_facilities(valued_mixed2)
ok("one row per facility household", len(facs), 1)
ok("facility keeps its own value", bool(facs["value_net_margin"].iloc[0] > 0), True)
ok("facility is tied to the nearest walk turf for hand-off",
   bool(facs["nearest_turf_id"].iloc[0] in set(turf_table["turf_id"])), True)
ok("no facilities in, empty frame out (not a crash)",
   len(rank_facilities(valued_grid)), 0)
close("walk-list value + facility value == total valued households",
     tt2["value_net_margin"].sum() + facs["value_net_margin"].sum(),
     valued_mixed2["value_net"].sum(), tol=1e-9)

# ---------------------------------------------------------------- H
print(" H. assign_arms — determinism and balance (§6.3)")
many_turfs = pd.DataFrame({
    "turf_id": np.arange(200),
    "value_net_margin": np.random.default_rng(0).uniform(0, 5, 200),
    "county": (["NASSAU"] * 100 + ["SUFFOLK"] * 100),
})
a1 = assign_arms(many_turfs, control_fraction=0.08, seed=12345, buffer=True)
a2 = assign_arms(many_turfs, control_fraction=0.08, seed=12345, buffer=True)
ok("same seed -> identical arm assignment", a1["arm"].tolist(), a2["arm"].tolist())
a3 = assign_arms(many_turfs, control_fraction=0.08, seed=999, buffer=True)
ok("a different seed can produce a different assignment",
   bool(a1["arm"].tolist() != a3["arm"].tolist()), True)
frac_control = (a1["arm"] == "control").mean()
ok("control share is in a sane range around control_fraction",
   bool(0.03 <= frac_control <= 0.15), True)
ok("buffer turfs exist and are excluded from both named arms",
   bool((a1["arm"] == "buffer").sum() >= 0), True)
ok("every arm is one the DB CHECK constraint accepts",
   set(a1["arm"]) <= {"treatment", "control", "buffer"}, True)

print("    'unassigned' violates CHECK (arm IN (...)) in 017_turfs.sql, and")
print("    write_supabase would hit it AFTER the TRUNCATE. Fail here instead.")
raises("an all-sentinel turf table raises rather than emitting 'unassigned'",
       lambda: assign_arms(many_turfs.assign(turf_id=-1)), mention=["sentinel"])
raises("an empty turf table raises too", lambda: assign_arms(many_turfs.iloc[:0]))

print()
if FAILURES:
    print(f"{len(FAILURES)} FAILURE(S):")
    for f in FAILURES:
        print(f"  - {f}")
    sys.exit(1)
print("ALL CHECKS PASSED")
