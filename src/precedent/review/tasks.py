"""Reconstruct real bugfix tasks from a repository's own history.

The construction follows SWE-bench: take a merged fix, rewind to the commit
immediately before it, and hand a coding agent only the problem statement. The
maintainer's own regression test - which the agent never sees - becomes the
oracle. Because the fix already happened and was reviewed, the ground truth is
unbiased by anything this project did.

Everything except the problem statement comes from the **local clone**, so
rebuilding the task set costs no API quota. Only the issue or pull-request text
needs GitHub, and it is cached on disk after the first fetch.

What is deliberately withheld from the agent: the fix diff, the test diff, the
commit subject, and any later commit. What it gets: the repository at the parent
commit, and the words a human used to describe the bug.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
import subprocess
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from precedent.logging import get_logger

log = get_logger(__name__)

# A commit worth reconstructing repaired something, said which issue, and
# shipped a test alongside the fix. The test is the whole point: without one
# there is no oracle, and the task cannot be scored.
_FIX_SUBJECT = re.compile(
    r"\b(fix(e[sd])?|bug|regression|broken|crash|incorrect|wrong|raise[sd]?)\b", re.IGNORECASE
)
_REF = re.compile(r"#(\d{2,6})")

# "closes #2936" in a pull-request body points at the user-filed issue behind
# it. That text is a better problem statement than the PR's own description,
# which is written by whoever already knew the answer.
_LINKED_ISSUE = re.compile(
    r"\b(?:close[sd]?|fixe?[sd]?|resolve[sd]?)\s*:?\s*#(\d{2,6})", re.IGNORECASE
)

# Sections a fix author adds to describe their own patch. They are stripped
# before the statement reaches the agent: leaving them in would be handing over
# the answer and would make every downstream number meaningless.
_SOLUTION_HEADING = re.compile(
    r"^\s{0,3}#{1,6}\s*(solution|the fix|fix|proposed fix|changes?|approach|implementation|patch)\b.*$",
    re.IGNORECASE | re.MULTILINE,
)
_ANY_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+\S.*$", re.MULTILINE)
_DIFF_FENCE = re.compile(r"```(?:diff|patch)\b.*?```", re.DOTALL)

MAX_STATEMENT_CHARS = 4000


@dataclass(frozen=True, slots=True)
class IssueTask:
    """One reconstructed bugfix task."""

    task_id: str
    target_slug: str

    base_commit: str
    """The commit immediately before the fix. The agent works from here."""

    fix_commit: str
    """The maintainer's real fix. Never shown to the agent; supplies the oracle."""

    reference: int
    """The issue or pull-request number the fix commit named."""

    reference_kind: str
    """``issue`` when a user-filed report was found, ``pull_request`` otherwise."""

    title: str
    problem_statement: str
    """What the agent is given, and the only thing it is given."""

    source_files: tuple[str, ...]
    test_files: tuple[str, ...]

    oracle_tests: tuple[str, ...]
    """Test functions the fix commit added. Absent from the base commit, so a
    base checkout cannot pass them and they cannot be satisfied by accident."""

    statement_redacted: bool = False
    """Whether a section describing the author's own fix had to be removed."""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> IssueTask:
        known = set(cls.__slots__)
        payload = {k: v for k, v in data.items() if k in known}
        for key in ("source_files", "test_files", "oracle_tests"):
            payload[key] = tuple(payload.get(key) or ())
        return cls(**payload)


@dataclass
class MiningReport:
    """Why candidates were dropped, so the write-up can state it exactly."""

    examined: int = 0
    matched_subject: int = 0
    rejected: dict[str, int] = field(default_factory=dict)

    def drop(self, reason: str) -> None:
        self.rejected[reason] = self.rejected.get(reason, 0) + 1


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()[:400]}")
    return proc.stdout


