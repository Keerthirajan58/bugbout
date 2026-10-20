"""Tasks are reconstructed from the fixes that landed on the mainline."""

from __future__ import annotations

from pathlib import Path

from bugbout.tasks.issues import Issue
from bugbout.tasks.mining import Miner, MiningReport, MiningRules
from bugbout.tasks.task import Task
from bugbout.workspace.repository import Repository
from tests.toy import ISSUE, GitRepo, StaticIssues, Toy

RULES = MiningRules(source_root="src/toy", test_root="tests")
REPORT = "Calling the function with an empty list raises IndexError instead of returning 0."


def _mine(
    path: Path, *issues: Issue, rules: MiningRules = RULES
) -> tuple[list[Task], MiningReport]:
    return Miner(Repository.open(path), StaticIssues(*issues), rules).mine()


def _project(tmp_path: Path) -> GitRepo:
    repo = GitRepo(tmp_path / "repo")
    repo.write({"src/toy/core.py": "def f(xs):\n    return xs[0]\n", "tests/test_core.py": ""})
    repo.commit("Initial commit")
    return repo


def _fix(repo: GitRepo, *message: str, test: str = "test_empty") -> str:
    repo.write(
        {
            "src/toy/core.py": f"def f(xs):\n    return xs[0] if xs else 0  # {test}\n",
            "tests/test_core.py": f"def {test}():\n    pass\n",
        }
    )
    return repo.commit(*message)


class TestWhatBecomesATask:
    def test_a_fix_that_shipped_a_new_test(self, toy: Toy) -> None:
        [task], report = _mine(toy.repo.path, ISSUE)
        assert (task.base_commit, task.fix_commit) == (toy.base, toy.fix)
        assert (task.reference, task.reference_kind) == (12, "issue")
        assert task.title == ISSUE.title and task.problem_statement == ISSUE.body
        assert task.source_files == ("src/toy/core.py",)
        assert task.test_files == ("tests/test_core.py",)
        assert task.added_tests == ("test_add",)
        assert report.examined == 2 and report.fix_shaped == 1

    def test_a_pull_request_merge_commit(self, tmp_path: Path) -> None:
        repo = _project(tmp_path)
        base = repo.git("rev-parse", "HEAD")
        repo.git("checkout", "--quiet", "-b", "empty-list")
        _fix(repo, "Fix typo #3")
        repo.git("checkout", "--quiet", "main")
        repo.git(
            "merge", "--quiet", "--no-ff", "empty-list",
            "-m", "Merge pull request #7 from someone/empty-list", "-m", "Fix f on an empty list",
        )  # fmt: skip
        merge = repo.git("rev-parse", "HEAD")

        [task], _ = _mine(repo.path, Issue(7, "Fix f on an empty list", REPORT, True))
        assert (task.base_commit, task.fix_commit, task.reference) == (base, merge, 7)

    def test_methods_of_test_classes_count_as_added_tests(self, tmp_path: Path) -> None:
        repo = _project(tmp_path)
        repo.write({"src/toy/core.py": "def f(xs):\n    return xs[0] if xs else 0\n"})
        repo.write(
            {"tests/test_core.py": "class TestF:\n    def test_empty(self):\n        pass\n"}
        )
        repo.commit("Fix f on an empty list (#5)")
        [task], _ = _mine(repo.path, Issue(5, "f fails", REPORT))
        assert task.added_tests == ("TestF.test_empty",)


class TestWhereTheStatementComesFrom:
    def test_a_pull_request_defers_to_the_issue_it_closes(self, tmp_path: Path) -> None:
        repo = _project(tmp_path)
        _fix(repo, "Fix f on an empty list (#9)")
        pull = Issue(
            9, "Handle empty lists", "Closes #4.\n\nGuard the index with a length check.", True
        )
        [task], _ = _mine(repo.path, pull, Issue(4, "f([]) crashes", REPORT))
        assert (task.reference, task.reference_kind) == (4, "issue")
        assert task.title == "f([]) crashes" and task.problem_statement == REPORT

    def test_an_issue_the_commit_closes_comes_first(self, tmp_path: Path) -> None:
        repo = _project(tmp_path)
        _fix(repo, "Fix f on an empty list (#9)", "Fixes #4")
        issues = StaticIssues(Issue(9, "PR", REPORT, True), Issue(4, "f([]) crashes", REPORT))
        [task], _ = Miner(Repository.open(repo.path), issues, RULES).mine()
        assert task.reference == 4 and issues.requested == [4]

    def test_the_author_s_own_solution_is_redacted(self, tmp_path: Path) -> None:
        repo = _project(tmp_path)
        _fix(repo, "Fix f on an empty list (#5)")
        body = f"{REPORT}\n\n## Solution\nReturn 0 when the list is empty."
        [task], _ = _mine(repo.path, Issue(5, "f fails", body))
        assert task.problem_statement == REPORT and task.statement_redacted


