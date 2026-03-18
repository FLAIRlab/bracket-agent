"""
Parameter update strategy between FEM iterations.

Implements propose_params() which inspects constraint violations and
adjusts bracket geometry parameters to drive the design toward feasibility
while minimising mass.

For L-bracket: delegates to _l_propose_params from bracket_types._helpers.
For other types: delegates to bracket_type.optimizer.propose_fn.
"""

from bracket_types._helpers import _l_propose_params, _L_PARAM_BOUNDS

# Exposed for backward compatibility with tests that import PARAM_BOUNDS directly.
PARAM_BOUNDS = _L_PARAM_BOUNDS


def propose_params(current_params: dict, violations: list, iteration: int,
                   bracket_type=None) -> dict:
    """
    Propose updated geometry parameters for the next iteration.

    Parameters
    ----------
    current_params : dict — current geometry (SI metres)
    violations     : list[str] — violation strings with prefixes
    iteration      : int — current iteration number
    bracket_type   : BracketType | None — if None, uses L-bracket strategy

    Returns
    -------
    dict — updated geometry params
    """
    if bracket_type is not None:
        return bracket_type.optimizer.propose_fn(current_params, violations, iteration)
    return _l_propose_params(current_params, violations, iteration)
