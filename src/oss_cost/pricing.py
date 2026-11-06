"""Price a vendor service that maintains a portfolio of Python packages for one bank.

    pixi run price                                     # proxy portfolio, all scenarios
    pixi run price --portfolio data/my_inventory.csv   # the bank's own enriched inventory

Per package it reports a cost floor, a fair price and an in-house ceiling. The formulas
are documented in docs/pricing-model.md; every input lives in assumptions/*.toml.
"""

import argparse
import copy
import csv
import json
import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel

TIERS = ("critical", "important", "standard")
CRITICALITY_TIER = {"high": "critical", "medium": "important", "low": "standard"}


class ByTier(BaseModel):
    critical: float
    important: float
    standard: float

    def __getitem__(self, tier: str) -> float:
        return getattr(self, tier)


class Engagement(BaseModel):
    vendor_clients: float
    bank_weight: float


class Rates(BaseModel):
    vendor_hourly_cost: float
    bank_hourly_cost: float
    hours_per_fte: float


class Classification(BaseModel):
    critical_min_downloads: float
    important_min_downloads: float
    dormant_months: float


class SharedHours(BaseModel):
    monitoring: float
    intake_per_release_pure: float
    intake_per_release_native: float
    max_billable_releases: int
    compliance: float
    copyleft_review: float
    advisory_response: float
    advisory_own_fix: float
    last_resort: ByTier


class DedicatedHours(BaseModel):
    version_lines: ByTier
    backport_per_line: float
    max_backport_events: float
    native_backport_multiplier: float
    tickets_per_year: ByTier
    hours_per_ticket: float
    evidence: float


class Adoption(BaseModel):
    share: ByTier
    dormant_multiplier: float


class Advisories(BaseModel):
    weight_last_12mo: float
    credibility: float
    prior: ByTier
    trend_multiplier: float


class UpstreamFunding(BaseModel):
    enabled: bool
    asc: ByTier
    commercial_share: float
    paying_pool: float
    bank_size_weight: float


class Fixed(BaseModel):
    customer_success_fte: float
    security_on_call_fte: float
    compliance_audit_fte: float
    platform_infra: float
    onboarding_amortized: float


class InHouse(BaseModel):
    fixed_fte: float
    platform_infra: float


class Pricing(BaseModel):
    risk_load: ByTier
    target_margin: float


class Assumptions(BaseModel):
    engagement: Engagement
    rates: Rates
    classification: Classification
    shared_hours: SharedHours
    dedicated_hours: DedicatedHours
    adoption: Adoption
    advisories: Advisories
    upstream_funding: UpstreamFunding
    fixed: Fixed
    inhouse: InHouse
    pricing: Pricing

    @property
    def vendor_fixed_total(self) -> float:
        f, r = self.fixed, self.rates
        fte = f.customer_success_fte + f.security_on_call_fte + f.compliance_audit_fte
        return (
            fte * r.hours_per_fte * r.vendor_hourly_cost + f.platform_infra + f.onboarding_amortized
        )

    @property
    def inhouse_fixed_total(self) -> float:
        r = self.rates
        return (
            self.inhouse.fixed_fte * r.hours_per_fte * r.bank_hourly_cost
            + self.inhouse.platform_infra
        )


