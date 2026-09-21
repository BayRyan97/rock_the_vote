# committees_to_tag.csv — tagging pass

**Date:** 2026-08-09 · **Rows:** 7,145 (unchanged) · **Method:** deterministic rule engine + 29 parallel labeling agents across five depth tiers + a four-front adversarial verification pass

---

## 1. What this file is, and what changed

`committees_to_tag.csv` is the **untagged remainder** left after the 2026-08-08 override pass. Every one of its 7,145 rows arrived with `side`, `labor`, and `corporate_trade` empty — 231,880 gifts and $79.5M carrying no party signal at all.

| | Before | After |
|---|---|---|
| rows with a party `side` | 0 | **2,458 (34.4%)** |
| **share of gifts carrying a side** | **0%** | **51.9%** |
| share of dollars carrying a side | 0% | 49.4% |
| rows with *any* signal (side, labor, or corporate_trade) | 0 | **2,939 (41.1%)** |
| **share of gifts carrying any signal** | **0%** | **68.8%** |
| `labor = true` | 0 | 25 |
| `corporate_trade = true` | 0 | 478 |

Coverage is deliberately front-loaded onto volume. **The top 300 committees by gift count are 93.0% covered.** Among all committees with ≥5 gifts, coverage is 69.7% of gifts. The uncovered mass is the 1–4 gift tail, where a committee name is often the only evidence that exists.

### Where the labels came from

| Source | Rows |
|---|---|
| `rule-engine-2026-08-09` — deterministic, name- or FEC-field-evident | 717 |
| `web-research-2026-08-09` — agent labeling, top tiers web-verified | 6,404 |
| `adversarial-review-2026-08-09` — corrected during verification | 24 |

## 2. The headline finding: rank 1 is NYSUT

`NEW YORKERS FOR A BRIGHTER FUTURE` — 3,547 gifts, $271,308, the largest committee in the file and the item the previous changelog flagged as *"the largest unresolved item and worth one manual NYSBOE filer lookup"* — **is NYSUT's PAC**, the New York State United Teachers' political fund. Now `side = DEM`, `labor = true`, `entity_type = union_pac`, confidence high.

This is the one row where this pass **overturns** the prior file, and it does so with evidence rather than inference. It is also internally consistent: NYSUT's other vehicle, VOTE-COPE, was already coded DEM/labor in the 2026-08-08 pass.

## 3. Method

### 3.1 Deterministic rules first (717 rows)

The strongest available signal is structural, not textual. FEC's `org_tp` field types a committee's sponsor directly: `C`/`W` → corporation, `T` → trade association, `L` → labor organization. That alone resolved 191 rows with no judgment involved.

Name rules covered the rest: party committees, Conservative→REP and Working Families→DEM fusion folding, union keywords, and employer/professional association patterns.

**Four false positives were caught and fixed before the rules were trusted:**

- `CARPENTER FOR THE COUNTY` matched the carpenters'-union pattern. It is **Angie Carpenter**, Islip Town Supervisor (R). Trade-surname tokens now require a plural or explicit union context.
- `BETHPAGE FEDERAL CREDIT UNION PAC` matched "UNION". A credit union is a bank.
- `CITIZENS UNION OF THE CITY OF NEW YORK` is a good-government group.
- `SUSAN B. ANTHONY LIST INC.` and `EVERYTOWN … INC. PAC` matched the corporate name shape on the token "INC." Both are ideological PACs; an ideological guard list now blocks that path.

The `STOP REPUBLICANS` class of name — anti-patterns where a party word means its opposite — is handled by a rule tier that runs *before* the party-name rules.

### 3.2 Tiered agent labeling (6,404 rows)

29 agents over five tiers, with research budget scaled to gift volume:

| Tier | Rank band | Rows | Budget |
|---|---|---|---|
| A | 1–300 | 230 | full web research |
| B | 301–1,000 | 603 | limited search |
| C–E | 1,001–7,145 | 5,571 | knowledge only |

The session's 200-call web-search budget was exhausted during tier A — the same wall the previous pass hit. Tiers C–E were designed as knowledge-only from the start, so this cost accuracy mainly in tier B.

Two errors **in the briefing document itself** were caught by agents and corrected mid-run: Brian X. Foley (D, state senator) had been grouped with Neil Foley (R, Brookhaven councilman), and Nasrin Ahmad was listed as a Democrat when she is a Republican. Both were fixed before the later tiers ran.

### 3.3 Adversarial verification (313 rows re-examined)

Four independent reviewers, each instructed to **refute** rather than confirm:

| Front | Sample | Confirmed | Weak | Refuted |
|---|---|---|---|---|
| Top 70 sided rows by gift volume | 70 | 58 | 10 | 2 |
| Medium-confidence DEM candidate calls | 85 | 76 | 8 | 1 |
| All labor + top 60 corporate/trade flags | 88 | 83 | 3 | 2 |
| REP calls (symmetric control) | 70 | 61 | 9 | 0 |

