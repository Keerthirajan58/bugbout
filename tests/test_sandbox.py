"""Tests for the static screen on generated test code.

The Prosecutor is scored on producing a test that passes before a change and
fails after it. A test that reads the *source text* satisfies that rule
perfectly while proving nothing about behaviour. Nobody has to intend the cheat
for a model to find it, so these guards are load-bearing for the integrity of
the headline number rather than merely defensive.
"""

from __future__ import annotations

import pytest

from precedent.sandbox.guards import inspect_test_code

LEGITIMATE = """
def test_set_value_nested():
    from marshmallow.utils import set_value
    d = {}
    set_value(d, "a.b.c", 1)
    assert d == {"a": {"b": {"c": 1}}}
"""


class TestLegitimateTests:
    def test_a_behavioural_test_is_allowed(self) -> None:
        assert inspect_test_code(LEGITIMATE).allowed

    def test_os_path_is_still_allowed(self) -> None:
        """`os` is not banned outright - tests legitimately use os.path."""
        code = "import os\ndef test_x():\n    assert os.path.join('a', 'b') == 'a/b'\n"
        assert inspect_test_code(code).allowed

    def test_pytest_raises_is_allowed(self) -> None:
        code = (
            "import pytest\n"
            "def test_x():\n"
            "    with pytest.raises(ValueError):\n"
            "        raise ValueError('x')\n"
        )
        assert inspect_test_code(code).allowed


class TestSourceInspectionIsBlocked:
    def test_inspect_getsource_is_rejected(self) -> None:
        code = (
            "import inspect\n"
            "def test_x():\n"
            "    from marshmallow import utils\n"
            "    assert 'split' in inspect.getsource(utils.set_value)\n"
        )
        verdict = inspect_test_code(code)
        assert not verdict.allowed
        assert "inspect" in verdict.reason

    def test_from_inspect_import_is_rejected(self) -> None:
        code = "from inspect import getsource\ndef test_x():\n    assert getsource\n"
        assert not inspect_test_code(code).allowed

    def test_reading_dunder_code_is_rejected(self) -> None:
        code = "def test_x():\n    from marshmallow.utils import set_value\n    assert set_value.__code__\n"
        verdict = inspect_test_code(code)
        assert not verdict.allowed
        assert "bytecode" in verdict.reason or "source" in verdict.reason

    def test_dis_is_rejected(self) -> None:
        code = "import dis\ndef test_x():\n    assert dis\n"
        assert not inspect_test_code(code).allowed


class TestSandboxEscapesAreBlocked:
    @pytest.mark.parametrize("module", ["subprocess", "socket", "httpx", "requests", "ctypes"])
    def test_escape_modules_are_rejected(self, module: str) -> None:
        code = f"import {module}\ndef test_x():\n    assert True\n"
        assert not inspect_test_code(code).allowed

    @pytest.mark.parametrize("call", ["eval", "exec", "open", "compile", "__import__"])
    def test_dynamic_execution_and_file_access_are_rejected(self, call: str) -> None:
        code = f"def test_x():\n    {call}('x')\n"
        assert not inspect_test_code(code).allowed

    def test_os_system_is_rejected(self) -> None:
        code = "import os\ndef test_x():\n    os.system('ls')\n"
        assert not inspect_test_code(code).allowed

    def test_os_remove_is_rejected(self) -> None:
        code = "import os\ndef test_x():\n    os.remove('/tmp/x')\n"
        assert not inspect_test_code(code).allowed


class TestStructuralRequirements:
    def test_code_without_a_test_function_is_rejected(self) -> None:
        """pytest would collect nothing and report success, which the harness
        would otherwise read as 'the test passed'."""
        verdict = inspect_test_code("x = 1\n")
        assert not verdict.allowed
        assert "no test function" in verdict.reason

    def test_syntactically_invalid_code_is_rejected_with_the_line(self) -> None:
        verdict = inspect_test_code("def test_x(:\n    pass\n")
        assert not verdict.allowed
        assert "not valid Python" in verdict.reason

    def test_reasons_are_deduplicated(self) -> None:
        code = "import inspect\nimport inspect as i2\ndef test_x():\n    assert True\n"
        reasons = inspect_test_code(code).reasons
        assert len(reasons) == len(set(reasons))
