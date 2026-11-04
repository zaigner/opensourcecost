"""Source-line counting over a GitHub source zip, read in memory (nothing is extracted)."""

import io
import re
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath

CODE_EXT = {".py", ".pyx", ".pxd", ".pyi", ".c", ".h", ".cc", ".cpp", ".hpp", ".rs"}
TEXT_EXT = CODE_EXT | {
    ".rst",
    ".md",
    ".txt",
    ".toml",
    ".cfg",
    ".ini",
    ".yml",
    ".yaml",
    ".json",
    ".html",
    ".css",
    ".js",
    ".in",
    ".sh",
}
MAX_FILE_BYTES = 2 * 1024 * 1024
ROOT_SKIP = re.compile(r"^(setup|noxfile|tasks|conftest)\.py$", re.I)
TEST_FILE = re.compile(r"^test_.*\.py$|_test\.py$", re.I)
TRIPLE_QUOTE = re.compile(r'"""|\'\'\'')
C_COMMENT = re.compile(r"^(//|/\*|\*)")


@dataclass
class LineCounts:
    product: int = 0
    test: int = 0
    physical: int = 0  # all lines in all text files, including docs/tests/excluded dirs


def split_lines(text: str) -> list[str]:
    """Split like .NET File.ReadAllLines: on \\r\\n, \\n or \\r, no trailing empty line."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def count_logical(lines: list[str], is_python: bool) -> int:
    """Non-blank lines excluding comments (and Python docstring blocks)."""
    n, in_doc = 0, False
    for line in lines:
        t = line.strip()
        if not t:
            continue
        if is_python:
            if in_doc:
                if TRIPLE_QUOTE.search(t):
                    in_doc = False
                continue
            if t.startswith(('"""', "'''")):
                if not (len(t) > 3 and TRIPLE_QUOTE.search(t[3:])):
                    in_doc = True
                continue
            if t.startswith("#"):
                continue
        elif C_COMMENT.match(t):
            continue
        n += 1
    return n


def count_zip(data: bytes, test_dir_names: list[str], exclude_dir_names: list[str]) -> LineCounts:
    test_dirs = {d.lower() for d in test_dir_names}
    exclude_dirs = {d.lower() for d in exclude_dir_names}
    counts = LineCounts()

    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            parts = PurePosixPath(info.filename).parts
            if len(parts) < 2:  # GitHub archives wrap everything in one top-level dir
                continue
            segs = [p.lower() for p in parts[1:]]
            name = parts[-1]
            ext = PurePosixPath(segs[-1]).suffix
            if ext not in TEXT_EXT or info.file_size > MAX_FILE_BYTES:
                continue

            lines = split_lines(zf.read(info).decode("utf-8-sig", errors="replace"))
            counts.physical += len(lines)
            if ext not in CODE_EXT:
                continue

            dirs = segs[:-1]
            if any(d in exclude_dirs for d in dirs):
                continue
            if len(segs) == 1 and ROOT_SKIP.search(name):
                continue
            is_test = any(d in test_dirs for d in dirs) or bool(TEST_FILE.search(name))

            n = count_logical(lines, is_python=ext.startswith(".py"))
            if is_test:
                counts.test += n
            else:
                counts.product += n
    return counts
