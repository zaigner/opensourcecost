"""Settings loaded from environment variables and `.env` (pydantic-settings).

Model assumptions use the ``OSSCOST_`` prefix (e.g. ``OSSCOST_MEDIAN_WAGE``); the GitHub
token is read from plain ``GITHUB_TOKEN``. Any field can also be overridden on the CLI.
"""

from pathlib import Path

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

HOURS_PER_YEAR = 1824  # 152 h/person-month x 12 (COCOMO II convention)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="OSSCOST_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    github_token: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("GITHUB_TOKEN", "OSSCOST_GITHUB_TOKEN"),
        description="GitHub token; avoids the 60 request/hour unauthenticated limit",
    )
    median_wage: float = Field(
        133_080, description="BLS OOH software developer median annual wage (May 2024)"
    )
    wage_share_of_comp: float = Field(
        0.701, description="BLS ECEC Dec 2025: wages as share of total compensation"
    )
    effort_multiplier: float = Field(
        1.287, description="COCOMO II EAF: RELY high (1.10) x CPLX high (1.17)"
    )
    commercial_share: float = Field(
        0.8, description="alpha: share of ASC attributed to commercial beneficiaries"
    )
    python_developers: float = Field(
        23_000_000, description="SlashData Q1 2025 Python developer population"
    )
    enterprise_pool: int = Field(2000, description="Forbes Global 2000 beneficiary pool")
    remediation_hours_per_dependent: float = Field(
        1.0, description="Hours each direct dependent spends per advisory"
    )
    test_dir_names: list[str] = Field(
        default_factory=lambda: ["test", "tests", "testing", "dummyserver"],
        description="Directory names counted as test code",
    )
    exclude_dir_names: list[str] = Field(
        default_factory=lambda: [
            "docs",
            "doc",
            "examples",
            "example",
            "benchmarks",
            "bench",
            "ci",
            ".github",
            "scripts",
            "tools",
            ".git",
        ],
        description="Directory names excluded from SLOC",
    )
    out_dir: Path = Field(Path("results"), description="Directory for <package>.json")
    user_agent: str = Field("oss-unit-cost-research", description="HTTP User-Agent")

    @property
    def loaded_annual(self) -> float:
        return self.median_wage / self.wage_share_of_comp

    @property
    def hourly(self) -> float:
        return self.loaded_annual / HOURS_PER_YEAR

    @property
    def per_person_month(self) -> float:
        return self.loaded_annual / 12
