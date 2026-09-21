#!/usr/bin/env python3
"""matching.py — the one place voter records are matched to campaign-finance filings.

Before this module the same routine existed three times, near-verbatim, in
fetch_fec.py, fetch_fec_bulk.py and fetch_nyboe.py — which is how they drifted
apart (see CITY CASING below). Everything here is pure and dependency-free so
test_matching.py can exercise it with no network, no DB and no voter file.

WHAT CHANGED, and why each change exists. Every number was measured against
production or against data/fec_ny_2018.csv on 2026-08-08.

1. CITY NORMALIZATION — the single biggest recall win available.
   The voter file abbreviates, FEC spells out. In one cycle file FEC carries
   1,647 rows for "EAST ISLIP" against 8 for "E ISLIP"; 643 for "NORTH BABYLON"
   against 1 for "N BABYLON"; 19,305 rows starting "EAST " against 357 starting
   "E ". Because the old confirm test was exact string equality, the 115,109
   voters (6.2%) living in abbreviated-form towns could essentially never be
   confirmed — they were silently demoted to "possible". normalize_city()
   expands both sides before comparison.

2. ZIP5 IS THE PRIMARY LOCALITY KEY, city is corroborating.
   ZIP5 already determines locality, so requiring an exact city string as well
   only added a second way to fail. Confirmation now needs ZIP5 plus a city that
   either agrees or is absent. Enumerating city variants can never be complete;
   this is the structural fix and (1) is the belt to its braces.

3. ZIP+4 IS RETAINED when the source supplies it.
   The old filter did zip[:5] at read time and threw the +4 away. A +4 usually
   identifies one block face, so when both sides have it, it is the only signal
   available that can separate two same-name people in the same town — which is
   the failure donor_key cannot fix on its own.

4. THE POSSIBLE LIST IS RANKED, not truncated arbitrarily.
   The old code kept possible[:10] in index order. 44,619 donors sit at exactly
   that cap, meaning ten-plus candidates were found and the remainder discarded
   unrecorded. Candidates are now scored and the best kept, so the bucket is
   reviewable rather than arbitrary.

5. MIDDLE TOKENS ARE OPTIONAL, not disqualifying.
   The test is voter_tokens subset-of filer_tokens, and subset is directional:
   voter "JOHN M SMITH" cannot match filer "SMITH, JOHN" because the M is extra,
   while voter "JOHN SMITH" matches "SMITH, JOHN MICHAEL" fine. 74,086 voters
   (4.0%) carry three or more tokens. Single-letter and middle tokens are now
   optional on the voter side; first and last must still both be present.

6. NICKNAMES resolve to their formal name. 6,604 voters (0.36%) have a
   diminutive as their first token.

7. CITY CASING BUG — a correctness fix, not a recall one.
   fetch_fec.py did NOT uppercase city while fetch_fec_bulk.py did, and both
   write the same cache file, which migrate_donations_psycopg2.py then uppercases
   at insert. One person could therefore hold two cache keys that collapse into
   one donor_key on load, DOUBLE-COUNTING their giving. cache_key() is now the
   only way a key is built.

8. MATCH SCORE, so the confirmed/possible split stops being all-or-nothing.
   Every candidate carries an additive 0-100 score and the rule that produced it.
   Downstream can weight evidence instead of discarding it.

DELIBERATELY NOT FIXED: surname suffixes. "SMITH JR" blocks on "JR" and can
never match, which is a real bug — but it affects 29 voters out of 1,854,934.
Left alone on purpose; see test_matching.py.
"""
import re

# ------------------------------------------------------------- the date window

# Both sources are capped at the same start date so the two are COMPARABLE.
# Without this they are not: measured 2026-08-08, NY BOE held 287,357 confirmed
# gifts before 2017 (43% of its confirmed volume, back to the 1990s) while FEC
# held 1,517 (0.4%) — and those 1,517 were an accident of the API path returning
# a donor's 30 most recent gifts with no date bound, not real coverage.
#
# The consequence, stated plainly: any "all history" analysis run across both
# sources would really have been measuring "all NY BOE plus recent FEC". A
# window comparison built on that would attribute a source effect to a time
# effect. Capping both at 2017-01-01 — the first year the FEC bulk cycles on
# disk (2018-2026) actually cover — is what makes the comparison honest.
#
# This is a coverage decision, not a claim that older giving is worthless. It is
# reversible: the NY BOE Socrata API has no date bound, and FEC publishes bulk
# cycles back to 1980, so lowering this constant and re-fetching restores the
# history. See build/consolidate_fec_cycles.py.
WINDOW_START = "2017-01-01"

