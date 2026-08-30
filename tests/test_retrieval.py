"""Tests for the retrieval layer.

Two properties here are load-bearing for the project's central claim and are
pinned deliberately:

* **Chunks are cut at AST boundaries, never at a fixed size.** The build brief
  called this out as a checkable engineering decision, and it is easy to let it
  silently degrade into text splitting.
* **Historical file paths are normalised.** marshmallow moved to a ``src/``
  layout in 2019; without normalisation, 65% of its bugfix history is invisible
  to retrieval - with no error, just a quietly weaker knowledge base.
"""

from __future__ import annotations

import time

import numpy as np
import pytest

from precedent.retrieval.chunking import chunk_module
from precedent.retrieval.documents import Document
from precedent.retrieval.index import KnowledgeBase, tokenize
from precedent.retrieval.mining.git_history import Commit
from precedent.retrieval.risk import rank, score_symbols
from precedent.targets import get_target

MODULE = '''\
"""Module docstring."""
import typing


def set_value(dct, key, value):
    """Set a nested value."""
    if "." in key:
        head, rest = key.split(".", 1)
        set_value(dct.setdefault(head, {}), rest, value)
    else:
        dct[key] = value


class Length:
    """Validate a length."""

    def __init__(self, minimum=None):
        self.minimum = minimum

    def __call__(self, value):
        if self.minimum is not None and len(value) < self.minimum:
            raise ValueError("too short")
        return value
'''


class TestASTChunking:
    def test_chunks_are_complete_definitions(self) -> None:
        """A fixed-size window would hand the Prosecutor a severed function
        body with no signature and no return - a bag of keywords describing
        nothing."""
        for chunk in chunk_module(MODULE, "m.py"):
            first = chunk.source.lstrip().splitlines()[0]
            assert first.startswith(("def ", "async def ", "class ")), first

    def test_methods_keep_their_class_in_the_qualified_name(self) -> None:
        """`Length.__call__` and `Range.__call__` must be distinguishable, or
        retrieval cannot be scoped to the right one."""
        names = {c.qualified_name for c in chunk_module(MODULE, "m.py")}
        assert "Length.__call__" in names
        assert "Length.__init__" in names

    def test_top_level_functions_are_chunked(self) -> None:
        assert "set_value" in {c.qualified_name for c in chunk_module(MODULE, "m.py")}

    def test_symbol_is_the_bare_name(self) -> None:
        """A diff's `function` field carries the bare name, so chunks must be
        matchable by it."""
        chunk = next(
            c for c in chunk_module(MODULE, "m.py") if c.qualified_name == "Length.__call__"
        )
        assert chunk.symbol == "__call__"

    def test_docstrings_are_captured(self) -> None:
        chunk = next(c for c in chunk_module(MODULE, "m.py") if c.qualified_name == "set_value")
        assert chunk.docstring == "Set a nested value."

    def test_line_ranges_are_sane(self) -> None:
        for chunk in chunk_module(MODULE, "m.py"):
            assert chunk.start_line <= chunk.end_line

    def test_unparseable_module_yields_no_chunks_rather_than_raising(self) -> None:
        assert chunk_module("def broken(:\n", "m.py") == []


class TestHistoricalPathNormalisation:
    def test_old_layout_paths_are_rewritten(self) -> None:
        """A 2016 commit touching `marshmallow/utils.py` concerns the same code
        as today's `src/marshmallow/utils.py`."""
        target = get_target("marshmallow")
        assert target.normalize_path("marshmallow/utils.py") == "src/marshmallow/utils.py"

    def test_current_paths_are_untouched(self) -> None:
        target = get_target("marshmallow")
        assert target.normalize_path("src/marshmallow/utils.py") == "src/marshmallow/utils.py"

    def test_unrelated_paths_are_untouched(self) -> None:
        assert get_target("marshmallow").normalize_path("docs/index.rst") == "docs/index.rst"

    def test_pathspecs_include_history(self) -> None:
        """`git log -- src/marshmallow` alone sees 484 of 1,385 commits."""
        assert set(get_target("marshmallow").source_pathspecs) == {
            "src/marshmallow",
            "marshmallow",
        }

    def test_a_target_that_never_moved_has_one_pathspec(self) -> None:
        assert get_target("arrow").source_pathspecs == ("arrow",)


