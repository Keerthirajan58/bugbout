"""The words a human used to describe a bug: issue and pull-request text.

Anything in which a pull-request author describes their own patch is stripped
before an agent sees it - leaving it in would hand over the answer.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from bugbout.log import get_logger

log = get_logger(__name__)

_SOLUTION_HEADING = re.compile(
    r"^\s{0,3}#{1,6}\s*(solution|the fix|fix|proposed fix|changes?|approach|implementation|patch)\b.*$",
    re.IGNORECASE | re.MULTILINE,
)
_ANY_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+\S.*$", re.MULTILINE)
_DIFF_FENCE = re.compile(r"```(?:diff|patch)\b.*?```", re.DOTALL)


@dataclass(frozen=True, slots=True)
class Issue:
    number: int
    title: str
    body: str
    is_pull_request: bool = False


class IssueTracker(Protocol):
    """Where issue and pull-request text comes from."""

    def fetch(self, number: int) -> Issue | None:
        """The issue or pull request numbered ``number``, or ``None`` if unavailable."""
        ...


def strip_solution(body: str) -> tuple[str, bool]:
    """Remove every section in which an author describes their own patch.

    Returns the cleaned text and whether anything was removed, so a task can
    record the redaction rather than have it happen silently.
    """
    cleaned, fences = _DIFF_FENCE.subn("", body)
    redacted = bool(fences)
    while (match := _SOLUTION_HEADING.search(cleaned)) is not None:
        following = _ANY_HEADING.search(cleaned, match.end())
        end = following.start() if following else len(cleaned)
        cleaned = cleaned[: match.start()] + cleaned[end:]
        redacted = True
    return cleaned.strip(), redacted


class GitHubIssues:
    """Issue and pull-request text for one GitHub repository, cached on disk.

    Credentials are tried in order - a configured token, then the GitHub CLI's,
    then none (60 requests an hour). A token GitHub rejects is dropped for the
    rest of the run, and once the rate limit is hit no further requests are made.
    """

    def __init__(self, slug: str, cache_dir: Path, tokens: list[str | None] | None = None) -> None:
        self.slug = slug
        self.cache_dir = cache_dir
        self.tokens = list(tokens) if tokens is not None else default_tokens()
        self._rate_limited = False

    def fetch(self, number: int) -> Issue | None:
        cached = self.cache_dir / f"{self.slug.replace('/', '_')}.{number}.json"
        if cached.exists():
            return _issue(json.loads(cached.read_text(encoding="utf-8")))
        payload = self._request(number)
        if payload is None:
            return None
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        cached.write_text(json.dumps(payload), encoding="utf-8")
        return _issue(payload)

    def _request(self, number: int) -> dict[str, Any] | None:
        while self.tokens and not self._rate_limited:
            token = self.tokens[0]
            request = urllib.request.Request(
                f"https://api.github.com/repos/{self.slug}/issues/{number}",
                headers={
                    "Accept": "application/vnd.github+json",
                    "User-Agent": "bugbout",
                    **({"Authorization": f"Bearer {token}"} if token else {}),
                },
            )
            try:
                # A literal https URL; the slug comes from the repository's own remote.
                with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
                    payload: dict[str, Any] = json.load(response)
                    return payload
            except urllib.error.HTTPError as exc:
                if exc.code == 401 and token is not None:
                    log.warning("GitHub rejected a token; trying the next credential")
                    self.tokens.pop(0)
                    continue
                if exc.code in (403, 429) and exc.headers.get("X-RateLimit-Remaining") == "0":
                    log.warning(
                        "GitHub's rate limit is exhausted; set GITHUB_TOKEN or run "
                        "`gh auth login` to fetch more issues"
                    )
                    self._rate_limited = True
                    return None
                if exc.code != 404:
                    log.warning("Could not fetch %s#%s: %s", self.slug, number, exc)
                return None
            except (urllib.error.URLError, TimeoutError) as exc:
                log.warning("Could not fetch %s#%s: %s", self.slug, number, exc)
                return None
        return None


def _issue(payload: dict[str, Any]) -> Issue:
    return Issue(
        number=int(payload["number"]),
        title=payload.get("title") or "",
        body=payload.get("body") or "",
        is_pull_request="pull_request" in payload,
    )


def default_tokens() -> list[str | None]:
    """``GITHUB_TOKEN``, then the GitHub CLI's token, then anonymous access."""
    tokens: list[str | None] = []
    if token := os.environ.get("GITHUB_TOKEN"):
        tokens.append(token)
    if (gh := shutil.which("gh")) is not None:
        try:
            completed = subprocess.run(  # noqa: S603 - the resolved gh binary, literal arguments
                [gh, "auth", "token"], capture_output=True, text=True, timeout=15, check=False
            )
        except (OSError, subprocess.TimeoutExpired):
            completed = None
        if completed is not None and completed.stdout.strip():
            tokens.append(completed.stdout.strip())
    return [*tokens, None]
