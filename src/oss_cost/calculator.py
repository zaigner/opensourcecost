"""Build the self-contained pricing calculator page from the portfolio and assumptions.

    pixi run build-calculator

Inlines web/pricing-model.js, web/manifest-parser.js, the proxy portfolio (plus the larger
reference dataset used to match uploaded manifests) and the TOML assumptions into
web/calculator.template.html, writing gsib-pricing-calculator.html.
"""

import argparse
import csv
import json
import tomllib
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def as_bool(v: str) -> int:
    return 1 if str(v).strip().lower() == "true" else 0


def portfolio_columns(path: Path) -> dict[str, list]:
    with path.open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    msr = [r["months_since_release"] for r in rows]
    return {
        "name": [r["package"] for r in rows],
        "dl": [int(r["monthly_downloads"] or 0) for r in rows],
        "rel": [int(r["releases_12mo"] or 0) for r in rows],
        "msr": [float(m) if m.strip() else None for m in msr],
        "nat": [as_bool(r["native"]) for r in rows],
        "cop": [as_bool(r["strong_copyleft"]) for r in rows],
        "ina": [as_bool(r["inactive_classifier"]) for r in rows],
        "a12": [int(r["advisories_12mo"] or 0) for r in rows],
        "a36": [int(r["advisories_36mo"] or 0) for r in rows],
    }


def assumptions_json(path: Path) -> dict:
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    scenarios = raw.pop("scenarios", {})
    return {"base": raw, "scenarios": scenarios}


def merged_columns(portfolio: Path, reference: Path | None) -> tuple[dict[str, list], int]:
    """Proxy portfolio rows first (the documented results), then any extra reference rows."""
    cols = portfolio_columns(portfolio)
    proxy_n = len(cols["name"])
    if reference and reference.exists():
        ref = portfolio_columns(reference)
        seen = set(cols["name"])
        for i, name in enumerate(ref["name"]):
            if name not in seen:
                for k in cols:
                    cols[k].append(ref[k][i])
    return cols, proxy_n


def build(
    portfolio: Path, assumptions: Path, template: Path, out: Path, as_of: str,
    reference: Path | None = None,
) -> Path:  # fmt: skip
    html = template.read_text(encoding="utf-8")
    web = ROOT / "web"
    model = "\n".join(
        (web / f).read_text(encoding="utf-8") for f in ("pricing-model.js", "manifest-parser.js")
    )
    cols, proxy_n = merged_columns(portfolio, reference)
    data = {"asOf": as_of, "proxyN": proxy_n, **cols}
    compact = {"separators": (",", ":")}
    for placeholder, value in {
        "/*__MODEL__*/": model,
        "/*__DATA__*/null": json.dumps(data, **compact),
        "/*__ASSUMPTIONS__*/null": json.dumps(assumptions_json(assumptions), **compact),
    }.items():
        if placeholder not in html:
            raise ValueError(f"Template is missing {placeholder}")
        html = html.replace(placeholder, value)
    out.write_text(html, encoding="utf-8")
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="build-calculator", description=__doc__.split("\n\n")[0])
    p.add_argument("--portfolio", type=Path, default=ROOT / "data" / "proxy_portfolio.csv")
    p.add_argument(
        "--reference", type=Path, default=ROOT / "data" / "reference_portfolio.csv",
        help="Larger dataset used to match uploaded manifests (top 15,000 packages)",
    )  # fmt: skip
    p.add_argument("--assumptions", type=Path, default=ROOT / "assumptions" / "gsib_vendor.toml")
    p.add_argument("--template", type=Path, default=ROOT / "web" / "calculator.template.html")
    p.add_argument("--out", type=Path, default=ROOT / "gsib-pricing-calculator.html")
    p.add_argument("--as-of", default=date.today().strftime("%-d %B %Y"))
    args = p.parse_args(argv)
    out = build(
        args.portfolio, args.assumptions, args.template, args.out, args.as_of, args.reference
    )
    print(f"Saved {out} ({out.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