class TestCommitClassification:
    @staticmethod
    def _commit(subject: str, body: str = "") -> Commit:
        return Commit(sha="a" * 40, timestamp=0, subject=subject, body=body, patch="")

    @pytest.mark.parametrize(
        "subject",
        [
            "Fix URL validator rejecting a fragment",
            "fix regression in Schema.load",
            "Handle edge case in Length",
            "Correct incorrect timezone offset",
            "hotfix: crash on empty input",
        ],
    )
    def test_repair_commits_are_incidents(self, subject: str) -> None:
        assert self._commit(subject).is_incident

    @pytest.mark.parametrize(
        "subject",
        ["Add support for Python 3.13", "Bump version to 4.0", "Update documentation"],
    )
    def test_feature_and_chore_commits_are_not(self, subject: str) -> None:
        """Indexing every commit would bury the defects in noise - and it is
        the defects the Prosecutor needs."""
        assert not self._commit(subject).is_incident

    def test_reverts_are_detected_and_count_as_incidents(self) -> None:
        commit = self._commit('Revert "Add type annotation for set_class (#2777)"')
        assert commit.is_revert
        assert commit.is_incident

    def test_issue_references_are_extracted(self) -> None:
        commit = self._commit("Fix validator (#3016)", "Closes #2937 and #2935")
        assert set(commit.issues) == {"3016", "2937", "2935"}

    def test_changed_text_excludes_diff_headers(self) -> None:
        commit = Commit(
            sha="a" * 40,
            timestamp=0,
            subject="Fix",
            body="",
            patch="--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-old\n+new\n",
        )
        changed = commit.changed_text()
        assert "-old" in changed and "+new" in changed
        assert "+++" not in changed


class TestTokenizer:
    def test_splits_snake_case_while_keeping_the_whole(self) -> None:
        """`set_value` must match documents that mention `value` alone, without
        losing the exact-match signal of the full identifier."""
        tokens = tokenize("set_value")
        assert "set_value" in tokens
        assert "value" in tokens

    def test_lowercases(self) -> None:
        assert "schema" in tokenize("Schema")

    def test_drops_punctuation(self) -> None:
        assert tokenize("foo.bar(baz)") == ["foo", "bar", "baz"]


class TestHybridSearch:
    @staticmethod
    def _kb() -> KnowledgeBase:
        docs = [
            Document(
                doc_id="aaa",
                kind="incident",
                target_slug="t",
                title="Fix set_value for dotted keys",
                body="nested key assignment was wrong",
                module_path="src/pkg/utils.py",
                symbols=("set_value",),
                metadata={"timestamp": int(time.time())},
            ),
            Document(
                doc_id="bbb",
                kind="code_chunk",
                target_slug="t",
                title="src/pkg/other.py:unrelated",
                body="something entirely different about networking",
                module_path="src/pkg/other.py",
                symbols=("unrelated",),
            ),
            Document(
                doc_id="ccc",
                kind="call_site",
                target_slug="t",
                title="set_value is called from 1 place",
                body="src/pkg/schema.py:689 in Schema._deserialize",
                module_path="src/pkg/schema.py",
                symbols=("set_value",),
                metadata={"callee": "set_value", "call_count": 3},
            ),
        ]
        vectors = np.eye(3, 384, dtype=np.float32)
        return KnowledgeBase(docs, vectors)

    def test_symbol_scope_lifts_the_matching_document(self) -> None:
        """Relevance to *this* symbol is a structural fact, not a textual
        similarity, so it is applied explicitly rather than left to keywords."""
        kb = self._kb()
        hits = kb.search(
            "set_value dotted key", np.eye(3, 384, dtype=np.float32)[0], symbols=("set_value",)
        )
        assert hits[0].document.doc_id == "aaa"
        assert "symbol match" in hits[0].boosts

    def test_results_are_deterministic(self) -> None:
        """The ablation number depends on both arms retrieving identically on
        every run."""
        kb = self._kb()
        query = np.eye(3, 384, dtype=np.float32)[0]
        first = [h.document.doc_id for h in kb.search("set_value", query, symbols=("set_value",))]
        second = [h.document.doc_id for h in kb.search("set_value", query, symbols=("set_value",))]
        assert first == second

    def test_kind_filter_restricts_results(self) -> None:
        kb = self._kb()
        hits = kb.search("set_value", np.eye(3, 384, dtype=np.float32)[0], kinds=("call_site",))
        assert {h.document.kind for h in hits} == {"call_site"}

    def test_each_hit_records_why_it_ranked(self) -> None:
        kb = self._kb()
        hits = kb.search("set_value", np.eye(3, 384, dtype=np.float32)[0], symbols=("set_value",))
        assert hits[0].explain()

    def test_mismatched_documents_and_embeddings_are_rejected(self) -> None:
        with pytest.raises(ValueError, match="documents but"):
            KnowledgeBase([], np.zeros((3, 384), dtype=np.float32))

    def test_empty_index_returns_nothing(self) -> None:
        kb = KnowledgeBase([], np.zeros((0, 384), dtype=np.float32))
        assert kb.search("anything", np.zeros(384, dtype=np.float32)) == []


