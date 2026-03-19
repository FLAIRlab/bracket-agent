"""
Bracket FEM Agent — conversational CLI entry point.

Supports two LLM backends:
  - Anthropic  claude-sonnet-4-6  (default when ANTHROPIC_API_KEY is set)
  - OpenAI     gpt-5.2            (default when OPENAI_API_KEY is set)

Backend selection (checked in this order):
  1. AGENT_BACKEND env var: "anthropic" or "openai"
  2. Auto-detect: ANTHROPIC_API_KEY present → anthropic, OPENAI_API_KEY → openai

Override the OpenAI model with OPENAI_MODEL env var (default: gpt-5.2).

API keys are loaded automatically from credentials/.env (relative to this
file) if it exists. Environment variables already set take priority.

Usage:
  # fill in credentials/.env, then just run:
  python agent.py
"""

import json
import logging
import os
import sys
from pathlib import Path

import agent_tools

# ---------------------------------------------------------------------------
# Load credentials/.env (keys already in the environment take priority)
# ---------------------------------------------------------------------------

def _load_dotenv() -> None:
    env_path = Path(__file__).parent / "credentials" / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if key and value and key not in os.environ:
            os.environ[key] = value

_load_dotenv()

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s  %(levelname)-8s  %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)

# ---------------------------------------------------------------------------
# System prompt (shared across backends)
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are a structural engineering assistant that helps users \
design bracket components using finite element analysis (FEA).

## Pipeline capabilities
You have access to a bracket FEM optimization pipeline. It:
- Creates a parametric 3D bracket (FreeCAD → Gmsh mesh → CalculiX solver)
- Supports L-bracket, T-bracket, and U-bracket (channel) geometries
- Iterates geometry until all constraints are met or 10 iterations are used
- Returns mass, factor of safety, peak stress, and tip deflection

## Bracket types
| bracket_type | Description                              | Fixed face  | Default load point     |
|--------------|------------------------------------------|-------------|------------------------|
| l_bracket    | L-shaped: web + flange (default)         | x ≈ 0       | Flange tip             |
| t_bracket    | Inverted T: central web + top flange     | z ≈ 0       | Flange center top      |
| u_bracket    | Channel: two walls + base plate          | z ≈ 0       | Top of left wall       |

## Geometry parameters (L-bracket and T-bracket — same 5 keys)
| Parameter        | L default | T default | Range           |
|------------------|-----------|-----------|-----------------|
| flange_width_mm  | 80 mm     | 120 mm    | 40 – 200 mm     |
| flange_height_mm | 60 mm     | 60 mm     | 30 – 150 mm     |
| web_height_mm    | 100 mm    | 100 mm    | 50 – 250 mm     |
| thickness_mm     | 6 mm      | 6 mm      | 3 – 20 mm       |
| fillet_radius_mm | 4 mm      | 4 mm      | 2 – 15 mm       |

## Geometry parameters (U-bracket — different keys)
| Parameter         | Default | Range           |
|-------------------|---------|-----------------|
| channel_width_mm  | 80 mm   | 40 – 200 mm     |
| wall_height_mm    | 100 mm  | 50 – 250 mm     |
| channel_depth_mm  | 60 mm   | 30 – 150 mm     |
| thickness_mm      | 6 mm    | 3 – 20 mm       |
| fillet_radius_mm  | 4 mm    | 2 – 15 mm       |

Geometry constraint always enforced: fillet_radius ≤ thickness × 0.45

## Material defaults (structural steel)
E = 200 GPa, nu = 0.3, rho = 7850 kg/m³, Sy = 250 MPa

## Design constraints (all must pass)
- Max von Mises stress ≤ Sy / 1.5  (166.7 MPa for default steel)
- Factor of safety ≥ 1.5
- Max tip deflection ≤ 5 mm
- Mass ≤ max_mass_kg (optional — only if user specifies a budget)

The optimizer adjusts geometry to meet constraints while minimising mass.

## Tool usage rules
1. **run_pipeline**: Call this when the user specifies (or implies) a load
   magnitude and wants a new bracket designed from scratch. Only `load_n` is
   required — use defaults for everything else. Use `bracket_type` to select
   the geometry (default "l_bracket"). Ask AT MOST two clarifying questions.

