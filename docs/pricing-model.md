# Pricing a vendor to maintain 5,000 Python packages for a GSIB

**Fair price on the proxy portfolio: about $7.2M a year ($1,446 per package). The plausible range is $4.7M–$15.0M. The bank's in-house alternative would cost about $14.5M a year.**

This model estimates what a global systemically important bank (GSIB) should expect to pay a vendor to maintain its Python open source dependencies for one year. It extends the base research on [the true cost of a Python package](../README.md). That research priced *upstream* maintenance, meaning what it costs the world to keep a package healthy. Here the question is narrower: what does it cost a vendor to give **one bank** a safe, supported, auditable supply of those packages, and what is a fair price for that service?

- Assumptions: [`assumptions/gsib_vendor.toml`](../assumptions/gsib_vendor.toml). Every number in the model is set there.
- Engine: [`src/oss_cost/pricing.py`](../src/oss_cost/pricing.py). Data builder: [`src/oss_cost/portfolio.py`](../src/oss_cost/portfolio.py)
- Outputs: `results/gsib_quote.csv` (per package), `results/gsib_quote_summary.json` (scenarios)

## The service being priced

| Module | What the vendor does | Shared across clients? |
|---|---|---|
| Curation and compliance | Monitors each package, rebuilds every release from source, scans, signs and publishes to a private index; SBOM, provenance and licence review | Yes |
| Vulnerability response | Triages each advisory, publishes a VEX statement, rebuilds and tests the fixed version | Yes |
| Maintainer of last resort | For dormant packages (no release in 24 months), keeps them working on new Python versions and writes security fixes itself | Yes |
| Bank backports and support | Backports fixes to the older versions the bank still runs; answers the bank's tickets; produces bank-specific audit evidence | No, dedicated |
| Service fixed costs | Account management, the bank's share of a 24x7 security on-call rotation (behind critical-CVE SLAs), regulatory and audit support, private index infrastructure | No, dedicated |
| Upstream funding | Passes the bank's fair share of maintainer funding through to upstream projects, at cost | Pass-through |

## Three prices per package

| Anchor | Meaning |
|---|---|
| **Cost floor** | Vendor's cost to serve the bank. Shared work is split across the vendor's clients. Below this the vendor loses money. |
| **Fair price** | Floor plus a risk load (SLA credits, indemnity) and a vendor margin. Upstream funding passes through at cost. This is the recommended price. |
| **In-house ceiling** | What the bank would spend doing the same work alone: no sharing, bank pay rates, its own 24x7 team and platform. Above this the bank should build, not buy. |

A sound contract sits near the fair price and well below the ceiling. In the base case the fair price is 50% of the ceiling.

## Formulas

For each package *p*:

```
tier_p      = critical if downloads ≥ 100M/month, important if ≥ 5M, else standard
              (or the bank's own criticality: high / medium / low)
dormant_p   = no release in 24 months, or "Development Status :: 7 - Inactive"

E_p         = expected advisories per year
            = [Z × (0.6 × adv_12mo + 0.4 × adv_36mo / 3) + (1 − Z) × prior_tier] × trend
              (credibility weighting: Z = 0.75 on the package's own history)

shared_h_p  = monitoring + min(releases, 52) × intake_hours(native?) + compliance (+ copyleft review)
            + E_p × (advisory_response + own_fix if dormant) + last_resort_tier if dormant
dedicated_h = min(E_p, 12) × (version_lines − 1) × backport_hours × (1.5 if native)
            + tickets_tier × hours_per_ticket + evidence

share_p     = bank_weight / (bank_weight + other clients using p)
              other clients = vendor_clients × adoption_tier (× 0.5 if dormant) − 1

labour_p    = (shared_h_p × share_p + dedicated_h_p) × vendor_rate
upstream_p  = α × ASC_tier × bank_size_weight / paying_pool        (0 if dormant)
fixed_p     = vendor fixed costs × labour_p / Σ labour

floor_p     = labour_p + fixed_p + upstream_p
fair_p      = (labour_p + fixed_p) × (1 + risk_load_tier) / (1 − margin) + upstream_p
ceiling_p   = (shared_h_p + dedicated_h_p) × bank_rate + in-house fixed costs (by hours) + upstream_p
```

## Per-package inputs

`pixi run build-portfolio` collects these from public sources. The bank can add two override columns.

| Column | Source | Drives |
|---|---|---|
| `monthly_downloads` | top-pypi-packages (30 days) | Tier |
| `releases_12mo`, `last_release`, `months_since_release` | PyPI JSON API | Intake hours, dormancy |
| `native` | PyPI: latest release has platform-specific wheels | Build and backport cost |
| `strong_copyleft` | PyPI licence fields (GPL/AGPL, not LGPL) | Legal review hours |
| `inactive_classifier` | PyPI classifiers | Dormancy |
| `advisories_12mo`, `advisories_36mo`, `high_critical_12mo` | GitHub Advisory Database | Expected advisories |
| `bank_criticality` *(optional)* | Bank: `high`, `medium` or `low` | Overrides the download tier |
| `version_lines` *(optional)* | Bank: versions in production | Backport hours |

