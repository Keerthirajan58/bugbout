"""Reconstruct tasks from the project's history, the way SWE-bench builds them.

A change that landed on the mainline becomes a task when it fixed something,
named the issue or pull request behind it, and shipped at least one new test
function alongside the fix. That test is the point: without one, nothing could
tell a correct patch from a plausible one.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator
from dataclasses import dataclass, field

from bugbout.tasks.issues import Issue, IssueTracker, strip_solution
from bugbout.tasks.task import Task, is_test_module
from bugbout.workspace.repository import Commit, Repository

_FIX = re.compile(
    r"\b(fix(e[sd])?|bug|regression|broken|crash|incorrect|wrong|raise[sd]?)\b", re.IGNORECASE
)
_REFERENCE = re.compile(r"#(\d{1,6})\b")
# "closes #2936" points at the user-filed issue behind a change, which describes
# the problem without knowing the answer.
_LINKED_ISSUE = re.compile(
    r"\b(?:close[sd]?|fixe?[sd]?|resolve[sd]?)\s*:?\s*#(\d{1,6})\b", re.IGNORECASE
)
_MERGED_PULL_REQUEST = re.compile(r"^Merge pull request #\d+")

MIN_STATEMENT_CHARS = 40
MAX_STATEMENT_CHARS = 4000


@dataclass(frozen=True, slots=True)
class MiningRules:
    source_root: str
    """Fixes must change Python files under this directory (the package)..."""

    test_root: str
    """...and add a test function under this one."""

    since: str | None = None
    """Only consider changes after this date (anything ``git log --since`` accepts)."""

    limit: int = 25
    max_source_files: int = 2
    """A fix spread over more source files than this is too broad to be one bug."""


@dataclass
class MiningReport:
    """How many commits were looked at, and why candidates were dropped."""

    examined: int = 0
    fix_shaped: int = 0
    rejected: dict[str, int] = field(default_factory=dict)

    def drop(self, reason: str) -> None:
        self.rejected[reason] = self.rejected.get(reason, 0) + 1


@dataclass(frozen=True, slots=True)
class _Statement:
    issue: Issue
    text: str
    redacted: bool


class Miner:
    def __init__(self, repository: Repository, issues: IssueTracker, rules: MiningRules) -> None:
        self.repository = repository
        self.issues = issues
        self.rules = rules

    def mine(self) -> tuple[list[Task], MiningReport]:
        """Tasks from the newest fixes backwards, one per issue, up to the limit."""
        report = MiningReport()
        tasks: list[Task] = []
        seen: set[int] = set()
        for commit in self.repository.log(self.rules.since):
            if len(tasks) >= self.rules.limit:
                break
            report.examined += 1
            if not _FIX.search(_title(commit)):
                continue
            report.fix_shaped += 1
            task = self._task(commit, report)
            if task is None:
                continue
            if task.reference in seen:
                report.drop("a later change already covers this issue")
                continue
            seen.add(task.reference)
            tasks.append(task)
        return tasks, report

    def _task(self, commit: Commit, report: MiningReport) -> Task | None:
        source_prefix, test_prefix = _prefix(self.rules.source_root), _prefix(self.rules.test_root)
        files = [f for f in self.repository.changed_files(commit) if f.endswith(".py")]
        tests = [f for f in files if f.startswith(test_prefix)]
        sources = [f for f in files if f.startswith(source_prefix) and f not in tests]
        modules = [f for f in tests if is_test_module(f)]
        if not sources:
            report.drop("no source change")
            return None
        if not modules:
            report.drop("no test shipped with the fix")
            return None
        if len(sources) > self.rules.max_source_files:
            report.drop("too broad to be one bug")
            return None
        references = _references(commit)
        if not references:
            report.drop("no issue or pull-request reference")
            return None
        base = commit.parents[0]
        added = self._added_tests(commit.sha, base, modules)
        if not added:
            report.drop("the fix added no test function")
            return None
        statement = self._statement(references)
        if statement is None:
            report.drop("problem statement unavailable")
            return None
        if len(statement.text) < MIN_STATEMENT_CHARS:
            report.drop("problem statement too thin to act on")
            return None
        return Task(
            task_id=commit.sha[:12],
            base_commit=base,
            fix_commit=commit.sha,
            reference=statement.issue.number,
            reference_kind="pull_request" if statement.issue.is_pull_request else "issue",
            title=statement.issue.title or _title(commit),
            problem_statement=statement.text[:MAX_STATEMENT_CHARS],
            source_files=tuple(sources),
            test_files=tuple(tests),
            added_tests=tuple(sorted(added)),
            statement_redacted=statement.redacted,
        )

    def _added_tests(self, sha: str, base: str, modules: list[str]) -> set[str]:
        added: set[str] = set()
        for path in modules:
            after = _test_functions(self.repository.show(sha, path))
            added |= after - _test_functions(self.repository.show(base, path))
        return added

    def _statement(self, references: list[int]) -> _Statement | None:
        """The best available description: the user's issue when a pull request links one."""
        for number in references:
            issue = self.issues.fetch(number)
            if issue is None:
                continue
            if issue.is_pull_request and (linked := _LINKED_ISSUE.search(issue.body)):
                reported = self.issues.fetch(int(linked.group(1)))
                if reported is not None and not reported.is_pull_request:
                    issue = reported
            text, redacted = strip_solution(issue.body)
            return _Statement(issue, text, redacted)
        return None


def _prefix(root: str) -> str:
    root = root.strip().strip("/")
    return "" if root in ("", ".") else root + "/"


def _title(commit: Commit) -> str:
    """The change's one-line summary; for a pull request's merge commit, the PR title."""
    if _MERGED_PULL_REQUEST.match(commit.subject):
        for line in commit.body.splitlines():
            if line.strip():
                return line.strip()
    return commit.subject


def _references(commit: Commit) -> list[int]:
    """Issue numbers worth fetching, best first: closed issues, then the change's own number."""
    message = f"{commit.subject}\n{commit.body}"
    numbers = [int(n) for n in _LINKED_ISSUE.findall(message)]
    numbers += [int(n) for n in _REFERENCE.findall(commit.subject)]
    return list(dict.fromkeys(numbers))


def _test_functions(source: bytes | None) -> set[str]:
    """Qualified names of the test functions in a module, e.g. ``TestParser.test_empty``."""
    if not source:
        return set()
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return set()
    return set(_collect(tree.body, prefix=""))


def _collect(body: list[ast.stmt], prefix: str) -> Iterator[str]:
    for node in body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name.startswith(
            "test"
        ):
            yield prefix + node.name
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            yield from _collect(node.body, f"{prefix}{node.name}.")
