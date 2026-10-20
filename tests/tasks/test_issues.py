"""Issue text: fetched once, cached, and stripped of any author's solution."""

from __future__ import annotations

import json
from pathlib import Path

from bugbout.tasks.issues import GitHubIssues, Issue, strip_solution


class TestStripSolution:
    def test_a_solution_section_is_removed(self) -> None:
        body = (
            "It crashes on empty input.\n\n## Solution\nGuard the loop.\n\n## Notes\nSeen on 3.12."
        )
        cleaned, redacted = strip_solution(body)
        assert redacted
        assert cleaned == "It crashes on empty input.\n\n## Notes\nSeen on 3.12."

    def test_a_fenced_diff_is_removed(self) -> None:
        cleaned, redacted = strip_solution("Broken.\n```diff\n- a\n+ b\n```\n")
        assert redacted and cleaned == "Broken."

    def test_a_plain_report_is_left_alone(self) -> None:
        body = "Steps to reproduce:\n```python\nf(1)\n```"
        assert strip_solution(body) == (body, False)


class TestGitHubIssues:
    def _cache(self, cache_dir: Path, number: int, payload: dict[str, object]) -> None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        (cache_dir / f"owner_name.{number}.json").write_text(json.dumps(payload))

    def test_a_cached_issue_is_served_without_a_request(self, tmp_path: Path) -> None:
        self._cache(tmp_path, 3, {"number": 3, "title": "Crash", "body": None})
        issues = GitHubIssues("owner/name", tmp_path, tokens=[])
        assert issues.fetch(3) == Issue(3, "Crash", "", is_pull_request=False)

    def test_a_pull_request_is_recognised(self, tmp_path: Path) -> None:
        self._cache(tmp_path, 4, {"number": 4, "title": "Fix", "body": "x", "pull_request": {}})
        assert GitHubIssues("owner/name", tmp_path, tokens=[]).fetch(4) == Issue(
            4, "Fix", "x", True
        )

    def test_with_no_credentials_left_nothing_is_fetched(self, tmp_path: Path) -> None:
        assert GitHubIssues("owner/name", tmp_path, tokens=[]).fetch(5) is None

    def test_the_caller_s_token_list_is_not_mutated(self, tmp_path: Path) -> None:
        tokens: list[str | None] = ["a"]
        GitHubIssues("owner/name", tmp_path, tokens=tokens).tokens.pop()
        assert tokens == ["a"]
