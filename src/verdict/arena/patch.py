"""A change to a source tree, stored as a portable unified diff.

Every patch in the arena - a contestant's fix, a recorded diff, a maintainer's
gold fix - is measured from disk by comparing two trees, never taken from an
agent's own account of what it did. Paths are relative (``a/`` and ``b/``
prefixes), so a patch means the same thing on any machine.

Applying is strict. Every context and removed line must match the target file
exactly, or the apply raises. A patch that half-applies and reports success is
the worst failure a measurement pipeline can have; the previous diff format hit
exactly that, because ``patch`` and ``git apply`` both split header paths on
whitespace and this project's directory name contains a space.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

NO_NEWLINE = "\\ No newline at end of file\n"
_HUNK = re.compile(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")

# Directories that are never part of a change, whoever created them: build and
# test residue, and the per-tool state some coding agents leave in their cwd.
IGNORED_PARTS = frozenset(
    {
        ".git",
        "__pycache__",
        ".pytest_cache",
        ".hypothesis",
        ".mypy_cache",
        ".ruff_cache",
        ".tox",
        ".venv",
        "venv",
        "node_modules",
        ".gemini",
        ".codex",
        ".claude",
    }
)


class PatchError(RuntimeError):
    """A patch does not apply cleanly to the tree it was given."""


@dataclass(frozen=True, slots=True)
class Patch:
    """A unified diff over a set of files, relative to a tree's root."""

    diff: str

    # --- construction --------------------------------------------------------

    @classmethod
    def between(cls, before: Path, after: Path, *, suffixes: Iterable[str] = (".py",)) -> Patch:
        """Every changed file with one of ``suffixes``, from ``before`` to ``after``."""
        wanted = tuple(suffixes)
        paths = sorted(_files(before, wanted) | _files(after, wanted))
        chunks = [_file_diff(rel, _read(before / rel), _read(after / rel)) for rel in paths]
        return cls("".join(c for c in chunks if c))

    @property
    def empty(self) -> bool:
        return not self.diff.strip()

    # --- inspection ----------------------------------------------------------

    @property
    def files(self) -> tuple[str, ...]:
        return tuple(sorted(_parse(self.diff)))

    def lines_changed(self) -> int:
        return sum(
            1
            for line in self.diff.splitlines()
            if line[:1] in "+-" and not line.startswith(("+++ ", "--- "))
        )

    def only(self, *, under: str, keep: bool) -> Patch:
        """The part of the patch inside (``keep=True``) or outside a directory.

        Used to separate a contestant's fix from the tests it wrote: the fix is
        what gets judged, the tests become evidence about everyone's fix.
        """
        prefix = under.rstrip("/") + "/"
        parsed = _parse(self.diff)
        chunks = [parsed[rel].text for rel in sorted(parsed) if rel.startswith(prefix) == keep]
        return Patch("".join(chunks))

    # --- application ---------------------------------------------------------

    def apply(self, root: Path) -> None:
        """Apply in place. Raises :class:`PatchError` on any mismatch."""
        for rel, change in _parse(self.diff).items():
            target = root / rel
            if change.deleted:
                if not target.exists():
                    raise PatchError(f"{rel}: deleted by the patch but absent from the tree")
                target.unlink()
                continue
            original = [] if change.created else _lines(target, rel)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("".join(_apply_hunks(rel, original, change.hunks)), encoding="utf-8")

    def to_dict(self) -> dict[str, str]:
        return {"diff": self.diff}

    @classmethod
    def from_dict(cls, data: dict[str, str]) -> Patch:
        return cls(data.get("diff", ""))


# --- diffing ------------------------------------------------------------------


def _files(root: Path, suffixes: tuple[str, ...]) -> set[str]:
    if not root.exists():
        return set()
    found: set[str] = set()
    for path in root.rglob("*"):
        rel = path.relative_to(root)
        if IGNORED_PARTS.intersection(rel.parts) or not path.is_file():
            continue
        if path.suffix in suffixes:
            found.add(rel.as_posix())
    return found


