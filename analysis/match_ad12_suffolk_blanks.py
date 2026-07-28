"""match_ad12_suffolk_blanks.py — Enrich AD-12 Suffolk blank-party registrants.

Reads:
  data/Suffolk_Unrolled.csv   (filter to assembly_district == 12)
  data/nyboe_cache.json
  data/fec_cache.json

Writes:
  data/ad12_suffolk_blanks_scored.csv

Score model (0-100):
  - Prior donation exists:         +40 pts
  - Dem donor (matched keywords):  +15 pts
  - Total donation amount (log):   up to +15 pts
  - Donation count (frequency):    up to +10 pts
  - Recency (donated in 3 yrs):    +10 pts
  - Engaged voter (tier_count>=3): +5 pts
  - High engagement (>=5):         +5 more pts
"""
import json
import math
import re
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

REF_DATE = pd.Timestamp("2026-07-17")
RECENCY_WINDOW_DAYS = 3 * 365

PERSON_PATTERN = re.compile(r"^(.*) \((\d+), ([A-Z]+), ([A-Z0-9]+)\)(?:: (.*))?$")

DEM_KEYWORDS = {
    "DEMOCRAT", "DNC", "DCCC", "DSCC", "HILLARY", "BIDEN", "OBAMA",
    "WARREN", "SCHUMER", "GILLIBRAND", "PROGRESSIVE",
}


def load_caches():
    print("Loading donation caches...")
    with open(DATA / "nyboe_cache.json") as f:
        nyboe = json.load(f)
    with open(DATA / "fec_cache.json") as f:
        fec = json.load(f)
    print(f"  NYBOE: {len(nyboe):,} entries  FEC: {len(fec):,} entries")
    return nyboe, fec


def lookup_donations(first: str, last: str, city: str, zip5: str, nyboe: dict, fec: dict):
    city_u = city.upper().strip()
    z = str(zip5).strip().split(".")[0]
    candidates = [
        f"{first.upper()} {last.upper()}|{city_u}|{z}",
        f"{last.upper()} {first.upper()}|{city_u}|{z}",
    ]
    all_recs = []
    for key in candidates:
        for src, table in (("nyboe", nyboe), ("fec", fec)):
            recs = (table.get(key) or {}).get("confirmed") or []
            for r in recs:
                all_recs.append({
                    "source": src,
                    "date": r.get("date") or "",
                    "amount": r.get("amount") or 0.0,
                    "committee": r.get("committee") or "",
                })
    # deduplicate by (source, date, amount, committee)
    seen = set()
    unique = []
    for r in all_recs:
        key = (r["source"], r["date"], r["amount"], r["committee"])
        if key not in seen:
            seen.add(key)
            unique.append(r)
    return unique


def is_dem_committee(committee: str) -> bool:
    upper = committee.upper()
    return any(kw in upper for kw in DEM_KEYWORDS)


def aggregate_donations(recs: list):
    if not recs:
        return {
            "has_donation": 0,
            "total_donated": 0.0,
            "fec_total": 0.0,
            "nyboe_total": 0.0,
            "num_donations": 0,
            "last_donation_date": "",
            "committees": "",
            "donations_detail": "",
            "dem_donor": 0,
            "dem_total": 0.0,
            "dem_committees": "",
            "recent_donor": 0,
        }

    recs_sorted = sorted(recs, key=lambda r: r["date"], reverse=True)

    total = sum(r["amount"] for r in recs)
    fec_total = sum(r["amount"] for r in recs if r["source"] == "fec")
    nyboe_total = sum(r["amount"] for r in recs if r["source"] == "nyboe")

    dates = [r["date"] for r in recs if r["date"]]
    last_date = max(dates) if dates else ""

    committees = "; ".join(sorted({r["committee"] for r in recs if r["committee"]}))

    # Detail string: newest first, "YYYY-MM-DD $AMOUNT.00 COMMITTEE [source]"
    detail_parts = []
    for r in recs_sorted:
        amt_str = f"${r['amount']:.2f}"
        parts = [r["date"], amt_str, r["committee"], f"[{r['source']}]"]
        detail_parts.append(" ".join(p for p in parts if p))
    donations_detail = "; ".join(detail_parts)

    dem_recs = [r for r in recs if is_dem_committee(r["committee"])]
    dem_total = sum(r["amount"] for r in dem_recs)
    dem_committees = "; ".join(sorted({r["committee"] for r in dem_recs if r["committee"]}))
    dem_donor = int(bool(dem_recs))

    recent = 0
    if last_date:
        recent = int((REF_DATE - pd.Timestamp(last_date)).days <= RECENCY_WINDOW_DAYS)

    return {
        "has_donation": 1,
        "total_donated": round(total, 2),
        "fec_total": round(fec_total, 2),
        "nyboe_total": round(nyboe_total, 2),
        "num_donations": len(recs),
        "last_donation_date": last_date,
        "committees": committees,
        "donations_detail": donations_detail,
        "dem_donor": dem_donor,
        "dem_total": round(dem_total, 2),
        "dem_committees": dem_committees,
        "recent_donor": recent,
    }


