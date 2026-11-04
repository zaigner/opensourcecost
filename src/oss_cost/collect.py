"""Collect public data for a package from PyPI, pypistats.org, deps.dev and GitHub."""

import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from oss_cost.config import Settings
from oss_cost.model import Inputs
from oss_cost.sloc import count_zip

log = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"
REPO_URL = re.compile(r"github\.com/([^/]+)/([^/#?]+)", re.I)
BOT = re.compile(r"\[bot\]", re.I)


@dataclass
class Collected:
    package: str
    version: str
    repo: str
    source_ref: str
    inputs: Inputs


def one_year_ago(now: datetime) -> datetime:
    try:
        return now.replace(year=now.year - 1)
    except ValueError:  # 29 Feb
        return now.replace(year=now.year - 1, day=28)


def parse_dt(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def detect_repo(info: dict[str, Any]) -> str | None:
    urls = list((info.get("project_urls") or {}).values())
    if info.get("home_page"):
        urls.append(info["home_page"])
    for url in urls:
        if m := REPO_URL.search(url or ""):
            return f"{m[1]}/{m[2].removesuffix('.git')}"
    return None


def count_recent_releases(releases: dict[str, list[dict]], since: datetime) -> int:
    n = 0
    for files in releases.values():
        if not files:
            continue
        uploaded = files[0].get("upload_time_iso_8601") or files[0]["upload_time"]
        if parse_dt(uploaded) >= since:
            n += 1
    return n


def analyze_commits(commits: list[dict[str, Any]]) -> dict[str, Any]:
    """Human-commit stats: author ranking, bus-factor proxy and maintainer commits."""

    def is_bot(c: dict[str, Any]) -> bool:
        login = (c.get("author") or {}).get("login") or ""
        return bool(re.search(r"\[bot\]$", login, re.I)) or bool(
            BOT.search(c["commit"]["author"]["name"] or "")
        )

    human = [c for c in commits if not is_bot(c)]

    # Group case-insensitively, keeping the first-seen spelling (PowerShell Group-Object).
    counts: dict[str, list] = {}
    for c in human:
        name = (c.get("author") or {}).get("login") or c["commit"]["author"]["name"]
        counts.setdefault(name.lower(), [name, 0])[1] += 1
    by_author = sorted(counts.values(), key=lambda g: g[1], reverse=True)

    # Bus-factor proxy: fewest authors accounting for >= 50% of human commits.
    acc = bus_factor = 0
    for _, n in by_author:
        acc += n
        bus_factor += 1
        if acc >= 0.5 * len(human):
            break

    # "Maintainer" commits: authors with >= max(3, 5%) of human commits.
    threshold = max(3, 0.05 * len(human))
    maintainer_commits = sum(n for _, n in by_author if n >= threshold)

    return {
        "human_commits_12mo": len(human),
        "distinct_human_authors_12mo": len(by_author),
        "top_authors_12mo": [f"{name}={n}" for name, n in by_author[:5]],
        "bus_factor_proxy": bus_factor,
        "maintainer_commits_12mo": maintainer_commits,
    }


class Collector:
    def __init__(self, settings: Settings, step: Callable[[str], None] = print):
        self.settings = settings
        self.step = step
        self.client = httpx.Client(
            headers={"User-Agent": settings.user_agent},
            timeout=60,
            follow_redirects=True,
        )
        self.gh_headers = {"Accept": "application/vnd.github+json"}
        if settings.github_token:
            self.gh_headers["Authorization"] = f"Bearer {settings.github_token.get_secret_value()}"

    def __enter__(self) -> "Collector":
        return self

    def __exit__(self, *exc: object) -> None:
        self.client.close()

    def get_json(self, url: str, *, github: bool = False, **params: Any) -> Any:
        r = self.client.get(url, params=params or None, headers=self.gh_headers if github else None)
        if r.status_code == 403 and r.headers.get("x-ratelimit-remaining") == "0":
            raise RuntimeError(
                "GitHub rate limit exceeded; set GITHUB_TOKEN in the environment or .env"
            )
        r.raise_for_status()
        return r.json()

    def gh(self, path: str, **params: Any) -> Any:
        return self.get_json(f"{GITHUB_API}{path}", github=True, **params)

    def search_count(self, query: str) -> int:
        return self.gh("/search/issues", q=query, per_page=1)["total_count"]

    def collect(self, package: str, repo: str | None = None) -> Collected:
        since = one_year_ago(datetime.now(UTC))
        since_s = since.strftime("%Y-%m-%d")

        # ---------- PyPI ----------
        self.step("PyPI metadata")
        pj = self.get_json(f"https://pypi.org/pypi/{package}/json")
        version = pj["info"]["version"]
        releases_12 = count_recent_releases(pj["releases"], since)
        repo = repo or detect_repo(pj["info"])
        if not repo:
            raise RuntimeError(f"Could not detect a GitHub repo for {package}; pass --repo")

        self.step("pypistats downloads")
        dl = self.get_json(f"https://pypistats.org/api/packages/{package.lower()}/recent")
        monthly_downloads = dl["data"]["last_month"]

        self.step("deps.dev dependents")
        direct_deps = all_deps = 0
        try:
            dd = self.get_json(
                f"https://api.deps.dev/v3alpha/systems/pypi/packages/{package}"
                f"/versions/{version}:dependents"
            )
            direct_deps = int(dd.get("directDependentCount", 0))
            all_deps = int(dd.get("dependentCount", 0))
        except httpx.HTTPError as e:
            log.warning("deps.dev: %s", e)

        # ---------- GitHub ----------
        self.step(f"GitHub repo {repo}")
        gr = self.gh(f"/repos/{repo}")
        commits: list[dict] = []
        for page in range(1, 11):
            batch = self.gh(f"/repos/{repo}/commits", since=since_s, per_page=100, page=page)
            commits += batch
            if len(batch) < 100:
                break
        commit_stats = analyze_commits(commits)

        pr_opened = self.search_count(f"repo:{repo} is:pr created:>={since_s}")
        pr_merged = self.search_count(f"repo:{repo} is:pr is:merged merged:>={since_s}")
        iss_opened = self.search_count(f"repo:{repo} is:issue created:>={since_s}")
        iss_closed = self.search_count(f"repo:{repo} is:issue closed:>={since_s}")

        self.step("security advisories")
        adv = self.gh("/advisories", ecosystem="pip", affects=package, per_page=100)
        adv_12 = [a for a in adv if a.get("published_at") and parse_dt(a["published_at"]) >= since]

        self.step("code churn (code_frequency)")
        added_12, deleted_12 = self.code_churn(repo, since)

        # ---------- source size ----------
        self.step("downloading source & counting lines")
        source_ref, data = self.download_source(repo, version, gr["default_branch"])
        lines = count_zip(data, self.settings.test_dir_names, self.settings.exclude_dir_names)

        inputs = Inputs(
            monthly_downloads=monthly_downloads,
            direct_dependents=direct_deps,
            all_dependents=all_deps,
            stars=gr["stargazers_count"],
            forks=gr["forks_count"],
            open_issues_and_prs=gr["open_issues_count"],
            commits_12mo=len(commits),
            **commit_stats,
            prs_opened_12mo=pr_opened,
            prs_merged_12mo=pr_merged,
            issues_opened_12mo=iss_opened,
            issues_closed_12mo=iss_closed,
            releases_12mo=releases_12,
            advisories_total=len(adv),
            advisories_12mo=len(adv_12),
            advisories_12mo_high_or_critical=sum(
                1 for a in adv_12 if a.get("severity") in ("high", "critical")
            ),
            lines_added_12mo=added_12,
            lines_deleted_12mo=deleted_12,
            product_sloc=lines.product,
            test_sloc=lines.test,
            physical_lines_all_text=lines.physical,
        )
        return Collected(package, version, repo, source_ref, inputs)

    def code_churn(self, repo: str, since: datetime) -> tuple[int, int]:
        """Lines added/deleted since `since`. GitHub answers 202 while it computes stats."""
        url = f"{GITHUB_API}/repos/{repo}/stats/code_frequency"
        weeks = None
        for attempt in range(6):
            r = self.client.get(url, headers=self.gh_headers)
            if r.status_code == 200 and len(r.content) > 5:
                weeks = r.json()
                break
            if attempt < 5:
                time.sleep(5)
        if weeks is None:
            log.warning(
                "code_frequency not ready after retries (HTTP %s); churn = 0", r.status_code
            )
            return 0, 0
        cut = since.timestamp()
        added = sum(w[1] for w in weeks if w[0] >= cut)
        deleted = sum(-w[2] for w in weeks if w[0] >= cut)
        return added, deleted

    def download_source(self, repo: str, version: str, default_branch: str) -> tuple[str, bytes]:
        for ref in (
            f"refs/tags/{version}",
            f"refs/tags/v{version}",
            f"refs/heads/{default_branch}",
        ):
            r = self.client.get(f"https://codeload.github.com/{repo}/zip/{ref}")
            if r.status_code == 200:
                return ref, r.content
        raise RuntimeError("Could not download source archive")
