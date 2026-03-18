# Plan: Agentic AI Bracket Design Pipeline

## Context

The bracket FEM pipeline (`pipeline.run()`) is a stable, tested tool that takes a
structured plain-text brief and produces optimized geometry + FEM results. The next
step is to make it conversationally accessible: users describe requirements in plain
English, the agent translates them into a brief, runs the pipeline, and explains
results — without the user ever needing to write a brief.txt manually.

This is implemented as a thin new layer (`agent.py` + `agent_tools.py`) on top of
the existing pipeline. **No existing file is modified.**

---

## Architecture

Claude (`claude-sonnet-4-6`) acts as the orchestrator via the Anthropic tool-use API.
Python runs a chat loop that sends messages to Claude with three tool definitions.
Claude decides when to call tools; Python dispatches them and feeds results back.

```
User (plain English)
    │
    ▼
agent.py  ─── Anthropic API (claude-sonnet-4-6) ──► Claude LLM
    │              ↑  tool_use calls
    ▼              │
dispatch_tool()
    │
    ├─ run_pipeline        → agent_tools.run_pipeline_tool()   → pipeline.run()
    ├─ modify_and_run      → agent_tools.modify_and_run_tool() → pipeline.run()
    └─ read_last_results   → agent_tools.read_last_results_tool() → reads runs/
```

---

## Files to Create

| File | Purpose | ~Lines |
|------|---------|--------|
| `agent.py` | CLI entry point: system prompt, tool schemas, chat loop | ~150 |
| `agent_tools.py` | Tool implementations + session state + brief builder | ~200 |

No existing files are modified.

### `agent_tools.py` — key functions

```python
_session_state = {           # in-process state, not persisted to disk
    "last_params_mm": None,  # geometry from last run
    "last_load_n":    None,  # load magnitude from last run
    "last_material":  None,  # material dict {E_pa, nu, rho, Sy_pa} — always complete
    "last_run_dir":   None,  # Path("runs/iter_NNN")
}

def build_brief(params: dict) -> str           # renders brief.txt format string
def run_pipeline_tool(inp: dict) -> dict       # backs the run_pipeline tool
def modify_and_run_tool(inp: dict) -> dict     # backs the modify_and_run tool
def read_last_results_tool(inp: dict) -> dict  # backs the read_last_results tool
```

The brief string is constructed internally and never shown to the user. It exactly
matches `parse_brief()`'s expected format (validated against `brief.txt`).

**Material defaults are always fully resolved in `build_brief()`** — all four material
fields (E, nu, rho, Sy) are explicitly set to structural steel values before the brief
is rendered. Pipeline fallbacks are never relied upon.

---

## Three Tools

### 1. `run_pipeline`
Builds a brief from tool inputs and calls `pipeline.run()`.

**Required:** `load_n` (N)

**Optional:** `flange_width_mm`, `flange_height_mm`, `web_height_mm`,
`thickness_mm`, `fillet_radius_mm`, `E_gpa`, `nu`, `rho`, `Sy_mpa`,
`max_mass_kg`, `max_iter`

**Defaults when omitted:** 80/60/100/6/4 mm geometry; 200 GPa / 0.3 / 7850 / 250 MPa
material. Starting thin (`thickness=6mm`) is intentional — optimizer increases if needed.

Prints `[Running FEM pipeline — this may take 2-5 minutes...]` to stdout before
calling `pipeline.run()`. Updates `_session_state` on completion.

**Returns structured dict:**
```json
{
  "status": "PASS",
  "iterations_run": 3,
  "mass_kg": 0.712,
  "fos": 1.62,
  "max_vm_mpa": 154.3,
  "max_disp_mm": 0.31,
  "stress_utilisation_pct": 61.7,
  "violations": [],
  "final_params_mm": { "flange_width": 80.0, "thickness": 9.65, "..." : "..." },
  "output_dir": "runs/iter_003"
}
```

### 2. `modify_and_run`
Merges a `changes` dict over the current session state and reruns.
Handles "make it lighter", "what if the load is 3000N?", "increase web height".

**Required:** `changes` (dict with any subset of the same keys as `run_pipeline`)

Loads prior geometry/load/material from `_session_state`; merges `changes`; calls
`build_brief()`; calls `pipeline.run()`. Returns same structured dict.

### 3. `read_last_results`
Reads the highest-numbered `runs/iter_NNN/params.json` without re-running.
Enables follow-up questions about a completed run without re-executing the pipeline.

**Optional:** `iter_dir` — specific path (e.g. `"runs/iter_002"`), otherwise reads latest.

**Returns extended dict:**
```json
{
  "status": "PASS",
  "iterations_run": 3,
  "mass_kg": 0.712,
  "fos": 1.62,
  "max_vm_mpa": 154.3,
  "max_disp_mm": 0.31,
  "stress_utilisation_pct": 61.7,
  "violations": [],
  "final_params_mm": { "..." : "..." },
  "output_dir": "runs/iter_003",
  "source": "cached",
  "timestamp": "2026-03-09T14:32:11",
  "used_defaults": []
}
```
`source: "cached"` prevents Claude from confusing a read with a new run.

---

## System Prompt Outline

1. **Role**: Structural engineering assistant for L-bracket design via FEM.
2. **Pipeline knowledge**: 5 geometry params + optimizer ranges, default steel
   material, three active constraints (stress ≤ Sy/1.5, FOS ≥ 1.5, disp ≤ 5mm),
   optional mass budget.
3. **Tool rules**:
   - Call `run_pipeline` only when load magnitude is known; use defaults for everything else.
   - Ask at most two clarifying questions before using defaults.
   - Warn the user a pipeline call will take ~2-5 minutes before invoking it.
   - **Never call `modify_and_run` before a successful `run_pipeline` in this session.**
   - Use `modify_and_run` for incremental changes; use `read_last_results` for prior-run questions.
   - If a tool returns `{"error": "..."}`, ask one targeted recovery question before retrying.
   - When the user asks "what changed?", summarize parameter deltas vs the previous run.
