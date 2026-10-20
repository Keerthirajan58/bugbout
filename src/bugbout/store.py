"""A bout's results on disk: one file per stage, in the project's ``.bugbout/``.

Stages stay in separate files because their costs differ by orders of magnitude.
A ground truth takes seconds to rebuild; an agent's patch took minutes of quota
and cannot be regenerated identically. Re-judging or re-scoring never requires
running an agent again.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from bugbout.__about__ import __version__
from bugbout.arena.judge import Verdict
from bugbout.arena.matrix import Matrix
from bugbout.arena.race import Attempt
from bugbout.errors import ArtifactError
from bugbout.jsonfile import read_json, write_json
from bugbout.tasks.mining import MiningReport
from bugbout.tasks.oracle import Oracle, Resolution
from bugbout.tasks.task import Task


class Store:
    def __init__(self, root: Path) -> None:
        self.root = root

    def _write(self, name: str, payload: dict[str, Any]) -> Path:
        if not self.root.exists():
            self.root.mkdir(parents=True)
            # Results stay out of version control unless the project decides otherwise.
            (self.root / ".gitignore").write_text("*\n", encoding="utf-8")
        return write_json(self.root / name, {"bugbout": __version__, **payload})

    def _read(self, name: str) -> dict[str, Any]:
        data = read_json(self.root / name)
        if not isinstance(data, dict):
            raise ArtifactError(f"{self.root / name} is not a bugbout results file")
        return data

    def has(self, name: str) -> bool:
        return (self.root / name).exists()

    # --- tasks and ground truth ---------------------------------------------------

    def save_tasks(self, tasks: list[Task], mining: MiningReport) -> Path:
        report = {
            "examined": mining.examined,
            "fix_shaped": mining.fix_shaped,
            "rejected": mining.rejected,
        }
        return self._write("tasks.json", {"mining": report, "tasks": [t.to_dict() for t in tasks]})

    def tasks(self) -> list[Task]:
        return [Task.from_dict(t) for t in self._read("tasks.json")["tasks"]]

    def mining(self) -> dict[str, Any]:
        mining: dict[str, Any] = self._read("tasks.json")["mining"]
        return mining

    def save_oracles(self, oracles: dict[str, Oracle]) -> Path:
        return self._write("oracles.json", {"oracles": [o.to_dict() for o in oracles.values()]})

    def oracles(self) -> dict[str, Oracle]:
        if not self.has("oracles.json"):
            return {}
        return {o["task_id"]: Oracle.from_dict(o) for o in self._read("oracles.json")["oracles"]}

    # --- agents' attempts ---------------------------------------------------------

    def journal(self, assignment: str) -> Path:
        """Where a race journals one attempt per file, as each finishes."""
        return self.root / "attempts" / assignment

    def attempts(self, assignment: str) -> list[Attempt]:
        folder = self.journal(assignment)
        if not folder.exists():
            return []
        return [Attempt.from_dict(read_json(path)) for path in sorted(folder.glob("*/*.json"))]

    def save_agents(self, agents: dict[str, dict[str, str]]) -> Path:
        return self._write("agents.json", {"agents": agents})

    def agents(self) -> dict[str, dict[str, str]]:
        if not self.has("agents.json"):
            return {}
        agents: dict[str, dict[str, str]] = self._read("agents.json")["agents"]
        return agents

    # --- evidence, verdicts and scores ----------------------------------------------

    def save_matrix(self, matrix: Matrix, inputs: str) -> Path:
        """``inputs`` fingerprints what the matrix was built from, to know when it is stale."""
        return self._write(
            f"matrices/{matrix.task_id}.json", {"inputs": inputs, "matrix": matrix.to_dict()}
        )

    def matrix(self, task_id: str) -> tuple[Matrix, str] | None:
        name = f"matrices/{task_id}.json"
        if not self.has(name):
            return None
        data = self._read(name)
        return Matrix.from_dict(data["matrix"]), str(data["inputs"])

    def save_verdicts(self, verdicts: list[Verdict]) -> Path:
        return self._write("verdicts.json", {"verdicts": [v.to_dict() for v in verdicts]})

    def verdicts(self) -> list[Verdict]:
        if not self.has("verdicts.json"):
            return []
        return [Verdict.from_dict(v) for v in self._read("verdicts.json")["verdicts"]]

    def save_resolutions(self, resolutions: dict[str, Resolution]) -> Path:
        """Resolutions keyed by a fingerprint of the patch each one scored."""
        entries = [{"patch": key, **r.to_dict()} for key, r in resolutions.items()]
        return self._write("resolutions.json", {"resolutions": entries})

    def resolutions(self) -> dict[str, Resolution]:
        if not self.has("resolutions.json"):
            return {}
        return {
            entry["patch"]: Resolution.from_dict(entry)
            for entry in self._read("resolutions.json")["resolutions"]
        }