2. **modify_and_run**: Call this for incremental changes to the current design
   (e.g. "what if the load is 3000 N?", "try a T-bracket instead", "add a mass
   budget of 1 kg"). Always loads the previous design from session state.

3. **read_last_results**: Call this when the user asks about results from a
   prior run WITHOUT requesting a new simulation. Never triggers the pipeline.

4. Always warn the user that the pipeline will take ~2-5 minutes before
   calling run_pipeline or modify_and_run.

## Result explanation style
After every tool result, always report:
- Pass / Fail status and bracket type
- Mass in grams (convert from kg)
- Factor of safety to 2 decimal places
- Peak von Mises stress as both MPa and % of yield
- Max tip deflection in mm
- Output directory for renders and reports
- If FAIL: explain the physics in plain English
- If violations remain after max iterations: suggest manual parameter changes
"""

# ---------------------------------------------------------------------------
# Tool definitions — backend-agnostic (use neutral "parameters" key)
# ---------------------------------------------------------------------------

_TOOL_DEFS = [
    {
        "name": "run_pipeline",
        "description": (
            "Design a bracket from scratch by running the FEM optimization pipeline. "
            "Builds a brief, runs up to max_iter CalculiX simulations, and returns "
            "the best-passing design. Required: load_n. All geometry, material, and "
            "type parameters are optional — omit to use defaults (l_bracket)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "load_n": {
                    "type": "number",
                    "description": "Applied point load in Newtons (required).",
                },
                "bracket_type": {
                    "type": "string",
                    "enum": ["l_bracket", "t_bracket", "u_bracket"],
                    "description": (
                        "Bracket geometry type. 'l_bracket' (default): L-shaped, "
                        "'t_bracket': inverted T with central web, "
                        "'u_bracket': channel section with two walls + base."
                    ),
                },
                "flange_width_mm": {
                    "type": "number",
                    "description": "L/T-bracket flange width in mm. Default 80 (L) / 120 (T).",
                },
                "flange_height_mm": {
                    "type": "number",
                    "description": "L/T-bracket out-of-plane depth in mm. Default 60.",
                },
                "web_height_mm": {
                    "type": "number",
                    "description": "L/T-bracket vertical web height in mm. Default 100.",
                },
                "channel_width_mm": {
                    "type": "number",
                    "description": "U-bracket total channel width in mm. Default 80.",
                },
                "wall_height_mm": {
                    "type": "number",
                    "description": "U-bracket side wall height in mm. Default 100.",
                },
                "channel_depth_mm": {
                    "type": "number",
                    "description": "U-bracket out-of-plane depth in mm. Default 60.",
                },
                "thickness_mm": {
                    "type": "number",
                    "description": "Uniform wall thickness in mm. Default 6.",
                },
                "fillet_radius_mm": {
                    "type": "number",
                    "description": "Interior corner fillet radius in mm. Default 4.",
                },
                "E_gpa": {
                    "type": "number",
                    "description": "Young's modulus in GPa. Default 200.",
                },
                "nu": {
                    "type": "number",
                    "description": "Poisson's ratio. Default 0.3.",
                },
                "rho": {
                    "type": "number",
                    "description": "Density in kg/m³. Default 7850.",
                },
                "Sy_mpa": {
                    "type": "number",
                    "description": "Yield stress in MPa. Default 250.",
                },
                "max_mass_kg": {
                    "type": "number",
                    "description": (
                        "Optional mass budget in kg. Omit for unconstrained minimisation."
                    ),
                },
                "max_iter": {
                    "type": "integer",
                    "description": "Max optimization iterations (1–10). Default 10.",
                },
            },
            "required": ["load_n"],
        },
    },
    {
        "name": "modify_and_run",
        "description": (
            "Apply incremental changes to the current bracket design and rerun "
            "the FEM pipeline. Use for follow-up questions like 'what if the load "
            "is higher?', 'try a T-bracket instead', 'add a mass budget of 1 kg'. "
            "Loads prior geometry, load, and material from session state."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "changes": {
                    "type": "object",
                    "description": (
                        "Dict of parameter overrides. Omit or pass {} to rerun the previous "
                        "design unchanged. Accepted keys: load_n, "
                        "bracket_type (to switch type), flange_width_mm, "
                        "flange_height_mm, web_height_mm, channel_width_mm, "
                        "wall_height_mm, channel_depth_mm, thickness_mm, "
                        "fillet_radius_mm, E_gpa, nu, rho, Sy_mpa, max_mass_kg, "
                        "max_iter. Only include keys that change."
                    ),
                },
            },
            "required": [],
        },
    },
    {
        "name": "read_last_results",
        "description": (
            "Read results from the most recent (or a specified) pipeline run "
            "without triggering a new simulation. Use when the user asks about "
            "existing results, wants to revisit a prior design, or starts a new "
            "session after a completed run."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "iter_dir": {
                    "type": "string",
                    "description": (
                        "Optional specific run directory to read, e.g. 'runs/iter_002'. "
                        "Omit to read the highest-numbered iter directory."
                    ),
                },
            },
            "required": [],
        },
    },
]


def _anthropic_tools() -> list:
    """Convert _TOOL_DEFS to Anthropic tool format (input_schema)."""
    return [
        {
            "name": t["name"],
            "description": t["description"],
            "input_schema": t["parameters"],
        }
        for t in _TOOL_DEFS
    ]


def _openai_tools() -> list:
    """Convert _TOOL_DEFS to OpenAI tool format (type + function wrapper)."""
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["parameters"],
            },
        }
        for t in _TOOL_DEFS
    ]


# ---------------------------------------------------------------------------
# Tool dispatcher (shared)
# ---------------------------------------------------------------------------

def dispatch_tool(name: str, tool_input: dict) -> dict:
    if name == "run_pipeline":
        return agent_tools.run_pipeline_tool(tool_input)
    elif name == "modify_and_run":
        return agent_tools.modify_and_run_tool(tool_input)
    elif name == "read_last_results":
        return agent_tools.read_last_results_tool(tool_input)
    else:
        return {"error": f"Unknown tool: {name}"}


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _print_banner(backend_label: str) -> None:
    print(f"Bracket FEM Agent  [{backend_label}]  (type 'exit' to stop)")
    print("=" * 55)


def _get_user_input() -> "str | None":
    """Prompt for user input. Returns None on EOF/interrupt or quit command."""
    try:
        text = input("\nYou: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\nGoodbye.")
        return None
    if text.lower() in ("exit", "quit", "q"):
        print("Goodbye.")
        return None
    return text or ""   # empty string → caller skips


# ---------------------------------------------------------------------------
# Anthropic backend
# ---------------------------------------------------------------------------

def _run_anthropic() -> None:
    try:
        import anthropic  # type: ignore[import-untyped]  # deferred: only needed for this backend
    except ImportError:
        print("Error: 'anthropic' package not installed. Run: pip install anthropic")
        sys.exit(1)

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("Error: ANTHROPIC_API_KEY environment variable is not set.")
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)
    tools = _anthropic_tools()
    messages: list = []

    _print_banner("Anthropic / claude-sonnet-4-6")

    while True:
        user_input = _get_user_input()
        if user_input is None:
            break
        if not user_input:
            continue

        messages.append({"role": "user", "content": user_input})

        # Inner agentic loop — Claude may call tools before responding
        while True:
            response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=4096,
                system=SYSTEM_PROMPT,
                tools=tools,
                messages=messages,
            )
            messages.append({"role": "assistant", "content": response.content})

            tool_uses = [b for b in response.content if b.type == "tool_use"]

            if not tool_uses:
                for block in response.content:
                    if hasattr(block, "text") and block.text:
                        print(f"\nAssistant: {block.text}")
                break

            tool_results = [
                {
                    "type": "tool_result",
                    "tool_use_id": tu.id,
                    "content": json.dumps(dispatch_tool(tu.name, tu.input)),
                }
                for tu in tool_uses
            ]
            messages.append({"role": "user", "content": tool_results})


# ---------------------------------------------------------------------------
# OpenAI backend
# ---------------------------------------------------------------------------

def _run_openai() -> None:
    try:
        import openai  # type: ignore[import-untyped]  # deferred: only needed for this backend
    except ImportError:
        print("Error: 'openai' package not installed. Run: pip install openai")
        sys.exit(1)

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("Error: OPENAI_API_KEY environment variable is not set.")
        sys.exit(1)

    model = os.environ.get("OPENAI_MODEL", "gpt-5.2")
    client = openai.OpenAI(api_key=api_key)
    tools = _openai_tools()
    # OpenAI takes system prompt as first message
    messages: list = [{"role": "system", "content": SYSTEM_PROMPT}]

    _print_banner(f"OpenAI / {model}")

    while True:
        user_input = _get_user_input()
        if user_input is None:
            break
        if not user_input:
            continue

        messages.append({"role": "user", "content": user_input})

        # Inner agentic loop
        while True:
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                tools=tools,
            )
            msg = response.choices[0].message

            # Append assistant turn as a plain dict (avoids SDK object serialisation issues)
            assistant_entry: dict = {"role": "assistant", "content": msg.content}
            if msg.tool_calls:
                assistant_entry["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in msg.tool_calls
                ]
            messages.append(assistant_entry)

            if not msg.tool_calls:
                if msg.content:
                    print(f"\nAssistant: {msg.content}")
                break

            # Execute each tool and append results individually (OpenAI format)
            for tc in msg.tool_calls:
                tool_input = json.loads(tc.function.arguments)
                result = dispatch_tool(tc.function.name, tool_input)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(result),
                })


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    backend = os.environ.get("AGENT_BACKEND", "").lower()

    if not backend:
        if os.environ.get("ANTHROPIC_API_KEY"):
            backend = "anthropic"
        elif os.environ.get("OPENAI_API_KEY"):
            backend = "openai"
        else:
            print(
                "Error: no API key found.\n"
                "Set ANTHROPIC_API_KEY or OPENAI_API_KEY, then rerun.\n"
                "To force a specific backend: export AGENT_BACKEND=anthropic|openai"
            )
            sys.exit(1)

    if backend == "anthropic":
        _run_anthropic()
    elif backend == "openai":
        _run_openai()
    else:
        print(f"Error: unknown AGENT_BACKEND={backend!r}. Use 'anthropic' or 'openai'.")
        sys.exit(1)


if __name__ == "__main__":
    main()