## Key assumptions

All are in [`assumptions/gsib_vendor.toml`](../assumptions/gsib_vendor.toml) with comments. The ones that matter most:

| Assumption | Base | Rationale |
|---|---|---|
| Vendor rate | $130/h | Base research BLS loaded rate $104.08/h × 1.25 security-specialist premium |
| Bank in-house rate | $175/h | GSIB engineer, fully loaded, NYC/London |
| Vendor clients sharing the work | 30 | Comparable regulated enterprises on the same service |
| Adoption (critical / important / standard) | 90% / 50% / 20% | How many of those clients use a package in each tier |
| Bank weight | 2.0 | A GSIB bears twice an average client's share (scale, SLAs, audits) |
| Version lines in production | 3 / 2 / 1.5 | Version sprawl across thousands of bank applications |
| Advisory priors | 1.23 / 0.27 / 0.11 per year | Measured on the top 5,000 PyPI packages, Nov 2026 |
| Last-resort upkeep, dormant | 120 / 40 / 12 h per year | Python version upgrades, dependency breakage, no upstream help |
| Upstream funding | α 0.8, size weight 5 | Base research fair-share formula: $515 / $243 / $61 per package |
| Risk load | 15% / 10% / 5% | SLA credits, indemnity, liability |
| Vendor margin | 30% | Services-heavy gross margin |

## Results on the proxy portfolio

The bank's real inventory isn't available yet, so the proxy is the **top 5,000 PyPI packages by downloads** (4,999 after one was removed from PyPI). Downloads are driven by transitive dependencies, so this list is a reasonable stand-in for what a large enterprise actually installs. Data as of 1 Nov 2026 (downloads) and 6 Nov 2026 (PyPI, GitHub).

### By tier

| Tier | Packages | Fair price | Per package | Cost floor | In-house ceiling |
|---|---|---|---|---|---|
| Critical | 297 | $1,586,101 | $5,340 | $1,020,829 | $2,473,480 |
| Important | 1,574 | $2,628,018 | $1,670 | $1,788,751 | $5,870,532 |
| Standard | 3,128 | $3,016,801 | $964 | $2,060,449 | $6,154,679 |
| **Total** | **4,999** | **$7,230,920** | **$1,446** | **$4,870,029** | **$14,498,691** |
| *of which dormant* | *982* | *$2,189,401* | *$2,230* | | |

### By service module

| Module | Fair price | Share |
|---|---|---|
| Bank backports and support | $2,964,184 | 41% |
| Maintainer of last resort | $1,393,658 | 19% |
| Curation and compliance | $1,213,264 | 17% |
| Service fixed costs | $861,345 | 12% |
| Upstream funding | $609,284 | 8% |
| Vulnerability response | $189,188 | 3% |

The vendor effort behind the base case is about 17.6 FTE.

### Scenarios

| | Low | Base | High |
|---|---|---|---|
| Fair price / year | $4.71M | $7.23M | $14.96M |
| Per package | $942 | $1,446 | $2,992 |
| Fair as % of in-house ceiling | 35% | 50% | 90% |

Low: 50 vendor clients, 25% margin, fewer version lines, smaller upstream share. High: 15 clients, 35% margin, 1.5× advisory growth, more version lines, $160/h. In the high case the vendor price approaches the cost of doing it in house, so the bank should not accept a quote in that region.

## Market cross-check

Vendors in this market don't publish prices. These figures come from third-party listings and should be treated as rough guides.

| Benchmark | Reported price | Comparable model figure |
|---|---|---|
| ActiveState managed components | ~$180 per component per year (100 minimum) | Curation and compliance: $243 per package |
| HeroDevs Never-Ending Support (EOL frameworks) | $25k–$75k per framework per year | One dormant critical package with no other client sharing the cost (e.g. openpyxl): about $29k |
| Tidelift subscription | $18k–$30k per year (starter); enterprise by quote | Not like for like |
| Anaconda Business | $600 per user per year | At 12,000 Python users, $7.2M; seat-based pricing is a different basis |
| Chainguard Libraries | By quote, licensed by engineering org size | n/a |

The curation module lands close to the only per-component benchmark. The rest of the price is the GSIB premium: version sprawl, SLAs, dormant-package cover and audit support. These are services commodity offerings don't include.

## What moves the price (one assumption at a time, base $7.23M)

