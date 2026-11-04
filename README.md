# The True Cost of an Open Source Python Package

A framework and pricing model for putting an annual unit cost on a single open source Python package, applied to **urllib3** with live data collected on 4 November 2026.

- Published brief with interactive calculator: https://claude.ai/code/artifact/9609dae1-cbde-49bf-8dc2-b511fca2a5b1 (public)
- Brief source: `true-cost-of-a-python-package.html`
- Collection and costing: Python package `src/oss_cost/` (pixi); original PowerShell script kept in `legacy/Get-OssUnitCost.ps1`
- urllib3 results: `results/urllib3.json`

## Framework: Total Cost of Stewardship

| Measure | What it answers | urllib3 result |
|---|---|---|
| **Replacement value (RV)** | Cost to rebuild the code and tests from scratch (COCOMO II) | $1.93M. An upper bound on value, not a price. |
| **Annual sustaining cost (ASC)** | Cost per year to maintain it responsibly. **This is the unit cost.** | **$257,304/yr** (range $170k–$370k) |
| **Downstream remediation cost (DRC)** | What dependent projects spend reacting to its security advisories | ≥ $7.5M/yr, about 29 times the ASC |
| **Fair share (FS)** | One company's slice: `α × Σ ASC × (size weight ÷ paying pool)` | About $103/yr per Global 2000 company |

### Formulas

```
RV  = 2.94 × KSLOC^1.0997 × EAF × rate_per_person_month     # EAF 1.287 = high reliability × high complexity

ASC_activity = Σ (workload_i × standard_hours_i) × rate_per_hour
ASC_cocomo   = (lines_added_12mo ÷ total_lines) × RV
ASC_staffing = tier_FTE × loaded_salary + tier_reserve
ASC          = median(ASC_activity, ASC_cocomo, ASC_staffing)

DRC = advisories_12mo × direct_dependents × hours_per_advisory × rate_per_hour

FS_o = α × Σ_p ASC_p × (w_o ÷ N)      # α commercial share, w size weight, N paying pool
```

The ASC is calculated three ways that rest on different evidence, and the model takes the median:
- **Observed workload:** last year's pull requests, issues, releases and advisories, times standard hours each. 0.9 FTE ($170,042).
- **COCOMO maintenance:** 19.2% of the code changed last year, times the rebuild effort. 1.95 FTE ($369,587).
- **Responsible staffing:** two maintainers plus a security role, so no single person is a point of failure. 1.25 FTE + $20k reserve ($257,304).

All hours are priced at the BLS median software developer wage ($133,080, May 2024) divided by 0.701 (wage share of total compensation, BLS ECEC Dec 2025): $189,843/yr, $104.08/hour, $15,820 per 152-hour person-month.

### Responsible-staffing tiers

| Tier | Rule | FTE | Reserve | Annual cost |
|---|---|---|---|---|
| Critical | ≥100M downloads/month or ≥1,000 direct dependents | 1.25 | $20,000 | $257,304 |
| Important | ≥5M downloads/month or ≥100 direct dependents | 0.60 | $7,500 | $121,406 |
| Standard | All other packages | 0.15 | $2,000 | $30,476 |

## urllib3 inputs (collected 4 Nov 2026)

| Input | Value | Source |
|---|---|---|
| Downloads, last 30 days | 1,323,555,688 | pypistats.org |
| Direct / all dependent PyPI packages | 9,045 / 204,350 | deps.dev |
| Product / test source lines | 7,341 / 16,150 | Source of tag 2.8.0 |
| Lines added / deleted, 12 months | 7,812 / 3,416 | GitHub code frequency |
| Annual change traffic | 19.2% | 7,812 ÷ 40,716 lines |
| Pull requests opened / merged | 415 / 160 | GitHub search |
| Issues opened / closed | 63 / 54 | GitHub search |
| Releases, 12 months | 6 | PyPI |
| Security advisories, 12 months (high) | 8 (7) | GitHub Advisory Database |
| Human commit authors, 12 months | 51 | GitHub |
| Share of human commits by top author | 43% (57 of 132) | GitHub |
| Contributors, all time | 423 | GitHub |
| Visible annual funding | $15,525 | Open Collective estimated annual budget |

## Findings

