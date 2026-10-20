"""Tasks: real, already-fixed bugs from a project's history, and their ground truth."""

from bugbout.tasks.issues import GitHubIssues, strip_solution
from bugbout.tasks.mining import Miner, MiningReport, MiningRules
from bugbout.tasks.oracle import Oracle, Referee, Resolution
from bugbout.tasks.task import Task

__all__ = [
    "GitHubIssues",
    "Miner",
    "MiningReport",
    "MiningRules",
    "Oracle",
    "Referee",
    "Resolution",
    "Task",
    "strip_solution",
]