def apply_overrides(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of `base` with dotted-key overrides (e.g. "pricing.target_margin")."""
    out = copy.deepcopy(base)
    for dotted, value in overrides.items():
        node = out
        *parents, leaf = dotted.split(".")
        for key in parents:
            node = node[key]
        if leaf not in node:
            raise KeyError(f"Unknown assumption in scenario override: {dotted}")
        node[leaf] = value
    return out


def load_scenarios(path: Path) -> dict[str, Assumptions]:
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    scenarios = raw.pop("scenarios", {})
    result = {"base": Assumptions.model_validate(raw)}
    for name, overrides in scenarios.items():
        result[name] = Assumptions.model_validate(apply_overrides(raw, overrides))
    return result


# ---------------------------------------------------------------------------
# Per-package model
# ---------------------------------------------------------------------------


def as_bool(v: Any) -> bool:
    return str(v).strip().lower() in ("true", "1", "yes", "y")


def as_float(v: Any, default: float = 0.0) -> float:
    return float(v) if str(v).strip() not in ("", "None") else default


def classify(row: dict[str, Any], a: Assumptions) -> tuple[str, bool]:
    """(tier, dormant) for a package row."""
    c = a.classification
    if crit := str(row.get("bank_criticality") or "").strip().lower():
        if crit not in CRITICALITY_TIER:
            raise ValueError(f"{row['package']}: bank_criticality must be high, medium or low")
        tier = CRITICALITY_TIER[crit]
    else:
        downloads = as_float(row.get("monthly_downloads"))
        tier = (
            "critical"
            if downloads >= c.critical_min_downloads
            else "important"
            if downloads >= c.important_min_downloads
            else "standard"
        )
    months = row.get("months_since_release")
    dormant = as_bool(row.get("inactive_classifier")) or (
        str(months).strip() != "" and as_float(months) >= c.dormant_months
    )
    return tier, dormant


def expected_advisories(row: dict[str, Any], tier: str, a: Assumptions) -> float:
    """Credibility-weighted expected advisories per year."""
    adv = a.advisories
    observed = (
        adv.weight_last_12mo * as_float(row.get("advisories_12mo"))
        + (1 - adv.weight_last_12mo) * as_float(row.get("advisories_36mo")) / 3
    )
    return (adv.credibility * observed + (1 - adv.credibility) * adv.prior[tier]) * (
        adv.trend_multiplier
    )


def bank_share(tier: str, dormant: bool, a: Assumptions) -> float:
    """Bank's share of the vendor's shared work on this package."""
    adoption = a.adoption.share[tier] * (a.adoption.dormant_multiplier if dormant else 1)
    others = max(0.0, a.engagement.vendor_clients * adoption - 1)
    w = a.engagement.bank_weight
    return w / (w + others)


def price_package(row: dict[str, Any], a: Assumptions) -> dict[str, Any]:
    """Variable cost components for one package (fixed costs are allocated later)."""
    sh, dh = a.shared_hours, a.dedicated_hours
    tier, dormant = classify(row, a)
    native = as_bool(row.get("native"))
    exp_adv = expected_advisories(row, tier, a)
    releases = min(as_float(row.get("releases_12mo")), sh.max_billable_releases)
    intake = sh.intake_per_release_native if native else sh.intake_per_release_pure

    shared = {
        "monitoring": sh.monitoring,
        "release_intake": releases * intake,
        "compliance": sh.compliance
        + (sh.copyleft_review if as_bool(row.get("strong_copyleft")) else 0),
        "advisory_response": exp_adv
        * (sh.advisory_response + (sh.advisory_own_fix if dormant else 0)),
        "last_resort": sh.last_resort[tier] if dormant else 0.0,
    }
    lines = as_float(row.get("version_lines"), dh.version_lines[tier])
    dedicated = {
        "backports": min(exp_adv, dh.max_backport_events)
        * max(0.0, lines - 1)
        * dh.backport_per_line
        * (dh.native_backport_multiplier if native else 1),
        "support_tickets": dh.tickets_per_year[tier] * dh.hours_per_ticket,
        "evidence": dh.evidence,
    }
    shared_h, dedicated_h = sum(shared.values()), sum(dedicated.values())
    share = bank_share(tier, dormant, a)
    rate = a.rates.vendor_hourly_cost
    labour = (shared_h * share + dedicated_h) * rate
    # Labour by service module, so each module can be compared with market offerings.
    modules = {
        "curation_and_compliance": (
            shared["monitoring"] + shared["release_intake"] + shared["compliance"]
        )
        * share
        * rate,
        "vulnerability_response": shared["advisory_response"] * share * rate,
        "maintainer_of_last_resort": shared["last_resort"] * share * rate,
        "bank_backports_and_support": dedicated_h * rate,
    }

    uf = a.upstream_funding
    upstream = (
        uf.commercial_share * uf.asc[tier] * uf.bank_size_weight / uf.paying_pool
        if uf.enabled and not dormant
        else 0.0
    )
    return {
        "package": row["package"],
        "tier": tier,
        "dormant": dormant,
        "native": native,
        "expected_advisories": round(exp_adv, 3),
        "version_lines": lines,
        **{f"h_{k}": round(v, 2) for k, v in {**shared, **dedicated}.items()},
        "shared_hours": round(shared_h, 2),
        "dedicated_hours": round(dedicated_h, 2),
        "bank_share_of_shared": round(share, 4),
        "_labour": labour,
        "_modules": modules,
        "_upstream": upstream,
        "_solo_hours": shared_h + dedicated_h,
    }


def price_portfolio(rows: list[dict[str, Any]], a: Assumptions) -> list[dict[str, Any]]:
    priced = [price_package(r, a) for r in rows]
    total_labour = sum(p["_labour"] for p in priced) or 1.0
    total_solo = sum(p["_solo_hours"] for p in priced) or 1.0
    margin, rate_bank = a.pricing.target_margin, a.rates.bank_hourly_cost
    for p in priced:
        fixed = a.vendor_fixed_total * p["_labour"] / total_labour
        floor = p["_labour"] + fixed + p["_upstream"]
        markup = (1 + a.pricing.risk_load[p["tier"]]) / (1 - margin)
        fair = (p["_labour"] + fixed) * markup + p["_upstream"]
        fair_modules = {k: v * markup for k, v in p.pop("_modules").items()}
        fair_modules["service_fixed"] = fixed * markup
        fair_modules["upstream_funding"] = p["_upstream"]
        ceiling = (
            p["_solo_hours"] * rate_bank
            + a.inhouse_fixed_total * p["_solo_hours"] / total_solo
            + p["_upstream"]
        )
        p.update(
            labour_cost=round(p.pop("_labour"), 2),
            fixed_cost_allocated=round(fixed, 2),
            upstream_funding=round(p.pop("_upstream"), 2),
            cost_floor=round(floor, 2),
            fair_price=round(fair, 2),
            inhouse_ceiling=round(ceiling, 2),
            **{f"fair_{k}": round(v, 2) for k, v in fair_modules.items()},
        )
        p.pop("_solo_hours")
    return priced


def summarize(priced: list[dict[str, Any]], a: Assumptions) -> dict[str, Any]:
    def agg(ps: list[dict]) -> dict[str, Any]:
        n = len(ps)
        tot = {k: sum(p[k] for p in ps) for k in ("cost_floor", "fair_price", "inhouse_ceiling")}
        return {
            "packages": n,
            **{k: round(v) for k, v in tot.items()},
            "fair_price_per_package": round(tot["fair_price"] / n, 2) if n else None,
        }

    groups: dict[str, list] = {t: [] for t in TIERS}
    for p in priced:
        groups[p["tier"]].append(p)
    dormant = [p for p in priced if p["dormant"]]
    total = agg(priced)
    vendor_hours = sum(
        p["shared_hours"] * p["bank_share_of_shared"] + p["dedicated_hours"] for p in priced
    )
    return {
        "total": total,
        "by_tier": {t: agg(ps) for t, ps in groups.items()},
        "dormant": agg(dormant),
        "fair_price_by_module": {
            k.removeprefix("fair_"): round(sum(p[k] for p in priced))
            for k in priced[0]
            if k.startswith("fair_") and k != "fair_price"
        },
        "vendor_fte_equivalent": round(
            vendor_hours / a.rates.hours_per_fte
            + (
                a.fixed.customer_success_fte
                + a.fixed.security_on_call_fte
                + a.fixed.compliance_audit_fte
            ),
            1,
        ),
        "fair_as_share_of_ceiling": round(total["fair_price"] / total["inhouse_ceiling"], 3),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def money(v: float) -> str:
    return f"${v:,.0f}"


def print_summary(results: dict[str, dict]) -> None:
    names = list(results)
    print()
    print(f"{'':28}" + "".join(f"{n:>16}" for n in names))

    def line(label: str, fn) -> None:
        print(f"{label:28}" + "".join(f"{fn(results[n]):>16}" for n in names))

    line("Packages", lambda s: f"{s['total']['packages']:,}")
    line("Cost floor (vendor cost)", lambda s: money(s["total"]["cost_floor"]))
    line("FAIR PRICE / year", lambda s: money(s["total"]["fair_price"]))
    line("In-house ceiling / year", lambda s: money(s["total"]["inhouse_ceiling"]))
    line("Fair price per package", lambda s: f"${s['total']['fair_price_per_package']:,.0f}")
    for t in TIERS:
        line(
            f"  per {t} package",
            lambda s, t=t: f"${s['by_tier'][t]['fair_price_per_package']:,.0f}",
        )
    line("  per dormant package", lambda s: f"${s['dormant']['fair_price_per_package']:,.0f}")
    print("Fair price by service module")
    for mod in results[names[0]]["fair_price_by_module"]:
        line(
            f"  {mod.replace('_', ' ')}"[:28], lambda s, m=mod: money(s["fair_price_by_module"][m])
        )
    line("Vendor FTE equivalent", lambda s: f"{s['vendor_fte_equivalent']}")
    line("Fair as % of ceiling", lambda s: f"{s['fair_as_share_of_ceiling']:.0%}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="price", description=__doc__.split("\n\n")[0])
    p.add_argument("--portfolio", type=Path, default=Path("data/proxy_portfolio.csv"))
    p.add_argument("--assumptions", type=Path, default=Path("assumptions/gsib_vendor.toml"))
    p.add_argument("--out", type=Path, default=Path("results/gsib_quote.csv"))
    args = p.parse_args(argv)

    with args.portfolio.open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    scenarios = load_scenarios(args.assumptions)

    results, base_priced = {}, None
    for name, a in scenarios.items():
        priced = price_portfolio(rows, a)
        results[name] = summarize(priced, a)
        if name == "base":
            base_priced = priced

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(base_priced[0]))
        w.writeheader()
        w.writerows(base_priced)
    summary_path = args.out.with_name(args.out.stem + "_summary.json")
    summary_path.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")

    print_summary(results)
    print(f"\nPer-package quote (base): {args.out}\nScenario summary: {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
