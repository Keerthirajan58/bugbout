"""JSON files written atomically, so a crash never leaves half a file behind."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from bugbout.errors import ArtifactError


def write_json(path: Path, data: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            json.dump(data, out, indent=2, ensure_ascii=False)
            out.write("\n")
        Path(temporary).replace(path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return path


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ArtifactError(f"{path} does not exist") from exc
    except json.JSONDecodeError as exc:
        raise ArtifactError(f"{path} is not valid JSON: {exc}") from exc
