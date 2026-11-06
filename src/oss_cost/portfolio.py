"""Build a package portfolio enriched with the public data the pricing model needs.

Sources: the top-PyPI-packages list (monthly downloads), the PyPI JSON API (release
history, native code, licence, development status) and the GitHub Advisory Database
(security advisories, last 36 months).

    pixi run build-portfolio                          # proxy: top 5,000 PyPI packages
    pixi run build-portfolio --packages uv.lock       # your inventory: any manifest or lock file
"""

import argparse
import asyncio
import csv
import json
import re
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from oss_cost.collect import parse_dt
from oss_cost.config import Settings
from oss_cost.manifest import read_manifest

TOP_PACKAGES_URL = "https://hugovk.github.io/top-pypi-packages/top-pypi-packages.min.json"
GITHUB_API = "https://api.github.com"
COPYLEFT_CLASSIFIER = re.compile(r"GNU (Affero )?General Public License")
COPYLEFT_TEXT = re.compile(r"(?<![L\w])A?GPL")

COLUMNS = [
    "package",
    "download_rank",
    "monthly_downloads",
    "releases_12mo",
    "first_release",
    "last_release",
    "months_since_release",
    "native",
    "strong_copyleft",
    "inactive_classifier",
    "advisories_12mo",
    "advisories_36mo",
    "high_critical_12mo",
    # Optional per-package overrides the bank can fill in (blank = use tier defaults):
    "bank_criticality",
    "version_lines",
]


def normalize(name: str) -> str:
    """PEP 503 name normalisation."""
    return re.sub(r"[-_.]+", "-", name).lower()


def read_inventory(path: Path) -> list[str]:
    """Package names from any supported manifest (see oss_cost.manifest)."""
    m = read_manifest(path)
    print(
        f"  - {path.name}: {m.format}, {len(m.packages)} packages"
        f" ({m.skipped_local} local, {m.skipped_system} system skipped)"
    )
    return m.names()


def summarize_pypi(data: dict[str, Any], now: datetime) -> dict[str, Any]:
    """Extract release cadence, native-code, licence and status fields from PyPI JSON."""
    info = data["info"]
    uploads = []
    for files in data.get("releases", {}).values():
        live = [f for f in files if not f.get("yanked")]
        if live:
            uploads.append(min(parse_dt(f["upload_time_iso_8601"]) for f in live))
    year_ago = now - timedelta(days=365)
    last = max(uploads) if uploads else None

    native = any(
        f["packagetype"] == "bdist_wheel" and not f["filename"].endswith("-any.whl")
        for f in data.get("urls", [])
    )
    classifiers = info.get("classifiers") or []
    licence_text = f"{info.get('license_expression') or ''} {(info.get('license') or '')[:200]}"
    copyleft = any(COPYLEFT_CLASSIFIER.search(c) for c in classifiers) or bool(
        COPYLEFT_TEXT.search(licence_text)
    )
    return {
        "releases_12mo": sum(1 for u in uploads if u >= year_ago),
        "first_release": min(uploads).date().isoformat() if uploads else "",
        "last_release": last.date().isoformat() if last else "",
        "months_since_release": round((now - last).days / 30.44, 1) if last else "",
        "native": native,
        "strong_copyleft": copyleft,
        "inactive_classifier": "Development Status :: 7 - Inactive" in classifiers,
    }


async def fetch_pypi(
    names: list[str], cache_file: Path, user_agent: str, concurrency: int = 16
) -> dict[str, dict]:
    cache: dict[str, dict] = {}
    if cache_file.exists():
        for line in cache_file.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            cache[row["package"]] = row
    todo = [n for n in names if n not in cache]
    if not todo:
        return cache

    now = datetime.now(UTC)
    sem = asyncio.Semaphore(concurrency)
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(
        headers={"User-Agent": user_agent}, timeout=120, follow_redirects=True
    ) as client:

        async def one(name: str) -> dict:
            async with sem:
                for attempt in range(3):
                    try:
                        r = await client.get(f"https://pypi.org/pypi/{name}/json")
                        break
                    except httpx.TransportError:
                        if attempt == 2:
                            raise
                        await asyncio.sleep(2)
            if r.status_code == 404:
                return {"package": name, "missing": True}
            r.raise_for_status()
            return {"package": name, **summarize_pypi(r.json(), now)}

        with cache_file.open("a", encoding="utf-8") as fh:
            for i, coro in enumerate(asyncio.as_completed([one(n) for n in todo]), 1):
                row = await coro
                cache[row["package"]] = row
                fh.write(json.dumps(row) + "\n")
                if i % 500 == 0 or i == len(todo):
                    print(f"  - PyPI metadata {i}/{len(todo)}")
    return cache


