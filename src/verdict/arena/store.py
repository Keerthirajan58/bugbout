"""The arena's artifacts, one suite at a time, one file per stage.

A **suite** is a set of tasks with its own artifacts: ``dev`` is where the
system is designed and tuned; ``test`` is held out until the design is frozen,
and is the only place a headline number may come from.

Stages stay in separate files because their costs differ by orders of
magnitude. An oracle takes seconds to rebuild; a contestant's patch took a
coding agent minutes of quota and cannot be regenerated identically. Re-scoring
or re-judging never requires re-running an agent.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from verdict.arena.examiners.exhibit import Exhibit
from verdict.arena.matrix import Matrix
from verdict.arena.race import Attempt
from verdict.arena.truth import Oracle, Resolution
from verdict.artifacts import payload_of, read_artifact, write_artifact
from verdict.paths import artifacts_dir
from verdict.review.tasks import IssueTask


class ArenaStore:
    def __init__(self, suite: str, root: Path | None = None) -> None:
        self.suite = suite
        self.root = (root or artifacts_dir() / "arena") / suite

    # --- generic -----------------------------------------------------------------

    def path(self, name: str) -> Path:
        return self.root / f"{name}.json"

    def exists(self, name: str) -> bool:
        return self.path(name).exists()

    def save(self, name: str, payload: dict[str, Any], *, freeze: bool = False) -> Path:
        path = self.path(name)
        write_artifact(path, kind=_kind(name), payload=payload, freeze=freeze)
        return path

    def load(self, name: str) -> dict[str, Any]:
        return payload_of(read_artifact(self.path(name), expect_kind=_kind(name)))

    # --- tasks and ground truth ---------------------------------------------------

    def save_tasks(self, tasks: list[IssueTask], mining: dict[str, Any]) -> Path:
        return self.save("tasks", {"mining": mining, "tasks": [t.to_dict() for t in tasks]})

    def tasks(self) -> list[IssueTask]:
        return [IssueTask.from_dict(t) for t in self.load("tasks")["tasks"]]

    def save_oracles(self, oracles: list[Oracle]) -> Path:
        return self.save("oracles", {"oracles": [o.to_dict() for o in oracles]})

    def oracles(self) -> dict[str, Oracle]:
        return {o["task_id"]: Oracle.from_dict(o) for o in self.load("oracles")["oracles"]}

    def scorable_tasks(self) -> list[IssueTask]:
        """Tasks whose oracle proves something: the denominator of every number."""
        valid = {tid for tid, oracle in self.oracles().items() if oracle.valid}
        return [t for t in self.tasks() if t.task_id in valid]

    # --- contestants ----------------------------------------------------------------

    def save_entries(self, entrant: str, attempts: list[Attempt], meta: dict[str, Any]) -> Path:
        return self.save(
            f"entries/{entrant.replace('/', '__')}",
            {"entrant": entrant, "meta": meta, "attempts": [a.to_dict() for a in attempts]},
        )

    def entrants(self) -> list[str]:
        folder = self.root / "entries"
        if not folder.exists():
            return []
        return sorted(p.stem.replace("__", "/") for p in folder.glob("*.json"))

    def entries(self, entrant: str) -> tuple[list[Attempt], dict[str, Any]]:
        payload = self.load(f"entries/{entrant.replace('/', '__')}")
        return [Attempt.from_dict(a) for a in payload["attempts"]], payload.get("meta", {})

    def save_resolutions(self, resolutions: list[Resolution]) -> Path:
        return self.save("resolutions", {"resolutions": [r.to_dict() for r in resolutions]})

    def resolutions(self) -> list[Resolution]:
        return [Resolution.from_dict(r) for r in self.load("resolutions")["resolutions"]]

    # --- evidence and verdicts -------------------------------------------------------

    def save_exhibits(self, exhibits: list[Exhibit], examiners: dict[str, Any]) -> Path:
        return self.save(
            "exhibits", {"examiners": examiners, "exhibits": [e.to_dict() for e in exhibits]}
        )

    def exhibits(self) -> tuple[list[Exhibit], dict[str, Any]]:
        payload = self.load("exhibits")
        return [Exhibit.from_dict(e) for e in payload["exhibits"]], payload.get("examiners", {})

    def save_matrices(self, matrices: list[Matrix]) -> Path:
        return self.save("matrices", {"matrices": [m.to_dict() for m in matrices]})

    def matrices(self) -> dict[str, Matrix]:
        return {m["task_id"]: Matrix.from_dict(m) for m in self.load("matrices")["matrices"]}

    def save_verdicts(self, policy: str, verdicts: list[dict[str, Any]]) -> Path:
        return self.save(f"verdicts/{policy}", {"policy": policy, "verdicts": verdicts})

    def verdicts(self, policy: str) -> list[dict[str, Any]]:
        verdicts: list[dict[str, Any]] = self.load(f"verdicts/{policy}")["verdicts"]
        return verdicts


def _kind(name: str) -> str:
    """``entries/claude-code`` -> ``arena_entries``: one artifact kind per stage."""
    return f"arena_{name.split('/', 1)[0]}"
