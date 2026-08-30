"""The target registry is the reproducibility contract; these guard it."""

from __future__ import annotations

import re

import pytest

from precedent.targets import TARGETS, get_target, list_targets
from precedent.targets.registry import UnknownTargetError

_FULL_SHA = re.compile(r"^[0-9a-f]{40}$")


def test_every_target_is_pinned_to_a_full_commit_sha() -> None:
    """A tag or short SHA could resolve differently later and silently
    invalidate the ablation comparison."""
    for target in list_targets():
        assert _FULL_SHA.match(target.commit), f"{target.slug} is not pinned to a full SHA"


def test_slugs_are_unique_and_match_their_keys() -> None:
    for slug, target in TARGETS.items():
        assert slug == target.slug


def test_targets_are_permissively_licensed() -> None:
    """Mutating and redistributing snippets of these repos requires it."""
    permissive = {"MIT", "Apache-2.0", "BSD-3-Clause", "BSD-2-Clause", "ISC"}
    for target in list_targets():
        assert target.spdx_license in permissive


def test_every_target_records_why_it_was_chosen() -> None:
    for target in list_targets():
        assert len(target.selection_rationale) > 80, f"{target.slug} lacks a rationale"


def test_clone_url_is_derived_from_owner_and_name() -> None:
    target = get_target("marshmallow")
    assert target.clone_url == "https://github.com/marshmallow-code/marshmallow.git"
    assert target.full_name == "marshmallow-code/marshmallow"


def test_unknown_target_names_the_valid_options() -> None:
    with pytest.raises(UnknownTargetError) as exc:
        get_target("django")
    assert "marshmallow" in str(exc.value)


def test_list_targets_is_deterministically_ordered() -> None:
    assert [t.slug for t in list_targets()] == sorted(TARGETS)