# Dates outside this range are unusable rather than merely old. The live table
# holds NY BOE rows dated 2034-02-16 and 1921-09-19, and FEC rows from 1977.
WINDOW_END = "2027-12-31"


def in_window(date_str: str, start: str = WINDOW_START, end: str = WINDOW_END) -> bool:
    """True when an ISO yyyy-mm-dd date falls inside the shared window.

    String comparison rather than date parsing: these run tens of millions of
    times during an index build, and ISO dates sort lexicographically. A blank
    or malformed date is excluded — a gift with no usable date cannot carry
    recency, which is the whole point of having a window.
    """
    if not date_str or len(date_str) < 10:
        return False
    return start <= date_str[:10] <= end


# ---------------------------------------------------------------- city rules

# Prefix expansions. Applied to BOTH sides, so it does not matter which source
# abbreviates -- the point is that they agree afterwards.
CITY_PREFIX = {
    "E": "EAST", "W": "WEST", "N": "NORTH", "S": "SOUTH",
    "NO": "NORTH", "SO": "SOUTH",
    "ST": "SAINT", "MT": "MOUNT", "PT": "PORT", "FT": "FORT",
    "NE": "NORTHEAST", "NW": "NORTHWEST", "SE": "SOUTHEAST", "SW": "SOUTHWEST",
}
# Trailing words that carry no locality information and disagree freely between
# sources ("HEMPSTEAD" vs "HEMPSTEAD VILLAGE").
CITY_SUFFIX_NOISE = {"VILLAGE", "VLG", "TOWN", "TWP", "TOWNSHIP", "CITY", "HAMLET"}


def normalize_city(city: str) -> str:
    """Upper, de-punctuate, expand directional/Saint/Mount prefixes, drop noise.

    Deliberately not a full USPS locality database: this handles the classes
    that were measurably costing matches, and anything cleverer belongs behind a
    real address service rather than a hand-maintained list that rots.
    """
    if not city:
        return ""
    s = re.sub(r"[^A-Z ]", " ", str(city).upper())
    parts = [p for p in s.split() if p]
    if not parts:
        return ""
    if parts[0] in CITY_PREFIX:
        parts[0] = CITY_PREFIX[parts[0]]
    while len(parts) > 1 and parts[-1] in CITY_SUFFIX_NOISE:
        parts.pop()
    return " ".join(parts)


# ---------------------------------------------------------------- zip rules

def split_zip(z: str) -> tuple[str, str]:
    """Return (zip5, plus4). plus4 is '' when the source did not supply one."""
    if not z:
        return "", ""
    digits = re.sub(r"[^0-9]", "", str(z))
    if len(digits) >= 9:
        return digits[:5], digits[5:9]
    return digits[:5], ""


# --------------------------------------------------------------- name rules

NICKNAMES = {
    "BOB": "ROBERT", "BOBBY": "ROBERT", "ROB": "ROBERT",
    "BILL": "WILLIAM", "BILLY": "WILLIAM", "WILL": "WILLIAM",
    "MIKE": "MICHAEL", "MICKEY": "MICHAEL",
    "JIM": "JAMES", "JIMMY": "JAMES", "JAMIE": "JAMES",
    "TOM": "THOMAS", "TOMMY": "THOMAS",
    "DAVE": "DAVID", "STEVE": "STEPHEN", "STEVEN": "STEPHEN",
    "DICK": "RICHARD", "RICK": "RICHARD", "RICH": "RICHARD",
    "TONY": "ANTHONY", "CHRIS": "CHRISTOPHER", "DAN": "DANIEL", "DANNY": "DANIEL",
    "JOE": "JOSEPH", "JOEY": "JOSEPH", "TED": "EDWARD", "ED": "EDWARD",
    "EDDIE": "EDWARD", "NED": "EDWARD", "FRANK": "FRANCIS", "FRED": "FREDERICK",
    "GREG": "GREGORY", "JEFF": "JEFFREY", "KEN": "KENNETH", "LARRY": "LAWRENCE",
    "MATT": "MATTHEW", "NICK": "NICHOLAS", "PAT": "PATRICK", "PETE": "PETER",
    "PHIL": "PHILIP", "RAY": "RAYMOND", "RON": "RONALD", "SAM": "SAMUEL",
    "TIM": "TIMOTHY", "VINNY": "VINCENT", "VINCE": "VINCENT", "WALT": "WALTER",
    "SUE": "SUSAN", "SUZY": "SUSAN", "BETH": "ELIZABETH", "LIZ": "ELIZABETH",
    "BETTY": "ELIZABETH", "KATHY": "KATHLEEN", "KATE": "KATHERINE",
    "KATIE": "KATHERINE", "CATHY": "CATHERINE", "PEGGY": "MARGARET",
    "MAGGIE": "MARGARET", "PATTY": "PATRICIA", "TRISH": "PATRICIA",
    "DEB": "DEBORAH", "DEBBIE": "DEBORAH", "BARB": "BARBARA",
    "JEN": "JENNIFER", "JENNY": "JENNIFER", "CINDY": "CYNTHIA",
    "SANDY": "SANDRA", "CHRISTY": "CHRISTINE", "CONNIE": "CONSTANCE",
    "DOTTIE": "DOROTHY", "NANCY": "ANN", "TERRY": "THERESA", "TERI": "THERESA",
}
SUFFIXES = {"JR", "SR", "II", "III", "IV", "V", "MD", "DDS", "ESQ", "PHD", "DO"}


