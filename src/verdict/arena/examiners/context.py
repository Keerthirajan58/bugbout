"""The code a prompted examiner is shown: the base version of what contestants touched.

Which files matter is something the arena legitimately knows - the contestants'
patches name them - but the examiner sees only those files as they were
*before* any patch. A file goes in whole when it fits the budget; otherwise
its imports plus every top-level definition a patch reached into. Files are
ordered by how many contestants touched them, so the shared hotspot is never
the one trimmed.
"""

from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path

from verdict.arena.layout import ArenaLayout
from verdict.arena.patch import Patch
from verdict.review.tasks import IssueTask


class SourceContext:
    def __init__(self, layout: ArenaLayout, patches: dict[str, dict[str, Patch]]) -> None:
        """``patches``: task id -> entrant -> that entrant's patch."""
        self.layout = layout
        self.patches = patches

    def __call__(self, task: IssueTask, budget: int) -> str:
        test_root = self.layout.bench(task.target_slug).target.test_root
        touched: dict[str, set[int]] = {}
        counts: Counter[str] = Counter()
        for patch in self.patches.get(task.task_id, {}).values():
            for rel, lines in patch.only(under=test_root, keep=False).touched_lines().items():
                touched.setdefault(rel, set()).update(lines)
                counts[rel] += 1
        files = [rel for rel, _ in counts.most_common()]
        return render(self.layout.pristine(task), files, touched, budget)


def render(base: Path, files: list[str], touched: dict[str, set[int]], budget: int) -> str:
    sections: list[str] = []
    remaining = budget
    for rel in files:
        path = base / rel
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if len(text) > remaining:
            text = excerpt(text, touched.get(rel, set()))
        text = text[:remaining]
        if not text:
            break
        sections.append(f"\n--- {rel} ---\n{text}")
        remaining -= len(text)
    return "".join(sections) or "\n(no source context available)"


def excerpt(source: str, lines: set[int]) -> str:
    """Imports plus every top-level definition that spans one of ``lines``."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return source
    keep = [
        (node.lineno, node.end_lineno or node.lineno)
        for node in tree.body
        if isinstance(node, ast.Import | ast.ImportFrom)
        or any(node.lineno <= n <= (node.end_lineno or node.lineno) for n in lines)
    ]
    rows = source.splitlines()
    out: list[str] = []
    last = 0
    for start, end in keep:
        if start > last + 1:
            out.append("# ...")
        out.extend(rows[start - 1 : end])
        last = end
    return "\n".join(out) + "\n"
