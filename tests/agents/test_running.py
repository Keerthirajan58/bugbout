"""Running an agent: what it leaves behind, how it ends, and that it never outlives a timeout."""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

from bugbout.agents import AgentRun, CommandAgent
from bugbout.agents.base import stop_all
from bugbout.sandbox import NoSandbox
from tests.processes import gone


def _agent(code: str) -> CommandAgent:
    return CommandAgent([sys.executable, "-c", code], name="script", sandboxed=False)


def _run(agent: CommandAgent, tmp_path: Path, timeout: int = 60) -> AgentRun:
    (tmp_path / "repo").mkdir(exist_ok=True)
    return agent.run(
        "Fix it.", tmp_path / "repo", tmp_path / "logs", sandbox=NoSandbox(), timeout=timeout
    )


def test_an_agent_edits_its_checkout_and_finishes(tmp_path: Path) -> None:
    agent = _agent("import pathlib, sys; pathlib.Path('fix.txt').write_text(sys.stdin.read())")
    run = _run(agent, tmp_path)
    assert run.ok and run.exit_code == 0 and run.agent == "script"
    assert (tmp_path / "repo" / "fix.txt").read_text() == "Fix it."


def test_a_failing_agent_is_recorded_with_its_exit_code(tmp_path: Path) -> None:
    run = _run(_agent("import sys; print('boom', file=sys.stderr); sys.exit(3)"), tmp_path)
    assert not run.ok and run.error == "exit code 3"
    assert (tmp_path / "logs" / "stderr.log").read_text().strip() == "boom"


def test_an_agent_that_is_not_installed_is_an_error_not_a_crash(tmp_path: Path) -> None:
    agent = CommandAgent(["/nonexistent/agent"], name="ghost", sandboxed=False)
    run = _run(agent, tmp_path)
    assert run.error.startswith("could not start") and run.exit_code is None


def test_a_timeout_takes_down_everything_the_agent_started(tmp_path: Path) -> None:
    spawner = (
        "import pathlib, subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "pathlib.Path('child.pid').write_text(str(child.pid))\n"
        "time.sleep(60)\n"
    )
    run = _run(_agent(spawner), tmp_path, timeout=3)
    assert run.timed_out and run.seconds < 30
    child = int((tmp_path / "repo" / "child.pid").read_text())
    assert gone(child)


def test_the_run_record_round_trips() -> None:
    run = AgentRun("a", 0, False, 1.5, "done", {"tokens": 3})
    assert AgentRun.from_dict(run.to_dict()) == run


def test_stop_all_ends_an_agent_that_is_still_running(tmp_path: Path) -> None:
    runs: list[AgentRun] = []
    worker = threading.Thread(
        target=lambda: runs.append(_run(_agent("import time; time.sleep(60)"), tmp_path))
    )
    worker.start()
    deadline = time.monotonic() + 10
    while worker.is_alive() and time.monotonic() < deadline:
        stop_all()
        worker.join(timeout=0.2)
    assert not worker.is_alive()
    assert runs[0].exit_code is not None and not runs[0].ok
