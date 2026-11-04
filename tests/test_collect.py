import io
import zipfile
from datetime import UTC, datetime

from oss_cost.collect import analyze_commits, count_recent_releases, detect_repo, one_year_ago
from oss_cost.sloc import count_logical, count_zip, split_lines


def commit(login, name=None):
    return {
        "author": {"login": login} if login else None,
        "commit": {"author": {"name": name or login or "anon"}},
    }


def test_detect_repo_prefers_project_urls_and_strips_git():
    info = {
        "project_urls": {"Docs": "https://example.org", "Code": "https://github.com/a/b.git"},
        "home_page": "https://github.com/x/y",
    }
    assert detect_repo(info) == "a/b"
    assert (
        detect_repo({"project_urls": None, "home_page": "https://github.com/x/y#readme"}) == "x/y"
    )
    assert detect_repo({"project_urls": {}, "home_page": ""}) is None


def test_one_year_ago_handles_leap_day():
    assert one_year_ago(datetime(2028, 2, 29, tzinfo=UTC)) == datetime(2027, 2, 28, tzinfo=UTC)


def test_count_recent_releases():
    since = datetime(2025, 11, 4, tzinfo=UTC)
    releases = {
        "1.0": [{"upload_time": "2024-01-01T00:00:00"}],
        "2.0": [{"upload_time": "2026-01-01T00:00:00"}],
        "2.1": [{"upload_time_iso_8601": "2026-03-01T00:00:00.000Z", "upload_time": "x"}],
        "yanked-empty": [],
    }
    assert count_recent_releases(releases, since) == 2


def test_analyze_commits_filters_bots_and_groups_case_insensitively():
    commits = (
        [commit("Alice")] * 6
        + [commit("alice")] * 2
        + [commit("bob")] * 3
        + [commit(None, "Carol")]
        + [commit("dependabot[bot]")] * 5
        + [commit(None, "pre-commit-ci[bot]")]
    )
    stats = analyze_commits(commits)
    assert stats["human_commits_12mo"] == 12
    assert stats["distinct_human_authors_12mo"] == 3
    assert stats["top_authors_12mo"] == ["Alice=8", "bob=3", "Carol=1"]
    assert stats["bus_factor_proxy"] == 1
    assert stats["maintainer_commits_12mo"] == 11  # threshold max(3, 0.6) = 3


def test_split_lines_matches_dotnet_readalllines():
    assert split_lines("a\r\nb\rc\n") == ["a", "b", "c"]
    assert split_lines("") == []


def test_count_logical_python_and_c():
    py = ['"""Module doc', "more", '"""', "# comment", "", "x = 1", "'''one-liner'''", "y = 2"]
    assert count_logical(py, is_python=True) == 2
    c = ["/* block", " * star", "// line", "int x;"]
    assert count_logical(c, is_python=False) == 1


def make_zip(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for path, text in files.items():
            zf.writestr(f"pkg-1.0/{path}", text)
    return buf.getvalue()


def test_count_zip_classifies_product_test_and_excluded():
    data = make_zip(
        {
            "src/pkg/core.py": "a = 1\nb = 2\n",
            "tests/test_core.py": "assert 1\n",
            "src/pkg/thing_test.py": "assert 2\n",
            "docs/conf.py": "x = 1\n",  # excluded dir: physical only
            "setup.py": "setup()\n",  # root build script: physical only
            "README.md": "# hi\ntext\n",  # text, not code: physical only
            "logo.png": "binary",  # not text: ignored
        }
    )
    counts = count_zip(data, ["tests"], ["docs"])
    assert (counts.product, counts.test, counts.physical) == (2, 2, 8)
