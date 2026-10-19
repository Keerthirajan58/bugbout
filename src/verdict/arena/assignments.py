"""What an agent is asked to do with a task.

The same coding agent plays two roles in the arena, and only the brief
differs. As a **contestant** it fixes the issue and adds a regression test. As
an **examiner** it must not touch the source at all: it writes a test that
reproduces the issue and runs it to confirm the test fails on the unfixed code
for the reason the issue describes. An examiner never sees a contestant's
patch - its test is evidence about the requirement, not about anyone's fix.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

REGRESSION_TEST = "test_issue_regression.py"
"""Where a contestant is asked to put the test for its own fix."""

REPRODUCTION_TEST = "test_issue_reproduction.py"
"""Where an examiner is asked to put its reproduction of the issue."""

SPECIFICATION_TEST = "test_issue_specification.py"
"""Where a specifying examiner is asked to put the tests it would demand of a fix."""


@dataclass(frozen=True, slots=True)
class Brief:
    """Everything an assignment's prompt is rendered from."""

    repo: str
    title: str
    statement: str
    source_root: str
    test_root: str
    runner: Path


class Assignment(ABC):
    """A role an agent can be given, expressed as the prompt it receives."""

    name: str
    test_file: str

    def prompt(self, brief: Brief) -> str:
        return self.template.format(
            repo=brief.repo,
            title=brief.title,
            statement=brief.statement.strip(),
            source_root=brief.source_root,
            test_root=brief.test_root,
            test_file=self.test_file,
            runner=brief.runner,
        )

    @property
    @abstractmethod
    def template(self) -> str: ...


class Fix(Assignment):
    """Contestant: resolve the issue, and prove it with a test."""

    name = "fix"
    test_file = REGRESSION_TEST

    @property
    def template(self) -> str:
        return """\
You are resolving an issue in the {repo} repository. Your working directory is a checkout of \
the repository at a commit where the issue below is still open. There is no git history.

<issue>
# {title}

{statement}
</issue>

Do the following:
1. Fix the issue by editing the library's source code (under `{source_root}/`).
2. Write a regression test for this issue in a NEW file, `{test_root}/{test_file}`. \
It must fail on the code as it is now and pass once your fix is in place. Do not modify or \
delete existing tests.
3. Run the tests to check your work with this exact command, which points Python at this \
checkout:
   {runner} {test_root}/{test_file}
   Pass any other test file, or a -k expression, the same way.

Work only inside this directory, and do not download the project or look it up online. When \
you are finished, reply with a short summary of what you changed and why.
"""


class Reproduce(Assignment):
    """Examiner: write a failing test that captures the issue, and fix nothing."""

    name = "reproduce"
    test_file = REPRODUCTION_TEST

    @property
    def template(self) -> str:
        return """\
You are writing the regression test for an issue in the {repo} repository, BEFORE anyone has \
fixed it. Your working directory is a checkout of the repository at a commit where the issue \
below is still open. There is no git history.

<issue>
# {title}

{statement}
</issue>

Your job is to capture the issue as a test - not to fix it:
1. Do NOT modify the library's source code (under `{source_root}/`) or any existing test.
2. Write pytest tests in a NEW file, `{test_root}/{test_file}`, that exercise exactly the \
behaviour the issue asks for, through the library's public API. Cover each concrete case the \
issue mentions or clearly implies. Do not test anything the issue does not ask to change.
3. Run them with this exact command, which points Python at this checkout:
   {runner} {test_root}/{test_file}
   They MUST fail on the current code, and each failure must be caused by the bug the issue \
describes - not by a typo, a wrong import, a misremembered API or a mistaken expectation. Read \
the source until you are sure what correct behaviour is, then make each test assert exactly that.

Work only inside this directory, and do not download the project or look it up online. When \
you are finished, reply with a short summary of what each test checks.
"""


class Specify(Assignment):
    """Examiner: write the tests a maintainer would demand of any fix - and fix nothing.

    A reproduction checks the case the issue reports, and a partial fix passes
    it. On the development suite most wrong patches were exactly that: right on
    the reported example, wrong on the general rule the maintainers' own tests
    checked. This brief asks for the rule, not just the example.
    """

    name = "specify"
    test_file = SPECIFICATION_TEST

    @property
    def template(self) -> str:
        return """\
You are the maintainer of the {repo} repository who will review fixes for the issue below. \
Before anyone has fixed it, write the regression tests you would require a correct fix to pass. \
Your working directory is a checkout at a commit where the issue is still open. There is no git \
history.

<issue>
# {title}

{statement}
</issue>

1. Do NOT modify the library's source code (under `{source_root}/`) or any existing test.
2. Read the relevant source and the existing tests until you understand the behaviour the \
library intends. Work out the general rule the issue implies - not just its literal example.
3. Write pytest tests in a NEW file, `{test_root}/{test_file}`, in three groups:
   a. the case the issue reports;
   b. the same rule on closely related inputs a complete fix must also handle - other types \
or values the same code path accepts, boundaries, the sibling branch of the same logic;
   c. nearby behaviour the issue does not ask to change, which a careless fix might break.
4. Run them with this exact command, which points Python at this checkout:
   {runner} {test_root}/{test_file}
   Every test in groups a and b MUST fail on the current code, because of the bug. If a related \
input already works, it is not part of the bug: move it to group c or drop it. Every test in \
group c MUST pass on the current code. Each failure must come from the bug - never from a typo, \
a wrong import, a misremembered API or a mistaken expectation. Check each expectation against \
the source and the existing tests before you keep it.

Work only inside this directory, and do not download the project or look it up online. When \
you are finished, reply with a short summary of the rule you tested and why.
"""


ASSIGNMENTS: dict[str, Assignment] = {a.name: a for a in (Fix(), Reproduce(), Specify())}
