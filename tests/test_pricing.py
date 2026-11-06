import csv
import io
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from oss_cost.portfolio import count_advisories, read_inventory, summarize_pypi
from oss_cost.pricing import (
    apply_overrides,
    bank_share,
    classify,
    load_scenarios,
    price_package,
    price_portfolio,
    summarize,
)

ASSUMPTIONS = Path(__file__).parents[1] / "assumptions" / "gsib_vendor.toml"


@pytest.fixture(scope="module")
def scenarios():
    return load_scenarios(ASSUMPTIONS)


@pytest.fixture(scope="module")
def base(scenarios):
    return scenarios["base"]


def row(**kw):
    r = {
        "package": "pkg",
        "monthly_downloads": 1_000_000,
        "releases_12mo": 4,
        "months_since_release": 2,
        "native": "False",
        "strong_copyleft": "False",
        "inactive_classifier": "False",
        "advisories_12mo": 0,
        "advisories_36mo": 0,
        "bank_criticality": "",
        "version_lines": "",
    }
    return {**r, **kw}


def test_scenarios_load_and_override(scenarios):
    assert set(scenarios) == {"base", "low", "high"}
    assert scenarios["high"].engagement.vendor_clients == 15
    assert scenarios["base"].engagement.vendor_clients == 30


def test_apply_overrides_rejects_unknown_keys():
    with pytest.raises(KeyError):
        apply_overrides({"pricing": {"target_margin": 0.3}}, {"pricing.margin": 0.2})
    assert apply_overrides({"a": {"b": 1}}, {"a.b": 2}) == {"a": {"b": 2}}


@pytest.mark.parametrize(
    ("kw", "expected"),
    [
        ({"monthly_downloads": 1e8}, ("critical", False)),
        ({"monthly_downloads": 5e6}, ("important", False)),
        ({"months_since_release": 24}, ("standard", True)),
        ({"inactive_classifier": "True"}, ("standard", True)),
        ({"months_since_release": ""}, ("standard", False)),
        ({"bank_criticality": "High"}, ("critical", False)),
    ],
)
def test_classify(base, kw, expected):
    assert classify(row(**kw), base) == expected


def test_classify_rejects_bad_criticality(base):
    with pytest.raises(ValueError):
        classify(row(bank_criticality="urgent"), base)


def test_bank_share(base):
    # 30 clients x 90% adoption = 27 users, 26 besides the bank; bank weight 2
    assert bank_share("critical", False, base) == pytest.approx(2 / 28)
    # dormant standard: 30 x 0.2 x 0.5 = 3 users, 2 others
    assert bank_share("standard", True, base) == pytest.approx(2 / 4)


def test_price_package_hand_calculation(base):
    p = price_package(row(), base)
    exp_adv = 0.25 * 0.11  # no history: (1 - Z) x tier prior
    shared = 1.0 + 4 * 0.25 + 0.5 + exp_adv * 3.0
    dedicated = exp_adv * 0.5 * 6.0 + 0.25 * 2.0 + 0.25
    assert p["expected_advisories"] == pytest.approx(exp_adv, abs=1e-3)
    assert p["shared_hours"] == pytest.approx(shared, abs=0.01)
    assert p["dedicated_hours"] == pytest.approx(dedicated, abs=0.01)
    assert p["_labour"] == pytest.approx((shared * 2 / 7 + dedicated) * 130.0)
    assert p["_upstream"] == pytest.approx(0.8 * 30476 * 5 / 2000)


def test_dormant_package_gets_last_resort_and_no_upstream_funding(base):
    p = price_package(row(months_since_release=40, advisories_12mo=1), base)
    assert p["h_last_resort"] == 12.0
    assert p["_upstream"] == 0.0
    assert p["h_advisory_response"] > p["expected_advisories"] * 3.0  # includes own fix


