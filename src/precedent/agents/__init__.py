"""The adversarial agent pair: Prosecutor accuses, Skeptic verifies by execution."""

from precedent.agents.context import DiffContext, build_context
from precedent.agents.prosecutor import Accusation, Prosecutor
from precedent.agents.skeptic import Skeptic, SkepticVerdict

__all__ = [
    "Accusation",
    "DiffContext",
    "Prosecutor",
    "Skeptic",
    "SkepticVerdict",
    "build_context",
]
