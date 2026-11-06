"""Extract package names from Python dependency manifests.

Mirrors web/manifest-parser.js (the calculator's parser); both are tested against
tests/fixtures/manifests. Supported: requirements files and `pip freeze` output,
pyproject.toml (PEP 621, dependency groups, Poetry, PDM, uv, pixi), pixi.toml, Pipfile,
Pipfile.lock, poetry/uv/pdm lock files, pixi.lock, conda-lock, environment.yml,
setup.cfg and setup.py.
"""

import json
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CONDA_ALIASES = {
    "pytorch": "torch", "matplotlib-base": "matplotlib", "pytables": "tables",
    "msgpack-python": "msgpack", "py-opencv": "opencv-python", "py-xgboost": "xgboost",
    "pyqt": "pyqt5", "tensorflow-base": "tensorflow",
}  # fmt: skip
NOT_PACKAGES = {"python"}
NAME_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)")
LOCAL_KEYS = {"path", "editable", "virtual", "directory"}


def normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


class Local:
    """Marker for a local or editable path dependency (not an open source package)."""


def requirement_name(line: str) -> str | Local | None:
    s = line.strip()
    if not s or s.startswith("#"):
        return None
    s = re.sub(r"\s+#.*$", "", s).strip()
    egg = re.search(r"#egg=([A-Za-z0-9._-]+)", s)
    if re.match(r"^(-e|--editable)\b", s):
        return normalize(egg[1]) if egg else Local()
    if s.startswith("-"):
        return None
    if re.match(r"^(https?:|git\+|hg\+|svn\+|bzr\+|file:|\.|/|~)", s):
        return normalize(egg[1]) if egg else Local()
    m = NAME_RE.match(s)
    return normalize(m[1]) if m else None


def conda_name(spec: str) -> str | None:
    s = spec.strip().strip("\"'")
    if "::" in s:
        s = s.split("::")[-1]
    m = NAME_RE.match(s)
    if not m:
        return None
    n = normalize(m[1])
    return CONDA_ALIASES.get(n, n)


@dataclass
class Manifest:
    format: str
    packages: dict[str, str] = field(default_factory=dict)  # name -> "pypi" | "conda"
    skipped_local: int = 0
    skipped_system: int = 0

    def add(self, name: str | Local | None, source: str) -> None:
        if name is None or name in NOT_PACKAGES:
            return
        if isinstance(name, Local):
            self.skipped_local += 1
            return
        if name not in self.packages or (self.packages[name] == "conda" and source == "pypi"):
            self.packages[name] = source

    def add_req(self, line: str) -> None:
        self.add(requirement_name(line), "pypi")

    def add_key(self, key: str, value: Any, source: str = "pypi") -> None:
        if isinstance(value, dict) and LOCAL_KEYS & value.keys():
            self.skipped_local += 1
        else:
            self.add(normalize(key) if source == "pypi" else conda_name(key), source)

    def names(self, source: str | None = None) -> list[str]:
        return sorted(n for n, s in self.packages.items() if source in (None, s))


# ---------------------------------------------------------------------------
# Format parsers
# ---------------------------------------------------------------------------


def _requirements(text: str, m: Manifest) -> None:
    for line in text.splitlines():
        m.add_req(re.sub(r"\\\s*$", "", line))


def _strings(values: Any) -> list[str]:
    return [v for v in values or [] if isinstance(v, str)]


def _pixi_tables(tables: dict[str, Any], m: Manifest) -> None:
    for key, deps in tables.get("pypi-dependencies", {}).items():
        m.add_key(key, deps)
    for key, deps in tables.get("dependencies", {}).items():
        m.add_key(key, deps, "conda")
    for sub in ("feature", "target"):
        for section in tables.get(sub, {}).values():
            if isinstance(section, dict):
                _pixi_tables(section, m)


def _poetry_tables(poetry: dict[str, Any], m: Manifest) -> None:
    for table in ("dependencies", "dev-dependencies"):
        for key, value in poetry.get(table, {}).items():
            m.add_key(key, value)
    for group in poetry.get("group", {}).values():
        for key, value in group.get("dependencies", {}).items():
            m.add_key(key, value)