def test_backports_are_capped(base):
    p = price_package(row(monthly_downloads=2e8, advisories_12mo=100, advisories_36mo=150), base)
    assert p["h_backports"] == pytest.approx(12 * 2 * 6.0)


def test_portfolio_modules_sum_to_fair_and_anchors_ordered(base):
    rows = [
        row(package="a", monthly_downloads=2e8, advisories_12mo=3, native="True"),
        row(package="b", monthly_downloads=6e6),
        row(package="c", months_since_release=30),
    ]
    priced = price_portfolio(rows, base)
    for p in priced:
        modules = sum(v for k, v in p.items() if k.startswith("fair_") and k != "fair_price")
        assert modules == pytest.approx(p["fair_price"], abs=0.05)
        assert p["cost_floor"] < p["fair_price"] < p["inhouse_ceiling"]
    s = summarize(priced, base)
    assert s["total"]["packages"] == 3
    assert sum(s["fair_price_by_module"].values()) == pytest.approx(s["total"]["fair_price"], abs=3)
    assert sum(p["fixed_cost_allocated"] for p in priced) == pytest.approx(
        base.vendor_fixed_total, abs=0.05
    )


def test_summarize_pypi_detects_native_copyleft_and_cadence():
    now = datetime(2026, 11, 5, tzinfo=UTC)
    data = {
        "info": {
            "classifiers": ["License :: OSI Approved :: GNU General Public License v3 (GPLv3)"],
            "license": "",
            "license_expression": None,
        },
        "releases": {
            "1.0": [{"upload_time_iso_8601": "2020-01-01T00:00:00Z"}],
            "2.0": [{"upload_time_iso_8601": "2026-06-01T00:00:00Z"}],
            "2.1": [{"upload_time_iso_8601": "2026-09-01T00:00:00Z", "yanked": True}],
        },
        "urls": [
            {"packagetype": "bdist_wheel", "filename": "pkg-2.0-cp312-cp312-manylinux_x86_64.whl"},
        ],
    }
    s = summarize_pypi(data, now)
    assert s["releases_12mo"] == 1
    assert s["last_release"] == "2026-06-01"
    assert s["native"] is True
    assert s["strong_copyleft"] is True


def test_summarize_pypi_lgpl_is_not_strong_copyleft():
    data = {
        "info": {"classifiers": [], "license": "LGPL-2.1", "license_expression": None},
        "releases": {},
        "urls": [{"packagetype": "bdist_wheel", "filename": "pkg-1.0-py3-none-any.whl"}],
    }
    s = summarize_pypi(data, datetime(2026, 11, 5, tzinfo=UTC))
    assert (s["native"], s["strong_copyleft"]) == (False, False)


def test_read_inventory_handles_requirements_lines(tmp_path):
    f = tmp_path / "inv.txt"
    f.write_text(
        "Requests==2.32.3\n# comment\nruamel.yaml>=0.18  # inline\n\nzope_interface\nrequests\n"
    )
    assert read_inventory(f) == ["requests", "ruamel-yaml", "zope-interface"]


def test_count_advisories_windows():
    now = datetime(2026, 11, 5, tzinfo=UTC)
    adv = [
        {"published": "2026-10-01T00:00:00Z", "severity": "high", "packages": ["a"]},
        {"published": "2025-01-01T00:00:00Z", "severity": "low", "packages": ["a", "b"]},
    ]
    c = count_advisories(adv, now)
    assert (c["12"]["a"], c["36"]["a"], c["hc12"]["a"], c["36"]["b"]) == (1, 2, 1, 1)


def test_proxy_portfolio_file_is_consistent():
    path = Path(__file__).parents[1] / "data" / "proxy_portfolio.csv"
    if not path.exists():
        pytest.skip("proxy portfolio not built")
    rows = list(csv.DictReader(io.StringIO(path.read_text(encoding="utf-8"))))
    assert len(rows) > 4900
    assert len({r["package"] for r in rows}) == len(rows)
    json.dumps(rows[0])  # plain, serialisable values
