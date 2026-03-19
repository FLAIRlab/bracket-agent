"""
Bracket type registry.

Defines BracketType and OptimizerStrategy dataclasses, the REGISTRY dict,
KNOWN_TYPE_NAMES frozenset, and get_type() lookup.

No imports from pipeline, optimizer, or tools at module level.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

KNOWN_TYPE_NAMES: frozenset = frozenset({"l_bracket", "t_bracket", "u_bracket"})

# Registry populated by each bracket type module when imported.
REGISTRY: dict[str, "BracketType"] = {}


@dataclass
class OptimizerStrategy:
    """Optimization strategy for a bracket type."""
    param_bounds: dict    # {key: (lo_m, hi_m)}
    propose_fn: Callable
    # Signature: (current_params, violations, iteration,
    #              metrics=None, constraints=None) -> dict
    # metrics and constraints are optional — functions must accept **kwargs or
    # explicit keyword args with None defaults for backward compatibility.


@dataclass
class BracketType:
    """Complete description of a bracket type for the FEM pipeline."""
    name: str
    display_name: str
    param_keys: tuple              # SI geometry keys expected by the pipeline
    defaults_mm: dict              # {key_mm: default_value} for build_brief()
    fillet_constraint: Callable    # (params_si) -> max_fillet_m
    freecad_script_fn: Callable    # (params_si, output_step, apply_fillet) -> str
    fixed_nodes_fn: Callable       # (nodes, params_si) -> list[node_id]
    tip_node_fn: Callable          # (nodes, params_si) -> node_id
    mass_fn: Callable              # (params_si, rho) -> float kg
    optimizer: OptimizerStrategy
    presizing_fn:    Callable | None = None  # (params, loads, material, constraints) -> params
    load_patch_fn:   Callable | None = None
    # (nodes, params_si, k=5) -> list[int]
    # k nearest nodes to load application point.
    # None → write_inp() uses [tip_node_fn()] as single-element list (old behaviour).


def get_type(name: str) -> BracketType:
    """
    Look up a bracket type by name.

    Triggers lazy registration of all built-in types on first call.

    Raises
    ------
    ValueError if name is not in KNOWN_TYPE_NAMES / REGISTRY.
    """
    _ensure_registered()
    if name not in REGISTRY:
        raise ValueError(
            f"Unknown bracket_type {name!r}. Allowed: {sorted(KNOWN_TYPE_NAMES)}"
        )
    return REGISTRY[name]


# ---------------------------------------------------------------------------
# Lazy registration — loads submodules on first get_type() call
# ---------------------------------------------------------------------------

_builtins_registered: bool = False


def _ensure_registered() -> None:
    global _builtins_registered
    if _builtins_registered:
        return
    _builtins_registered = True
    # Deferred imports to avoid circular dependencies at module initialisation.
    # Each submodule registers itself into REGISTRY when imported.
    from bracket_types import l_bracket, t_bracket, u_bracket  # noqa: F401