| Assumption (low / high) | Low | High |
|---|---|---|
| Vendor clients sharing the work (50 / 15) | −$1.00M | +$2.02M |
| Vendor rate ($110 / $160) | −$1.00M | +$1.50M |
| Last-resort hours (×0.5 / ×2) | −$0.70M | +$1.39M |
| Bank weight (1 / 3) | −$1.11M | +$0.82M |
| Version lines (−0.5 / +1) | −$0.63M | +$1.25M |
| Support tickets (×0.5 / ×2) | −$0.60M | +$1.20M |
| Upstream funding (off / weight 10) | −$0.61M | +$0.61M |
| Vendor margin (25% / 35%) | −$0.44M | +$0.51M |
| Advisory trend (×1.0 / ×1.5) | — | +$0.64M |

**For negotiation:** ask the vendor how many clients share each package's work. It is the largest lever, and the vendor knows it while the bank doesn't. Without that number, a vendor can price as if the bank were its only customer.

**What the bank controls:** fewer version lines in production and fewer dormant dependencies cut the price directly. Removing half the dormant packages saves about $1.1M a year, and running one fewer version line in every tier saves about $1.1M. Both also reduce real risk.

## Pricing your own manifests

The [calculator](https://claude.ai/artifact/NjrrSZLae5wTKbj8pkChD9) accepts any number of Python manifests and prices the packages they name. Files are parsed in the browser and are not uploaded.

| Supported | Notes |
|---|---|
| `requirements*.txt`, `*.in`, `pip freeze` output | Options, hashes, URLs and local paths are skipped; `#egg=` names are kept |
| `pyproject.toml` | PEP 621 dependencies and extras, dependency groups, Poetry, PDM, uv and pixi tables |
| `pixi.toml`, `environment.yml` | Conda names are kept only when they are Python packages in the dataset; a few are mapped (pytorch → torch) |
| `Pipfile`, `setup.cfg`, `setup.py` | `setup.py` is read with a pattern match, not executed |
| `uv.lock`, `poetry.lock`, `pdm.lock`, `Pipfile.lock`, `pixi.lock`, `conda-lock.yml` | **Recommended**: lock files include transitive dependencies, which the vendor also maintains. pixi.lock conda entries count only when they map to a PyPI package |

Packages are matched against the top 15,000 PyPI packages (`data/reference_portfolio.csv`). A package outside that list (private, niche or misspelled) is priced as an active standard-tier package with no advisory history, and is flagged; it can be left out.

Two ways to price a manifest:

- **Share of the bank-wide program** (default): the manifest's packages carry fixed costs at the rate the full 5,000-package program sets. Use it to ask what one application's dependencies contribute to the bank's bill.
- **Standalone engagement**: the manifest carries all of the vendor's fixed costs (account management, on-call, audit support, platform). A small manifest then looks expensive, because a dedicated service for a few dozen packages is.

For live data on every package, including those outside the top 15,000, use the command line: it accepts the same formats.

## Using it on the bank's inventory

```bash
# any supported manifest or lock file; for several, concatenate requirements or run once per file
pixi run build-portfolio --packages uv.lock --out data/bank_portfolio.csv
# optionally fill bank_criticality / version_lines in data/bank_portfolio.csv
pixi run price --portfolio data/bank_portfolio.csv --out results/bank_quote.csv
```

Packages outside the top 15,000 by downloads get 0 downloads and default to the standard tier; set `bank_criticality` for any that matter more. Edit or add `[scenarios.*]` tables in the TOML to test negotiation positions.

## Limitations

- **Sharing assumptions are unverified.** Client count, adoption and bank weight are estimates; vendors don't disclose them.
- **Hour standards are estimates.** Per-task hours are informed judgement, not time-and-motion data. Vendor automation could lower intake and monitoring hours; the curation benchmark suggests the base values are in the right range.
- **The proxy is not the bank's portfolio.** A GSIB's real inventory will include internal-only and niche packages that are probably dormant more often and cost more per package.
- **Dormancy is measured by release date.** Some dormant packages are simply finished, so last-resort hours may be overstated for tiny, stable libraries and understated for large ones.
- **Advisory volume is rising fast.** Top-5,000 packages had 1,462 advisories in the last 12 months against 2,005 over three years; the trend multiplier exists to test continued growth.
- **Liability is priced as a percentage.** The risk load doesn't model the actual indemnity terms, which should be priced from the contract.
- **Market prices are unofficial.** Benchmarks come from third-party listings, not vendor price lists.

## Sources

- Top PyPI packages (30-day downloads): https://hugovk.github.io/top-pypi-packages/
- PyPI JSON API: https://docs.pypi.org/api/json/
- GitHub Advisory Database API: https://docs.github.com/en/rest/security-advisories/global-advisories
- ActiveState pricing (third party): https://us.fitgap.com/products/activestate-platform
- HeroDevs pricing: https://www.herodevs.com/pricing ; buyer data: https://www.vendr.com/marketplace/herodevs
- Tidelift pricing (third party): https://sourceforge.net/software/product/Tidelift/
- Anaconda pricing: https://www.anaconda.com/pricing/individuals
- Chainguard pricing: https://www.chainguard.dev/pricing
- Base research inputs (BLS rates, ASC tiers, fair-share formula): [README](../README.md)