def score(row: dict) -> int:
    pts = 0
    if row["has_donation"]:
        pts += 40
        if row["dem_donor"]:
            pts += 15
        amt = row["total_donated"]
        if amt > 0:
            pts += min(15, int(math.log10(amt + 1) * 6))
        pts += min(10, row["num_donations"] * 2)
        if row["recent_donor"]:
            pts += 10
    tc = row["tier_count"]
    if tc >= 3:
        pts += 5
    if tc >= 5:
        pts += 5
    return min(100, pts)


def parse_voting_history(history_str: str) -> str:
    """Return semicolon-separated election entries preserving method text."""
    if not history_str:
        return ""
    entries = []
    for segment in history_str.split(", "):
        segment = segment.strip()
        if not segment:
            continue
        # Each segment is like "2024 GENERAL: Early Voting"
        # Keep the full text as-is; just collect them
        entries.append(segment)
    return "; ".join(entries)


def count_elections(history_str: str) -> int:
    if not history_str:
        return 0
    return sum(1 for s in history_str.split(", ") if s.strip())


def main():
    print("Loading Suffolk_Unrolled.csv (AD-12 only)...")
    df = pd.read_csv(
        DATA / "Suffolk_Unrolled.csv",
        usecols=["address_number", "street_name", "city", "zip_code",
                 "assembly_district", "household_detail"],
        dtype={"assembly_district": str, "zip_code": str},
        low_memory=False,
    )
    df = df[df["assembly_district"] == "12"].copy()
    print(f"  {len(df):,} households in AD-12")

    nyboe, fec = load_caches()

    rows = []
    total_persons = 0
    matched_donations = 0
    dem_donors = 0

    for _, hh in df.iterrows():
        city = str(hh["city"] or "").strip()
        zip5 = str(hh["zip_code"] or "").strip().split(".")[0]
        addr_num = str(hh["address_number"] or "").strip()
        street = str(hh["street_name"] or "").strip()
        address = f"{addr_num} {street}".strip() if addr_num and addr_num != "null" else street

        detail = hh["household_detail"]
        if not isinstance(detail, str) or not detail.strip():
            continue

        for entry in detail.split(" | "):
            entry = entry.strip()
            m = PERSON_PATTERN.match(entry)
            if not m:
                continue

            full_name = m.group(1).strip()
            age = m.group(2)
            party = m.group(3)
            tier = m.group(4)
            history_raw = m.group(5) or ""

            if party != "BLK":
                continue

            total_persons += 1

            # Split name: first word = first name, rest = last name
            name_parts = full_name.split(" ", 1)
            first_name = name_parts[0].title()
            last_name = name_parts[1].title() if len(name_parts) > 1 else ""

            # Tier breakdown
            tier_letter = tier[0] if tier else ""
            tier_count = int(tier[1:]) if len(tier) > 1 and tier[1:].isdigit() else 0

            # Voting history
            voting_history = parse_voting_history(history_raw)
            num_elections_voted = count_elections(history_raw)

            # Donations
            recs = lookup_donations(first_name, last_name, city, zip5, nyboe, fec)
            don = aggregate_donations(recs)

            if don["has_donation"]:
                matched_donations += 1
            if don["dem_donor"]:
                dem_donors += 1

            row = {
                "first_name": first_name,
                "last_name": last_name,
                "address": address,
                "city": city,
                "zip_code": zip5,
                "age": age,
                "party": party,
                "assembly_district": 12,
                "tier": tier,
                "tier_letter": tier_letter,
                "tier_count": tier_count,
                "num_elections_voted": num_elections_voted,
                "voting_history": voting_history,
                **don,
            }
            row["donor_score"] = score(row)
            rows.append(row)

    out = pd.DataFrame(rows)
    out = out.sort_values(
        ["dem_donor", "has_donation", "donor_score"],
        ascending=[False, False, False],
    )

    out_path = DATA / "ad12_suffolk_blanks_scored.csv"
    out.to_csv(out_path, index=False)

    print(f"\nResults:")
    print(f"  Households in AD-12:   {len(df):,}")
    print(f"  BLK persons:           {total_persons:,}")
    print(f"  Donation matches:      {matched_donations:,} ({100*matched_donations/max(total_persons,1):.1f}%)")
    print(f"  Dem donors:            {dem_donors:,} ({100*dem_donors/max(total_persons,1):.1f}%)")
    print(f"  Score distribution:")
    for lo, hi in [(80, 100), (60, 79), (40, 59), (20, 39), (0, 19)]:
        n = out["donor_score"].between(lo, hi).sum()
        print(f"    {lo:3d}-{hi:3d}: {n:,}")
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
