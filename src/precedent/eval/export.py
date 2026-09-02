"""Export evaluation results for the dashboard.

The dashboard is a static site. It reads a single generated JSON file rather
than calling the pipeline, so a deployed page has no API keys, no database and
no cold-start - and it still shows exactly what happened, because every case
record already carries the diff, the retrieved context, the debate and the
verdict.

Only what the UI renders is exported. The full artifacts stay in
``data/artifacts`` as the evidence; this is a projection of them.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from precedent.artifacts import read_artifact
from precedent.eval.store import ARM_NO_RETRIEVAL, ARM_WITH_RETRIEVAL, eval_path
from precedent.logging import get_logger
from precedent.mutation.store import run_path
from precedent.targets import list_targets

log = get_logger(__name__)

# Long diffs and test bodies are the point of the case view, but a whole corpus
# of them would bloat the bundle. These caps keep the payload servable while
# leaving every case fully readable.
MAX_DIFF_CHARS = 4000
MAX_TEST_CHARS = 4000


def _trim(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "\n... (truncated)"


def _case_payload(case: dict[str, Any], arm: str) -> dict[str, Any]:
    return {
        "mutantId": case["mutant_id"],
        "arm": arm,
        "target": case["target_slug"],
        "modulePath": case["module_path"],
        "function": case.get("function"),
        "operator": case["operator"].split("/")[-1],
        "family": case["family"],
        "diff": _trim(case.get("diff", ""), MAX_DIFF_CHARS),
        "verdict": case["verdict"],
        "caught": case["caught"],
        "reason": case.get("reason", ""),
        "hypothesis": case.get("hypothesis", ""),
        "reasoning": case.get("reasoning", ""),
        "testName": case.get("test_name", ""),
        "testCode": _trim(case.get("test_code", ""), MAX_TEST_CHARS),
        "confident": case.get("confident", False),
        "repairs": case.get("repairs", 0),
        # The grounding gate's own record. `gateFailures` is how many candidate
        # tests were rejected for failing against unmutated code, and the two
        # behaviour fields are the Prosecutor's stated prediction for each side
        # of the diff - which is what makes a direction inversion visible in the
        # UI rather than something a reader has to infer from a test body.
        "gateFailures": case.get("gate_failures", 0),
        "originalBehaviour": case.get("original_behaviour", ""),
        "changedBehaviour": case.get("changed_behaviour", ""),
        "model": case.get("model", ""),
        "reviewReason": case.get("review_reason"),
        "citation": case.get("citation"),
        "retrieved": case.get("retrieved", []),
    }


def build_payload() -> dict[str, Any]:
    """Assemble everything the dashboard needs, across targets and arms."""
    targets: list[dict[str, Any]] = []
    cases: list[dict[str, Any]] = []

    for target in list_targets():
        entry: dict[str, Any] = {
            "slug": target.slug,
            "fullName": target.full_name,
            "ref": target.ref,
            "commit": target.commit,
            "license": target.spdx_license,
            "rationale": target.selection_rationale,
            "arms": {},
        }
        mutation = run_path(target.slug)
        if mutation.exists():
            payload = read_artifact(mutation)["payload"]
            entry["mutation"] = {
                "total": payload["stats"]["total"],
                "killRate": payload["stats"]["kill_rate"],
                "survivors": payload["stats"]["survived"],
                "operators": payload["operators"]["count"],
            }

        for arm in (ARM_NO_RETRIEVAL, ARM_WITH_RETRIEVAL):
            path = eval_path(target.slug, arm)
            if not path.exists():
                continue
            payload = read_artifact(path, expect_kind="eval_run")["payload"]
            stats = payload["stats"]
            entry["arms"][arm] = {
                "total": stats["total"],
                "caught": stats["caught"],
                "catchRate": stats["catch_rate"],
                "executionConfirmed": stats.get("execution_confirmed"),
                "executionRate": stats.get("execution_rate"),
                "byVerdict": stats["by_verdict"],
                "model": payload.get("model", ""),
                "corpusHash": payload["corpus"]["mutation_run_hash"],
            }
            cases.extend(_case_payload(case, arm) for case in payload["cases"])

        if entry["arms"] or "mutation" in entry:
            targets.append(entry)

    return {"targets": targets, "cases": cases, "generator": "precedent"}


def export(destination: Path) -> tuple[Path, int]:
    """Write the dashboard payload. Returns the path and case count."""
    payload = build_payload()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    log.info("Exported %d cases to %s", len(payload["cases"]), destination)
    return destination, len(payload["cases"])
