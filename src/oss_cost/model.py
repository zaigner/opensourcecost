"""Total Cost of Stewardship (TCS) calculation. Pure functions, no I/O."""

from dataclasses import asdict, dataclass, field
from typing import Any

from oss_cost.config import HOURS_PER_YEAR, Settings

# Standard times (hours) for activity-based costing.
STANDARD_HOURS = {
    "merged_pr": 2.5,  # review, CI iteration, merge
    "unmerged_pr": 0.75,  # review / close / redirect
    "issue_triage": 1.5,  # reproduce, label, respond
    "release": 8,  # changelog, verification, publish, backports
    "security_advisory": 40,  # intake, fix, test, coordinated disclosure, CVE, release
    "maintainer_commit": 4,  # maintainer-authored engineering work
    "weekly_upkeep": 4,  # CI, dependency bumps, new Python versions (per week)
    "weekly_community": 2,  # docs, governance, mentoring, discussions (per week)
}


@dataclass(frozen=True)
class Tier:
    name: str
    fte: float
    reserve: float
    rule: str


TIERS = (
    Tier("Critical", 1.25, 20000, ">=100M downloads/month OR >=1,000 direct dependents"),
    Tier("Important", 0.60, 7500, ">=5M downloads/month OR >=100 direct dependents"),
    Tier("Standard", 0.15, 2000, "all other packages"),
)

COCOMO_A = 2.94
COCOMO_E = 0.91 + 0.01 * 18.97  # nominal scale factors


@dataclass
class Inputs:
    """Observed data for one package; field names match the `inputs` block of the JSON."""

    monthly_downloads: float
    direct_dependents: int
    all_dependents: int
    stars: int
    forks: int
    open_issues_and_prs: int
    commits_12mo: int
    human_commits_12mo: int
    distinct_human_authors_12mo: int
    top_authors_12mo: list[str] = field(default_factory=list)
    bus_factor_proxy: int = 0
    maintainer_commits_12mo: int = 0
    prs_opened_12mo: int = 0
    prs_merged_12mo: int = 0
    issues_opened_12mo: int = 0
    issues_closed_12mo: int = 0
    releases_12mo: int = 0
    advisories_total: int = 0
    advisories_12mo: int = 0
    advisories_12mo_high_or_critical: int = 0
    lines_added_12mo: int = 0
    lines_deleted_12mo: int = 0
    product_sloc: int = 0
    test_sloc: int = 0
    physical_lines_all_text: int = 0


def select_tier(monthly_downloads: float, direct_dependents: int) -> Tier:
    if monthly_downloads >= 1e8 or direct_dependents >= 1000:
        return TIERS[0]
    if monthly_downloads >= 5e6 or direct_dependents >= 100:
        return TIERS[1]
    return TIERS[2]


def cocomo_person_months(sloc: int, effort_multiplier: float) -> float:
    return COCOMO_A * (sloc / 1000) ** COCOMO_E * effort_multiplier


