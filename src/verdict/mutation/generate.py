"""Enumerate the mutant population for a target.

Enumeration is pure static analysis: it parses each source file and asks every
selected operator where it could apply. Nothing is executed and nothing is
written to disk, so this is fast and side-effect free, and it can run before the
target's virtualenv even exists.

The output is a deterministic, sorted list of :class:`MutantSpec` recipes. Given
the same pinned commit and the same operator set, this function returns exactly
the same mutants in exactly the same order on any machine - which is what lets
the two arms of the ablation be joined mutant-by-mutant.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import cosmic_ray.plugins as plugins
from cosmic_ray.ast import ast_nodes, get_ast_from_path
from cosmic_ray.ast.ast_query import ASTQuery

from verdict.logging import get_logger
from verdict.mutation.mutant import MutantSpec
from verdict.mutation.operators import DEFAULT_OPERATORS, family_of
from verdict.targets.workspace import TargetWorkspace

log = get_logger(__name__)

# parso node types that represent genuine arithmetic expressions.
#
# Why this filter exists: cosmic-ray's operators match on *tokens*, so
# ReplaceBinaryOperator_Mul_Div happily rewrites the keyword-only marker in
# ``def f(self, *, a, b)`` into ``def f(self, /, a, b)``. That changes the
# calling convention, not any arithmetic, and it accounted for 26% of all
# arithmetic mutations across the two targets (63 of 239) and 16% of
# marshmallow's surviving mutants.
#
# Keeping them would do two kinds of damage: the operator-family breakdown
# would be wrong, and phase 2 would hand the Prosecutor a diff described as an
# arithmetic change that is nothing of the sort - inviting a hypothesis that
# cannot possibly be right.
#
# A whitelist is used rather than a blacklist so that unanticipated non-arithmetic
# contexts (``f(*args)``, ``{**a}``, ``[*xs]``) are excluded by default rather
# than by enumeration.
_ARITHMETIC_CONTEXTS = frozenset({"term", "arith_expr", "factor", "power"})


def _is_genuine_site(operator_name: str, node: object) -> bool:
    """Whether an operator match is really the construct the operator names."""
    if family_of(operator_name) != "arithmetic":
        return True
    parent = getattr(node, "parent", None)
    return getattr(parent, "type", None) in _ARITHMETIC_CONTEXTS


def _mutants_in_file(
    workspace: TargetWorkspace,
    path: Path,
    operators: tuple[str, ...],
    rejected: Counter[str],
) -> list[MutantSpec]:
    """Every mutant the selected operators can produce in one source file."""
    module_path = workspace.relative(path)
    try:
        tree = get_ast_from_path(path)
    except Exception as exc:  # parso raises assorted types on exotic syntax
        log.warning("Skipping %s: could not parse (%s)", module_path, exc)
        return []

    # Parsing dominates the cost, so the node list is walked once per file and
    # reused across all operators rather than re-parsed for each.
    nodes = list(ast_nodes(tree))
    target = workspace.target
    found: list[MutantSpec] = []

    for operator_name in operators:
        operator = plugins.get_operator(operator_name)()
        occurrence = 0
        for node in nodes:
            for start_pos, end_pos in operator.mutation_positions(node):
                if not _is_genuine_site(operator_name, node):
                    # Skipped, but the occurrence counter still advances: it is
                    # cosmic-ray's index into its own walk, and desynchronising
                    # it would make every later mutant in this file mutate the
                    # wrong site.
                    occurrence += 1
                    rejected[operator_name] += 1
                    continue
                found.append(
                    MutantSpec(
                        mutant_id=MutantSpec.make_id(
                            target.slug, target.commit, module_path, operator_name, occurrence
                        ),
                        target_slug=target.slug,
                        target_commit=target.commit,
                        module_path=module_path,
                        operator=operator_name,
                        family=family_of(operator_name),
                        occurrence=occurrence,
                        start_line=start_pos[0],
                        start_col=start_pos[1],
                        end_line=end_pos[0],
                        end_col=end_pos[1],
                        function=ASTQuery(node).get_definition_name(),
                    )
                )
                # The occurrence index is what cosmic-ray uses to re-locate this
                # exact site later, so it must count every position the operator
                # matches, in the same order, whether or not we keep it.
                occurrence += 1
    return found


def generate_mutants(
    workspace: TargetWorkspace,
    *,
    operators: tuple[str, ...] = DEFAULT_OPERATORS,
) -> list[MutantSpec]:
    """The full mutant population for a prepared target workspace."""
    files = workspace.source_files()
    log.info(
        "Enumerating mutants for %s across %d source files with %d operators",
        workspace.target.slug,
        len(files),
        len(operators),
    )
    mutants: list[MutantSpec] = []
    rejected: Counter[str] = Counter()
    for path in files:
        in_file = _mutants_in_file(workspace, path, operators, rejected)
        log.debug("  %s: %d mutants", workspace.relative(path), len(in_file))
        mutants.extend(in_file)

    if rejected:
        # Reported, never silent: dropping mutants changes the denominator, so
        # it has to be visible in the run output.
        log.info(
            "Rejected %d token-level false matches (operator matched a construct "
            "it does not actually describe): %s",
            sum(rejected.values()),
            ", ".join(f"{k.split('/')[-1]}={v}" for k, v in rejected.most_common(5)),
        )

    # Sorting by the content-addressed id gives a stable order that does not
    # depend on filesystem traversal order.
    mutants.sort(key=lambda m: m.mutant_id)
    log.info("Enumerated %d mutants for %s", len(mutants), workspace.target.slug)
    return mutants


def summarize(mutants: list[MutantSpec]) -> dict[str, int]:
    """Counts per operator family, for reporting."""
    return dict(Counter(m.family for m in mutants).most_common())
