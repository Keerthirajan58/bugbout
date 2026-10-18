"""The adversarial agent pair: Prosecutor accuses, Skeptic verifies by execution."""

from verdict.agents.context import DiffContext, build_context
from verdict.agents.prosecutor import Accusation, Prosecutor
from verdict.agents.skeptic import Skeptic, SkepticVerdict

__all__ = [
    "Accusation",
    "DiffContext",
    "Prosecutor",
    "Skeptic",
    "SkepticVerdict",
    "build_context",
]
