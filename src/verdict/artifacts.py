"""Durable, integrity-checked run artifacts.

Verdict's headline claim is a comparison between two evaluation runs made days
apart. That comparison is only honest if the earlier run cannot be quietly
altered, overwritten, or silently regenerated with different settings once the
later run's numbers are known.

So artifacts support **freezing**. A frozen artifact is written alongside a
``.sha256`` sidecar recording its content hash. From then on:

* reading it verifies the hash and raises if the bytes have changed;
* writing to the same path is **refused** rather than allowed to clobber it.

This is what turns "remember to save the no-RAG baseline before building the RAG
layer" from a discipline into a property of the system. By the time phase 3
exists, the phase 2 baseline is physically write-protected, and the ablation
delta cannot be invalidated by accident.
"""

from __future__ import annotations

import hashlib
import json
import platform
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from verdict import __version__

SCHEMA_VERSION = 1


class ArtifactError(RuntimeError):
    """Base class for artifact problems."""


class FrozenArtifactError(ArtifactError):
    """Raised on an attempt to overwrite a frozen artifact."""


class ArtifactIntegrityError(ArtifactError):
    """Raised when a frozen artifact's contents no longer match its hash."""


def _canonical_bytes(payload: dict[str, Any]) -> bytes:
    """Serialise deterministically, so the hash depends on content alone.

    Sorted keys and a fixed separator mean the same data always produces the
    same bytes, regardless of dict insertion order or Python version.
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()


def content_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_bytes(payload)).hexdigest()


def _sidecar(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".sha256")


def is_frozen(path: Path) -> bool:
    return _sidecar(path).exists()


def environment_fingerprint() -> dict[str, str]:
    """What produced this artifact, recorded so results stay interpretable."""
    try:
        from importlib.metadata import version

        cosmic_ray_version = version("cosmic-ray")
    except Exception:  # pragma: no cover - only when cosmic-ray is absent
        cosmic_ray_version = "unknown"
    return {
        "verdict": __version__,
        "python": platform.python_version(),
        "platform": f"{platform.system()}-{platform.machine()}",
        "cosmic_ray": cosmic_ray_version,
    }


def write_artifact(
    path: Path,
    *,
    kind: str,
    payload: dict[str, Any],
    freeze: bool = False,
    allow_refreeze: bool = False,
) -> str:
    """Write an artifact, optionally freezing it. Returns the content hash.

    Args:
        path: Destination ``.json`` file.
        kind: Discriminator, e.g. ``"mutation_run"``.
        payload: The artifact body.
        freeze: Write a hash sidecar and protect the file from overwriting.
        allow_refreeze: Explicitly permit replacing an existing frozen artifact.
            Requiring this to be passed deliberately means a re-freeze is always
            a conscious act, never a side effect of re-running a command.
    """
    if is_frozen(path) and not allow_refreeze:
        raise FrozenArtifactError(
            f"{path} is frozen and will not be overwritten.\n"
            f"  This artifact is evidence for a published comparison. Regenerating it "
            f"would invalidate any result computed against it.\n"
            f"  To replace it deliberately, pass allow_refreeze=True (CLI: --allow-refreeze)."
        )

    document = {
        "schema_version": SCHEMA_VERSION,
        "kind": kind,
        "created_at": datetime.now(UTC).isoformat(),
        "environment": environment_fingerprint(),
        "payload": payload,
    }
    digest = content_hash(document)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2, sort_keys=True, default=str), encoding="utf-8")

    if freeze:
        _sidecar(path).write_text(f"{digest}  {path.name}\n", encoding="utf-8")
    return digest


def read_artifact(
    path: Path,
    *,
    expect_kind: str | None = None,
    verify: bool = True,
) -> dict[str, Any]:
    """Read an artifact, verifying its hash when frozen."""
    if not path.exists():
        raise ArtifactError(f"No artifact at {path}")
    document: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))

    if expect_kind and document.get("kind") != expect_kind:
        raise ArtifactError(
            f"{path} is a {document.get('kind')!r} artifact, expected {expect_kind!r}"
        )

    if verify and is_frozen(path):
        recorded = _sidecar(path).read_text(encoding="utf-8").split()[0]
        actual = content_hash(document)
        if recorded != actual:
            raise ArtifactIntegrityError(
                f"{path} has been modified since it was frozen.\n"
                f"  recorded {recorded}\n  actual   {actual}\n"
                f"  Any comparison against this artifact is no longer trustworthy."
            )
    return document


def payload_of(document: dict[str, Any]) -> dict[str, Any]:
    """The body of an artifact document."""
    body: dict[str, Any] = document["payload"]
    return body


def describe(path: Path) -> str:
    """One-line human summary of an artifact on disk."""
    if not path.exists():
        return f"{path.name}: missing"
    document = json.loads(path.read_text(encoding="utf-8"))
    lock = " [frozen]" if is_frozen(path) else ""
    return (
        f"{path.name}: {document.get('kind')} created {document.get('created_at', '?')[:19]}{lock}"
    )
