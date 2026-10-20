"""The contest itself: briefs, the race, cross-examination and the judge."""

from bugbout.arena.briefs import ASSIGNMENTS, FIX, Assignment, Brief, Fix, Reproduce, Specify
from bugbout.arena.exhibits import Exhibit, exhibit_of
from bugbout.arena.judge import DEFAULT_POLICY, POLICIES, Judge, JudgePolicy, Standing, Verdict
from bugbout.arena.matrix import BASE, CrossExaminer, Matrix
from bugbout.arena.race import Attempt, Race

__all__ = [
    "ASSIGNMENTS",
    "BASE",
    "DEFAULT_POLICY",
    "FIX",
    "POLICIES",
    "Assignment",
    "Attempt",
    "Brief",
    "CrossExaminer",
    "Exhibit",
    "Fix",
    "Judge",
    "JudgePolicy",
    "Matrix",
    "Race",
    "Reproduce",
    "Specify",
    "Standing",
    "Verdict",
    "exhibit_of",
]
