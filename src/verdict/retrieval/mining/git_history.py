"""Mine incidents and reverts from a repository's git history.

Everything here comes from the **local clone**, not the GitHub API. Phase 0
cloned full history precisely so this step costs no API quota, has no rate
limit, and works offline - which matters because the corpus is rebuilt whenever
the operator set or chunking strategy changes.

What makes a commit worth indexing is that someone fixed a real defect and
explained it. marshmallow's history has 655 such commits and arrow 317, many
with bodies that name the exact edge case:

    Fix URL validator rejecting a fragment after an empty path (#3016)

    `validate.URL` rejected absolute URLs whose fragment follows an empty
    path with no query, such as `https://example.com#frag`. ...

That is the difference the retrieval layer is meant to make. Given a diff in
``validate.URL``, a Prosecutor that has seen this writes a test about fragments
and empty paths. One that has not writes a generic edge-case test.
"""

from __future__ import annotations

import ast
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from verdict.logging import get_logger
from verdict.retrieval.documents import Document, Kind
from verdict.targets.workspace import TargetWorkspace, _resolve

log = get_logger(__name__)

# Record and field separators that cannot appear in a commit message.
_RECORD = "\x1e"
_FIELD = "\x1f"

# A commit is an "incident" if its subject says it repaired something. Feature
# commits are excluded deliberately: the Prosecutor is looking for what has
# broken here before, and indexing every commit would bury that in noise.
_INCIDENT_SUBJECT = re.compile(
    r"\b(fix(e[sd])?|bug|regression|broken|crash|incorrect|wrong|fail(s|ed|ing)?|"
    r"hotfix|patch|workaround|edge ?case|off.by.one|typo in|raise[sd]?)\b",
    re.IGNORECASE,
)
_REVERT_SUBJECT = re.compile(r"^\s*revert\b", re.IGNORECASE)
_ISSUE_REF = re.compile(r"#(\d{2,6})")
_IDENTIFIER = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]{2,}\b")

# Commits that touch a very large number of files are refactors or reformattings.
# Their "fix" wording is incidental and they carry no localised lesson.
MAX_FILES_PER_INCIDENT = 12
MAX_PATCH_CHARS = 4000


@dataclass(frozen=True, slots=True)
class Commit:
    """One parsed commit from the local history."""

    sha: str
    timestamp: int
    subject: str
    body: str
    patch: str

    @property
    def is_revert(self) -> bool:
        return bool(_REVERT_SUBJECT.match(self.subject))

    @property
    def is_incident(self) -> bool:
        return self.is_revert or bool(_INCIDENT_SUBJECT.search(self.subject))

    @property
    def issues(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(_ISSUE_REF.findall(f"{self.subject}\n{self.body}")))

    def files(self, normalize: Callable[[str], str] | None = None) -> tuple[str, ...]:
        """Paths touched, read from the patch's ``+++`` headers.

        ``normalize`` rewrites historical paths onto their present-day location,
        so a commit from before a directory move still matches the file a diff
        is in today.
        """
        found = [line[6:].strip() for line in self.patch.splitlines() if line.startswith("+++ b/")]
        if normalize is not None:
            found = [normalize(path) for path in found]
        return tuple(dict.fromkeys(found))

    def changed_text(self) -> str:
        """Only the added and removed lines - the substance of the change."""
        return "\n".join(
            line
            for line in self.patch.splitlines()
            if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
        )


def _run_git(args: list[str], cwd: Path) -> str:
    proc = subprocess.run(  # noqa: S603 - fixed argv from a pinned registry
        [_resolve("git"), *args], cwd=cwd, capture_output=True, text=True, check=False
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr[:400]}")
    return proc.stdout


