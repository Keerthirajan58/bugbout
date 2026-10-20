"""``bugbout doctor``: everything a bout needs, checked before any agent spends quota."""

from __future__ import annotations

import shutil
from dataclasses import dataclass

from bugbout.agents.base import CodingAgent, version_of
from bugbout.errors import BugboutError
from bugbout.project import Project
from bugbout.sandbox import get_sandbox
from bugbout.tasks.issues import default_tokens


@dataclass(frozen=True, slots=True)
class Check:
    name: str
    ok: bool
    detail: str
    required: bool = True
    """A failed check that is not required is a warning: the bout can still run."""


def diagnose(project: Project) -> list[Check]:
    checks = [_tool("git", "needed to read the project's history")]
    if not project.config.project.interpreter:
        checks.append(_tool("uv", "builds the test environment: https://docs.astral.sh/uv/"))
    checks += [_sandbox(project), _repository(project), _github()]
    try:
        contestants, examiners = project.contestants(), project.examiners()
    except BugboutError as exc:
        return [*checks, Check("agents", False, str(exc))]
    if not contestants:
        checks.append(Check("agents", False, "no contestants configured"))
    checks += [_agent(agent.name, agent) for agent in contestants]
    return checks + [_agent(examiner.author, examiner.agent) for examiner in examiners]


def _tool(name: str, purpose: str) -> Check:
    path = shutil.which(name)
    return Check(name, path is not None, path or f"not found; {purpose}")


def _sandbox(project: Project) -> Check:
    try:
        sandbox = get_sandbox(project.config.run.sandbox)
    except BugboutError as exc:
        return Check("sandbox", False, str(exc))
    if sandbox.name == "none":
        return Check("sandbox", False, "none: agents' commands run unconfined", required=False)
    return Check("sandbox", True, sandbox.name)


def _repository(project: Project) -> Check:
    if url := project.repository_url:
        return Check("repository", True, f"{url} (cloned on first run)")
    try:
        repository = project.open_repository()
    except BugboutError as exc:
        return Check("repository", False, str(exc))
    slug = project.config.project.github or repository.github_slug()
    if slug is None:
        return Check("repository", False, 'origin is not on GitHub; set github = "owner/name"')
    return Check("repository", True, f"{repository.path} (issues from {slug})")


def _github() -> Check:
    if any(token is not None for token in default_tokens()):
        return Check("GitHub access", True, "authenticated")
    return Check(
        "GitHub access",
        False,
        "anonymous, 60 requests an hour; set GITHUB_TOKEN or run `gh auth login`",
        required=False,
    )


def _agent(label: str, agent: CodingAgent) -> Check:
    try:
        agent.check()
    except BugboutError as exc:
        return Check(label, False, str(exc))
    return Check(label, True, version_of(agent.command()))
