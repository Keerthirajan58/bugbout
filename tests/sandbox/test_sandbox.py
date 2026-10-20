"""Sandboxes let a process read anything, but write and connect only where allowed."""

from __future__ import annotations

import os
import platform
import subprocess
import sys
from pathlib import Path

import pytest

from bugbout.errors import ConfigError, SandboxUnavailableError
from bugbout.sandbox import (
    Bubblewrap,
    Confinement,
    NoSandbox,
    Sandbox,
    Seatbelt,
    detect,
    get_sandbox,
)
from bugbout.sandbox.bubblewrap import explain
from bugbout.sandbox.seatbelt import profile

NATIVE: list[type[Sandbox]] = [b for b in (Seatbelt, Bubblewrap) if b.available()]
WRITE = "import pathlib, sys; pathlib.Path(sys.argv[1]).write_text('x')"
CONNECT = "import socket; socket.create_connection(('1.1.1.1', 53), timeout=3)"


def _option(argv: list[str], flag: str) -> list[str]:
    """A two-argument option with its arguments."""
    start = argv.index(flag)
    return argv[start : start + 3]


def _run(sandbox: Sandbox, confinement: Confinement, code: str, *args: str) -> int:
    argv = sandbox.wrap([sys.executable, "-c", code, *args], confinement)
    return subprocess.run(argv, capture_output=True, timeout=60, check=False).returncode


@pytest.mark.skipif(not NATIVE, reason="no native sandbox on this machine")
class TestNativeSandbox:
    @pytest.fixture(params=NATIVE, ids=lambda backend: backend.name)
    def sandbox(self, request: pytest.FixtureRequest) -> Sandbox:
        backend: type[Sandbox] = request.param
        return backend()

    def test_writes_inside_the_writable_directories_succeed(
        self, sandbox: Sandbox, tmp_path: Path
    ) -> None:
        allowed = tmp_path / "allowed"
        allowed.mkdir()
        confinement = Confinement(writable=(allowed,))
        assert _run(sandbox, confinement, WRITE, str(allowed / "file")) == 0
        assert (allowed / "file").read_text() == "x"

    def test_writes_anywhere_else_are_refused(self, sandbox: Sandbox) -> None:
        # The repository itself is never writable, and it is not a temporary directory.
        outside = Path(__file__).resolve().parent / "must-not-exist"
        assert _run(sandbox, Confinement(), WRITE, str(outside)) != 0
        assert not outside.exists()

    def test_the_network_is_cut_off_unless_allowed(self, sandbox: Sandbox) -> None:
        assert _run(sandbox, Confinement(network=False), CONNECT) != 0


class TestProfiles:
    def test_the_seatbelt_profile_lists_writable_roots_and_denies_the_network(
        self, tmp_path: Path
    ) -> None:
        text = profile(Confinement(writable=(tmp_path,)))
        assert f'(subpath "{tmp_path.resolve()}")' in text
        assert "(deny network*)" in text
        assert "(deny network*)" not in profile(Confinement(network=True))

    def test_quotes_in_a_path_cannot_break_out_of_the_profile(self, tmp_path: Path) -> None:
        awkward = tmp_path / 'a"b'
        escaped = str(awkward.resolve()).replace('"', '\\"')
        assert f'(subpath "{escaped}")' in profile(Confinement(writable=(awkward,)))

    def test_bubblewrap_mounts_writable_roots_and_unshares_the_network(
        self, tmp_path: Path
    ) -> None:
        argv = Bubblewrap().prefix(Confinement(writable=(tmp_path,)))
        root = str(tmp_path.resolve())
        assert _option(argv, "--ro-bind") == ["--ro-bind", "/", "/"]
        assert _option(argv, "--bind") == ["--bind", root, root]
        assert "--unshare-net" in argv and argv[-1] == "--"
        assert "--unshare-net" not in Bubblewrap().prefix(Confinement(network=True))


class TestSelection:
    def test_none_runs_commands_as_they_are(self) -> None:
        assert get_sandbox("none").wrap(["echo"], Confinement()) == ["echo"]

    def test_an_unknown_name_is_a_configuration_error(self) -> None:
        with pytest.raises(ConfigError, match="unknown sandbox"):
            get_sandbox("docker")

    @pytest.mark.skipif(not NATIVE, reason="no native sandbox on this machine")
    def test_auto_picks_the_platform_s_sandbox(self) -> None:
        assert not isinstance(detect(), NoSandbox)


class TestProblems:
    def test_a_backend_for_another_platform_says_so(self) -> None:
        other = Bubblewrap if platform.system() == "Darwin" else Seatbelt
        assert other.problem() in {"bubblewrap only runs on Linux", "seatbelt only runs on macOS"}

    def test_a_permission_error_points_at_apparmor(self) -> None:
        reason = explain("bwrap: setting up uid map: Permission denied")
        assert reason.startswith("bubblewrap cannot create a sandbox: bwrap: setting up uid map")
        assert "apparmor_restrict_unprivileged_userns" in reason

    def test_an_unavailable_sandbox_explains_itself(self) -> None:
        name = "seatbelt" if platform.system() != "Darwin" else "bubblewrap"
        with pytest.raises(SandboxUnavailableError, match="only runs on"):
            get_sandbox(name)


@pytest.mark.skipif(
    not os.environ.get("BUGBOUT_REQUIRE_NATIVE_SANDBOX"), reason="only where a sandbox is required"
)
def test_this_machine_has_a_working_native_sandbox() -> None:
    """CI sets the variable, so the real sandbox tests can never be skipped there unnoticed."""
    native = Seatbelt if platform.system() == "Darwin" else Bubblewrap
    assert native.problem() is None, native.problem()
