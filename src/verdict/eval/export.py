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

from verdict.artifacts import read_artifact
from verdict.eval.store import ARM_NO_RETRIEVAL, ARM_WITH_RETRIEVAL, eval_path
from verdict.logging import get_logger
from verdict.mutation.store import run_path
from verdict.targets import list_targets

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


def _real_diff_payload() -> dict[str, Any] | None:
    """The real-diff study, if it has been run.

    Returned separately from the mutation cases because it answers a different
    question about a different kind of change, and merging the two would let a
    reader average numbers that are not comparable.
    """
    from verdict.artifacts import ArtifactError
    from verdict.review.store import load_audit, load_diffs, load_ground_truth, load_tasks

    try:
        tasks = {t.task_id: t for t in load_tasks()}
        diffs = {d.task_id: d for d in load_diffs()}
        truths = {g.task_id: g for g in load_ground_truth()}
    except ArtifactError:
        return None

    audit: dict[str, dict[str, Any]] = {}
    arm: str | None = None
    try:
        recorded = load_audit()
        audit = {o["task_id"]: o for o in recorded["outcomes"]}
        arm = recorded.get("arm")
    except ArtifactError:
        pass

    rows = []
    for task_id, task in tasks.items():
        if not task.oracle_valid:
            continue
        truth = truths.get(task_id)
        diff = diffs.get(task_id)
        outcome: dict[str, Any] = audit.get(task_id, {})
        rows.append(
            {
                "taskId": task_id,
                "target": task.target_slug,
                "reference": task.reference,
                "referenceKind": task.reference_kind,
                "title": task.title,
                "problemStatement": _trim(task.problem_statement, 1200),
                "baseCommit": task.base_commit[:10],
                "oracleTests": list(task.oracle_tests),
                "changedFiles": list(diff.changed_files) if diff else [],
                "patch": _trim(diff.patch, MAX_DIFF_CHARS) if diff else "",
                "oracle": truth.oracle if truth else None,
                "regression": truth.regression if truth else None,
                "resolved": bool(truth and truth.resolved),
                "auditVerdict": outcome.get("verdict"),
                "flagged": bool(outcome.get("flagged")),
                "hypothesis": outcome.get("hypothesis", ""),
                "testCode": _trim(outcome.get("test_code", ""), MAX_TEST_CHARS),
                "originalBehaviour": outcome.get("original_behaviour", ""),
                "changedBehaviour": outcome.get("changed_behaviour", ""),
                "citation": outcome.get("citation"),
                "retrieved": outcome.get("retrieved", []),
                "auditReason": outcome.get("reason", ""),
            }
        )
    return {"tasks": rows, "arm": arm} if rows else None


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

    bundle: dict[str, Any] = {"targets": targets, "cases": cases, "generator": "verdict"}
    if (study := _real_diff_payload()) is not None:
        bundle["realDiffs"] = study
    return bundle


def export(destination: Path) -> tuple[Path, int]:
    """Write the dashboard payload. Returns the path and case count."""
    payload = build_payload()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    log.info("Exported %d cases to %s", len(payload["cases"]), destination)
    return destination, len(payload["cases"])
