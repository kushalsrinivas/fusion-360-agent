# Fusion AI CAD Agent

Terminal-based **agentic CAD system** for Fusion 360:

```
User → TUI → LangGraph Agent System → Fusion 360 MCP → Fusion 360 → 3D Model
```

LangGraph orchestrates specialized agents (requirements → planning → review →
execution → inspection → repair). The LLM never touches Fusion directly — it
produces *structured requirements* and *validated CAD plans*; a tool registry
maps every operation onto discovered MCP tools with schema-checked arguments,
and a model inspector verifies the live document before anything is called
"done".

## Quick start

```bash
python -m venv .venv && .venv/bin/pip install -e ".[dev]"

# Works immediately — simulated Fusion document, no API key needed:
.venv/bin/fusion-ai

# Or one-shot headless:
.venv/bin/python -m app.main --headless "Create a phone stand 80mm wide with rounded edges"

# Exercise the repair loop (mock injects one deterministic failure):
.venv/bin/python -m app.main --simulate-failures --headless "Create a simple phone stand"
```

With a `GEMINI_API_KEY` (or `OPENAI_API_KEY`) in the environment / `.env`,
the requirement, planner, and repair agents use LLM structured output
(**Gemini is the default provider**, via its OpenAI-compatible endpoint,
model `gemini-2.5-flash`); without a key they fall back to deterministic
heuristics so the pipeline is always runnable.

## Connecting to real Fusion 360

Uses the [faust-machines fusion360-mcp-server](https://github.com/faust-machines/fusion360-mcp-server):

1. **Install their Fusion add-in** (one-time):
   ```bash
   git clone https://github.com/faust-machines/fusion360-mcp-server.git
   cd fusion360-mcp-server && ./scripts/install-addon.sh
   ```
   Then in Fusion: **Shift+S → Add-Ins → Fusion360MCP → Run** (you should see
   `[MCP] Server listening on localhost:9876`).

2. **Run the agent** (`uvx` required — the server is pulled from PyPI; we pin
   `mcp==1.16.0` since newer SDKs break its low-level API):
   ```bash
   export GEMINI_API_KEY=... 
   FUSION_AI_MODE=faust fusion-ai          # socket mode -> live Fusion
   # or without Fusion installed, real protocol + their mock data:
   fusion-ai --mode faust-mock
   ```

The app speaks millimeters semantically; the built-in **Faust adapter**
(`app/fusion/faust_adapter.py`) converts every operation to their cm-based
tool signatures, maps `cut_extrude` → `extrude(operation="cut")`, composes
inspection from `get_scene_info` + per-body `get_bounding_box`, and skips
operations that can't be represented safely rather than guessing.

Generic bridges still work for other MCP servers: `--mode stdio
--mcp-command "..."` or `--mode http --mcp-url ...`.

## Configuration (env)

| Variable | Default | Purpose |
|---|---|---|
| `OPENAI_API_KEY` / `OPENAI_BASE_URL` / `FUSION_AI_MODEL` | — / OpenAI / `gpt-4o-mini` | LLM backend (any OpenAI-compatible API) |
| `FUSION_AI_MODE` | `mock` | `mock` \| `stdio` \| `http` |
| `FUSION_MCP_COMMAND` / `FUSION_MCP_URL` | — | stdio command or HTTP URL |
| `FUSION_AI_MAX_REPAIRS` | `3` | bounded repair attempts |
| `FUSION_AI_MAX_PLAN_STEPS` | `40` | plan size cap |
| `FUSION_AI_SIMULATE_FAILURES` | `0` | mock: inject one failure |
| `FUSION_AI_DATA_DIR` | `~/.fusion-ai-agent` | sessions + JSONL run history |

## Architecture

```
app/
├── main.py                  CLI: TUI + headless modes
├── tui/                     Textual UI: status bar, pipeline panel, activity log,
│                            slash commands, HITL approval prompts
├── graph/
│   ├── graph.py             LangGraph state machine assembly
│   ├── state.py             AgentState (typed, accumulating reducers)
│   ├── routing.py           conditional edges incl. bounded repair loop
│   ├── approvals.py         process-wide HITL gate (threading events)
│   ├── llm.py               OpenAI-compatible structured-output client
│   └── nodes/
│       ├── requirements.py  NL → structured CADRequirements (+ follow-up merge)
│       ├── planner.py       requirements → parametric CADPlan (LLM or template)
│       ├── review.py        static plan sanity checks
│       ├── executor.py      sequential MCP execution, never continues past failure
│       ├── inspector.py     verifies live document vs requirements
│       ├── repair.py        bounded diagnosis + corrective steps
│       └── finish.py        honest summary: planned vs executed vs verified
├── fusion/
│   ├── mcp_client.py        MockFusionClient / StdioMCPClient / HttpMCPClient
│   ├── tool_registry.py     op→tool mapping + argument validation
│   └── executor.py          step runner with approval gating
├── models/                  pydantic: requirements, cad_plan, events (+ EventBus)
└── storage/                 session store + JSONL execution history
```

### Safety properties

- Planner may only emit whitelisted semantic operations (`KNOWN_OPERATIONS`);
  destructive ops (`delete_body`) require explicit `/approve`.
- Tool arguments are validated/stripped against each tool's declared schema.
- Execution halts on first failed step; remaining steps are skipped.
- Success is only claimed when the inspector confirms geometry via MCP.
- Repair loops are bounded; exhaustion surfaces the failure honestly.

## TUI commands

Anything typed is treated as a conversational CAD request (context is kept
across turns — *"make the base 20mm wider"* just works). Slash commands:

```
/approve /reject   answer a pending destructive-op approval
/retry             re-run last request        /undo    revert design revision
/status            show current requirements  /model   latest inspection report
/history           persisted runs             /help    all commands
```

## Tests

```bash
.venv/bin/python -m pytest tests -q
```

Covers extraction heuristics, template plans, registry validation, mock-bridge
behavior, end-to-end offline graph runs, repair-loop recovery, follow-up
context retention, and the approval gate.

## Extension points

The operation vocabulary, node set, and event bus are designed for later
addition of parallel design alternatives, manufacturability/3D-printability
analysis nodes, export/BOM/drawing steps, versioning, and image→CAD inputs.
