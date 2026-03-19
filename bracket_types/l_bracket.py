"""
L-bracket type definition and registration.

Imports only from bracket_types.__init__ and bracket_types._helpers.
Registers L_BRACKET into REGISTRY on import.
"""

from bracket_types import REGISTRY, BracketType, OptimizerStrategy
from bracket_types._helpers import (
    _l_build_freecad_script,
    _l_fillet_constraint,
    _l_fixed_nodes,
    _l_tip_node,
    _l_load_patch,
    _l_compute_mass,
    _l_propose_params,
    _L_PARAM_BOUNDS,
)
from tools.presizing import l_presizing

L_BRACKET = BracketType(
    name="l_bracket",
    display_name="L-bracket",
    param_keys=(
        "flange_width",
        "flange_height",
        "web_height",
        "thickness",
        "fillet_radius",
    ),
    defaults_mm={
        "flange_width_mm":  80.0,
        "flange_height_mm": 60.0,
        "web_height_mm":    100.0,
        "thickness_mm":     6.0,
        "fillet_radius_mm": 4.0,
    },
    fillet_constraint=_l_fillet_constraint,
    freecad_script_fn=_l_build_freecad_script,
    fixed_nodes_fn=_l_fixed_nodes,
    tip_node_fn=_l_tip_node,
    mass_fn=_l_compute_mass,
    optimizer=OptimizerStrategy(
        param_bounds=_L_PARAM_BOUNDS,
        propose_fn=_l_propose_params,
    ),
    presizing_fn=l_presizing,
    load_patch_fn=_l_load_patch,
)

REGISTRY["l_bracket"] = L_BRACKET