def compute(
    inputs: Inputs,
    settings: Settings,
    *,
    annual_funding: float | None = None,
) -> dict[str, Any]:
    """Return the `rates`, `replacement_value`, `sustaining`, `downstream`, `unit_costs`
    and `funding` sections of the result."""
    s, std = settings, STANDARD_HOURS
    per_pm, hourly = s.per_person_month, s.hourly

    # 1. Replacement value (COCOMO II post-architecture)
    ksloc = (inputs.product_sloc + inputs.test_sloc) / 1000
    pm_dev = cocomo_person_months(inputs.product_sloc + inputs.test_sloc, s.effort_multiplier)
    rv = pm_dev * per_pm
    pm_prod_only = cocomo_person_months(inputs.product_sloc, s.effort_multiplier)

    # 2a. Activity-based sustaining cost
    unmerged = max(0, inputs.prs_opened_12mo - inputs.prs_merged_12mo)
    activity = {
        "PR review (merged)": inputs.prs_merged_12mo * std["merged_pr"],
        "PR review (not merged)": unmerged * std["unmerged_pr"],
        "Issue triage": inputs.issues_opened_12mo * std["issue_triage"],
        "Releases": inputs.releases_12mo * std["release"],
        "Security advisories": inputs.advisories_12mo * std["security_advisory"],
        "Maintainer engineering": inputs.maintainer_commits_12mo * std["maintainer_commit"],
        "Upkeep (CI, deps, Python)": 52 * std["weekly_upkeep"],
        "Community & governance": 52 * std["weekly_community"],
    }
    act_hours = sum(activity.values())
    asc_activity = act_hours * hourly

    # 2b. COCOMO maintenance (Annual Change Traffic)
    physical = inputs.physical_lines_all_text
    act_ratio = min(1.0, inputs.lines_added_12mo / physical) if physical else 0.0
    pm_maint = act_ratio * pm_dev
    asc_cocomo = pm_maint * per_pm

    # 2c. Responsible-staffing standard
    tier = select_tier(inputs.monthly_downloads, inputs.direct_dependents)
    asc_standard = tier.fte * s.loaded_annual + tier.reserve

    low, asc, high = sorted((asc_activity, asc_cocomo, asc_standard))  # median = point estimate

    # 3. Downstream remediation cost
    drc = (
        inputs.advisories_12mo
        * inputs.direct_dependents
        * s.remediation_hours_per_dependent
        * hourly
    )

    # 4. Unit costs & allocation
    annual_downloads = inputs.monthly_downloads * 12
    deps = inputs.direct_dependents

    return {
        "rates": {
            "loaded_annual": round(s.loaded_annual),
            "hourly": round(hourly, 2),
            "per_person_month": round(per_pm),
        },
        "replacement_value": {
            "ksloc": round(ksloc, 2),
            "exponent": round(COCOMO_E, 4),
            "effort_multiplier": s.effort_multiplier,
            "person_months": round(pm_dev, 1),
            "cost": round(rv),
            "product_only_person_months": round(pm_prod_only, 1),
            "product_only_cost": round(pm_prod_only * per_pm),
        },
        "sustaining": {
            "activity_hours": round(act_hours),
            "activity_breakdown_hours": activity,
            "activity_fte": round(act_hours / HOURS_PER_YEAR, 2),
            "activity_cost": round(asc_activity),
            "annual_change_traffic": round(act_ratio, 3),
            "cocomo_maint_person_months": round(pm_maint, 1),
            "cocomo_fte": round(pm_maint / 12, 2),
            "cocomo_cost": round(asc_cocomo),
            "tier": tier.name,
            "tier_rule": tier.rule,
            "tier_fte": tier.fte,
            "tier_reserve": tier.reserve,
            "standard_cost": round(asc_standard),
            "asc_low": round(low),
            "asc_point": round(asc),
            "asc_high": round(high),
        },
        "downstream": {
            "hours_per_dependent_per_advisory": s.remediation_hours_per_dependent,
            "annual_cost": round(drc),
            "leverage_ratio": round(drc / asc, 1) if asc else None,
        },
        "unit_costs": {
            "per_million_downloads": (
                round(asc / (annual_downloads / 1e6), 2) if annual_downloads else None
            ),
            "per_direct_dependent_per_year": round(asc / deps, 2) if deps else None,
            "commercial_share": s.commercial_share,
            "per_global2000_enterprise_per_year": round(
                s.commercial_share * asc / s.enterprise_pool, 2
            ),
            "per_python_developer_per_year": round(
                s.commercial_share * asc / s.python_developers, 4
            ),
        },
        "funding": (
            {
                "annual_funding": annual_funding,
                "coverage_ratio": round(annual_funding / asc, 3),
                "annual_gap": round(asc - annual_funding),
            }
            if annual_funding is not None and asc
            else None
        ),
    }


def inputs_dict(inputs: Inputs, annual_funding: float | None) -> dict[str, Any]:
    return {**asdict(inputs), "annual_funding_known": annual_funding}