def _read(path: Path) -> str | None:
    if not path.exists():
        return None
    # Lenient decoding: one stray non-UTF-8 byte must not drop a whole file.
    return path.read_bytes().decode("utf-8", errors="replace")


def _file_diff(rel: str, before: str | None, after: str | None) -> str:
    if before == after:
        return ""
    old = (before or "").splitlines(keepends=True)
    new = (after or "").splitlines(keepends=True)
    out = [
        f"--- {'a/' + rel if before is not None else '/dev/null'}\n",
        f"+++ {'b/' + rel if after is not None else '/dev/null'}\n",
    ]
    body = list(difflib.unified_diff(old, new, n=3))[2:]  # drop difflib's own headers
    for line in body:
        if line.endswith("\n"):
            out.append(line)
        else:
            # A final line without a newline must stay distinguishable from one
            # with it, or the round trip silently adds a byte.
            out.extend([line + "\n", NO_NEWLINE])
    return "".join(out)


# --- parsing and applying --------------------------------------------------------


@dataclass
class _FileChange:
    created: bool
    deleted: bool
    hunks: list[tuple[int, list[str]]]
    text: str


def _parse(diff: str) -> dict[str, _FileChange]:
    changes: dict[str, _FileChange] = {}
    lines = diff.splitlines(keepends=True)
    i = 0
    while i < len(lines):
        if not lines[i].startswith("--- "):
            i += 1
            continue
        start = i
        old = lines[i][4:].rstrip("\n")
        new = lines[i + 1][4:].rstrip("\n")
        rel = (new if new != "/dev/null" else old)[2:]
        i += 2
        hunks: list[tuple[int, list[str]]] = []
        while i < len(lines) and (header := _HUNK.match(lines[i])):
            # Consume exactly the line counts the header declares. Scanning for
            # the next `--- ` instead would end a hunk early at any removed line
            # whose own text happens to begin with "-- ".
            old_len = int(header[2] or 1)
            new_len = int(header[4] or 1)
            body: list[str] = []
            i += 1
            seen_old = seen_new = 0
            while seen_old < old_len or seen_new < new_len:
                tag = lines[i][:1]
                seen_old += tag in (" ", "-")
                seen_new += tag in (" ", "+")
                body.append(lines[i])
                i += 1
            while i < len(lines) and lines[i].startswith("\\"):
                body.append(lines[i])
                i += 1
            hunks.append((int(header[1]), body))
        changes[rel] = _FileChange(
            created=old == "/dev/null",
            deleted=new == "/dev/null",
            hunks=hunks,
            text="".join(lines[start:i]),
        )
    return changes


def _lines(path: Path, rel: str) -> list[str]:
    if not path.exists():
        raise PatchError(f"{rel}: modified by the patch but absent from the tree")
    return path.read_bytes().decode("utf-8", errors="replace").splitlines(keepends=True)


def _apply_hunks(rel: str, source: list[str], hunks: list[tuple[int, list[str]]]) -> list[str]:
    result: list[str] = []
    cursor = 0
    for old_start, body in hunks:
        begin = max(old_start - 1, 0)
        if begin < cursor:
            raise PatchError(f"{rel}: overlapping hunks at line {old_start}")
        result.extend(source[cursor:begin])
        position = begin
        for index, line in enumerate(body):
            tag, text = line[:1], line[1:]
            if tag == "\\":
                continue
            if index + 1 < len(body) and body[index + 1] == NO_NEWLINE:
                text = text[:-1] if text.endswith("\n") else text
            if tag in (" ", "-"):
                if position >= len(source) or source[position] != text:
                    raise PatchError(f"{rel}: context mismatch at line {position + 1}")
                position += 1
            if tag in (" ", "+"):
                result.append(text)
        cursor = position
    result.extend(source[cursor:])
    return result