def fetch_advisories(settings: Settings, since: datetime) -> list[dict]:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": settings.user_agent}
    if settings.github_token:
        headers["Authorization"] = f"Bearer {settings.github_token.get_secret_value()}"
    url: str | None = (
        f"{GITHUB_API}/advisories?ecosystem=pip&per_page=100"
        f"&published=>={since.strftime('%Y-%m-%d')}"
    )
    out = []
    with httpx.Client(headers=headers, timeout=60) as client:
        while url:
            r = client.get(url)
            r.raise_for_status()
            for a in r.json():
                if a.get("withdrawn_at"):
                    continue
                out.append(
                    {
                        "published": a["published_at"],
                        "severity": a.get("severity"),
                        "packages": sorted(
                            {
                                normalize(v["package"]["name"])
                                for v in a.get("vulnerabilities") or []
                                if v.get("package", {}).get("ecosystem") == "pip"
                            }
                        ),
                    }
                )
            url = r.links.get("next", {}).get("url")
    return out


def count_advisories(advisories: list[dict], now: datetime) -> dict[str, Counter]:
    year_ago = now - timedelta(days=365)
    c12, c36, hc12 = Counter(), Counter(), Counter()
    for a in advisories:
        recent = parse_dt(a["published"]) >= year_ago
        for p in a["packages"]:
            c36[p] += 1
            if recent:
                c12[p] += 1
                if a["severity"] in ("high", "critical"):
                    hc12[p] += 1
    return {"12": c12, "36": c36, "hc12": hc12}


def build_rows(
    names: list[str], downloads: dict[str, int], pypi: dict[str, dict], adv: dict[str, Counter]
) -> list[dict]:
    rank = {p: i + 1 for i, p in enumerate(sorted(downloads, key=downloads.get, reverse=True))}
    rows = []
    for name in names:
        meta = pypi.get(name, {})
        if meta.get("missing"):
            print(f"WARNING: {name} not found on PyPI; skipped")
            continue
        rows.append(
            {
                "package": name,
                "download_rank": rank.get(name, ""),
                "monthly_downloads": downloads.get(name, 0),
                **{k: meta.get(k, "") for k in COLUMNS if k in meta},
                "advisories_12mo": adv["12"][name],
                "advisories_36mo": adv["36"][name],
                "high_critical_12mo": adv["hc12"][name],
                "bank_criticality": "",
                "version_lines": "",
            }
        )
    return rows


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="build-portfolio", description=__doc__.split("\n\n")[0])
    p.add_argument(
        "--packages",
        type=Path,
        help="Manifest or lock file (requirements, pyproject, uv.lock, ...)",
    )
    p.add_argument("--top", type=int, default=5000, help="Proxy size when no inventory given")
    p.add_argument("--out", type=Path, default=Path("data/proxy_portfolio.csv"))
    p.add_argument("--cache-dir", type=Path, default=Path("data/cache"))
    args = p.parse_args(argv)
    settings = Settings()
    now = datetime.now(UTC)

    print("  - top PyPI packages (downloads)")
    top = httpx.get(TOP_PACKAGES_URL, timeout=60, follow_redirects=True).json()
    downloads = {normalize(r["project"]): r["download_count"] for r in top["rows"]}
    names = read_inventory(args.packages) if args.packages else list(downloads)[: args.top]

    print("  - GitHub advisories, last 36 months")
    advisories = fetch_advisories(settings, now - timedelta(days=3 * 365))
    pypi = asyncio.run(fetch_pypi(names, args.cache_dir / "pypi.jsonl", settings.user_agent))
    rows = build_rows(names, downloads, pypi, count_advisories(advisories, now))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)
    print(f"Saved {len(rows)} packages: {args.out}")
    print(f"Source data as of {top.get('last_update')} (downloads), {now.date()} (PyPI, GitHub)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