def _pyproject(text: str, m: Manifest) -> None:
    doc = tomllib.loads(text)
    project = doc.get("project", {})
    for req in _strings(project.get("dependencies")):
        m.add_req(req)
    for reqs in project.get("optional-dependencies", {}).values():
        for req in _strings(reqs):
            m.add_req(req)
    for reqs in doc.get("dependency-groups", {}).values():
        for req in _strings(reqs):
            m.add_req(req)
    tool = doc.get("tool", {})
    for reqs in tool.get("pdm", {}).get("dev-dependencies", {}).values():
        for req in _strings(reqs):
            m.add_req(req)
    for req in _strings(tool.get("uv", {}).get("dev-dependencies")):
        m.add_req(req)
    _poetry_tables(tool.get("poetry", {}), m)
    _pixi_tables(tool.get("pixi", {}), m)


def _pixi_toml(text: str, m: Manifest) -> None:
    _pixi_tables(tomllib.loads(text), m)


def _pipfile(text: str, m: Manifest) -> None:
    doc = tomllib.loads(text)
    for table in ("packages", "dev-packages"):
        for key, value in doc.get(table, {}).items():
            m.add_key(key, value)


def _toml_lock(text: str, m: Manifest) -> None:
    for pkg in tomllib.loads(text).get("package", []):
        source = pkg.get("source") or {}
        local = bool(LOCAL_KEYS & source.keys()) or source.get("type") == "directory"
        if local:
            m.skipped_local += 1
        elif pkg.get("name"):
            m.add(normalize(pkg["name"]), "pypi")


def _pipfile_lock(text: str, m: Manifest) -> None:
    doc = json.loads(text)
    for section in ("default", "develop"):
        for key in doc.get(section, {}):
            m.add(normalize(key), "pypi")


def _environment_yml(text: str, m: Manifest) -> None:
    in_deps, pip_indent = False, None
    for raw in text.splitlines():
        line = re.sub(r"(^|\s)#.*$", "", raw)
        if not line.strip():
            continue
        if re.match(r"^dependencies:\s*$", line):
            in_deps = True
            continue
        if re.match(r"^\S", line):
            in_deps, pip_indent = False, None
            continue
        if not in_deps or not (item := re.match(r"^(\s*)-\s*(.*)$", line)):
            continue
        indent, value = len(item[1]), item[2].strip().strip("\"'")
        if pip_indent is not None and indent > pip_indent:
            m.add_req(value)
            continue
        pip_indent = None
        if re.match(r"^pip:\s*$", value):
            pip_indent = indent
            continue
        m.add(conda_name(value), "conda")


def _pixi_lock(text: str, m: Manifest) -> None:
    in_packages, cur = False, None

    def flush() -> None:
        if cur is None:
            return
        if cur["kind"] == "pypi":
            if re.match(r"^https?:", cur["url"]) and cur["name"]:
                m.add(normalize(cur["name"]), "pypi")
            else:
                m.skipped_local += 1
        elif cur["purls"]:
            for name in cur["purls"]:
                m.add(normalize(name), "pypi")
        else:
            m.skipped_system += 1

    for line in text.splitlines():
        if re.match(r"^packages:\s*$", line):
            in_packages = True
            continue
        if not in_packages:
            continue
        if entry := re.match(r"^- (conda|pypi): (.*)$", line):
            flush()
            cur = {"kind": entry[1], "url": entry[2].strip(), "name": None, "purls": []}
        elif cur is not None and (name := re.match(r"^ {2}name: (.+)$", line)):
            cur["name"] = name[1].strip().strip("\"'")
        elif cur is not None and (purl := re.match(r"^ {2}- pkg:pypi/([^?@\s]+)", line)):
            cur["purls"].append(purl[1])
    flush()


def _conda_lock(text: str, m: Manifest) -> None:
    cur: dict[str, str] | None = None

    def flush() -> None:
        if cur:
            if cur["manager"] == "pip":
                m.add(normalize(cur["name"]), "pypi")
            else:
                m.add(conda_name(cur["name"]), "conda")

    for line in text.splitlines():
        if entry := re.match(r"^- name: (.+)$", line):
            flush()
            cur = {"name": entry[1].strip(), "manager": "conda"}
        elif cur is not None and (mgr := re.match(r"^ {2}manager: (\w+)", line)):
            cur["manager"] = mgr[1]
    flush()


