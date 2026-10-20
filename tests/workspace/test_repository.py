"""Mainline history and history-free checkouts, from real git repositories."""

from __future__ import annotations

from pathlib import Path

import pytest

from bugbout.errors import RepositoryError
from bugbout.workspace.repository import Repository
from tests.toy import BUGGY, GitRepo, Toy


def test_a_directory_without_git_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(RepositoryError, match="not a git repository"):
        Repository.open(tmp_path)


class TestArchive:
    def test_a_checkout_has_the_files_but_no_history(self, toy: Toy, tmp_path: Path) -> None:
        toy.repository.archive(toy.base, tmp_path / "tree")
        assert (tmp_path / "tree" / "src" / "toy" / "core.py").read_text() == BUGGY
        assert not (tmp_path / "tree" / ".git").exists()

    def test_the_repository_itself_is_left_untouched(self, toy: Toy, tmp_path: Path) -> None:
        before = toy.repo.git("status", "--porcelain"), toy.repo.git("rev-parse", "HEAD")
        toy.repository.archive(toy.base, tmp_path / "tree")
        assert (toy.repo.git("status", "--porcelain"), toy.repo.git("rev-parse", "HEAD")) == before

    def test_export_ignore_cannot_hide_the_tests(self, tmp_path: Path) -> None:
        repo = GitRepo(tmp_path / "repo")
        repo.write({".gitattributes": "tests export-ignore\n", "tests/test_x.py": "x = 1\n"})
        sha = repo.commit("Initial commit")
        Repository.open(repo.path).archive(sha, tmp_path / "tree")
        assert (tmp_path / "tree" / "tests" / "test_x.py").exists()

    def test_an_existing_destination_is_replaced(self, toy: Toy, tmp_path: Path) -> None:
        (tmp_path / "tree").mkdir()
        (tmp_path / "tree" / "stale.py").write_text("")
        toy.repository.archive(toy.base, tmp_path / "tree")
        assert not (tmp_path / "tree" / "stale.py").exists()


class TestHistory:
    def test_the_log_follows_the_mainline_only(self, tmp_path: Path) -> None:
        repo = GitRepo(tmp_path / "repo")
        repo.write({"a.py": "a = 1\n"})
        base = repo.commit("Initial commit")
        repo.git("checkout", "--quiet", "-b", "feature")
        repo.write({"a.py": "a = 2\n"})
        repo.commit("Work in progress")
        repo.git("checkout", "--quiet", "main")
        repo.git(
            "merge", "--quiet", "--no-ff", "feature", "-m", "Merge pull request #7", "-m", "Bump a"
        )
        merge = repo.git("rev-parse", "HEAD")

        [newest, oldest] = Repository.open(repo.path).log()
        assert (newest.sha, newest.parents[0], newest.body) == (merge, base, "Bump a")
        assert newest.subject == "Merge pull request #7"
        assert oldest.sha == base and oldest.parents == ()

    def test_changed_files_are_measured_against_the_first_parent(self, toy: Toy) -> None:
        [fix, initial] = toy.repository.log()
        assert sorted(toy.repository.changed_files(fix)) == [
            "src/toy/core.py",
            "tests/test_core.py",
        ]
        assert toy.repository.changed_files(initial) == []

    def test_show_returns_exact_bytes_or_none(self, toy: Toy) -> None:
        assert toy.repository.show(toy.base, "src/toy/core.py") == BUGGY.encode()
        assert toy.repository.show(toy.base, "missing.py") is None


@pytest.mark.parametrize(
    ("url", "slug"),
    [
        ("https://github.com/owner/name.git", "owner/name"),
        ("https://github.com/owner/name", "owner/name"),
        ("git@github.com:owner/name.git", "owner/name"),
        ("https://gitlab.com/owner/name.git", None),
    ],
)
def test_the_github_slug_comes_from_origin(tmp_path: Path, url: str, slug: str | None) -> None:
    repo = GitRepo(tmp_path / "repo")
    repo.git("remote", "add", "origin", url)
    assert Repository.open(repo.path).github_slug() == slug


def test_a_repository_without_origin_has_no_slug(toy: Toy) -> None:
    assert toy.repository.github_slug() is None
