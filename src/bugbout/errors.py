"""Every error bugbout raises on purpose derives from :class:`BugboutError`.

Callers can catch the base class to handle anything the tool reports, and the
CLI turns these into a one-line message instead of a traceback.
"""

from __future__ import annotations


class BugboutError(Exception):
    """Base class for errors bugbout raises deliberately."""


class ConfigError(BugboutError):
    """The project configuration is missing, malformed or inconsistent."""


class RepositoryError(BugboutError):
    """A git operation on the project repository failed."""


class EnvironmentSetupError(BugboutError):
    """The test environment for the project could not be built."""


class AgentNotAvailableError(BugboutError):
    """An agent's command-line tool is not installed or not configured."""


class SandboxUnavailableError(BugboutError):
    """No sandbox backend is available on this platform."""


class PatchError(BugboutError):
    """A patch does not apply cleanly to the tree it was given."""


class ArtifactError(BugboutError):
    """A stored artifact is missing or is not of the expected kind."""