def _test_functions(source: str) -> set[str]:
    """Every test function in a file, including methods on test classes."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name.startswith(
            "test_"
        ):
            found.add(node.name)
    return found


def _file_at(repo: Path, commit: str, path: str) -> str:
    try:
        return _git(repo, "show", f"{commit}:{path}")
    except RuntimeError:
        return ""


def strip_solution(body: str) -> tuple[str, bool]:
    """Remove any section in which the author describes their own patch.

    A pull-request description often carries both the bug report and the fix.
    Only the report may reach the agent. Everything from the first
    solution-shaped heading to the next heading is dropped, along with any
    fenced diff, and the redaction is recorded on the task rather than done
    silently.
    """
    redacted = False
    cleaned, count = _DIFF_FENCE.subn("", body)
    redacted = redacted or bool(count)

    while (match := _SOLUTION_HEADING.search(cleaned)) is not None:
        following = _ANY_HEADING.search(cleaned, match.end())
        end = following.start() if following else len(cleaned)
        cleaned = cleaned[: match.start()] + cleaned[end:]
        redacted = True

    return cleaned.strip(), redacted


class GitHubText:
    """Fetches issue and pull-request text, cached on disk.

    Cached because the task set gets rebuilt whenever the filters change, and
    an unauthenticated caller gets 60 requests an hour.
    """

    def __init__(self, token: str | None, cache_dir: Path) -> None:
        self.token = token
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def fetch(self, full_name: str, number: int) -> dict[str, Any] | None:
        cached = self.cache_dir / f"{full_name.replace('/', '_')}.{number}.json"
        if cached.exists():
            hit: dict[str, Any] = json.loads(cached.read_text(encoding="utf-8"))
            return hit

        request = urllib.request.Request(
            f"https://api.github.com/repos/{full_name}/issues/{number}",
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "precedent",
                **({"Authorization": f"Bearer {self.token}"} if self.token else {}),
            },
        )
        try:
            # The URL is built from a literal https prefix, not from input.
            with urllib.request.urlopen(request, timeout=30) as response:
                payload: dict[str, Any] = json.load(response)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
            log.warning("GitHub fetch failed for %s#%s: %s", full_name, number, exc)
            return None
        cached.write_text(json.dumps(payload), encoding="utf-8")
        return payload


def mine_tasks(
    repo: Path,
    *,
    target_slug: str,
    full_name: str,
    github: GitHubText,
    since: str = "2021-01-01",
    source_prefix: str = "src/",
    limit: int = 40,
) -> tuple[list[IssueTask], MiningReport]:
    """Find fixes that can be replayed as tasks, newest first."""
    report = MiningReport()
    # One task per issue. arrow's history fixes issue #703 in four separate
    # commits; keeping all four would weight one bug report four times over
    # while presenting the agent with the same problem statement each time.
    seen_references: set[int] = set()
    raw = _git(
        repo,
        "log",
        f"--since={since}",
        "--pretty=format:@@@%H|%s",
        "--name-only",
    )
    tasks: list[IssueTask] = []

    for block in raw.split("@@@"):
        if not block.strip() or len(tasks) >= limit:
            continue
        report.examined += 1
        header, *rest = block.strip().splitlines()
        sha, subject = header.split("|", 1)
        files = [f for f in rest if f.strip()]

        if not _FIX_SUBJECT.search(subject):
            continue
        report.matched_subject += 1

        sources = [f for f in files if f.startswith(source_prefix) and f.endswith(".py")]
        tests = [f for f in files if f.startswith("tests/") and f.endswith(".py")]
        if not sources:
            report.drop("no source change")
            continue
        if not tests:
            report.drop("no test shipped with the fix, so no oracle")
            continue
        if any("mypy_test_cases" in t for t in tests):
            # These assert on type-checker output, not on runtime behaviour, so
            # neither the agent's diff nor the auditor can be scored by them.
            report.drop("type-checking test only")
            continue
        if len(sources) > 2 or len(files) > 6:
            report.drop("too broad to be one bug")
            continue

        reference = _REF.search(subject)
        if not reference:
            report.drop("no issue or PR reference")
            continue

        base = _git(repo, "rev-parse", f"{sha}^").strip()

        added: set[str] = set()
        for path in tests:
            added |= _test_functions(_file_at(repo, sha, path)) - _test_functions(
                _file_at(repo, base, path)
            )
        if not added:
            report.drop("fix added no new test function")
            continue

        text = github.fetch(full_name, int(reference.group(1)))
        if text is None:
            report.drop("problem statement unavailable")
            continue

        number = int(reference.group(1))
        kind = "pull_request" if "pull_request" in text else "issue"
        body = text.get("body") or ""
        title = text.get("title") or subject

        # Prefer the user-filed issue a pull request closes: it states the
        # problem without knowing the answer.
        if kind == "pull_request" and (linked := _LINKED_ISSUE.search(body)):
            issue = github.fetch(full_name, int(linked.group(1)))
            if issue is not None and "pull_request" not in issue:
                number, kind = int(linked.group(1)), "issue"
                body = issue.get("body") or body
                title = issue.get("title") or title

        statement, redacted = strip_solution(body)
        if len(statement) < 40:
            report.drop("problem statement too thin to act on")
            continue
        if number in seen_references:
            report.drop("a later commit already covers this issue")
            continue
        seen_references.add(number)

        tasks.append(
            IssueTask(
                task_id=hashlib.sha256(f"{target_slug}:{sha}".encode()).hexdigest()[:12],
                target_slug=target_slug,
                base_commit=base,
                fix_commit=sha,
                reference=number,
                reference_kind=kind,
                title=title,
                problem_statement=statement[:MAX_STATEMENT_CHARS],
                source_files=tuple(sources),
                test_files=tuple(tests),
                oracle_tests=tuple(sorted(added)),
                statement_redacted=redacted,
            )
        )

    return tasks, report
