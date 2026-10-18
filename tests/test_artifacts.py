"""Tests for artifact freezing.

The build brief flagged one specific failure mode: build the RAG layer without
first saving the no-RAG baseline, and the ablation number cannot be computed
later without re-running everything. Freezing turns that from a thing to
remember into a property of the system, so it is worth testing carefully.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from verdict.artifacts import (
    ArtifactIntegrityError,
    FrozenArtifactError,
    content_hash,
    describe,
    is_frozen,
    payload_of,
    read_artifact,
    write_artifact,
)


@pytest.fixture
def artifact(tmp_path: Path) -> Path:
    return tmp_path / "baseline.json"


class TestRoundTrip:
    def test_writes_and_reads_back(self, artifact: Path) -> None:
        write_artifact(artifact, kind="eval_run", payload={"kill_rate": 0.617})
        assert payload_of(read_artifact(artifact))["kill_rate"] == 0.617

    def test_records_what_produced_it(self, artifact: Path) -> None:
        """Results are only interpretable if you know the environment that
        produced them."""
        write_artifact(artifact, kind="eval_run", payload={})
        env = read_artifact(artifact)["environment"]
        assert env["verdict"] and env["python"] and env["cosmic_ray"]

    def test_rejects_the_wrong_kind(self, artifact: Path) -> None:
        write_artifact(artifact, kind="mutation_run", payload={})
        with pytest.raises(Exception, match="expected 'eval_run'"):
            read_artifact(artifact, expect_kind="eval_run")

    def test_missing_artifact_is_reported_clearly(self, tmp_path: Path) -> None:
        with pytest.raises(Exception, match="No artifact at"):
            read_artifact(tmp_path / "absent.json")


class TestHashing:
    def test_hash_depends_only_on_content(self) -> None:
        """Key insertion order must not change the hash, or an unmodified
        artifact could be reported as tampered with."""
        assert content_hash({"a": 1, "b": 2}) == content_hash({"b": 2, "a": 1})

    def test_different_content_hashes_differently(self) -> None:
        assert content_hash({"kill_rate": 0.617}) != content_hash({"kill_rate": 0.893})


class TestFreezing:
    def test_unfrozen_artifacts_can_be_overwritten(self, artifact: Path) -> None:
        write_artifact(artifact, kind="eval_run", payload={"v": 1})
        write_artifact(artifact, kind="eval_run", payload={"v": 2})
        assert payload_of(read_artifact(artifact))["v"] == 2

    def test_freezing_writes_a_hash_sidecar(self, artifact: Path) -> None:
        digest = write_artifact(artifact, kind="eval_run", payload={"v": 1}, freeze=True)
        assert is_frozen(artifact)
        assert digest in artifact.with_suffix(".json.sha256").read_text()

    def test_a_frozen_artifact_refuses_to_be_overwritten(self, artifact: Path) -> None:
        """This is the whole point: once the no-RAG baseline is frozen, phase 3
        physically cannot clobber it by re-running a command."""
        write_artifact(artifact, kind="eval_run", payload={"kill_rate": 0.617}, freeze=True)
        with pytest.raises(FrozenArtifactError, match="frozen"):
            write_artifact(artifact, kind="eval_run", payload={"kill_rate": 0.893})
        assert payload_of(read_artifact(artifact))["kill_rate"] == 0.617

    def test_the_refusal_explains_the_escape_hatch(self, artifact: Path) -> None:
        write_artifact(artifact, kind="eval_run", payload={}, freeze=True)
        with pytest.raises(FrozenArtifactError, match="allow_refreeze"):
            write_artifact(artifact, kind="eval_run", payload={})

    def test_refreezing_is_possible_but_must_be_deliberate(self, artifact: Path) -> None:
        write_artifact(artifact, kind="eval_run", payload={"v": 1}, freeze=True)
        write_artifact(
            artifact, kind="eval_run", payload={"v": 2}, freeze=True, allow_refreeze=True
        )
        assert payload_of(read_artifact(artifact))["v"] == 2

    def test_tampering_with_a_frozen_artifact_is_detected(self, artifact: Path) -> None:
        """Editing the JSON by hand to improve a number must not go unnoticed."""
        write_artifact(artifact, kind="eval_run", payload={"kill_rate": 0.617}, freeze=True)
        document = json.loads(artifact.read_text())
        document["payload"]["kill_rate"] = 0.893
        artifact.write_text(json.dumps(document, indent=2))
        with pytest.raises(ArtifactIntegrityError, match="modified since it was frozen"):
            read_artifact(artifact)

    def test_verification_can_be_skipped_explicitly(self, artifact: Path) -> None:
        write_artifact(artifact, kind="eval_run", payload={"v": 1}, freeze=True)
        document = json.loads(artifact.read_text())
        document["payload"]["v"] = 2
        artifact.write_text(json.dumps(document))
        assert payload_of(read_artifact(artifact, verify=False))["v"] == 2


class TestDescribe:
    def test_marks_frozen_artifacts(self, artifact: Path) -> None:
        write_artifact(artifact, kind="eval_run", payload={}, freeze=True)
        assert "[frozen]" in describe(artifact)

    def test_omits_the_marker_when_not_frozen(self, artifact: Path) -> None:
        write_artifact(artifact, kind="eval_run", payload={})
        assert "[frozen]" not in describe(artifact)

    def test_reports_a_missing_artifact(self, tmp_path: Path) -> None:
        assert "missing" in describe(tmp_path / "absent.json")
