"""Tool registry: discovers MCP tools and maps plan operations onto them safely.

The planner emits *semantic operations* (``extrude``, ``fillet``, ...). This
registry resolves each one to an actual discovered MCP tool and validates
arguments against the tool's declared JSON schema before any call happens.
Nothing reaches Fusion without passing through here.
"""
from __future__ import annotations

import re
from typing import Any, Optional

from app.models.events import EventBus, EventType
from app.models.cad_plan import KNOWN_OPERATIONS


class ToolResolutionError(Exception):
    pass


class ArgumentValidationError(Exception):
    pass


class ToolRegistry:
    def __init__(self, tools: list[dict[str, Any]], translator=None) -> None:
        self.tools = {t["name"]: t for t in tools}
        self.translator = translator
        self._alias_map = self._build_alias_map()

    @classmethod
    async def discover(cls, bridge, translator=None) -> "ToolRegistry":
        raw = await bridge.list_tools()
        registry = cls(raw, translator=translator)
        EventBus.get().emit(
            "mcp", EventType.STATUS,
            f"Discovered {len(raw)} MCP tool(s)"
            + (f" [profile: {translator.profile}]" if translator else ""),
            {"tools": sorted(registry.tools.keys())},
        )
        return registry

    def _build_alias_map(self) -> dict[str, str]:
        """Map canonical operation names -> actual tool names (fuzzy)."""
        mapping: dict[str, str] = {}
        names = list(self.tools.keys())
        lowered = {n.lower(): n for n in names}
        norm = {re.sub(r"[^a-z]", "", n.lower()): n for n in names}

        # Server-profile explicit mappings take precedence.
        if self.translator is not None:
            for op in KNOWN_OPERATIONS:
                target = self.translator.tool_for(op, self.tools)
                if target and target in self.tools:
                    mapping[op] = target

        for op in KNOWN_OPERATIONS:
            if op in mapping:
                continue
            if op in lowered:
                mapping[op] = lowered[op]
                continue
            op_norm = re.sub(r"[^a-z]", "", op)
            if op_norm in norm:
                mapping[op] = norm[op_norm]
                continue
            candidates = [n for n in names if op in n.lower()]
            if len(candidates) == 1:
                mapping[op] = candidates[0]
        return mapping

    def resolve(self, operation: str) -> Optional[str]:
        return self._alias_map.get(operation)

    def coverage_report(self) -> tuple[list[str], list[str]]:
        supported = sorted(op for op in KNOWN_OPERATIONS if op in self._alias_map)
        missing = sorted(op for op in KNOWN_OPERATIONS if op not in self._alias_map)
        return supported, missing

    def prepare_call(self, operation: str, args: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        """Full pipeline: resolve tool -> translate semantic args -> validate.

        Returns ``(tool_name, clean_args)``. Raises ``ToolResolutionError``,
        ``UnsupportedTranslation``, or ``ArgumentValidationError``.
        """
        tool_name = self.resolve(operation)
        if tool_name is None:
            raise ToolResolutionError(
                f"Operation '{operation}' has no matching MCP tool "
                f"(discovered: {sorted(self.tools.keys())})"
            )
        translated = args
        if self.translator is not None:
            from app.fusion.faust_adapter import UnsupportedTranslation
            try:
                translated = self.translator.translate(operation, dict(args))
            except UnsupportedTranslation:
                raise
        cleaned = self._validate_against_schema(operation, tool_name, translated)
        return tool_name, cleaned

    def validate_args(self, operation: str, args: dict[str, Any]) -> dict[str, Any]:
        """Validate + sanitize args against the resolved tool's input schema.

        Raises ``ToolResolutionError`` / ``ArgumentValidationError``.
        Returns a cleaned copy containing only schema-known keys (plus extras
        the mock-style servers accept, when the schema declares none).
        """
        tool_name = self.resolve(operation)
        if tool_name is None:
            raise ToolResolutionError(
                f"Operation '{operation}' has no matching MCP tool "
                f"(discovered: {sorted(self.tools.keys())})"
            )
        return self._validate_against_schema(operation, tool_name, args)

    def _validate_against_schema(self, operation: str, tool_name: str,
                                 args: dict[str, Any]) -> dict[str, Any]:
        schema = self.tools[tool_name].get("inputSchema") or {}
        properties = schema.get("properties") or {}
        required = set(schema.get("required") or [])

        cleaned: dict[str, Any] = {}
        for key, value in args.items():
            if properties and key not in properties:
                continue  # strip unknown keys rather than forwarding blindly
            cleaned[key] = _coerce(value, (properties.get(key) or {}).get("type"))

        missing = [r for r in required if r not in cleaned]
        if missing:
            raise ArgumentValidationError(
                f"'{operation}' -> {tool_name}: missing required argument(s) {missing}"
            )
        return cleaned


def _coerce(value: Any, json_type: Optional[str]) -> Any:
    if json_type == "number":
        try:
            return float(value)
        except (TypeError, ValueError):
            return value
    if json_type == "integer":
        try:
            return int(value)
        except (TypeError, ValueError):
            return value
    if json_type == "string":
        return value if isinstance(value, str) else str(value)
    if json_type == "boolean":
        return bool(value)
    return value