def read_commits(workspace: TargetWorkspace, *, limit: int = 4000) -> list[Commit]:
    """Read the history of the target's source directory in one git call.

    One invocation rather than one per commit: marshmallow has 3,735 commits,
    and spawning a process for each would dominate the runtime of indexing.
    """
    raw = _run_git(
        [
            "log",
            "--no-merges",
            f"--max-count={limit}",
            f"--format={_RECORD}%H{_FIELD}%at{_FIELD}%s{_FIELD}%b{_FIELD}",
            "--patch",
            "--unified=0",
            "--no-color",
            "--",
            *workspace.target.source_pathspecs,
        ],
        cwd=workspace.repo_dir,
    )
    commits: list[Commit] = []
    for block in raw.split(_RECORD):
        if not block.strip():
            continue
        parts = block.split(_FIELD)
        if len(parts) < 5:
            continue
        sha, timestamp, subject, body, patch = parts[0], parts[1], parts[2], parts[3], parts[4]
        try:
            when = int(timestamp)
        except ValueError:
            continue
        commits.append(
            Commit(
                sha=sha.strip(),
                timestamp=when,
                subject=subject.strip(),
                body=body.strip(),
                patch=patch,
            )
        )
    return commits


def _current_symbols(workspace: TargetWorkspace) -> dict[str, set[str]]:
    """Function and class names defined in each source file, at the pinned commit."""
    index: dict[str, set[str]] = {}
    for path in workspace.source_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        names = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
        }
        index[workspace.relative(path)] = names
    return index


def _symbols_touched(
    commit: Commit, symbols_by_file: dict[str, set[str]], normalize: Callable[[str], str]
) -> tuple[str, ...]:
    """Which *currently existing* symbols a historical commit relates to.

    Historical line numbers cannot be mapped onto the pinned checkout - the file
    has moved on. So instead of trying, this intersects the identifiers appearing
    in the commit's changed lines with the symbols that exist in the files it
    touched today. A commit that mentions ``set_value`` is linked to ``set_value``
    regardless of where it lived at the time.
    """
    candidates: set[str] = set()
    for path in commit.files(normalize):
        candidates |= symbols_by_file.get(path, set())
    if not candidates:
        return ()
    mentioned = set(_IDENTIFIER.findall(commit.changed_text())) | set(
        _IDENTIFIER.findall(f"{commit.subject}\n{commit.body}")
    )
    return tuple(sorted(candidates & mentioned))


def mine_history(workspace: TargetWorkspace, *, limit: int = 4000) -> list[Document]:
    """Turn bugfix and revert commits into retrievable documents."""
    slug = workspace.target.slug
    commits = read_commits(workspace, limit=limit)
    symbols_by_file = _current_symbols(workspace)
    normalize = workspace.target.normalize_path
    log.info(
        "Read %d commits touching %s",
        len(commits),
        " or ".join(workspace.target.source_pathspecs),
    )

    documents: list[Document] = []
    skipped_large = 0
    for commit in commits:
        if not commit.is_incident:
            continue
        files = commit.files(normalize)
        if not files:
            continue
        if len(files) > MAX_FILES_PER_INCIDENT:
            # A sweeping refactor whose message happens to say "fix" teaches
            # nothing localised, and would dilute every query it matches.
            skipped_large += 1
            continue

        symbols = _symbols_touched(commit, symbols_by_file, normalize)
        patch = commit.changed_text()
        if len(patch) > MAX_PATCH_CHARS:
            patch = patch[:MAX_PATCH_CHARS] + "\n... (truncated)"

        kind: Kind = "revert" if commit.is_revert else "incident"
        body_parts = [commit.body] if commit.body else []
        body_parts.append(f"files: {', '.join(files)}")
        if patch:
            body_parts.append("change:\n" + patch)

        documents.append(
            Document(
                doc_id=Document.make_id(kind, slug, commit.sha),
                kind=kind,
                target_slug=slug,
                title=commit.subject,
                body="\n\n".join(body_parts),
                module_path=files[0],
                symbols=symbols,
                metadata={
                    "sha": commit.sha,
                    "short_sha": commit.sha[:12],
                    "timestamp": commit.timestamp,
                    "issues": list(commit.issues),
                    "files": list(files),
                    "url": f"https://github.com/{workspace.target.full_name}/commit/{commit.sha}",
                },
            )
        )

    log.info(
        "Mined %d incident documents for %s (%d wide-refactor commits skipped)",
        len(documents),
        slug,
        skipped_large,
    )
    return documents
