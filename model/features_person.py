"""features_person.py — turn raw source frames into the flat person table.

Every source (local Parquet cache, *_Unrolled.csv) hands `assemble()` the same
four frames and gets back the persons table the rest of the pipeline expects.
Keeping one implementation matters: the household aggregates, leave-self-out
party shares, donation roll-ups, labels and ED context are all definitions, and
when a second copy of them existed it drifted (see score_voters.py's history).

Input contract — every source must produce these:

    hh       household_uuid, county, town, city, zip_code, address_number,
             street_name, election_district, congressional_district,
             senate_district, assembly_district, legislative_district, lon, lat
    ppl      person_uuid, household_uuid, name, age, party, tier_letter,
             tier_count, donor_key            (row order is authoritative)
    ballots  person_row, year, etype, method  (person_row indexes ppl)
    don      donor_key, source, committee, amount, donation_date
             (already filtered to confirmed + strictly before the cutoff)
"""
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

import config as C

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "build"))
from build import LOW_TIERS  # noqa: E402

# Household aggregate definitions, recovered by fitting against the CSV export's
# own columns. Measured on 29,996 Nassau households: num_reliable 99.9967%,
# max_vote_count 99.9933%, voters_at_address 99.9833% — the residue is
# households whose detail string does not fully parse, which etl.report() now
# reports against the source's declared count.
# num_reliable counts tier X4/X5 specifically — it is not "not low", not
# "letter X", and not "count >= 3"; all three were wrong when tested.
# test_features_person.py encodes that discrimination and re-runs this fit
# against the export whenever data/*_Unrolled.csv is present.
RELIABLE_LETTER, RELIABLE_MIN_COUNT = "X", 4

HH_KEY = "household_uuid"


def household_aggregates(ppl: pd.DataFrame) -> pd.DataFrame:
    tier = (ppl["tier_letter"].fillna("I").astype(str)
            + ppl["tier_count"].fillna(0).astype(int).astype(str))
    tmp = pd.DataFrame({
        HH_KEY: ppl[HH_KEY],
        "tier_count": ppl["tier_count"].fillna(0).astype(int),
        "age": ppl["age"],
        "_low": tier.isin(LOW_TIERS).astype(int),
        "_rel": (ppl["tier_letter"].eq(RELIABLE_LETTER)
                 & (ppl["tier_count"].fillna(0) >= RELIABLE_MIN_COUNT)).astype(int),
    })
    agg = tmp.groupby(HH_KEY, sort=False).agg(
        voters_at_address=("tier_count", "size"),
        max_vote_count=("tier_count", "max"),
        min_vote_count=("tier_count", "min"),
        oldest_age=("age", "max"),
        youngest_age=("age", "min"),
        num_reliable=("_rel", "sum"),
        num_low_engagement=("_low", "sum"),
    )
    agg["engagement_gap"] = agg["max_vote_count"] - agg["min_vote_count"]
    return agg.reset_index()


def leave_one_out_mean(values: pd.Series, key: pd.Series) -> pd.Series:
    """Mean of `values` within each group of `key`, EXCLUDING the row itself.

    Singleton groups get 0.0 — there are no neighbours to average. That
    convention is load-bearing (most households are one person) and was
    previously spelled out here and in features_history.py, two places that
    could drift apart on it silently.
    """
    g = values.groupby(key)
    n = g.transform("size")
    return ((g.transform("sum") - values) / (n - 1)).where(n > 1, 0.0)


def leave_self_out_shares(persons: pd.DataFrame, key: str,
                          prefix: str) -> pd.DataFrame:
    """Party mix of a group EXCLUDING the row itself.

    Excluding self is what makes these safe as a node's own feature; the
    manifest additionally keeps them out of the GNN encoder, because message
    passing would hand a 2-person household the partner's copy, which IS the
    node's own registration.
    """
    # From C.PARTY_CLASS, not a second copy of the fusion folding: y_party is
    # built from it below, and a new fusion line must move the label and these
    # six share features together.
    cls = persons["party"].map(C.PARTY_CLASS)
    is_dem = cls.eq(0).astype(np.float64)
    is_rep = cls.eq(1).astype(np.float64)
    is_blk = persons["party"].eq("BLK").astype(np.float64)
    size = persons.groupby(key)["party"].transform("size").astype(np.float64)
    out = {}
    for name, ind in ((f"{prefix}_dem_share_excl", is_dem),
                      (f"{prefix}_rep_share_excl", is_rep),
                      (f"{prefix}_blk_share_excl", is_blk)):
        out[name] = leave_one_out_mean(ind, persons[key]).astype(np.float32)
    return pd.DataFrame(out, index=persons.index), size


