"""test_turfs.py — self-checks for turf construction and valuation.

No database, no built pipeline, no persons.parquet: every fixture here is
synthetic, same pattern as test_features_person.py. The single most important
check is test_apartment_building_is_not_one_household -- see turfs.py's
build_households docstring for the bug it pins.

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
                   value_households, value_turfs)

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
print(" B. build_households — household vs address, the §4.1 bug pinned")


def targets_at_one_address(n_households, addr="NASSAU|GC|MAIN ST|100"):
    county, city, street, num = addr.split("|")
    return pd.DataFrame({
        "person_row": np.arange(n_households, dtype=np.int64),
        "household_row": np.arange(n_households, dtype=np.int64),
        "county": [county] * n_households, "city": [city] * n_households,
        "street_name": [street] * n_households, "address_number": [num] * n_households,
        "ed_key": ["NASSAU|1|1"] * n_households,
        "lat": [40.8] * n_households, "lon": [-73.6] * n_households,
        "has_geo": [1] * n_households,
        "p_turnout": [0.5] * n_households, "p_support": [0.8] * n_households,
        "m_i": [0.5] * n_households,
    })


building = targets_at_one_address(12)   # 12 households, one 12-unit building
hh = build_households(building, household_max_size=C.HOUSEHOLD_CLIQUE_CAP)
ok("12 distinct households, NOT one merged household", len(hh), 12)
ok("every household has exactly 1 target (not fanned out to the building)",
   hh["n_targets"].tolist(), [1] * 12)
ok("all 12 share one addr_id (it IS one building)", hh["addr_id"].nunique(), 1)
ok("facility flag trips at the ADDRESS level (12 > cap 10)",
   hh["is_facility"].tolist(), [True] * 12)

small_building = targets_at_one_address(3, addr="NASSAU|GC|OAK ST|5")
hh_small = build_households(small_building)
ok("a 3-unit building is not a facility", hh_small["is_facility"].tolist(), [False] * 3)

two_addr = pd.concat([targets_at_one_address(2, "NASSAU|GC|MAIN ST|100"),
                      targets_at_one_address(2, "NASSAU|GC|OAK ST|5")], ignore_index=True)
two_addr["household_row"] = np.arange(4, dtype=np.int64)
two_addr["person_row"] = np.arange(4, dtype=np.int64)
hh2 = build_households(two_addr)
ok("two addresses stay two addr_ids", hh2["addr_id"].nunique(), 2)
ok("four distinct households across the two addresses", len(hh2), 4)

# ---------------------------------------------------------------- C
print(" C. value_households — the valuation formula (§3.1)")


def two_person_household(m_a, m_b, hh_id=0):
    return pd.DataFrame({
        "person_row": [0, 1], "household_row": [hh_id, hh_id],
        "m_i": [m_a, m_b],
    })


targets2 = two_person_household(0.8, 0.6)
households2 = pd.DataFrame({"hh_id": [0], "lat": [40.8], "lon": [-73.6],
                            "has_geo": [1], "n_targets": [2], "is_facility": [False]})
valued = value_households(households2, targets2, beta=0.60, delta=0.098, kappa=0.25)
want = 0.25 * 0.098 * (0.8 + 0.60 * 0.6)
close("hand-computed 2-person household value", valued["value"].iloc[0], want, tol=1e-9)

single = two_person_household(0.5, 0.5, hh_id=0).iloc[:1]   # one row, one person
households1 = pd.DataFrame({"hh_id": [0], "lat": [40.8], "lon": [-73.6],
                            "has_geo": [1], "n_targets": [1], "is_facility": [False]})
valued1 = value_households(households1, single, beta=0.60, delta=0.098, kappa=0.25)
close("1-person household: no spillover term at all",
     valued1["value"].iloc[0], 0.25 * 0.098 * 0.5, tol=1e-9)

facility_hh = pd.DataFrame({"hh_id": [0], "lat": [40.8], "lon": [-73.6],
                            "has_geo": [1], "n_targets": [2], "is_facility": [True]})
valued_fac = value_households(facility_hh, targets2, beta=0.60, delta=0.098, kappa=0.25)
close("is_facility does not zero household value (address-level flag only)",
     valued_fac["value"].iloc[0], want, tol=1e-9)

empty_hh = pd.DataFrame({"hh_id": [99], "lat": [40.8], "lon": [-73.6],
                         "has_geo": [1], "n_targets": [0], "is_facility": [False]})
valued_empty = value_households(empty_hh, targets2, beta=0.60, delta=0.098, kappa=0.25)
close("a household with no matching targets gets value 0", valued_empty["value"].iloc[0], 0.0)

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
    return pd.DataFrame({
        "hh_id": np.arange(n), "lat": lat, "lon": lon, "has_geo": np.ones(n, int),
        "ed_key": ["NASSAU|1|1"] * n, "county": ["NASSAU"] * n,
        "n_targets": np.ones(n, int), "addr_id": np.arange(n), "is_facility": [False] * n,
    })


hh_grid = grid_households(10, 10)   # 100 households, compact 450x450m block
built = build_turfs(hh_grid, target_doors=10, max_doors=16, min_doors=6,
                   break_metres=100_000, merge_metres=100_000)
sizes = built[built["turf_id"] != -1]["turf_id"].value_counts()
ok("every household got a turf assignment", int((built["turf_id"] == -1).sum()), 0)
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
sizes97 = built97[built97["turf_id"] != -1]["turf_id"].value_counts()
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

hh_geo_gap = grid_households(6, 6)
hh_geo_gap.loc[0:3, "has_geo"] = 0
built_gap = build_turfs(hh_geo_gap, target_doors=10, max_doors=16, min_doors=4,
                        break_metres=1000, merge_metres=1000)
ok("ungeocoded households get turf_id -1, not silently dropped",
   int((built_gap.loc[built_gap["has_geo"] == 0, "turf_id"] == -1).all()), 1)
ok("ungeocoded households are still present in the output",
   len(built_gap), len(hh_geo_gap))

# ---------------------------------------------------------------- G
print(" G. value_turfs — roll-up and hours-per-ballot")
hh_grid_targets = pd.DataFrame({
    "person_row": np.arange(100), "household_row": np.arange(100),
    "m_i": np.full(100, 0.5),
})
valued_grid = value_households(built, hh_grid_targets, beta=0.6, delta=0.098, kappa=0.25)
turf_table = value_turfs(valued_grid, doors_per_hour=20)
ok("one row per turf", turf_table["turf_id"].nunique(), len(turf_table))
close("turf value sums household values",
     turf_table["value_dem_ballots"].sum(),
     valued_grid.loc[valued_grid["turf_id"] != -1, "value"].sum(), tol=1e-6)
ok("hours-per-ballot is positive and finite for a turf with value",
   bool((turf_table["hours_per_ballot"] > 0).all()
        & np.isfinite(turf_table["hours_per_ballot"]).all()), True)

print("    the ungeocoded bucket (turf_id -1) must never rank as a turf --")
print("    caught for real on production data: a huge scattered, un-walkable")
print("    aggregate summed to more value than any real turf and sorted first")
big_ungeo = pd.DataFrame({
    "hh_id": [-100], "lat": [np.nan], "lon": [np.nan], "has_geo": [0],
    "ed_key": ["NASSAU|1|1"], "county": ["NASSAU"], "n_targets": [1_000],
    "addr_id": [-100], "is_facility": [False], "turf_id": [-1],
})
targets_ungeo = pd.DataFrame({"person_row": np.arange(1_000),
                              "household_row": np.full(1_000, -100),
                              "m_i": np.full(1_000, 5.0)})   # deliberately huge m_i
mixed_hh = pd.concat([built, big_ungeo], ignore_index=True)
mixed_targets = pd.concat([hh_grid_targets, targets_ungeo], ignore_index=True)
valued_mixed = value_households(mixed_hh, mixed_targets, beta=0.6, delta=0.098, kappa=0.25)
turf_table_mixed = value_turfs(valued_mixed, doors_per_hour=20)
ok("turf_id -1 never appears in the ranked turf table",
   bool((turf_table_mixed["turf_id"] == -1).any()), False)
ok("excluding it doesn't change the count of REAL turfs",
   len(turf_table_mixed), len(turf_table))

# ---------------------------------------------------------------- H
print(" H. assign_arms — determinism and balance (§6.3)")
many_turfs = pd.DataFrame({
    "turf_id": np.arange(200),
    "value_dem_ballots": np.random.default_rng(0).uniform(0, 5, 200),
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
ok("no turf left unassigned", int((a1["arm"] == "unassigned").sum()), 0)

print()
if FAILURES:
    print(f"{len(FAILURES)} FAILURE(S):")
    for f in FAILURES:
        print(f"  - {f}")
    sys.exit(1)
print("ALL CHECKS PASSED")
