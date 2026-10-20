"""What a bout found: the numbers, and three ways to show them."""

from bugbout.report.build import Results, build_report
from bugbout.report.markdown import render_markdown
from bugbout.report.model import AgentResult, EvidenceSource, Gate, Report, Selection, TaskResult
from bugbout.report.terminal import render_terminal

__all__ = [
    "AgentResult",
    "EvidenceSource",
    "Gate",
    "Report",
    "Results",
    "Selection",
    "TaskResult",
    "build_report",
    "render_markdown",
    "render_terminal",
]
