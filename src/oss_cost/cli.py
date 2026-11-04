"""Command line entry point: `pixi run cost <package> [options]`."""

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from oss_cost.collect import Collector
from oss_cost.config import HOURS_PER_YEAR, Settings
from oss_cost.model import compute, inputs_dict

# Settings fields exposed as CLI overrides (the token stays env/.env only).
OVERRIDABLE = [
    "median_wage",
    "wage_share_of_comp",
    "effort_multiplier",
    "commercial_share",
    "python_developers",
    "enterprise_pool",
    "remediation_hours_per_dependent",
    "test_dir_names",
    "exclude_dir_names",
    "out_dir",
]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="oss-cost",
        description="Estimate the Total Cost of Stewardship (annual unit cost) of a PyPI package.",
        epilog="Defaults come from OSSCOST_* environment variables or .env; GITHUB_TOKEN is "
        "read from the environment.",
    )
    p.add_argument("package", help="PyPI package name, e.g. urllib3")
    p.add_argument("--repo", help="GitHub owner/name; auto-detected from PyPI if omitted")
    p.add_argument("--annual-funding", type=float, help="Known annual funding received")
    for name in OVERRIDABLE:
        f = Settings.model_fields[name]
        kwargs: dict[str, Any] = {"dest": name, "default": None, "help": f.description}
        if f.annotation == list[str]:
            kwargs["nargs"] = "+"
        elif f.annotation in (int, float):
            kwargs["type"] = f.annotation
        elif f.annotation == Path:
            kwargs["type"] = Path
        p.add_argument(f"--{name.replace('_', '-')}", **kwargs)
    return p


def money(v: float, places: int = 0) -> str:
    return f"${v:,.{places}f}"


def print_summary(result: dict[str, Any], out: Path) -> None:
    rv, sus = result["replacement_value"], result["sustaining"]
    down, unit, fund = result["downstream"], result["unit_costs"], result["funding"]
    print()
    print(f"=== Total Cost of Stewardship: {result['package']} {result['version']} ===")
    print(
        f"Replacement value (rebuild)     : {money(rv['cost'])}  "
        f"({rv['person_months']} person-months, {rv['ksloc']:.1f} KSLOC incl. tests)"
    )
    print(
        f"Sustaining cost - activity      : {money(sus['activity_cost'])}  "
        f"({sus['activity_hours']} h, {sus['activity_hours'] / HOURS_PER_YEAR:.2f} FTE)"
    )
    print(
        f"Sustaining cost - COCOMO ACT    : {money(sus['cocomo_cost'])}  "
        f"(ACT {sus['annual_change_traffic']:.1%})"
    )
    print(f"Sustaining cost - {sus['tier']:<9} tier  : {money(sus['standard_cost'])}")
    print(
        f"ANNUAL SUSTAINING COST (median) : {money(sus['asc_point'])}  "
        f"(range {money(sus['asc_low'])} - {money(sus['asc_high'])})"
    )
    print(
        f"Downstream remediation cost     : {money(down['annual_cost'])}/yr  "
        f"({down['leverage_ratio']}x the sustaining cost)"
    )
    if unit["per_million_downloads"] is not None:
        print(f"Per million downloads           : {money(unit['per_million_downloads'], 2)}")
    print(
        f"Per Global-2000 enterprise / yr : {money(unit['per_global2000_enterprise_per_year'], 2)}"
    )
    if fund:
        print(
            f"Funding coverage                : {fund['coverage_ratio']:.1%}  "
            f"(gap {money(fund['annual_gap'])}/yr)"
        )
    print(f"Saved: {out}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="WARNING: %(message)s")

    overrides = {k: getattr(args, k) for k in OVERRIDABLE if getattr(args, k) is not None}
    settings = Settings(**overrides)
    if not settings.github_token:
        logging.warning("GITHUB_TOKEN not set; GitHub allows only 60 requests/hour.")

    print(f"Collecting data for {args.package} ...")
    try:
        with Collector(settings, step=lambda m: print(f"  - {m}")) as collector:
            data = collector.collect(args.package, args.repo)
    except Exception as e:  # surface a one-line error rather than a traceback
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    result = {
        "package": data.package,
        "version": data.version,
        "repo": data.repo,
        "source_ref": data.source_ref,
        "collected": datetime.now().isoformat(timespec="seconds"),
        "inputs": inputs_dict(data.inputs, args.annual_funding),
        **compute(data.inputs, settings, annual_funding=args.annual_funding),
    }

    settings.out_dir.mkdir(parents=True, exist_ok=True)
    out = settings.out_dir / f"{data.package}.json"
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print_summary(result, out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