class TestRiskScoring:
    @staticmethod
    def _incident(symbol: str, age_days: float, kind: str = "incident") -> Document:
        return Document(
            doc_id=f"{symbol}{age_days}{kind}",
            kind=kind,  # type: ignore[arg-type]
            target_slug="t",
            title=f"Fix {symbol}",
            body="",
            symbols=(symbol,),
            metadata={"timestamp": int(time.time() - age_days * 86400)},
        )

    def test_more_incidents_means_more_risk(self) -> None:
        docs = [self._incident("a", 10), self._incident("a", 20), self._incident("b", 10)]
        scores = score_symbols(docs)
        assert scores["a"].score > scores["b"].score

    def test_recent_incidents_outweigh_old_ones(self) -> None:
        """A defect from ten years ago says less about today's code."""
        scores = score_symbols([self._incident("recent", 30), self._incident("ancient", 3650)])
        assert scores["recent"].score > scores["ancient"].score

    def test_a_revert_weighs_more_than_a_plain_fix(self) -> None:
        """Someone shipped a change here and had to take it back - the
        strongest signal the history offers."""
        scores = score_symbols([self._incident("r", 10, "revert"), self._incident("f", 10)])
        assert scores["r"].score > scores["f"].score
        assert scores["r"].reverts == 1

    def test_ranking_is_deterministic_for_ties(self) -> None:
        docs = [self._incident("b", 10), self._incident("a", 10)]
        assert [r.key for r in rank(score_symbols(docs))] == ["a", "b"]

    def test_evidence_is_human_readable(self) -> None:
        scores = score_symbols([self._incident("a", 10, "revert")])
        assert "revert" in scores["a"].explain()


class TestDocument:
    def test_ids_are_content_addressed(self) -> None:
        """A citation recorded in a result must still resolve after the index
        is rebuilt, or the dashboard cannot show what was retrieved."""
        assert Document.make_id("incident", "t", "sha") == Document.make_id("incident", "t", "sha")

    def test_different_content_gives_different_ids(self) -> None:
        assert Document.make_id("incident", "t", "a") != Document.make_id("incident", "t", "b")

    def test_citation_format_is_stable(self) -> None:
        doc = Document(doc_id="abc123", kind="incident", target_slug="t", title="x", body="y")
        assert doc.cite() == "[incident:abc123]"

    def test_round_trips_through_a_dict(self) -> None:
        doc = Document(
            doc_id="abc", kind="incident", target_slug="t", title="x", body="y", symbols=("s",)
        )
        assert Document.from_dict(doc.to_dict()) == doc
