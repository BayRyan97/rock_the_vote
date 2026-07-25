"""match_ld16_primes.py — Match LD 16 Super Primes to donation data and score.

Reads:
  data/LD 16 Super Primes.xlsx
  data/nyboe_cache.json
  data/fec_cache.json
  data/Nassau_Unrolled.csv   (for AD15 flag)

Writes:
  data/ld16_primes_scored.csv

Score model (0–100):
  - Prior donation exists:         +40 pts
  - Total donation amount (log):   up to +20 pts
  - Donation count (frequency):    up to +10 pts
  - Recency (donations in 3 yrs):  +10 pts
  - Has phone or email:            +10 pts
  - Democrat:                       +5 pts
  - Is in AD15:                     flag only (not in score)
"""
import json
import math
import re
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

REF_DATE = pd.Timestamp("2026-07-16")
RECENCY_WINDOW_DAYS = 3 * 365


def load_caches():
    with open(DATA / "nyboe_cache.json") as f:
        nyboe = json.load(f)
    with open(DATA / "fec_cache.json") as f:
        fec = json.load(f)
    return nyboe, fec


def lookup_donations(first: str, last: str, city: str, zip5: str, nyboe: dict, fec: dict):
    """Try both FIRST LAST and LAST FIRST key variants against both caches."""
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
                    "date": r.get("date"),
                    "amount": r.get("amount") or 0,
                    "committee": r.get("committee") or "",
                })
    return all_recs


def aggregate_donations(recs: list):
    if not recs:
        return {
            "total_donated": 0.0,
            "num_donations": 0,
            "last_donation_date": "",
            "committees": "",
            "recent_donor": 0,
            "has_donation": 0,
        }
    total = sum(r["amount"] for r in recs)
    dates = [r["date"] for r in recs if r.get("date")]
    last_date = max(dates) if dates else ""
    committees = "; ".join(sorted({r["committee"] for r in recs if r["committee"]}))
    recent = 0
    if last_date:
        last_ts = pd.Timestamp(last_date)
        recent = int((REF_DATE - last_ts).days <= RECENCY_WINDOW_DAYS)
    return {
        "total_donated": round(total, 2),
        "num_donations": len(recs),
        "last_donation_date": last_date,
        "committees": committees,
        "recent_donor": recent,
        "has_donation": 1,
    }


def score(row: dict) -> int:
    pts = 0
    if row["has_donation"]:
        pts += 40
        # Log-scaled amount bonus up to 20 pts: $10->7, $100->13, $1000->17, $5000->20
        amt = row["total_donated"]
        if amt > 0:
            pts += min(20, int(math.log10(amt + 1) * 7))
        # Frequency bonus up to 10 pts
        pts += min(10, row["num_donations"] * 2)
        # Recency bonus
        if row["recent_donor"]:
            pts += 10
    # Contactability
    has_contact = bool(row.get("preferred_phone") or row.get("cell_phone") or row.get("preferred_email"))
    if has_contact:
        pts += 10
    # Democrat
    if str(row.get("party", "")).upper() == "D":
        pts += 5
    return min(100, pts)


def parse_address(address: str):
    """Split '36 Blanche St' into ('36', 'BLANCHE ST')."""
    address = address.strip()
    m = re.match(r"^(\d+[A-Za-z]?)\s+(.+)$", address)
    if m:
        return m.group(1), m.group(2).upper().strip()
    return "", address.upper().strip()


def build_ad_lookup(nassau_path: Path) -> dict:
    """Build dict: (addr_num, street_upper, zip_str) -> assembly_district."""
    print("Building AD lookup from Nassau_Unrolled.csv...")
    df = pd.read_csv(nassau_path, usecols=["address_number", "street_name", "zip_code", "assembly_district"])
    df["address_number"] = df["address_number"].astype(str).str.strip().str.upper()
    df["street_name"] = df["street_name"].astype(str).str.strip().str.upper()
    df["zip_code"] = df["zip_code"].astype(str).str.strip().str.split(".").str[0]
    lookup = {}
    for _, row in df.iterrows():
        key = (row["address_number"], row["street_name"], row["zip_code"])
        lookup[key] = row["assembly_district"]
    print(f"  {len(lookup):,} address keys loaded")
    return lookup


def main():
    print("Loading LD 16 Super Primes...")
    xl = pd.read_excel(DATA / "LD 16 Super Primes.xlsx")
    print(f"  {len(xl):,} rows")

    print("Loading donation caches...")
    nyboe, fec = load_caches()

    ad_lookup = build_ad_lookup(DATA / "Nassau_Unrolled.csv")

    rows = []
    matched = 0
    for _, p in xl.iterrows():
        first = str(p.get("FirstName") or "").strip()
        last  = str(p.get("LastName") or "").strip()
        city  = str(p.get("City") or "").strip()
        zip5  = str(p.get("Zip5") or "").strip().split(".")[0]
        addr  = str(p.get("Address") or "").strip()

        recs  = lookup_donations(first, last, city, zip5, nyboe, fec)
        don   = aggregate_donations(recs)
        if don["has_donation"]:
            matched += 1

        addr_num, street = parse_address(addr)
        ad = ad_lookup.get((addr_num.upper(), street, zip5), "")
        in_ad15 = int(str(ad) == "15")

        row = {
            "first_name":       first,
            "last_name":        last,
            "address":          addr,
            "city":             city,
            "zip":              zip5,
            "age":              p.get("Age"),
            "party":            p.get("Party"),
            "sex":              p.get("Sex"),
            "preferred_email":  p.get("PreferredEmail"),
            "preferred_phone":  p.get("Preferred Phone"),
            "cell_phone":       p.get("Cell Phone"),
            "precinct":         p.get("PrecinctName"),
            "assembly_district": ad,
            "in_ad15":          in_ad15,
            **don,
        }
        row["donor_score"] = score(row)
        rows.append(row)

    out = pd.DataFrame(rows).sort_values("donor_score", ascending=False)
    out_path = DATA / "ld16_primes_scored.csv"
    out.to_csv(out_path, index=False)

    print(f"\nResults:")
    print(f"  Total primes:      {len(out):,}")
    print(f"  Donation matches:  {matched:,} ({100*matched/len(out):.1f}%)")
    print(f"  In AD15:           {out['in_ad15'].sum():,}")
    print(f"  Score distribution:")
    for bucket in [(80, 100), (60, 79), (40, 59), (20, 39), (0, 19)]:
        n = out["donor_score"].between(bucket[0], bucket[1]).sum()
        print(f"    {bucket[0]:3d}-{bucket[1]:3d}: {n:,}")
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