def swap_comma_name(name: str) -> str:
    """'SMITH, JOHN M' -> 'JOHN M SMITH'. Anything without a comma is returned as is.

    Needed because name_tokens folds nicknames on the FIRST token, so a
    "Last, First" string would run the surname through the nickname table --
    'Will, Robert' would become 'WILLIAM ROBERT'. NYC CFB stores a single
    combined NAME field in that order; FEC's own path sidesteps this by
    order-insensitive token sets, but a shared helper is cheaper than a rule
    each caller has to remember.
    """
    s = str(name or "").strip()
    if "," not in s:
        return s
    last, rest = s.split(",", 1)
    return f"{rest.strip()} {last.strip()}".strip()


def name_tokens(name: str) -> list[str]:
    """Alphabetic tokens, uppercased, suffixes removed, first token de-nicknamed."""
    toks = re.findall(r"[A-Z]+", str(name or "").upper())
    toks = [t for t in toks if t not in SUFFIXES]
    if toks:
        toks[0] = NICKNAMES.get(toks[0], toks[0])
    return toks


def blocking_keys(name: str) -> set[str]:
    """Candidate surnames to look the filer index up under.

    Returns a SET, not one value: the old code took the last whitespace token,
    which sends "SMITH JR" to the "JR" bucket. Emitting both the last and the
    second-to-last token costs one extra probe and makes suffixed and
    two-word surnames reachable.
    """
    toks = re.findall(r"[A-Z]+", str(name or "").upper())
    keys = set()
    real = [t for t in toks if t not in SUFFIXES]
    if real:
        keys.add(real[-1])
        if len(real) > 2:
            keys.add(" ".join(real[-2:]))
    if toks:
        keys.add(toks[-1])
    return {k for k in keys if k}


def names_compatible(voter_name: str, filer_tokens: set[str]) -> bool:
    """First and last must both appear; middle names and initials are optional.

    The old rule was strict subset, which is directional and threw away every
    voter carrying a middle token the filer omitted (74,086 voters). A voter
    initial is also allowed to match a filer's full middle name by first letter.
    """
    v = name_tokens(voter_name)
    if len(v) < 2 or not filer_tokens:
        return False
    first, last, middles = v[0], v[-1], v[1:-1]
    if first not in filer_tokens or last not in filer_tokens:
        return False
    for m in middles:
        if m in filer_tokens:
            continue
        if len(m) == 1 and any(t.startswith(m) for t in filer_tokens):
            continue
        # An extra middle token the filer simply did not report is not evidence
        # against the match -- it is the commonest reason a real donor was
        # being missed. It costs score, not the match.
    return True


# -------------------------------------------------------------------- score

CONFIRM_THRESHOLD = 70

# Name ambiguity, measured where the match actually operates: how many registered
# voters share this first+last WITHIN THIS ZIP5.
#
# The measurement first, because it overturned the obvious design. Of 1,854,934
# voters, 97.4% are the only person with their first+last in their own ZIP; 2.4%
# share with one or two others, 0.1% with three to eight, 176 voters with more,
# and nobody shares with fifty. So rarity cannot spread scores across the file --
# 97.4% of cases would land on one tier and the histogram would stay bimodal.
#
# That also means the bimodality is mostly CORRECT rather than a defect: when a
# name is unique in its ZIP, agreeing on ZIP5 really is near-decisive evidence,
# and a score that said otherwise would be miscalibrated.
#
# What rarity is genuinely for is the 47,997 voters who DO share a name in their
# ZIP. They are exactly the people donor_key = NAME|CITY|ZIP5 silently merges
# into one identity, and no address signal can detect it -- the addresses agree,
# that is the problem. So this demotes the ambiguous minority instead of
# rewarding the unambiguous majority, and `name_shared_by` rides along on every
# record so the ambiguity stays visible downstream rather than being buried in a
# single number.
AMBIGUITY_PENALTY = ((1, 0), (3, -15), (9, -25), (10**9, -35))