- urllib3 is downloaded 1.32 billion times a month, and 204,350 PyPI packages depend on it.
- Its visible funding covers **6%** of the sustaining cost, leaving an unfunded balance of about **$241,779/yr**.
- Security work is rising fast: 6 advisories in 2026 (5 high severity), out of 22 in the project's whole history.
- Unit prices: $16.20 per million downloads; $28.45 per direct dependent per year; $102.92 per Global 2000 company per year; $0.0089 per Python developer per year.
- **The ask is small.** Keeping a critical package healthy costs each large company about $100 a year. A company that had to maintain its own copy would carry the full $257k alone. The real problem is coordination: every company waits for another to pay.

## Limitations

- Standard hours per task, the staffing tiers, the commercial share (α), size weights and paying pool are policy assumptions. The calculator exposes each as a control.
- COCOMO II was calibrated on commercial projects; volunteer projects with large test suites may sit outside its range.
- Download counts include CI runs and mirrors, so per-download prices understate cost per real user.
- Open Collective is the only funding channel visible from outside. Maintainers may get employer time or grants that don't appear there, so real coverage could exceed 6%. Of $69,331 raised on Open Collective since 2016, about $15,900 came from one long-time maintainer.
- The $7.5M remediation figure counts only public PyPI packages at one hour per advisory and excludes private corporate code, so it is a minimum.

## Running the script

The Python package in `src/oss_cost/` is managed with [pixi](https://pixi.sh). Configuration comes from `.env` and environment variables (pydantic-settings).

```bash
cp .env.example .env            # set GITHUB_TOKEN; unauthenticated GitHub allows only 60 requests/hour
pixi run cost urllib3 --annual-funding 15525
pixi run cost requests
pixi run cost --help            # every model assumption can be overridden per run
pixi run test                   # unit tests, including reproducing results/urllib3.json
```

Model assumptions (wage, effort multiplier, α, pools, test/exclude dirs, output dir) default to the values above. Set an `OSSCOST_*` variable in `.env` to change one for every run, or pass the matching CLI flag (e.g. `--median-wage 140000`) to change it for one run.

| Module | Role |
|---|---|
| `config.py` | `Settings` (pydantic-settings): env/.env config and derived labour rates |
| `collect.py` | PyPI, pypistats, deps.dev and GitHub data collection |
| `sloc.py` | Source-line counting from the GitHub source zip (read in memory) |
| `model.py` | Pure TCS calculation (RV, ASC, DRC, unit costs, funding) |
| `cli.py` | Command line, JSON output and summary |

Results are written to `results/<package>.json`. The Python port was checked end to end against urllib3 on 4 Nov 2026 and reproduced every figure in the saved PowerShell output. The original PowerShell script is kept in `legacy/Get-OssUnitCost.ps1` for reference.

Suggested next step: run the script on 10–20 top packages to replace the illustrative tier counts in the fair-share invoice with measured averages.

## Sources

- Hoffmann, Nagle & Zhou, "The Value of Open Source Software," HBS working paper 24-038 (2024); critique: https://openpath.quest/2024/questioning-the-value-of-open-source-software/ ; summary: https://simonwillison.net/2025/Mar/26/value-of-open-source/
- Tidelift 2024 maintainer report: https://dev.to/tidelift/60-of-maintainers-are-still-not-paid-for-their-work-35jo
- Black Duck OSSRA: https://www.blackduck.com/resources/analyst-reports/open-source-security-risk-analysis.html
- CISA, Lessons from XZ Utils: https://www.cisa.gov/news-events/news/lessons-xz-utils-achieving-more-sustainable-open-source-ecosystem
- Log4Shell figures: https://www.scworld.com/feature/digging-into-the-numbers-one-year-after-log4shell
- Sovereign Tech Agency awards: https://www.phoronix.com/news/STF-2026-systemd-PHP
- Open Source Pledge: https://opensourcepledge.com/about/
- BLS ECEC Dec 2025: https://www.bls.gov/news.release/archives/ecec_03202026.htm
- SlashData developer population 2025: https://www.slashdata.co/blog/global-developer-population-trends-2025-how-many-developers-are-there
- Powering PyPI (Ingram, 2021): https://dustingram.com/articles/2021/04/14/powering-the-python-package-index-in-2021/
- PSF on PyPI AWS spend: https://x.com/ThePSF/status/2090064027216998893
- urllib3 Open Collective: https://opencollective.com/urllib3
- Boehm et al., *Software Cost Estimation with COCOMO II* (2000)
