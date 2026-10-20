"""bugbout: find out which coding agent actually fixes bugs in your codebase.

Replay your project's own fixed bugs, let coding agents race to fix them again,
and score every patch with the tests the maintainers wrote::

    from bugbout import Bout, render_markdown

    report = Bout.load(".").run()  # reads bugbout.toml
    print(render_markdown(report))

Agents can also be passed in directly, including ones of your own::

    from bugbout import Bout, ClaudeCode, Codex, Project

    bout = Bout(Project.load("."), contestants=[Codex("gpt-5.1-codex"), ClaudeCode("sonnet")])
"""

from bugbout.__about__ import __version__
from bugbout.agents import (
    AgentRun,
    ClaudeCode,
    Codex,
    CodingAgent,
    CommandAgent,
    GeminiCli,
    Invocation,
    MiniSweAgent,
    Session,
    create_agent,
)
from bugbout.arena.briefs import ASSIGNMENTS
from bugbout.arena.judge import POLICIES, JudgePolicy
from bugbout.bout import Bout
from bugbout.errors import BugboutError
from bugbout.project import Examiner, Project
from bugbout.report import Report, render_markdown, render_terminal

__all__ = [
    "ASSIGNMENTS",
    "POLICIES",
    "AgentRun",
    "Bout",
    "BugboutError",
    "ClaudeCode",
    "Codex",
    "CodingAgent",
    "CommandAgent",
    "Examiner",
    "GeminiCli",
    "Invocation",
    "JudgePolicy",
    "MiniSweAgent",
    "Project",
    "Report",
    "Session",
    "__version__",
    "create_agent",
    "render_markdown",
    "render_terminal",
]