def ambiguity_penalty(n_sharing: int) -> int:
    for upper, penalty in AMBIGUITY_PENALTY:
        if n_sharing <= upper:
            return penalty
    return AMBIGUITY_PENALTY[-1][1]


def build_name_frequency(voters) -> dict:
    """{(first, last, zip5): n_voters} for names shared by 2+ people in one ZIP.

    Only collisions are stored. 97.4% of names are unique in their ZIP, so
    keeping every key would be 1.83M entries to express a default; the shared
    ones are 22,423. Callers treat a miss as 1.

    `voters` is any iterable of (name, zip_code) pairs -- the fetchers already
    walk the voter file once, so this costs one extra pass over it.
    """
    from collections import Counter
    counts = Counter()
    for name, zip_code in voters:
        toks = name_tokens(name)
        if len(toks) < 2:
            continue
        z5, _ = split_zip(zip_code)
        if z5:
            counts[(toks[0], toks[-1], z5)] += 1
    return {k: v for k, v in counts.items() if v > 1}


class VoterProbe:
    """Everything about one voter, computed once instead of once per candidate.

    This exists for speed, and the speed is not incidental. A surname bucket is
    power-law distributed — the average is 69 filings but SMITH runs to tens of
    thousands — and the first cut of this module recomputed the voter's tokens,
    normalized city and split zip inside the per-candidate loop, then built a
    dict for every record in the bucket before the name test rejected almost all
    of them. Measured on the real corpus that was under 100,000 voters in 20
    minutes, i.e. six hours for the file. Hoisting the voter-side work and
    rejecting on names BEFORE materialising anything is what makes it tractable.
    """
    __slots__ = ("tokens", "first", "last", "middles", "city", "zip5", "zip4",
                 "n_sharing")

    def __init__(self, name, city, zip_code, name_freq=None):
        v = name_tokens(name)
        self.tokens = frozenset(v)
        self.first = v[0] if v else ""
        self.last = v[-1] if len(v) > 1 else ""
        self.middles = tuple(v[1:-1])
        self.city = normalize_city(city)
        self.zip5, self.zip4 = split_zip(zip_code)
        # How many voters share this first+last in this ZIP. Absent map or absent
        # key both mean 1 -- build_name_frequency only stores collisions, and a
        # caller with no map should behave exactly as before this existed.
        self.n_sharing = (name_freq or {}).get((self.first, self.last, self.zip5), 1)

    def name_ok(self, filer_tokens) -> bool:
        """The cheap rejection: first and last must both be present.

        Deliberately identical in meaning to names_compatible() — which is kept
        as the readable reference implementation and is what the tests pin.
        """
        return bool(self.first) and bool(self.last) and \
            self.first in filer_tokens and self.last in filer_tokens


def score_probe(probe, filer_tokens, filer_city_norm, filer_zip5, filer_zip4=""):
    """score_match, but against a prepared probe and pre-normalized filer fields.

    Same rules and same numbers as score_match; only the redundant work is gone.
    test_matching.py asserts the two agree, so they cannot drift.
    """
    if not probe.name_ok(filer_tokens):
        return 0, ["name-incompatible"]

    score = 40
    reasons = ["first+last"]
    if probe.middles and all(
            m in filer_tokens or (len(m) == 1 and any(t.startswith(m) for t in filer_tokens))
            for m in probe.middles):
        score += 10
        reasons.append("middle-agrees")

    if probe.zip5 and probe.zip5 == filer_zip5:
        score += 35
        reasons.append("zip5")
        if probe.zip4 and filer_zip4:
            if probe.zip4 == filer_zip4:
                score += 15
                reasons.append("zip+4")
            else:
                score -= 10
                reasons.append("zip+4-differs")

    if probe.city and filer_city_norm:
        if probe.city == filer_city_norm:
            score += 15
            reasons.append("city")
        else:
            score -= 20
            reasons.append("city-differs")

    # Applied LAST and only when the name is actually shared: the addresses
    # agreeing is exactly what makes this case dangerous, so the penalty has to
    # be able to pull an otherwise-perfect 90 back below the confirm threshold.
    if probe.n_sharing > 1:
        pen = ambiguity_penalty(probe.n_sharing)
        score += pen
        reasons.append(f"shared-name-{probe.n_sharing}")

    return max(0, min(100, score)), reasons


