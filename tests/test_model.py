"""The Python model must reproduce the figures the PowerShell script produced for urllib3."""

import json
from dataclasses import fields
from pathlib import Path

import pytest

from oss_cost.config import Settings
from oss_cost.model import Inputs, compute, select_tier

SAVED = json.loads(
    (Path(__file__).parents[1] / "results" / "urllib3.json").read_text(encoding="utf-8-sig")
)


@pytest.fixture
def settings() -> Settings:
    # Pin the documented defaults so a local .env / OSSCOST_* override can't change the test.
    return Settings(
        _env_file=None,
        commercial_share=0.8,
        median_wage=133080,
        wage_share_of_comp=0.701,
        effort_multiplier=1.287,
        python_developers=23_000_000,
        enterprise_pool=2000,
        remediation_hours_per_dependent=1.0,
    )


@pytest.fixture
def inputs() -> Inputs:
    names = {f.name for f in fields(Inputs)}
    return Inputs(**{k: v for k, v in SAVED["inputs"].items() if k in names})


@pytest.mark.parametrize(
    "section",
    ["rates", "replacement_value", "sustaining", "downstream", "unit_costs", "funding"],
)
def test_reproduces_saved_urllib3_result(settings, inputs, section):
    result = compute(inputs, settings, annual_funding=SAVED["inputs"]["annual_funding_known"])
    assert result[section] == SAVED[section]


def test_no_funding_section_when_funding_unknown(settings, inputs):
    assert compute(inputs, settings)["funding"] is None


@pytest.mark.parametrize(
    ("downloads", "deps", "tier"),
    [
        (1e8, 0, "Critical"),
        (0, 1000, "Critical"),
        (5e6, 0, "Important"),
        (0, 100, "Important"),
        (4_999_999, 99, "Standard"),
    ],
)
def test_select_tier(downloads, deps, tier):
    assert select_tier(downloads, deps).name == tier
