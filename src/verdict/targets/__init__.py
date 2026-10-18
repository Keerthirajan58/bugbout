"""Target repository selection, checkout and test execution."""

from verdict.targets.registry import TARGETS, TargetRepo, get_target, list_targets

__all__ = ["TARGETS", "TargetRepo", "get_target", "list_targets"]
