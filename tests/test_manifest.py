"""Both manifest parsers (Python CLI and browser calculator) against shared fixtures."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from oss_cost.manifest import parse_manifest

ROOT = Path(__file__).parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "manifests"
EXPECTED = json.loads((FIXTURES / "expected.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("filename", sorted(EXPECTED))
def test_python_parser(filename):
    m = parse_manifest(filename, (FIXTURES / filename).read_text(encoding="utf-8"))
    assert {"pypi": m.names("pypi"), "conda": m.names("conda")} == EXPECTED[filename]


@pytest.mark.parametrize("filename", sorted(EXPECTED))
def test_format_is_detected_from_content_alone(filename):
    text = (FIXTURES / filename).read_text(encoding="utf-8")
    assert parse_manifest("pasted", text).format == parse_manifest(filename, text).format


def test_skipped_counts():
    m = parse_manifest("pixi.lock", (FIXTURES / "pixi.lock").read_text(encoding="utf-8"))
    assert (m.skipped_local, m.skipped_system) == (1, 1)


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_browser_parser_matches_python():
    js = """
const P = require(process.argv[1]);
const fs = require("fs");
const dir = process.argv[2];
const files = Object.keys(JSON.parse(fs.readFileSync(dir + "/expected.json", "utf8")));
const out = {};
for (const f of files) {
  const r = P.parseManifest(f, fs.readFileSync(dir + "/" + f, "utf8"));
  const pick = (s) => r.packages.filter((p) => p.source === s).map((p) => p.name).sort();
  out[f] = {
    pypi: pick("pypi"), conda: pick("conda"),
    local: r.skippedLocal, system: r.skippedSystem,
  };
}
console.log(JSON.stringify(out));
"""
    proc = subprocess.run(
        ["node", "-e", js, str(ROOT / "web" / "manifest-parser.js"), str(FIXTURES)],
        capture_output=True,
        text=True,
        check=True,
    )
    got = json.loads(proc.stdout)
    for filename, expected in EXPECTED.items():
        assert {"pypi": got[filename]["pypi"], "conda": got[filename]["conda"]} == expected
        m = parse_manifest(filename, (FIXTURES / filename).read_text(encoding="utf-8"))
        assert (got[filename]["local"], got[filename]["system"]) == (
            m.skipped_local,
            m.skipped_system,
        ), filename
