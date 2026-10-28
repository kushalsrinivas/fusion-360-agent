# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- Pin the `fusion360-mcp-server` runtime to `mcp==1.30.0`. The server's
  low-level API breaks on `mcp` 1.17.x, while `mcp` 1.16.0 fails against
  pydantic 2.12+ (`eval_type_backport` import error), which made the MCP
  subprocess exit before the handshake and surface as `Connection closed`.
- Remove the unsupported `--host` flag from the documented server command.
  The published `fusion360-mcp-server` 0.1.0 CLI only accepts `--mode` and
  `--port`; the flag exists only on the upstream development branch.

## [0.1.0] - 2026-10-28

### Added

- LangGraph pipeline: requirements → planner → review → executor → inspector →
  bounded repair → finish.
- Terminal UI (Textual) and non-interactive `--headless` mode.
- Fusion bridge modes: built-in `mock`, `faust` (live Fusion 360 via the
  Fusion360MCP add-in), `faust-mock`, generic `stdio`, and `http`.
- Faust adapter that converts millimeter-based operations to the server's
  centimeter-based tool signatures.
- Tool registry with per-tool argument validation, destructive-operation
  approval gating, and halt-on-first-failure execution.
- Model inspection and verification against the requested requirements.
- Session storage and JSONL run history.
