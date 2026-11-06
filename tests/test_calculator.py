"""The browser model (web/pricing-model.js) must match the Python pricing engine."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from oss_cost.calculator import assumptions_json, portfolio_columns
from oss_cost.pricing import apply_overrides, load_scenarios, price_portfolio, summarize

ROOT = Path(__file__).parents[1]
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")

JS = """
const M = require(process.argv[1]);
const inp = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const out = {};
for (const [k, a] of Object.entries(inp.cases)) {
  const r = M.price(inp.data, a);
  out[k] = {fair: r.total.fair, floor: r.total.floor, ceiling: r.total.ceiling, modules: r.modules};
}
console.log(JSON.stringify(out));
"""


def test_js_model_matches_python():
    portfolio = ROOT / "data" / "proxy_portfolio.csv"
    if not portfolio.exists():
        pytest.skip("proxy portfolio not built")
    toml = ROOT / "assumptions" / "gsib_vendor.toml"
    raw = assumptions_json(toml)
    cases = {"base": raw["base"]}
    cases |= {k: apply_overrides(raw["base"], v) for k, v in raw["scenarios"].items()}
    data = portfolio_columns(portfolio)
    proc = subprocess.run(
        ["node", "-e", JS, str(ROOT / "web" / "pricing-model.js")],
        input=json.dumps({"data": data, "cases": cases}),
        capture_output=True,
        text=True,
        check=True,
    )
    js = json.loads(proc.stdout)

    import csv

    rows = list(csv.DictReader(portfolio.open(encoding="utf-8")))
    for name, a in load_scenarios(toml).items():
        s = summarize(price_portfolio(rows, a), a)
        assert js[name]["fair"] == pytest.approx(s["total"]["fair_price"], abs=10)
        assert js[name]["floor"] == pytest.approx(s["total"]["cost_floor"], abs=10)
        assert js[name]["ceiling"] == pytest.approx(s["total"]["inhouse_ceiling"], abs=10)
        for module, value in s["fair_price_by_module"].items():
            assert js[name]["modules"][module] == pytest.approx(value, abs=50)


def test_merged_columns_keeps_proxy_rows_first(tmp_path):
    from oss_cost.calculator import merged_columns

    header = (
        "package,download_rank,monthly_downloads,releases_12mo,first_release,last_release,"
        "months_since_release,native,strong_copyleft,inactive_classifier,advisories_12mo,"
        "advisories_36mo,high_critical_12mo,bank_criticality,version_lines\n"
    )
    proxy = tmp_path / "proxy.csv"
    ref = tmp_path / "ref.csv"
    proxy.write_text(header + "a,1,100,1,,,1,False,False,False,1,1,0,,\n")
    ref.write_text(
        header + "a,1,999,9,,,1,False,False,False,9,9,0,,\nb,2,50,1,,,,True,False,False,0,0,0,,\n"
    )
    cols, proxy_n = merged_columns(proxy, ref)
    assert proxy_n == 1
    assert cols["name"] == ["a", "b"]
    assert cols["dl"] == [100, 50]  # proxy row wins for "a"
    assert cols["msr"] == [1.0, None] and cols["nat"] == [0, 1]