## 4. The DEM skew is real, not bias

Agent-labeled candidate committees came out **2.7:1 Democratic**, rising to 3.8:1 in the medium-confidence subset. That pattern is exactly what labeler bias would look like, so it was tested directly with a symmetric audit: one reviewer attacking DEM calls, another attacking REP calls under an identical standard.

**Result: 1 refutation in 85 DEM calls (1.2%), 0 in 70 REP calls.** Error rates are symmetric. A 1% error rate cannot produce a 3.8:1 ratio.

The skew is a **composition artifact**, and it decomposes cleanly. Splitting candidate rows by whether the evidence names a Long Island place:

| Stratum | high-conf DEM:REP | medium-conf DEM:REP |
|---|---|---|
| Long Island geography | **0.83** | 1.62 |
| Non-LI (NYC / upstate) | 3.53 | 4.63 |

**Long Island rows lean Republican.** The dataset-wide DEM skew comes entirely from the non-LI tail — NYC candidate committees, where offices are decided in Democratic primaries and a long tail of minor candidates genuinely is 4–5:1 Democratic.

Two further counter-indicators: the blank rate for medium-confidence candidate rows is **53.9%** (against 6.4% at high confidence) — labelers left over half of uncertain rows empty, the opposite of defaulting. And the judicial rule was applied at **98.6%**, with the 10 exceptions breaking in both partisan directions.

A residual within-stratum effect survives (LI χ²=7.79, p≈0.005). The likely mechanism is **confidence-assignment asymmetry**, not side assignment: the briefing document handed labelers a memory list of ~50 sitting Long Island Republican incumbents, so LI Republicans got *recognized* into the high tier while LI Democratic challengers fell to medium. That inflates the high tier's REP share without any row being wrong.

## 5. Conventions applied

**Judicial committees are blank by design — 705 rows, 4.2% of gifts.** Nassau and Suffolk judicial races are routinely cross-endorsed by both major parties, so a side would be fiction. `FRIENDS OF JUDGE X`, `X FOR SUPREME COURT`, surrogate and family court committees all carry an empty `side` with the cross-endorsement noted in `evidence`. This is the single largest deliberate blank category.

**Corporate and trade PACs get `corporate_trade = true` and no side** (478 rows, 17.0% of gifts), per the project decision. The flag carries the signal; no invented lean enters the model.

**Union PACs get `labor = true`, `side = DEM`** — except police, corrections, court officers and sheriffs, whose Long Island endorsements skew Republican or split. Those carry `labor = true` with an empty side (FOP Empire State Lodge, Southold PBA).

**Fusion folding** follows the project convention: Conservative → REP, Working Families → DEM, Independence / Libertarian / Green / Reform / SAM / Forward → neither.

**Party is coded by the line the candidate ran on, not registration** — the `DESENA FOR NORTH HEMPSTEAD` rule.

## 6. Why only 25 labor rows

25 union committees out of 7,145 looks low. It was challenged directly and survives four independent structural checks:

1. **Zero rows in the entire file carry FEC `org_tp = L`.** Every federally registered union separate segregated fund had already been skimmed off by the prior pass. This is the strongest single piece of evidence.
2. A broad union-keyword sweep over all 7,145 names returns 58 hits; the 33 not flagged as labor are all legitimately non-labor (sheriff *candidate* committees, contractors' associations, credit unions, surname collisions). **Zero missed unions.**
3. A union-fund-idiom sweep (COPE, DRIVE, AFSCME PEOPLE, Active Ballot Club, EPEC, FIREPAC, LIUNA) returns 6 hits, 3 already flagged, 3 correctly not.
4. The major Long Island police PACs — Nassau PBA, Suffolk PBA, Suffolk COPS, NYSCOPBA — are **absent from the file entirely**, exactly as expected if the prior pass took them.

What remains is the expected residue: small locals (Iron Workers 40, Sheet Metal 28, IATSE Local One and Local 52, IUOE 891, UA 22) rather than any major PAC.

## 7. Corrections applied from verification

**Overturned (5):**

| Row | Was | Now | Why |
|---|---|---|---|
| `FRIENDS OF JOHN ROUSE` ($103k) | DEM | blank | Suffolk County Court judge — judicial cross-endorsement rule. Highest-dollar error found. |
| `CITIZENS FOR FRED THIELE` ($50k) | DEM | blank | ~15 years a Republican assemblyman, then Independence Party until ~2021, then Democrat. Committee spans all three. |
| `SIMCHA NY` ($116k) | DEM | blank | First-name-only. Simcha Felder ran on Republican and Conservative lines and caucused with the Senate GOP majority 2013–2018. |
| `SUPPORT OUR FIREFIGHTERS AND PARAMEDICS PAC` | labor/DEM | neither | FEC `cmte_tp = O`, an IE-only committee with no labor org type. The keyword rule fired on "FIREFIGHTERS". |
| `FREELANCERS UNION PAC` | labor/DEM | neither | A 501(c)(4) benefits nonprofit; freelancers are legally barred from collective bargaining, so it fails the contract-bargaining test. |

**Downgraded to blank (9)** for unidentifiable sponsors, surname-only collisions, judicial-era committees, or Independence-line generals: `LONG ISLAND FIREFIGHTERS LEGISLATIVE COMMITTEE PAC`, `FRIENDS OF PETER FOX COHALAN`, `EMPIRE STRIKE PAC`, `FRIENDS OF NICK LIBOUS`, `FRIENDS OF TABONE`, `VALESKY FOR SENATE`, `RICHARD DAVIS FOR DISTRICT ATTORNEY`, `NEW YORK WORKERS COMPENSATION ALLIANCE` (re-typed from trade to ideological), `ESSAA`.

Plus 20 confidence demotions where the side survived but the stated evidence did not support `high`.

## 8. Open decision: the Independent Democratic Conference

**This needs a project ruling and is worth more than every error found above combined.**

19 committees in this file, **$341,186**, belong to Independent Democratic Conference members — Klein, Savino, Carlucci, Avella, Alcantara, Hamilton, Valesky, Felder. All are currently coded `DEM`, most at high confidence, because they ran and won on the Democratic line, which is what the project's stated rule requires.

But from 2011 to 2018 the IDC caucused with Senate Republicans and sustained a Republican majority. For a model asking *"what did this donor's money support?"*, DEM is arguably the wrong answer for that window.

The rows are flagged with a new **`idc_caucus`** column (`true`/`false`) so the decision can be made once and applied uniformly without re-deriving the set. Options: leave DEM (current), blank them, or treat `idc_caucus` as its own feature. **No side was changed** — this is surfaced, not decided.

## 9. Known limits

**Dollar coverage reads worse than it is.** The untagged remainder is 43.1% of dollars but only 31.2% of gifts, because a handful of single-gift super-PAC transfers dominate the dollar column: `NEW START NYC` ($6.8M / 6 gifts), `FF PAC` ($2.2M / 1 gift), `RIGHT FOR AMERICA` ($600k / 1 gift). These are committee-to-committee money, not voter behaviour. **Gift coverage — 68.8% — is the number that matters for a voter model.**

**Largest committees still unresolved:** `COMMITTEE FOR FAIR PROPERTY TAXES` ($2.1M / 77 gifts, no sponsor documented anywhere reachable), `NVS VICTORY CAMPAIGN FUND` ($343k / 316 gifts), `KATZ NYS` ($177k — ambiguous between Melinda Katz (D) and Steve Katz (R)), `FRIENDS OF GLENDA JACKSON` ($70k, no matching NY officeholder). These are the rows worth a manual NYSBOE filer lookup.

**Deliberate blanks retained from the prior pass:** AIPAC, `TEAM KENNEDY`, `FRIENDS OF STEVE LEVY`, `NORPAC`, `UNITED DEMOCRACY PROJECT`. All bipartisan or genuinely mixed. Zero contradictions were introduced against the 2026-08-08 corrected file — the 23 overlapping committees agree on every side, with NYSUT the single intentional change.

**3,344 rows are `low` confidence**, and every one of them carries an empty `side` by construction. No low-confidence row asserts a party.

**Not verified:** whether a committee's *donors* lean the same way the committee does. `side` describes the recipient. This was a caveat on the prior pass and remains one here.

**Source URLs are sparse below tier A.** Where an agent did not consult a page, `source_url` is empty rather than invented — the same discipline as the prior pass.

## 10. New columns

| Column | Purpose |
|---|---|
| `corporate_trade` | `true` for bipartisan corporate / trade-association PACs; `side` is empty on all of them |
| `entity_type` | `candidate` (5,308) · `party_committee` (417) · `ideological_pac` · `trade_assoc_pac` · `corporate_pac` · `joint_fundraising` · `leadership_pac` · `union_pac` · `conduit` · `other` |
| `idc_caucus` | `true` for the 19 Independent Democratic Conference committees — see §8 |
| `confidence` | `high` (2,138) · `medium` (1,663) · `low` (3,344) |
| `evidence` | one sentence on every row, populated on all 7,145 |
| `proposed_by` | which pass assigned the label |

**Invariants verified on the final file:** no row has both `labor` and `corporate_trade` true; no `corporate_trade` row carries a side; no `low`-confidence row carries a side; no row has empty evidence; all 7,145 input rows are present exactly once with `rank` preserved.
