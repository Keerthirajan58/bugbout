"""What a sandbox promises, independent of the operating system that enforces it.

Agents run shell commands a model wrote, and bugbout runs tests a model wrote.
Both happen inside a sandbox that lets a process read anything but write only
beneath the directories it was given, and - unless it needs to reach a model
API - not use the network at all.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, override


@dataclass(frozen=True, slots=True)
class Confinement:
    """What a sandboxed process may do beyond reading files."""

    writable: tuple[Path, ...] = ()
    """Directories it may write beneath, besides the system's temporary directories."""

    network: bool = False


class Sandbox(ABC):
    name: ClassVar[str]

    @classmethod
    @abstractmethod
    def problem(cls) -> str | None:
        """Why this backend cannot run on this machine; ``None`` when it can."""

    @classmethod
    def available(cls) -> bool:
        return cls.problem() is None

    @abstractmethod
    def prefix(self, confinement: Confinement) -> list[str]:
        """The argv that runs a command under ``confinement`` when placed before it."""

    def wrap(self, argv: Sequence[str], confinement: Confinement) -> list[str]:
        return [*self.prefix(confinement), *argv]


class NoSandbox(Sandbox):
    """Runs commands as they are. Only for machines where nothing else is possible."""

    name = "none"

    @classmethod
    @override
    def problem(cls) -> str | None:
        return None

    @override
    def prefix(self, confinement: Confinement) -> list[str]:
        return []
