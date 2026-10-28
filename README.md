# Fusion AI CAD Agent

[![CI](https://github.com/kushalsrinivas/fusion-360-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/kushalsrinivas/fusion-360-agent/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)

Terminal-based **agentic CAD system** for Autodesk Fusion 360:

```
User → TUI → LangGraph Agent System → Fusion 360 MCP → Fusion 360 → 3D Model
```

LangGraph orchestrates specialized agents (requirements → planning → review →
execution → inspection → repair). The LLM never touches Fusion directly — it
produces *structured requirements* and *validated CAD plans*; a tool registry
maps every operation onto discovered MCP tools with schema-checked arguments,
and a model inspector verifies the live document before anything is called
"done".

> **Status: alpha.** The offline mock pipeline is stable; the live Fusion
> bridge depends on the upstream `fusion360-mcp-server` and its add-in.

## Features

- **Conversational CAD** — describe a part in millimeters; the agent extracts
  requirements, plans operations, executes them, and verifies the result.
- **Model-agnostic LLM layer** — Gemini by default, any OpenAI-compatible
  endpoint via `OPENAI_API_KEY` + `OPENAI_BASE_URL`. Without a key the
  pipeline falls back to deterministic heuristics.
- **Runs without Fusion** — a built-in mock document and the upstream
  server's `faust-mock` mode make the full pipeline testable offline.
- **Safety first** — whitelisted operations, schema validation, destructive
  operation approval, halt-on-first-failure, and bounded repair loops.
- **Two interfaces** — a Textual TUI with slash commands, and a headless
  one-shot mode for scripts and CI.
- **Honest reporting** — success is only claimed after MCP inspection
  confirms the geometry.

## Requirements

- Python 3.10+.
- For live modeling: Autodesk Fusion 360, the
  [Fusion360MCP add-in](https://github.com/faust-machines/fusion360-mcp-server),
  and [`uv`](https://docs.astral.sh/uv/) (the agent launches the server with
  `uvx`).
- Optional: a Gemini or OpenAI-compatible API key for LLM-backed planning.

## Quick start

```bash
git clone https://github.com/kushalsrinivas/fusion-360-agent.git
cd fusion-360-agent
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

Run the built-in mock (no Fusion, no API key):

```bash
python -m app.main --headless "Create a phone stand 80mm wide with rounded edges"
python -m app.main               # interactive TUI
```

Exercise the repair loop (the mock injects one deterministic failure):

```bash
python -m app.main --simulate-failures --headless "Create a simple phone stand"
```

To enable LLM planning, copy `.env.example` to `.env` and set
`GEMINI_API_KEY` (or `OPENAI_API_KEY`). Without a key the deterministic
heuristics are used. The default example mode is `mock`.

## Connecting to real Fusion 360

Uses the [faust-machines fusion360-mcp-server](https://github.com/faust-machines/fusion360-mcp-server):

1. **Install their Fusion add-in** (one-time):
   ```bash
   git clone https://github.com/faust-machines/fusion360-mcp-server.git
   cd fusion360-mcp-server && ./scripts/install-addon.sh
   ```
   Then in Fusion: **Shift+S → Add-Ins → Fusion360MCP → Run** (you should see
   `[MCP] Server listening on localhost:9876`).

2. **Run the agent** (`uvx` required — the server is pulled from PyPI; the
   agent pins `mcp==1.30.0` because 1.17.x breaks the server's low-level API):
   ```bash
   export GEMINI_API_KEY=...
   FUSION_AI_MODE=faust fusion-ai          # socket mode -> live Fusion
   # or without Fusion installed, real protocol + their mock data:
   fusion-ai --mode faust-mock
   ```

   On Windows PowerShell:
   ```powershell
   $env:GEMINI_API_KEY = "..."
   $env:FUSION_AI_MODE = "faust"
   .venv\Scripts\fusion-ai.exe
   ```

The app speaks millimeters semantically; the built-in **Faust adapter**
(`app/fusion/faust_adapter.py`) converts every operation to their cm-based
tool signatures, maps `cut_extrude` → `extrude(operation="cut")`, composes
inspection from `get_scene_info` + per-body `get_bounding_box`, and skips
operations that can't be represented safely rather than guessing.

Generic bridges still work for other MCP servers: `--mode stdio
--mcp-command "..."` or `--mode http --mcp-url ...`.

### Troubleshooting `Connection closed`

A `Connection closed` error during startup means the MCP server subprocess
exited before the handshake. Run its command manually to see the real error:

```bash
uvx --link-mode copy --with mcp==1.30.0 fusion360-mcp-server --mode socket
```

Two known causes are a stale `mcp` pin (the server needs `mcp==1.30.0` with
current pydantic) and the `--host` flag, which exists only on the upstream
development branch, not in the published 0.1.0 package. Fusion on the same
machine should use the default localhost connection.

## Configuration (env)

Copy `.env.example` to `.env` for local overrides. Environment variables take
precedence.

| Variable | Default | Purpose |
|---|---|---|
| `GEMINI_API_KEY` / `GOOGLE_API_KEY` | — | Gemini API key (default LLM provider) |
| `OPENAI_API_KEY` / `OPENAI_BASE_URL` / `FUSION_AI_MODEL` | — / OpenAI / `gpt-4o-mini` | Any OpenAI-compatible backend |
| `FUSION_AI_MODE` | `mock` | `mock` \| `faust` \| `faust-mock` \| `stdio` \| `http` |
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
  destructive operations require explicit `/approve`.
- Tool arguments are validated/stripped against each tool's declared schema.
- Execution halts on first failed step; remaining steps are skipped.
- Success is only claimed when the inspector confirms geometry via MCP.
- Repair loops are bounded; exhaustion surfaces the failure honestly.
- Live mode can modify an open design — keep Fusion backups enabled and
  review destructive steps before approving them.

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
pytest -q
```

Covers extraction heuristics, template plans, registry validation, mock-bridge
behavior, end-to-end offline graph runs, repair-loop recovery, follow-up
context retention, and the approval gate. CI runs the suite on Linux and
Windows.

## Contributing

Contributions are welcome. Read [CONTRIBUTING.md](CONTRIBUTING.md) for the
development setup, test instructions, and pull-request checklist. Please follow
the [Code of Conduct](CODE_OF_CONDUCT.md).

For security issues, use [SECURITY.md](SECURITY.md) instead of a public issue.
Never commit API keys or `.env` files.

## License

MIT — see [LICENSE](LICENSE). Copyright (c) 2026 Kushal Srinivas.

## Extension points

The operation vocabulary, node set, and event bus are designed for later
addition of parallel design alternatives, manufacturability/3D-printability
analysis nodes, export/BOM/drawing steps, versioning, and image→CAD inputs.
