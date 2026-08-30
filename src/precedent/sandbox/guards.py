"""Static checks on generated test code.

The Prosecutor is scored on whether its test passes on the original code and
fails on the changed code. That scoring rule has an exploit: a test that reads
the *source text* rather than exercising behaviour satisfies it perfectly.

    def test_regression():
        import inspect, marshmallow.utils
        assert '.split(".", 1)' in inspect.getsource(marshmallow.utils.set_value)

That passes before the change and fails after it, so it would be scored as a
confirmed catch - while demonstrating nothing about behaviour at all. Nobody has
to intend the cheat for it to happen; it is simply an easy way to satisfy the
instruction, and a model under pressure will find it.

These guards reject such tests before they are ever executed. They also block
the obvious ways generated code could reach outside the sandbox. Every rejection
is reported with a reason, and a rejected candidate is scored as a failure to
catch the regression - never quietly retried into a success.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass

# Reading a function's own source is not a behavioural test.
_FORBIDDEN_ATTRIBUTES = {
    "getsource",
    "getsourcelines",
    "getsourcefile",
    "__code__",
    "co_code",
    "co_consts",
    "__wrapped__",
}
# Modules that let a test inspect the program instead of running it, or that let
# it escape the sandbox entirely.
_FORBIDDEN_MODULES = {
    "inspect",
    "dis",
    "subprocess",
    "socket",
    "shutil",
    "urllib",
    "urllib.request",
    "http",
    "http.client",
    "requests",
    "httpx",
    "ctypes",
    "multiprocessing",
    "importlib.util",
}
_FORBIDDEN_CALLS = {"eval", "exec", "compile", "__import__", "open", "input", "breakpoint"}
# `os` is allowed (tests legitimately use os.path), but not these members.
_FORBIDDEN_OS_MEMBERS = {"system", "popen", "remove", "unlink", "rmdir", "removedirs", "execv"}


@dataclass(frozen=True, slots=True)
class GuardVerdict:
    """Whether generated code may be executed, and why not if it may not."""

    allowed: bool
    reasons: tuple[str, ...] = ()

    @property
    def reason(self) -> str:
        return "; ".join(self.reasons)


def inspect_test_code(code: str) -> GuardVerdict:
    """Decide whether a generated test is safe and legitimate to run."""
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return GuardVerdict(
            False, (f"generated test is not valid Python: {exc.msg} (line {exc.lineno})",)
        )

    reasons: list[str] = []

    for node in ast.walk(tree):
        # import inspect / from inspect import getsource
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _root(alias.name) in _FORBIDDEN_MODULES or alias.name in _FORBIDDEN_MODULES:
                    reasons.append(f"imports forbidden module {alias.name!r}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if _root(module) in _FORBIDDEN_MODULES or module in _FORBIDDEN_MODULES:
                reasons.append(f"imports from forbidden module {module!r}")

        # inspect.getsource(...), fn.__code__, os.system(...)
        elif isinstance(node, ast.Attribute):
            if node.attr in _FORBIDDEN_ATTRIBUTES:
                reasons.append(f"reads source or bytecode via .{node.attr}")
            elif node.attr in _FORBIDDEN_OS_MEMBERS and _is_name(node.value, "os"):
                reasons.append(f"calls os.{node.attr}")

        # eval(...), open(...), exec(...)
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in _FORBIDDEN_CALLS
        ):
            reasons.append(f"calls {node.func.id}()")

    if not _has_test_function(tree):
        reasons.append("defines no test function (pytest collects names starting with 'test_')")

    # Deduplicate while preserving order, so a repeated violation reads once.
    unique = tuple(dict.fromkeys(reasons))
    return GuardVerdict(not unique, unique)


def _root(module: str) -> str:
    return module.split(".", 1)[0]


def _is_name(node: ast.expr, name: str) -> bool:
    return isinstance(node, ast.Name) and node.id == name


def _has_test_function(tree: ast.Module) -> bool:
    return any(
        isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name.startswith("test_")
        for node in ast.walk(tree)
    )
