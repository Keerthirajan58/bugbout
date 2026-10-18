"""Which mutation operators Verdict uses, and why.

cosmic-ray ships 213 operators, formed by taking the full cross-product within
each family: every binary operator can become every other binary operator, so
``a + b`` yields eleven mutants including ``a >> b`` and ``a | b``.

Using all 213 would be a mistake, in both directions:

* **It inflates kill-rate.** ``a + b -> a >> b`` on two strings raises
  ``TypeError`` immediately. Almost any test touching that line kills it. These
  are free kills that make a test suite look stronger than it is.
* **It is unrepresentative.** No human and no coding agent has ever turned ``+``
  into ``>>`` in a real change. Verdict's claim is about catching *plausible*
  regressions, so the mutant population has to look like plausible regressions.

The subset below keeps operators that correspond to mistakes people and coding
agents actually make: boundary slips, inverted conditions, flipped defaults,
off-by-one constants, swapped loop control.

Two things make this defensible rather than convenient:

1. Every inclusion and exclusion is justified here in writing, in advance.
2. **The identical set is used in both arms of the ablation.** The delta between
   "without retrieval" and "with retrieval" is computed over exactly the same
   mutants, so operator choice cannot manufacture the headline number.
"""

from __future__ import annotations

from typing import Final

# --- boundary and comparison ------------------------------------------------
# The canonical silent regression. `<` vs `<=` changes behaviour only at the
# boundary, so the code stays correct almost everywhere and fails on one input.
_COMPARISON: Final = (
    "core/ReplaceComparisonOperator_Lt_LtE",
    "core/ReplaceComparisonOperator_LtE_Lt",
    "core/ReplaceComparisonOperator_Gt_GtE",
    "core/ReplaceComparisonOperator_GtE_Gt",
    # Direction flips: a reversed inequality in a guard clause.
    "core/ReplaceComparisonOperator_Lt_Gt",
    "core/ReplaceComparisonOperator_Gt_Lt",
    # Inverted equality checks.
    "core/ReplaceComparisonOperator_Eq_NotEq",
    "core/ReplaceComparisonOperator_NotEq_Eq",
    # Identity inversion: `if x is None` -> `if x is not None` is a real and
    # frequent bug, and marshmallow's None handling is full of these.
    "core/ReplaceComparisonOperator_Is_IsNot",
    "core/ReplaceComparisonOperator_IsNot_Is",
    # `==` vs `is`: a genuine Python trap that behaves identically for interned
    # values and diverges for everything else - a textbook silent regression.
    "core/ReplaceComparisonOperator_Eq_Is",
    "core/ReplaceComparisonOperator_NotEq_IsNot",
)
# Deliberately excluded: Lt_Is, Gt_IsNot and the rest of the ordering/identity
# cross-product. Comparing with `is` where `<` was meant is a type confusion
# that raises or returns a constant, not a plausible regression.

# --- arithmetic --------------------------------------------------------------
# Sign and scale errors within the same numeric family.
_ARITHMETIC: Final = (
    "core/ReplaceBinaryOperator_Add_Sub",
    "core/ReplaceBinaryOperator_Sub_Add",
    "core/ReplaceBinaryOperator_Mul_Div",
    "core/ReplaceBinaryOperator_Div_Mul",
    "core/ReplaceBinaryOperator_Div_FloorDiv",
    "core/ReplaceBinaryOperator_FloorDiv_Div",
)
# Deliberately excluded: cross-family swaps (Add_RShift, Mul_BitOr, Add_Pow and
# so on). These raise TypeError on non-numeric operands or produce wildly
# out-of-range values, both of which are trivially caught.

# --- logic and control flow --------------------------------------------------
_LOGIC: Final = (
    "core/ReplaceAndWithOr",
    "core/ReplaceOrWithAnd",
    "core/AddNot",
)
_CONTROL: Final = (
    "core/ReplaceBreakWithContinue",
    "core/ReplaceContinueWithBreak",
    # A loop whose body never executes: the shape of a wrong guard condition.
    "core/ZeroIterationForLoop",
)

# --- constants ---------------------------------------------------------------
_CONSTANTS: Final = (
    # Flipped boolean defaults. Directly relevant to both targets: marshmallow's
    # `required=`/`allow_none=` and arrow's normalisation flags are exactly this.
    "core/ReplaceTrueWithFalse",
    "core/ReplaceFalseWithTrue",
    # Off-by-one and magic-number drift.
    "core/NumberReplacer",
)

# --- exceptions and unary ----------------------------------------------------
_EXCEPTIONS: Final = ("core/ExceptionReplacer",)
_UNARY: Final = (
    "core/ReplaceUnaryOperator_USub_UAdd",
    "core/ReplaceUnaryOperator_UAdd_USub",
)

DEFAULT_OPERATORS: Final[tuple[str, ...]] = (
    *_COMPARISON,
    *_ARITHMETIC,
    *_LOGIC,
    *_CONTROL,
    *_CONSTANTS,
    *_EXCEPTIONS,
    *_UNARY,
)

OPERATOR_FAMILIES: Final[dict[str, tuple[str, ...]]] = {
    "comparison": _COMPARISON,
    "arithmetic": _ARITHMETIC,
    "logic": _LOGIC,
    "control_flow": _CONTROL,
    "constants": _CONSTANTS,
    "exceptions": _EXCEPTIONS,
    "unary": _UNARY,
}

# Operators requiring constructor arguments produce combinatorial noise rather
# than regressions, and RemoveDecorator almost always raises on the first call.
EXCLUDED_WITH_REASON: Final[dict[str, str]] = {
    "core/VariableReplacer": "needs parameterisation; substituting arbitrary names is not a regression shape",
    "core/VariableInserter": "needs parameterisation; injects undefined names, which raises immediately",
    "core/RemoveDecorator": "dropping @property or @classmethod raises on first use - a free kill, not a silent one",
}


def family_of(operator_name: str) -> str:
    """Which family an operator belongs to, for reporting and stratified sampling."""
    for family, members in OPERATOR_FAMILIES.items():
        if operator_name in members:
            return family
    return "other"