class TestWhatIsDropped:
    def _rejected(
        self, repo: GitRepo, *issues: Issue, rules: MiningRules = RULES
    ) -> dict[str, int]:
        tasks, report = _mine(repo.path, *issues, rules=rules)
        assert tasks == []
        return report.rejected

    def test_a_fix_without_issue_text(self, tmp_path: Path) -> None:
        repo = _project(tmp_path)
        _fix(repo, "Fix f on an empty list (#5)")
        assert self._rejected(repo) == {"problem statement unavailable": 1}

    def test_a_statement_too_thin_to_act_on(self, tmp_path: Path) -> None:
        repo = _project(tmp_path)
        _fix(repo, "Fix f on an empty list (#5)")
        assert self._rejected(repo, Issue(5, "f fails", "It fails.")) == {
            "problem statement too thin to act on": 1
        }

    def test_a_fix_with_no_reference(self, tmp_path: Path) -> None:
        repo = _project(tmp_path)
        _fix(repo, "Fix f on an empty list")
        assert self._rejected(repo) == {"no issue or pull-request reference": 1}

    def test_a_fix_that_only_touched_test_helpers(self, tmp_path: Path) -> None:
        repo = _project(tmp_path)
        repo.write(
            {"src/toy/core.py": "def f(xs):\n    return 0\n", "tests/conftest.py": "x = 1\n"}
        )
        repo.commit("Fix f on an empty list (#5)")
        assert self._rejected(repo, Issue(5, "f fails", REPORT)) == {
            "no test shipped with the fix": 1
        }

    def test_a_fix_that_added_no_test_function(self, tmp_path: Path) -> None:
        repo = _project(tmp_path)
        repo.write(
            {"src/toy/core.py": "def f(xs):\n    return 0\n", "tests/test_core.py": "x = 1\n"}
        )
        repo.commit("Fix f on an empty list (#5)")
        assert self._rejected(repo, Issue(5, "f fails", REPORT)) == {
            "the fix added no test function": 1
        }

    def test_a_fix_too_broad_to_be_one_bug(self, tmp_path: Path) -> None:
        repo = _project(tmp_path)
        repo.write({f"src/toy/m{i}.py": "x = 1\n" for i in range(3)})
        repo.write({"tests/test_core.py": "def test_x():\n    pass\n"})
        repo.commit("Fix everything (#5)")
        assert self._rejected(repo, Issue(5, "f fails", REPORT)) == {"too broad to be one bug": 1}

    def test_a_change_that_is_not_a_fix(self, tmp_path: Path) -> None:
        repo = _project(tmp_path)
        _fix(repo, "Add support for empty lists (#5)")
        tasks, report = _mine(repo.path, Issue(5, "f fails", REPORT))
        assert tasks == [] and report.fix_shaped == 0


def test_only_the_newest_fix_for_an_issue_is_kept(tmp_path: Path) -> None:
    repo = _project(tmp_path)
    _fix(repo, "Fix f on an empty list (#5)", test="test_empty")
    newest = _fix(repo, "Fix f on an empty list, again (#5)", test="test_empty_again")
    [task], report = _mine(repo.path, Issue(5, "f fails", REPORT))
    assert task.fix_commit == newest
    assert report.rejected == {"a later change already covers this issue": 1}


def test_mining_stops_at_the_limit(tmp_path: Path) -> None:
    repo = _project(tmp_path)
    _fix(repo, "Fix f on an empty list (#5)", test="test_one")
    _fix(repo, "Fix f on an empty list (#6)", test="test_two")
    issues = (Issue(5, "f fails", REPORT), Issue(6, "f fails", REPORT))
    tasks, _ = _mine(repo.path, *issues, rules=MiningRules("src/toy", "tests", limit=1))
    assert [task.reference for task in tasks] == [6]
