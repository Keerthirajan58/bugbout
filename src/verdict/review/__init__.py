"""Auditing real code changes, not just injected mutants.

Mutants are objective ground truth but they are synthetic: a single token
flipped by a tool. This package builds the other half of the evaluation - diffs
an actual coding agent wrote, against real historical bugs, scored by the test
the maintainer actually added when they fixed it.
"""
