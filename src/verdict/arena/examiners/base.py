"""The examiner interface: whoever writes blind evidence about a set of tasks.

Two kinds exist and the arena treats them identically. A prompted examiner
answers in one model call; an agentic one is a coding agent that explores a
checkout and runs its own test before submitting it. Both see the issue and
the code as it was before anyone touched it - never a contestant's patch.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass

from verdict.arena.agents.base import CodingAgent
from verdict.arena.examiners.exhibit import Exhibit
from verdict.arena.layout import ArenaLayout
from verdict.arena.vendors import vendor_of
from verdict.review.tasks import IssueTask


@dataclass(frozen=True, slots=True)
class ExamRoom:
    """What an examiner is allowed to see while it works."""

    layout: ArenaLayout
    suite: str
    source_context: Callable[[IssueTask, int], str]
    """The base version of the code relevant to a task, within a character budget."""

    retry_failed: bool = False
    """Re-run journaled attempts that ended without producing anything."""


class Examiner(ABC):
    author: str

    @property
    def vendor(self) -> str:
        return vendor_of(self.author)

    @property
    def agents(self) -> list[CodingAgent]:
        """The coding agents this examiner drives, if any."""
        return []

    @property
    def uses_local_models(self) -> bool:
        """Whether it needs the arena's private Ollama server while it works."""
        return any(agent.lane == "ollama" for agent in self.agents)

    @abstractmethod
    def examine(self, tasks: list[IssueTask], room: ExamRoom) -> list[Exhibit]:
        """One exhibit per task - empty, with the reason, when none could be written."""

    @abstractmethod
    def describe(self) -> dict[str, object]: ...