def score_match(voter_name, voter_city, voter_zip, filer_tokens, filer_city, filer_zip,
                name_freq=None):
    """Convenience entry point: score one raw pair without preparing a probe.

    Delegates to score_probe rather than reimplementing the rules. It used to be
    a second copy kept honest by an equivalence test across 5,120 combinations --
    which worked, but only because someone remembered to extend the test every
    time a rule changed. Structural equivalence needs no such discipline.

    The rules themselves, in one place (score_probe):
      40  first and last name both present   -- required; 0 without it
     +10  every middle token agrees, initials matching full names by first letter
     +35  ZIP5 agrees                        -- this is what carries confirmation
     +15  ZIP+4 agrees / -10 if it differs
     +15  city agrees / -20 if it positively differs
      -0/-15/-25/-35 by how many voters share this name in this ZIP
    Confirmed at >= CONFIRM_THRESHOLD (70).
    """
    return score_probe(
        VoterProbe(voter_name, voter_city, voter_zip, name_freq),
        filer_tokens, normalize_city(filer_city), *split_zip(filer_zip))


def classify_rows(rows, voter_name, voter_city, voter_zip, *, possible_cap=10,
                  payload=None, name_freq=None):
    """The fast path: score compact rows, materialise only what survives.

    `rows` is an iterable of (tokens_str, city_norm, zip5, zip4, *rest) where
    tokens_str is "|"-joined sorted name tokens and city_norm is ALREADY
    normalized — both done once at index build rather than tens of millions of
    times here. `payload` maps a row to the dict that ends up in the cache, and
    is called only for candidates that actually score.
    """
    probe = VoterProbe(voter_name, voter_city, voter_zip, name_freq)
    if not probe.first or not probe.last:
        return [], []

    confirmed, possible = [], []
    for row in rows:
        toks = row[0]
        # Substring pre-filter before building a frozenset: on a large bucket
        # this rejects the overwhelming majority for the cost of two scans.
        if probe.first not in toks or probe.last not in toks:
            continue
        ftok = frozenset(toks.split("|"))
        score, reasons = score_probe(probe, ftok, row[1], row[2],
                                     row[3] if len(row) > 3 else "")
        if score <= 0:
            continue
        rec = payload(row) if payload else {}
        rec["match_score"] = score
        rec["match_reasons"] = ",".join(reasons)
        # Ambiguity rides along so downstream can see WHY, not just the number.
        rec["name_shared_by"] = probe.n_sharing
        (confirmed if score >= CONFIRM_THRESHOLD else possible).append(rec)

    confirmed.sort(key=lambda r: -r["match_score"])
    possible.sort(key=lambda r: -r["match_score"])
    return confirmed, possible[:possible_cap]


def classify(candidates, voter_name, voter_city, voter_zip, *, possible_cap=10,
             name_freq=None):
    """Dict-shaped convenience wrapper around classify_rows.

    `candidates` is an iterable of dicts carrying at least name_tokens, city and
    zip; anything else on them (date, amount, committee) is passed through
    untouched so callers keep their own payload shape.

    Delegates rather than reimplementing, for the same reason score_match does:
    a second copy of the loop is a second place for the rules to drift.
    """
    rows, payloads = [], []
    for cand in candidates:
        toks = cand.get("name_tokens") or frozenset()
        rows.append(("|".join(sorted(toks)),
                     normalize_city(cand.get("city", "")),
                     *split_zip(cand.get("zip", "")),
                     len(payloads)))
        payloads.append({k: v for k, v in cand.items() if k != "name_tokens"})
    return classify_rows(rows, voter_name, voter_city, voter_zip,
                         possible_cap=possible_cap, name_freq=name_freq,
                         payload=lambda r: dict(payloads[r[4]]))


def cache_key(name: str, city: str, zip_code: str) -> str:
    """The ONE way a donor cache key is built — NAME|CITY|ZIP5, all uppercase.

    Exists because fetch_fec.py and fetch_fec_bulk.py built this string
    differently (rule 7) and both wrote the same cache file. Note this keeps the
    RAW uppercased city, not the normalized one, so keys stay byte-compatible
    with the existing `people.donor_key` generated column. Normalization is for
    COMPARING, not for keying — changing the key shape would silently orphan
    every existing row.
    """
    z5, _ = split_zip(zip_code)
    return f"{str(name or '').strip().upper()}|{str(city or '').strip().upper()}|{z5}"