4. **Explanation style**: Always report pass/fail, mass in grams, FOS, peak stress as
   % of yield, deflection in mm, output directory. If failed, explain the physics
   ("the section is too thin for the applied load").

---

## Conversation Flow

```
User: "I need a bracket to hold 2000N on a 100mm arm, keep it light"
  → Claude: "Do you have a mass budget, or should I minimize without a limit?"
User: "No hard limit, just minimize"
  → Claude calls run_pipeline({load_n: 2000, web_height_mm: 100})
  → [Running FEM pipeline — this may take 2-5 minutes...]
  → Claude: "Passed on iteration 3 — 712 g, FOS 1.62, 154 MPa (62% of yield),
             0.31 mm deflection. Results in runs/iter_003/"

User: "What if the load is 3000N?"
  → Claude calls modify_and_run({changes: {load_n: 3000}})

User: "What was the stress in the last run?"
  → Claude calls read_last_results()  — no new pipeline run
```

---

## Chat Loop (`agent.py`)

```python
# Pseudocode
while True:                              # outer: one user message at a time
    user_input = input("You: ")
    messages.append({"role": "user", "content": user_input})

    while True:                          # inner: agentic loop until no tool calls
        response = client.messages.create(
            model="claude-sonnet-4-6",
            system=SYSTEM_PROMPT,
            tools=TOOL_DEFINITIONS,
            messages=messages,
        )
        messages.append({"role": "assistant", "content": response.content})

        tool_uses = [b for b in response.content if b.type == "tool_use"]
        if not tool_uses:
            print(f"\nAgent: {response.content[0].text}\n")
            break                        # wait for next user message

        results = [dispatch_tool(t.name, t.input) for t in tool_uses]
        messages.append({"role": "user", "content": results})
        # Claude now interprets results; may call another tool or reply
```

Full message history sent every turn — Claude remembers the entire design session.
Synchronous, no streaming. `ANTHROPIC_API_KEY` read from environment.

---

## Error Handling

| Scenario | Handling |
|----------|---------|
| `GeometryError / MeshError / SimulationError` | Caught in tool fn; returned as `{"error": "..."}`. Claude explains failure in plain English. |
| `runs/` empty on `read_last_results` | Returns `{"error": "No runs found. Please run the pipeline first."}` |
| `ANTHROPIC_API_KEY` not set | Caught at startup; prints clear message and exits |
| Invalid geometry params | Handled inside existing `propose_params()` — agent layer does not duplicate validation |
| Pipeline timeout | `run_pipeline_tool` wraps `pipeline.run()` in a `threading.Timer` with a 900 s hard cap; returns `{"error": "Pipeline timed out after 900s"}` on expiry |

---

## Known Limitations

- **Run namespacing**: `pipeline.run()` writes `runs/iter_001/`, `iter_002/`, etc.
  Repeated agent sessions share the same namespace and will overwrite prior runs.
  Fixing this requires modifying `pipeline.py` (out of scope for this layer).
  **Workaround**: Manually rename or archive `runs/` between sessions if preservation
  is needed. Document this in the agent's startup message.

- **No mid-run heartbeat**: `pipeline.run()` is a blocking call; there is no progress
  reporting between the start message and the result. Streaming intermediate solver
  state would require threading and `pipeline.py` callbacks — post-MVP.

---

## What NOT to Add

- No `modify_geometry` tool — would allow geometry creation without FEM (inconsistent state)
- No `propose_params` tool — optimizer is internal to pipeline; exposing it risks conflicting logic
- No streaming for pipeline execution — progress line + synchronous wait is sufficient
- No `read_summary_md` tool — structured dict is more reliable for Claude to interpret than markdown
- No modification to any existing file

---

## Critical Reference Files

| File | Used for |
|------|---------|
| `pipeline.py` | `run(brief_text, max_iter)` signature; `parse_brief()` format contract |
| `brief.txt` | Canonical template; all key names/section headers in `build_brief()` must match |
| `optimizer.py` | `PARAM_BOUNDS` for valid geometry ranges |
| `constraints.py` | `CONSTRAINTS` defaults for system prompt and result explanation |

---

## Verification

```bash
# 1. Import check — no external tools needed
python -c "import agent, agent_tools"

# 2. Brief builder round-trip — no pipeline needed
python -c "
from agent_tools import build_brief
from pipeline import parse_brief
brief = build_brief({'load_n': 2000, 'flange_width_mm': 80, 'web_height_mm': 100,
                     'thickness_mm': 6, 'flange_height_mm': 60, 'fillet_radius_mm': 4,
                     'E_gpa': 200, 'nu': 0.3, 'rho': 7850, 'Sy_mpa': 250})
params, constraints = parse_brief(brief)
assert params['loads']['magnitude_n'] == 2000
print('brief builder OK')
"

# 3. Error path — read_last_results with no prior runs
python -c "
from agent_tools import read_last_results_tool
import shutil, pathlib
# Temporarily hide runs dir
p = pathlib.Path('runs')
p.rename('runs_bak')
try:
    result = read_last_results_tool({})
    assert 'error' in result, 'Expected error dict'
    print('error path OK:', result['error'])
finally:
    pathlib.Path('runs_bak').rename('runs')
"

# 4. Full end-to-end (requires FreeCAD + gmsh + ccx + ANTHROPIC_API_KEY)
python agent.py
# Prompt: "Design a bracket to hold 500N, keep it light"
# Expected: pipeline runs, results explained in plain language
```
