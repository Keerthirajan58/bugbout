"""macOS seatbelt profiles for agents whose CLI does not sandbox itself.

A profile is the whole policy, so it is kept small and readable: everything is
allowed except writing outside the attempt's own directories, and - for
commands an agent runs - any network at all. The model calls an agent makes
happen in the agent's own process, outside the sandbox; only the shell
commands it issues run inside it.
"""

from __future__ import annotations

from pathlib import Path

SANDBOX_EXEC = "/usr/bin/sandbox-exec"

_PROFILE = """\
(version 1)
(allow default)
(deny file-write*)
(allow file-write*
{writable}
    (literal "/dev/null")
    (literal "/dev/tty")
    (regex #"^/dev/fd/"))
(deny network*)
(allow network* (local unix-socket))
"""


def command_profile(*writable: Path) -> str:
    """No network, and writes only beneath ``writable`` and the system temp dirs."""
    roots = [*writable, Path("/private/tmp"), Path("/private/var/folders")]
    lines = "\n".join(f'    (subpath "{root.resolve()}")' for root in roots)
    return _PROFILE.format(writable=lines)
