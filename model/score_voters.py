#!/usr/bin/env python3
"""
score_voters.py — Train CatBoost models and score all voters for
turnout_prob and dem_lean_prob, then write results back to the DB.

Bypasses the ETL/geocoding pipeline by pulling directly from Supabase.

Usage:
    python3 model/score_voters.py [--quick] [--dry-run]
    --quick    : 150 CatBoost iterations instead of 800 (smoke test)
    --dry-run  : compute scores but don't write to DB
"""
import argparse
import os
import time
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras
from catboost import CatBoostClassifier
from dotenv import load_dotenv
from sklearn.metrics import roc_auc_score, average_precision_score

# ── Config ─────────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env.local")
load_dotenv(ROOT / ".env")
DATABASE_URL = os.environ["DATABASE_URL"]

SEED = 20260710
TARGET_YEAR = 2024
CUTOFF_DATE = date(TARGET_YEAR, 11, 5)
DEM_CONDUITS = {"ACTBLUE"}
REP_CONDUITS = {"WINRED"}


# ── DB helpers ─────────────────────────────────────────────────────────────────

def connect():
    return psycopg2.connect(DATABASE_URL)


def fetch_df(sql: str, conn, batch_size: int = 50_000) -> pd.DataFrame:
    """Stream a large result set via a server-side named cursor to avoid timeouts."""
    chunks = []
    with conn.cursor("fetch_cursor", cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.itersize = batch_size
        cur.execute(sql)
        while True:
            rows = cur.fetchmany(batch_size)
            if not rows:
                break
            chunks.append(pd.DataFrame([dict(r) for r in rows]))
    return pd.concat(chunks, ignore_index=True) if chunks else pd.DataFrame()


# ── Data loading ───────────────────────────────────────────────────────────────

def load_people(conn) -> pd.DataFrame:
    print("Loading people + households …")
    sql = """
        SELECT
            p.id                     AS person_id,
            p.household_id,
            p.name,
            p.age,
            p.party,
            p.tier_letter,
            p.tier_count,
            p.elections,
            p.donor_key,
            p.city,
            p.zip                    AS zip_code,
            h.county,
            h.town,
            h.congressional_district,
            h.senate_district,
            h.assembly_district,
            h.election_district,
            h.lat::float             AS lat,
            h.lon::float             AS lon,
            h.score_total,
            h.score_wake_ups,
            h.score_unaffiliated,
            h.score_dropoff,
            h.ev_score,
            h.people_count           AS hh_people_count
        FROM people p
        JOIN households h ON p.household_id = h.id
    """
    df = fetch_df(sql, conn)
    print(f"  {len(df):,} people loaded")
    return df


def load_donations(conn) -> pd.DataFrame:
    """Aggregate donations per donor_key+source, date-filtered to before the cutoff."""
    print("Loading donations …")
    sql = f"""
        SELECT
            donor_key,
            source,
            SUM(amount)                                          AS total,
            COUNT(*)                                             AS n,
            MAX(donation_date)                                   AS last_date,
            COUNT(DISTINCT committee)                            AS n_committees,
            SUM(CASE WHEN UPPER(committee) = ANY(ARRAY['ACTBLUE'])
                     THEN amount ELSE 0 END)                     AS dem_conduit_total,
            SUM(CASE WHEN UPPER(committee) = ANY(ARRAY['WINRED'])
                     THEN amount ELSE 0 END)                     AS rep_conduit_total
        FROM donations
        WHERE donation_date < '{CUTOFF_DATE}'
           OR donation_date IS NULL
        GROUP BY donor_key, source
    """
    df = fetch_df(sql, conn)
    print(f"  {len(df):,} donor-key×source rows loaded")
    return df


# ── Feature engineering ────────────────────────────────────────────────────────

def parse_elections(elections_col: pd.Series) -> pd.DataFrame:
    """Expand elections JSONB [[year, code], ...] into (person_idx, year, etype) rows."""
    records = []
    for idx, elecs in enumerate(elections_col):
        if not elecs:
            continue
        for item in elecs:
            if isinstance(item, list) and len(item) == 2:
                yr, code = item
                if isinstance(code, str) and len(code) >= 1:
                    records.append((idx, int(yr), code[0].upper()))
    return pd.DataFrame(records, columns=["person_idx", "year", "etype"])


def build_history_features(persons: pd.DataFrame) -> pd.DataFrame:
    """Compute as-of-cutoff voting history features from elections JSONB."""
    n = len(persons)
    E = TARGET_YEAR
    ref_year = 2026

    elec_df = parse_elections(persons["elections"])

    # Only use ballots cast BEFORE the target general (years < E, plus E primary)
    general_before = elec_df[(elec_df["etype"] == "G") & (elec_df["year"] < E)]
    primary_before = elec_df[(elec_df["etype"] == "P") & (elec_df["year"] <= E)]

    # General election years available in the data (before E)
    gen_years = sorted(y for y in elec_df["year"].unique() if y < E)
    if not gen_years:
        gen_years = list(range(2002, E, 2))

    # Participation matrix [n, k_gen_years]
    G = np.zeros((n, len(gen_years)), dtype=bool)
    for _, row in general_before.iterrows():
        yi = next((i for i, y in enumerate(gen_years) if y == row["year"]), None)
        if yi is not None:
            G[row["person_idx"], yi] = True

    def rate_for_years(years_subset):
        if not years_subset:
            return np.zeros(n)
        idx = [gen_years.index(y) for y in years_subset if y in gen_years]
        if not idx:
            return np.zeros(n)
        return G[:, idx].mean(axis=1)

    recent5  = [y for y in gen_years if y >= E - 10 and y % 2 == 0][-5:]
    recent8  = [y for y in gen_years if y >= E - 16 and y % 2 == 0][-8:]
    recent12 = [y for y in gen_years if y >= E - 24 and y % 2 == 0][-12:]

    hist_n_generals        = G.sum(axis=1).astype(np.int16)
    hist_general_rate_5    = rate_for_years(recent5).astype(np.float32)
    hist_general_rate_8    = rate_for_years(recent8).astype(np.float32)
    hist_general_rate_12   = rate_for_years(recent12).astype(np.float32)
    hist_eligible_8        = np.full(n, len(recent8), dtype=np.int8)
    hist_pres_rate         = rate_for_years([y for y in recent8 if y % 4 == 0]).astype(np.float32)
    hist_midterm_rate      = rate_for_years([y for y in recent8 if y % 4 == 2]).astype(np.float32)
    hist_oddyear_rate      = rate_for_years([y for y in gen_years if y % 2 == 1 and y >= E - 16]).astype(np.float32)

    last_gen_year  = general_before.groupby("person_idx")["year"].max().reindex(range(n), fill_value=np.nan).to_numpy()
    first_gen_year = general_before.groupby("person_idx")["year"].min().reindex(range(n), fill_value=np.nan).to_numpy()
    SENTINEL = 99.0
    hist_years_since_last  = np.where(np.isnan(last_gen_year),  SENTINEL, E - last_gen_year).astype(np.float32)
    hist_years_since_first = np.where(np.isnan(first_gen_year), SENTINEL, ref_year - first_gen_year).astype(np.float32)
    hist_never_voted = (hist_n_generals == 0).astype(np.int8)

    # Voted in each of the last 4 generals (g1=most recent before E)
    recent4 = sorted([y for y in gen_years if y < E], reverse=True)[:4]
    while len(recent4) < 4:
        recent4.append(None)

    def voted_in(year):
        if year is None or year not in gen_years:
            return np.zeros(n, dtype=np.int8)
        return G[:, gen_years.index(year)].astype(np.int8)

    hist_voted_g1 = voted_in(recent4[0])
    hist_voted_g2 = voted_in(recent4[1])
    hist_voted_g3 = voted_in(recent4[2])
    hist_voted_g4 = voted_in(recent4[3])

    # Streaks
    yrs_before_idx = [gen_years.index(y) for y in gen_years if y < E]
    if yrs_before_idx:
        sub = G[:, yrs_before_idx]
        longest = np.zeros(n, np.int8)
        run = np.zeros(n, np.int8)
        for col in range(sub.shape[1]):
            run = np.where(sub[:, col], run + 1, np.zeros(n, np.int8)).astype(np.int8)
            longest = np.maximum(longest, run)
        cur_run = np.zeros(n, np.int8)
        for col in range(sub.shape[1] - 1, -1, -1):
            cur_run = np.where(sub[:, col], cur_run + 1, np.zeros(n, np.int8)).astype(np.int8)
        hist_streak_longest = longest
        hist_streak_current = cur_run
    else:
        hist_streak_longest = hist_streak_current = np.zeros(n, np.int8)

    r3 = rate_for_years(sorted([y for y in gen_years if y < E], reverse=True)[:3])
    r5 = rate_for_years(sorted([y for y in gen_years if y < E], reverse=True)[:5])
    hist_trend_3v5 = (r3 - r5).astype(np.float32)

    total_votes = (
        general_before.groupby("person_idx").size().reindex(range(n), fill_value=0).to_numpy()
        + primary_before.groupby("person_idx").size().reindex(range(n), fill_value=0).to_numpy()
    )
    n_primaries = primary_before.groupby("person_idx").size().reindex(range(n), fill_value=0).to_numpy()

    e_primary = elec_df[(elec_df["etype"] == "P") & (elec_df["year"] == E)]
    voted_primary_e = np.zeros(n, np.int8)
    if not e_primary.empty:
        voted_primary_e[e_primary["person_idx"].values] = 1

    return pd.DataFrame({
        "hist_n_votes":           total_votes.astype(np.int16),
        "hist_n_generals":        hist_n_generals,
        "hist_general_rate_5":    hist_general_rate_5,
        "hist_general_rate_8":    hist_general_rate_8,
        "hist_general_rate_12":   hist_general_rate_12,
        "hist_eligible_8":        hist_eligible_8,
        "hist_pres_rate":         hist_pres_rate,
        "hist_midterm_rate":      hist_midterm_rate,
        "hist_oddyear_rate":      hist_oddyear_rate,
        "hist_years_since_last":  hist_years_since_last,
        "hist_years_since_first": hist_years_since_first,
        "hist_never_voted":       hist_never_voted,
        "hist_voted_g1":          hist_voted_g1,
        "hist_voted_g2":          hist_voted_g2,
        "hist_voted_g3":          hist_voted_g3,
        "hist_voted_g4":          hist_voted_g4,
        "hist_streak_current":    hist_streak_current,
        "hist_streak_longest":    hist_streak_longest,
        "hist_trend_3v5":         hist_trend_3v5,
        "hist_n_primaries":       n_primaries.astype(np.int16),
        "hist_voted_primary_cycle": voted_primary_e,
    })


def build_donation_features(persons: pd.DataFrame, donations: pd.DataFrame) -> pd.DataFrame:
    """Pivot donation aggregates onto each person via donor_key."""
    fec   = donations[donations["source"] == "fec"].copy()
    nyboe = donations[donations["source"] == "nyboe"].copy()

    def agg_cols(src_df, prefix):
        if src_df.empty:
            return pd.DataFrame(columns=[f"{prefix}_total", f"{prefix}_n",
                                         f"{prefix}_last", f"{prefix}_nc",
                                         f"{prefix}_dem", f"{prefix}_rep"])
        out = src_df.set_index("donor_key")[["total","n","last_date","n_committees",
                                              "dem_conduit_total","rep_conduit_total"]]
        out.columns = [f"{prefix}_total", f"{prefix}_n", f"{prefix}_last",
                       f"{prefix}_nc", f"{prefix}_dem", f"{prefix}_rep"]
        return out

    fec_a   = agg_cols(fec,   "fec")
    nyboe_a = agg_cols(nyboe, "nyboe")

    df = persons[["donor_key"]].copy().reset_index(drop=True)
    df = df.join(fec_a,   on="donor_key").join(nyboe_a, on="donor_key")

    ref = pd.Timestamp(CUTOFF_DATE)
    for p in ("fec", "nyboe"):
        df[f"{p}_recency_days"] = (ref - pd.to_datetime(df[f"{p}_last"], errors="coerce")).dt.days

    df["fec_total"]   = df["fec_total"].fillna(0).astype(np.float32)
    df["fec_n"]       = df["fec_n"].fillna(0).astype(np.int16)
    df["fec_recency_days"] = df["fec_recency_days"].fillna(9999).astype(np.float32)
    df["nyboe_total"] = df["nyboe_total"].fillna(0).astype(np.float32)
    df["nyboe_n"]     = df["nyboe_n"].fillna(0).astype(np.int16)
    df["nyboe_recency_days"] = df["nyboe_recency_days"].fillna(9999).astype(np.float32)
    df["n_committees"]      = (df["fec_nc"].fillna(0) + df["nyboe_nc"].fillna(0)).astype(np.int16)
    df["dem_conduit_total"] = (df["fec_dem"].fillna(0) + df["nyboe_dem"].fillna(0)).astype(np.float32)
    df["rep_conduit_total"] = (df["fec_rep"].fillna(0) + df["nyboe_rep"].fillna(0)).astype(np.float32)
    df["has_donation"]      = ((df["fec_n"] > 0) | (df["nyboe_n"] > 0)).astype(np.int8)

    return df[["has_donation","fec_n","fec_total","fec_recency_days",
               "nyboe_n","nyboe_total","nyboe_recency_days",
               "n_committees","dem_conduit_total","rep_conduit_total"]].reset_index(drop=True)


def build_household_party_features(persons: pd.DataFrame) -> pd.DataFrame:
    """Leave-self-out household party composition."""
    party_dummies = pd.get_dummies(persons["party"], prefix="p")
    for col in ["p_DEM","p_REP","p_BLK"]:
        if col not in party_dummies.columns:
            party_dummies[col] = 0

    w = persons[["household_id"]].copy().reset_index(drop=True)
    w["dem"] = party_dummies["p_DEM"].values
    w["rep"] = party_dummies["p_REP"].values
    w["blk"] = party_dummies["p_BLK"].values

    hh = w.groupby("household_id")[["dem","rep","blk"]].sum()
    hh["hh_size"] = w.groupby("household_id").size()
    w = w.join(hh, on="household_id", rsuffix="_hh")

    denom = (w["hh_size"] - 1).clip(lower=1)
    return pd.DataFrame({
        "hh_dem_share_excl": ((w["dem_hh"] - w["dem"]) / denom).astype(np.float32).values,
        "hh_rep_share_excl": ((w["rep_hh"] - w["rep"]) / denom).astype(np.float32).values,
        "hh_blk_share_excl": ((w["blk_hh"] - w["blk"]) / denom).astype(np.float32).values,
    })


def build_ed_features(persons: pd.DataFrame) -> pd.DataFrame:
    """Leave-self-out election-district party composition."""
    party_dummies = pd.get_dummies(persons["party"], prefix="p")
    for col in ["p_DEM","p_REP","p_BLK"]:
        if col not in party_dummies.columns:
            party_dummies[col] = 0

    w = persons[["election_district","county"]].copy().reset_index(drop=True)
    w["ed_key"] = w["county"].fillna("") + "_" + w["election_district"].fillna("").astype(str)
    w["dem"] = party_dummies["p_DEM"].values
    w["rep"] = party_dummies["p_REP"].values
    w["blk"] = party_dummies["p_BLK"].values

    ed = w.groupby("ed_key")[["dem","rep","blk"]].sum()
    ed["ed_size"] = w.groupby("ed_key").size()
    w = w.join(ed, on="ed_key", rsuffix="_ed")

    denom = (w["ed_size"] - 1).clip(lower=1)
    return pd.DataFrame({
        "ed_n_voters":       w["ed_size"].astype(np.int32).values,
        "ed_dem_share_excl": ((w["dem_ed"] - w["dem"]) / denom).astype(np.float32).values,
        "ed_rep_share_excl": ((w["rep_ed"] - w["rep"]) / denom).astype(np.float32).values,
        "ed_blk_share_excl": ((w["blk_ed"] - w["blk"]) / denom).astype(np.float32).values,
    })


# ── Labels ─────────────────────────────────────────────────────────────────────

def build_labels(persons: pd.DataFrame):
    """
    y_turnout: 1=voted in TARGET_YEAR general, 0=didn't, -1=ineligible (age<18).
    y_party:   0=DEM/WOR, 1=REP/CON, 2=other; -1=BLK/IND (masked).
    """
    voted_target = np.zeros(len(persons), dtype=np.int8)
    for i, elecs in enumerate(persons["elections"]):
        if not elecs:
            continue
        for item in elecs:
            if isinstance(item, list) and len(item) == 2:
                yr, code = item
                if int(yr) == TARGET_YEAR and isinstance(code, str) and code.startswith("G"):
                    voted_target[i] = 1
                    break

    age_at_election = persons["age"].fillna(0).astype(int) - (2026 - TARGET_YEAR)
    y_turnout = np.where(age_at_election < 18, np.int8(-1), voted_target)

    PARTY_MAP = {"DEM": 0, "WOR": 0, "REP": 1, "CON": 1}
    y_party = persons["party"].map(PARTY_MAP).fillna(2).astype(np.int8)
    y_party[persons["party"].isin(["BLK","IND",""])] = -1
    y_party[persons["party"].isna()] = -1

    return y_turnout.astype(np.int8), y_party.values.astype(np.int8)


# ── Feature lists ──────────────────────────────────────────────────────────────

NUMERIC_TURNOUT = [
    "age",
    "lat","lon",
    "hh_people_count",
    "score_total","score_wake_ups","score_unaffiliated","score_dropoff","ev_score",
    "hh_dem_share_excl","hh_rep_share_excl","hh_blk_share_excl",
    "ed_n_voters","ed_dem_share_excl","ed_rep_share_excl","ed_blk_share_excl",
    "has_donation","fec_n","fec_total","fec_recency_days",
    "nyboe_n","nyboe_total","nyboe_recency_days",
    "n_committees","dem_conduit_total","rep_conduit_total",
    "hist_n_votes","hist_n_generals","hist_general_rate_5","hist_general_rate_8",
    "hist_general_rate_12","hist_eligible_8","hist_pres_rate",
    "hist_midterm_rate","hist_oddyear_rate","hist_years_since_last",
    "hist_years_since_first","hist_never_voted",
    "hist_voted_g1","hist_voted_g2","hist_voted_g3","hist_voted_g4",
    "hist_streak_current","hist_streak_longest","hist_trend_3v5",
    "hist_n_primaries","hist_voted_primary_cycle",
]
CATEGORICAL_TURNOUT = ["party","county","town","city","zip_code",
                        "congressional_district","senate_district","assembly_district"]

NUMERIC_PARTY = [c for c in NUMERIC_TURNOUT
                 if c not in ("hist_n_primaries","hist_voted_primary_cycle")]
NUMERIC_PARTY += ["tier_count"]
NUMERIC_PARTY = list(dict.fromkeys(NUMERIC_PARTY))

CATEGORICAL_PARTY = ["tier_letter","county","town","city","zip_code",
                      "congressional_district","senate_district","assembly_district"]


def make_X(df: pd.DataFrame, numeric: list, categorical: list) -> pd.DataFrame:
    X = pd.DataFrame(index=df.index)
    for c in numeric:
        X[c] = df[c].fillna(0).astype(np.float32) if c in df.columns else np.float32(0)
    for c in categorical:
        X[c] = df[c].astype(str).fillna("NA") if c in df.columns else "NA"
    return X


def spatial_split(persons: pd.DataFrame) -> pd.Series:
    """80/10/10 train/val/test split on election district."""
    rng = np.random.default_rng(SEED)
    ed_keys = (persons["county"].fillna("") + "_" +
               persons["election_district"].fillna("").astype(str))
    unique_eds = np.array(sorted(ed_keys.unique()))
    rng.shuffle(unique_eds)
    n = len(unique_eds)
    train_eds = set(unique_eds[:int(0.8 * n)])
    val_eds   = set(unique_eds[int(0.8 * n):int(0.9 * n)])
    return ed_keys.map(lambda e: "train" if e in train_eds else ("val" if e in val_eds else "test"))


def train_catboost(X, y, cat_idx, split, loss, quick, name):
    model = CatBoostClassifier(
        loss_function=loss,
        iterations=150 if quick else 800,
        learning_rate=0.1,
        depth=6,
        early_stopping_rounds=40,
        random_seed=SEED,
        verbose=100,
    )
    mask = y >= 0
    Xm, ym, sm = X[mask], y[mask], split[mask]
    print(f"  [{name}] train={( sm=='train').sum():,}  val={(sm=='val').sum():,}  test={(sm=='test').sum():,}")
    model.fit(Xm[sm == "train"], ym[sm == "train"],
              cat_features=cat_idx,
              eval_set=(Xm[sm == "val"], ym[sm == "val"]))

    if loss == "Logloss":
        p = model.predict_proba(Xm[sm == "test"])[:, 1]
        print(f"  [{name}] Test AUC={roc_auc_score(ym[sm=='test'], p):.4f}  "
              f"PR-AUC={average_precision_score(ym[sm=='test'], p):.4f}")
    else:
        p = model.predict_proba(Xm[sm == "test"])
        print(f"  [{name}] Test accuracy={(p.argmax(1) == ym[sm=='test']).mean():.4f}")

    return model


# ── DB write-back ──────────────────────────────────────────────────────────────

def write_scores(person_ids, turnout_probs, dem_lean_probs, conn, dry_run):
    if dry_run:
        print(f"  [dry-run] Would update {len(person_ids):,} rows")
        return
    print(f"  Writing {len(person_ids):,} scores to DB …")
    CHUNK = 5000
    with conn.cursor() as cur:
        for i in range(0, len(person_ids), CHUNK):
            batch = list(zip(
                [float(x) for x in turnout_probs[i:i+CHUNK]],
                [float(x) for x in dem_lean_probs[i:i+CHUNK]],
                person_ids[i:i+CHUNK],
            ))
            psycopg2.extras.execute_batch(
                cur,
                "UPDATE people SET turnout_prob=%s, dem_lean_prob=%s WHERE id=%s",
                batch,
                page_size=500,
            )
            if i % (CHUNK * 10) == 0 and i > 0:
                print(f"    {i:,}/{len(person_ids):,}")
    conn.commit()
    print("  Done.")


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick",   action="store_true", help="150 CB iterations (smoke test)")
    ap.add_argument("--dry-run", action="store_true", help="skip DB write-back")
    args = ap.parse_args()

    t0 = time.time()
    conn = connect()

    persons   = load_people(conn)
    donations = load_donations(conn)

    print("Computing history features …")
    hist = build_history_features(persons)

    print("Computing donation features …")
    don = build_donation_features(persons, donations)

    print("Computing household party features …")
    hh_party = build_household_party_features(persons)

    print("Computing ED features …")
    ed = build_ed_features(persons)

    df = pd.concat([persons.reset_index(drop=True), hist, don, hh_party, ed], axis=1)

    print("Building labels …")
    y_turnout, y_party = build_labels(df)

    eligible = y_turnout >= 0
    print(f"  Turnout: {y_turnout[eligible].sum():,} voted / "
          f"{(y_turnout[eligible]==0).sum():,} didn't / "
          f"{(~eligible).sum():,} ineligible (age)")

    labeled_party = y_party >= 0
    print(f"  Party:   {(y_party==0).sum():,} Dem  "
          f"{(y_party==1).sum():,} Rep  "
          f"{(y_party==2).sum():,} other  "
          f"{(~labeled_party).sum():,} masked")

    split = spatial_split(df)

    print("\n── Turnout model ─────────────────────────────────────")
    X_t   = make_X(df, NUMERIC_TURNOUT, CATEGORICAL_TURNOUT)
    cat_t = [X_t.columns.get_loc(c) for c in CATEGORICAL_TURNOUT if c in X_t.columns]
    turnout_model = train_catboost(X_t, y_turnout, cat_t, split, "Logloss", args.quick, "turnout")

    print("\n── Party model ───────────────────────────────────────")
    X_p   = make_X(df, NUMERIC_PARTY, CATEGORICAL_PARTY)
    cat_p = [X_p.columns.get_loc(c) for c in CATEGORICAL_PARTY if c in X_p.columns]
    party_model = train_catboost(X_p, y_party, cat_p, split, "MultiClass", args.quick, "party")

    print("\nScoring all voters …")
    turnout_probs  = turnout_model.predict_proba(X_t)[:, 1]
    party_proba    = party_model.predict_proba(X_p)
    dem_lean_probs = party_proba[:, 0]

    print(f"  turnout_prob  mean={turnout_probs.mean():.3f}  p10={np.percentile(turnout_probs,10):.3f}  p90={np.percentile(turnout_probs,90):.3f}")
    print(f"  dem_lean_prob mean={dem_lean_probs.mean():.3f}  p10={np.percentile(dem_lean_probs,10):.3f}  p90={np.percentile(dem_lean_probs,90):.3f}")

    # Reconnect — the original connection may have idled out during training (3+ hrs)
    try:
        conn.close()
    except Exception:
        pass
    conn2 = connect()
    write_scores(df["person_id"].tolist(), turnout_probs, dem_lean_probs, conn2, args.dry_run)
    conn2.close()
    print(f"\nTotal time: {(time.time()-t0)/60:.1f} min")


if __name__ == "__main__":
    main()