def donation_features(persons: pd.DataFrame, don: pd.DataFrame, cutoff: date):
    """Per-person donation aggregates + the (person_row, committee) edge table."""
    n = len(persons)
    don = don.copy()
    don["committee"] = don["committee"].fillna("")
    up = don["committee"].str.upper()
    don["_dem"] = np.where(up.isin(C.DEM_CONDUITS), don["amount"], 0.0)
    don["_rep"] = np.where(up.isin(C.REP_CONDUITS), don["amount"], 0.0)

    feats = pd.DataFrame(index=persons.index)
    key = persons["donor_key"]
    for src in ("fec", "nyboe"):
        sub = don[don["source"] == src]
        if sub.empty:
            # Same dtypes as the populated branch below. Without the casts, a
            # source with no records produced int64/float64 beside the other
            # source's int16/float32 in the same table -- live on any subset
            # with FEC donors but no NY BOE ones, not just when don is empty.
            feats[f"{src}_n"] = np.zeros(n, np.int16)
            feats[f"{src}_total"] = np.zeros(n, np.float32)
            feats[f"{src}_recency_days"] = np.full(n, np.nan, np.float32)
            continue
        a = sub.groupby("donor_key").agg(n=("amount", "size"),
                                         total=("amount", "sum"),
                                         last=("donation_date", "max"))
        feats[f"{src}_n"] = key.map(a["n"]).fillna(0).astype(np.int16)
        feats[f"{src}_total"] = key.map(a["total"]).fillna(0).astype(np.float32)
        last = pd.to_datetime(key.map(a["last"]), errors="coerce")
        feats[f"{src}_recency_days"] = (
            pd.Timestamp(cutoff) - last).dt.days.astype(np.float32)

    b = don.groupby("donor_key").agg(dem=("_dem", "sum"), rep=("_rep", "sum"),
                                     nc=("committee", "nunique"))
    feats["dem_conduit_total"] = key.map(b["dem"]).fillna(0).astype(np.float32)
    feats["rep_conduit_total"] = key.map(b["rep"]).fillna(0).astype(np.float32)
    feats["n_committees"] = key.map(b["nc"]).fillna(0).astype(np.int16)
    feats["has_donation"] = ((feats["fec_n"] > 0)
                             | (feats["nyboe_n"] > 0)).astype(np.int8)

    # Every person sharing a donor_key inherits the records (the key is
    # NAME|CITY|ZIP5, so this is per-identity, not per-row).
    idx = persons[["donor_key"]].copy()
    idx["person_row"] = np.arange(n, dtype=np.int32)
    donors = don.merge(idx, on="donor_key", how="inner")[
        ["person_row", "committee", "source", "amount"]]
    return feats, donors.reset_index(drop=True)


def add_labels_and_ed_context(persons: pd.DataFrame,
                              elections: pd.DataFrame) -> pd.DataFrame:
    persons["has_geo"] = persons["lat"].notna().astype(np.int8)
    # ED numbers repeat across assembly districts, so the AD must be part of
    # the key — omitting it merges unrelated districts into one split unit and
    # one aggregate group.
    persons["ed_key"] = (persons["county"].astype(str) + "|"
                         + persons["assembly_district"].astype(str) + "|"
                         + persons["election_district"].astype(str))

    E = C.TARGET_GENERAL_YEAR
    ref_year = int(C.REF_DATE[:4])          # ages are current as of the export
    voted = np.zeros(len(persons), dtype=bool)
    sel = (elections["year"] == E) & (elections["etype"] == "G")
    voted[elections.loc[sel, "person_row"].to_numpy()] = True
    eligible = (persons["age"] - (ref_year - E)) >= 18
    persons["y_turnout"] = np.where(eligible, voted.astype(np.int8), -1).astype(np.int8)

    party_class = persons["party"].map(C.PARTY_CLASS)
    party_class = party_class.where(persons["party"] != "BLK", C.PARTY_MASKED)
    persons["y_party"] = party_class.fillna(C.PARTY_OTHER).astype(np.int8)

    ed_shares, ed_n = leave_self_out_shares(persons, "ed_key", "ed")
    persons = pd.concat([persons, ed_shares], axis=1)
    persons["ed_n_voters"] = ed_n
    persons["ed_mean_tier_count"] = persons.groupby("ed_key")["tier_count"].transform("mean")
    return persons


def assemble(hh: pd.DataFrame, ppl: pd.DataFrame, ballots: pd.DataFrame,
             don: pd.DataFrame, cutoff: date):
    """Raw source frames -> (persons, donors, ballots) ready for parquet."""
    if hh.empty or ppl.empty:
        raise SystemExit("source returned no households/people — check filters")

    hh = hh.merge(household_aggregates(ppl), on=HH_KEY, how="right",
                  validate="one_to_one")
    hh["household_row"] = np.arange(len(hh), dtype=np.int32)

    n_before = len(ppl)
    persons = ppl.merge(hh, on=HH_KEY, how="left", validate="many_to_one")
    # ballots.person_row indexes ppl positionally; a merge that reorders or
    # fans out would silently mis-assign every vote history.
    assert len(persons) == n_before, "row count changed; ballots.person_row would be wrong"
    # A person whose household did not match keeps NaN geography, which collapses
    # their ed_key to "nan|nan|nan" AND promotes the district columns to float64,
    # shifting every other row's ed_key too. Sources must drop these before
    # ballots are built (ballots.person_row indexes ppl positionally, so they
    # cannot be dropped here).
    orphaned = persons["county"].isna()
    if orphaned.any():
        raise ValueError(
            f"{int(orphaned.sum()):,} people reference a household absent from "
            f"the household table (e.g. "
            f"{sorted(persons.loc[orphaned, HH_KEY].unique()[:3].tolist())}). "
            f"The source must drop these before ballots are built.")

    hh_shares, size = leave_self_out_shares(persons, HH_KEY, "hh")
    persons = pd.concat([persons, hh_shares], axis=1)
    persons["household_size"] = size.astype(np.int16)

    feats, donors = donation_features(persons, don, cutoff)
    persons = pd.concat([persons, feats], axis=1)

    persons["person_id"] = np.arange(len(persons), dtype=np.int64)
    persons["tier_count"] = persons["tier_count"].fillna(0).astype(np.int16)
    persons["tier_letter"] = persons["tier_letter"].fillna("I").astype(str)
    persons = add_labels_and_ed_context(persons, ballots)
    return persons, donors, ballots