def _setup_cfg(text: str, m: Manifest) -> None:
    section, key = "", None
    for raw in text.splitlines():
        if raw.strip().startswith(("#", ";")):
            continue
        line = raw
        if sec := re.match(r"^\s*\[(.+)\]\s*$", line):
            section, key = sec[1].strip(), None
            continue
        if kv := re.match(r"^(\S[^=]*?)\s*=\s*(.*)$", line):
            wanted = (section == "options" and kv[1] == "install_requires") or (
                section == "options.extras_require"
            )
            key = kv[1] if wanted else None
            if key and kv[2].strip():
                m.add_req(kv[2])
            continue
        if key and re.match(r"^\s+\S", line):
            m.add_req(line)
        elif re.match(r"^\S", line):
            key = None


def _setup_py(text: str, m: Manifest) -> None:
    string = r"\"((?:[^\"\\]|\\.)*)\"|'([^']*)'"
    if ir := re.search(r"install_requires\s*=\s*\[([\s\S]*?)\]", text):
        for a, b in re.findall(string, ir[1]):
            m.add_req(a or b)
    if er := re.search(r"extras_require\s*=\s*\{([\s\S]*?)\}\s*[,)]", text):
        body = re.sub(r"[\"'][^\"']*[\"']\s*:", "", er[1])
        for a, b in re.findall(string, body):
            m.add_req(a or b)


PARSERS = {
    "requirements": _requirements, "pyproject.toml": _pyproject, "pixi.toml": _pixi_toml,
    "Pipfile": _pipfile, "Pipfile.lock": _pipfile_lock, "lock (TOML)": _toml_lock,
    "environment.yml": _environment_yml, "pixi.lock": _pixi_lock, "conda-lock": _conda_lock,
    "setup.cfg": _setup_cfg, "setup.py": _setup_py,
}  # fmt: skip


def detect(filename: str, text: str) -> str:
    base = Path(filename).name.lower()
    by_name = {
        "pixi.lock": "pixi.lock", "poetry.lock": "lock (TOML)", "uv.lock": "lock (TOML)",
        "pdm.lock": "lock (TOML)", "pipfile.lock": "Pipfile.lock", "pipfile": "Pipfile",
        "pyproject.toml": "pyproject.toml", "pixi.toml": "pixi.toml",
        "setup.cfg": "setup.cfg", "setup.py": "setup.py",
    }  # fmt: skip
    if base in by_name:
        return by_name[base]
    sniff = [
        (r'^\s*\{[\s\S]*"_meta"', "Pipfile.lock"),
        (r"(?m)^\[\[package\]\]", "lock (TOML)"),
        (r"(?m)^packages:\s*$[\s\S]*^- (conda|pypi): ", "pixi.lock"),
        (r"(?m)^package:\s*$[\s\S]*^\s+manager: ", "conda-lock"),
        (r"(?m)^\[(project|tool\.[\w-]+|dependency-groups)[\].]", "pyproject.toml"),
        (r"(?m)^\[(workspace|pypi-dependencies|feature\.)", "pixi.toml"),
        (r"(?m)^\[(packages|dev-packages)\]", "Pipfile"),
        (r"(?m)^dependencies:\s*$", "environment.yml"),
        (r"(?m)^\[options\]", "setup.cfg"),
    ]
    for pattern, fmt in sniff:
        if re.search(pattern, text):
            return fmt
    if re.search(r"\bsetup\s*\(", text) and "install_requires" in text:
        return "setup.py"
    return "requirements"


def parse_manifest(filename: str, text: str) -> Manifest:
    fmt = detect(filename, text)
    m = Manifest(fmt)
    PARSERS[fmt](text, m)
    return m


def read_manifest(path: Path) -> Manifest:
    return parse_manifest(path.name, path.read_text(encoding="utf-8"))
