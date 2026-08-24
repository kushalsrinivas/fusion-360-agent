"""Fusion 360 bridge layer.

Three implementations of the same interface:
- MockFusionClient : simulated Fusion document (default; no Fusion required)
- StdioMCPClient   : launches an MCP server subprocess
- HttpMCPClient    : connects to a streamable-HTTP MCP endpoint

The rest of the system only ever talks to ``FusionBridge``.
"""
from __future__ import annotations

import asyncio
import math
from typing import Any, Optional, Protocol

from app.config import Config, FAUST_COMMAND_MOCK, FAUST_COMMAND_SOCKET
from app.models.events import EventBus, EventType


class BridgeError(Exception):
    pass


class FusionBridge(Protocol):
    mode: str
    profile: Optional[str]  # server adapter profile, e.g. "faust"

    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...
    async def list_tools(self) -> list[dict[str, Any]]: ...
    async def call_tool(self, name: str, args: dict[str, Any]) -> dict[str, Any]: ...
    async def inspect_document(self) -> dict[str, Any]: ...


# --------------------------------------------------------------------------
# Mock bridge — a tiny in-memory "Fusion document" so the whole agent pipeline
# is runnable and testable without Fusion 360 running.
# --------------------------------------------------------------------------

class MockFusionClient:
    """Simulated Fusion 360 MCP server backed by a simple document model."""

    mode = "mock"
    profile = None

    TOOLS: dict[str, dict[str, Any]] = {
        "create_component": {"desc": "Create a new component", "required": ["name"]},
        "create_sketch": {"desc": "Create a sketch on a plane", "required": ["name", "plane"]},
        "draw_rectangle": {"desc": "Draw a rectangle", "required": ["sketch", "width_mm", "depth_mm"]},
        "draw_circle": {"desc": "Draw a circle", "required": ["sketch", "diameter_mm", "x_mm", "y_mm"]},
        "add_dimension": {"desc": "Add a driving dimension to a sketch", "required": ["sketch", "name", "value"]},
        "add_constraint": {"desc": "Add a constraint", "required": ["sketch", "type"]},
        "extrude": {"desc": "Extrude a sketch profile into a body", "required": ["sketch", "distance_mm", "body_name"]},
        "cut_extrude": {"desc": "Cut through a body using a profile", "required": ["sketch", "distance_mm"]},
        "fillet": {"desc": "Apply fillets to edges", "required": ["body_name", "radius_mm"]},
        "chamfer": {"desc": "Apply chamfer", "required": ["body_name", "distance_mm"]},
        "rectangular_pattern": {"desc": "Pattern a feature", "required": ["count_x", "spacing_mm"]},
        "delete_body": {"desc": "Delete a body (destructive)", "required": ["body_name"]},
        "inspect_model": {"desc": "Inspect current document state", "required": []},
        "export_model": {"desc": "Export model to STL/STEP", "required": ["path", "format"]},
    }

    def __init__(self, simulate_failures: bool = False) -> None:
        self.simulate_failures = simulate_failures
        self.connected = False
        self._call_count = 0
        self.doc: dict[str, Any] = {
            "components": [],
            "sketches": {},
            "bodies": {},       # name -> {volume_mm3, bbox}
            "dimensions": {},
            "constraints": [],
        }
        self.failure_injected = False

    async def connect(self) -> None:
        self.connected = True
        EventBus.get().emit("fusion-mock", EventType.STATUS, "Mock Fusion document initialized")

    async def disconnect(self) -> None:
        self.connected = False

    async def list_tools(self) -> list[dict[str, Any]]:
        return [
            {"name": name, "description": spec["desc"],
             "inputSchema": {"type": "object", "required": spec["required"], "properties": {}}}
            for name, spec in self.TOOLS.items()
        ]

    async def call_tool(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        if not self.connected:
            raise BridgeError("Mock Fusion not connected")
        if name not in self.TOOLS:
            return {"success": False, "error": f"Unknown tool '{name}'"}
        missing = [r for r in self.TOOLS[name]["required"] if r not in args]
        if missing:
            return {"success": False, "error": f"Missing required args: {missing}"}

        self._call_count += 1
        # Deterministically fail one mid-plan extrude once, to exercise repair loop.
        if (
            self.simulate_failures
            and name == "extrude"
            and not self.failure_injected
            and self._call_count >= 3
        ):
            self.failure_injected = True
            return {"success": False, "error": "Fusion compute error: profile not fully constrained"}

        handler = getattr(self, f"_op_{name}", None)
        if handler is None:
            return {"success": True, "note": f"{name} recorded"}
        result = handler(**args)
        return {"success": True, **(result or {})}

    # ---- operation handlers -------------------------------------------

    def _op_create_component(self, name: str, **_: Any) -> dict[str, Any]:
        if name not in self.doc["components"]:
            self.doc["components"].append(name)
        return {"component": name}

    def _op_create_sketch(self, name: str, plane: str = "XY", **_: Any) -> dict[str, Any]:
        self.doc["sketches"][name] = {"plane": plane, "entities": [], "fully_constrained": False}
        return {"sketch": name}

    def _op_draw_rectangle(self, sketch: str, width_mm: float, depth_mm: float, **_: Any) -> dict[str, Any]:
        sk = self._sketch(sketch)
        sk["entities"].append({"type": "rectangle", "width_mm": float(width_mm), "depth_mm": float(depth_mm)})
        sk["fully_constrained"] = True
        return {"sketch": sketch, "rectangle": [width_mm, depth_mm]}

    def _op_draw_circle(self, sketch: str, diameter_mm: float, x_mm: float = 0.0, y_mm: float = 0.0,
                        **_: Any) -> dict[str, Any]:
        sk = self._sketch(sketch)
        sk["entities"].append({"type": "circle", "diameter_mm": float(diameter_mm),
                               "x_mm": float(x_mm), "y_mm": float(y_mm)})
        return {"sketch": sketch, "diameter_mm": diameter_mm}

    def _op_add_dimension(self, sketch: str, name: str, value: float, **_: Any) -> dict[str, Any]:
        self.doc["dimensions"][f"{sketch}.{name}"] = float(value)
        sk = self._sketch(sketch)
        sk["fully_constrained"] = True
        return {"dimension": f"{sketch}.{name}", "value": float(value)}

    def _op_add_constraint(self, sketch: str, type: str = "coincident", **_: Any) -> dict[str, Any]:  # noqa: A002
        self.doc["constraints"].append({"sketch": sketch, "type": type})
        sk = self._sketch(sketch)
        sk["fully_constrained"] = True
        return {"constraint": type}

    def _op_extrude(self, sketch: str, distance_mm: float, body_name: str, **_: Any) -> dict[str, Any]:
        sk = self.doc["sketches"].get(sketch)
        if sk is None:
            return {"success": False, "error": f"Sketch '{sketch}' does not exist"}
        rects = [e for e in sk["entities"] if e["type"] == "rectangle"]
        circles = [e for e in sk["entities"] if e["type"] == "circle"]
        area = 0.0
        if rects:
            r = rects[-1]
            area = r["width_mm"] * r["depth_mm"]
            w, d = r["width_mm"], r["depth_mm"]
        elif circles:
            c = circles[-1]
            area = math.pi * (c["diameter_mm"] / 2) ** 2
            w = d = c["diameter_mm"]
        else:
            return {"success": False, "error": f"Sketch '{sketch}' has no closed profile"}
        holes_area = sum(math.pi * (c["diameter_mm"] / 2) ** 2 for c in circles)
        volume = max(area - holes_area, 0.0) * float(distance_mm)
        # Cut-through holes reduce effective width/depth reporting slightly;
        # simulate a real-world tolerance error when slot cut present.
        self.doc["bodies"][body_name] = {
            "volume_mm3": round(volume, 3),
            "bbox": {"w": round(w - 0.1 * len(circles), 2), "d": round(d, 2), "h": round(float(distance_mm), 2)},
            "source_sketch": sketch,
        }
        return {"body": body_name, "volume_mm3": round(volume, 3)}

    def _op_cut_extrude(self, sketch: str, distance_mm: float, target_body: str | None = None,
                        **_: Any) -> dict[str, Any]:
        sk = self.doc["sketches"].get(sketch)
        if sk is None:
            return {"success": False, "error": f"Sketch '{sketch}' does not exist"}
        target = target_body or (list(self.doc["bodies"])[-1] if self.doc["bodies"] else None)
        if target is None or target not in self.doc["bodies"]:
            return {"success": False, "error": "No target body to cut"}
        circles = [e for e in sk["entities"] if e["type"] == "circle"]
        rects = [e for e in sk["entities"] if e["type"] == "rectangle"]
        removed = sum(math.pi * (c["diameter_mm"] / 2) ** 2 for c in circles)
        if rects:
            r = rects[-1]
            removed += r["width_mm"] * r["depth_mm"]
        body = self.doc["bodies"][target]
        body["volume_mm3"] = round(max(body["volume_mm3"] - removed * float(distance_mm), 0.0), 3)
        body["cuts"] = body.get("cuts", 0) + len(circles) + len(rects)
        # Simulated manufacturing tolerance drift on slot-like cuts
        # (small enough to stay inside the inspector's ±0.5 mm tolerance).
        drift = round(min(removed / 5000.0, 0.2), 3)
        body["last_cut_width"] = round((rects[0]["width_mm"] if rects else (circles[0]["diameter_mm"] if circles else 0)) + drift, 2)
        return {"cut": sketch, "target": target, "effective_width": body["last_cut_width"]}

    def _op_fillet(self, body_name: str, radius_mm: float, **_: Any) -> dict[str, Any]:
        body = self.doc["bodies"].get(body_name)
        if body is None:
            return {"success": False, "error": f"Body '{body_name}' does not exist"}
        body.setdefault("fillets", []).append(radius_mm)
        return {"body": body_name, "fillet_radius_mm": radius_mm}

    def _op_chamfer(self, body_name: str, distance_mm: float, **_: Any) -> dict[str, Any]:
        body = self.doc["bodies"].get(body_name)
        if body is None:
            return {"success": False, "error": f"Body '{body_name}' does not exist"}
        body.setdefault("chamfers", []).append(distance_mm)
        return {"body": body_name, "chamfer_mm": distance_mm}

    def _op_rectangular_pattern(self, count_x: int, spacing_mm: float, **_: Any) -> dict[str, Any]:
        return {"pattern_count": count_x, "spacing_mm": spacing_mm}

    def _op_delete_body(self, body_name: str, **_: Any) -> dict[str, Any]:
        if body_name not in self.doc["bodies"]:
            return {"success": False, "error": f"Body '{body_name}' does not exist"}
        del self.doc["bodies"][body_name]
        return {"deleted": body_name}

    def _op_inspect_model(self, **_: Any) -> dict[str, Any]:
        return {
            "components": list(self.doc["components"]),
            "sketch_count": len(self.doc["sketches"]),
            "bodies": {
                n: {"volume_mm3": b["volume_mm3"], "bbox": b["bbox"],
                    "cuts": b.get("cuts", 0), "last_cut_width": b.get("last_cut_width"),
                    "fillets": b.get("fillets", [])}
                for n, b in self.doc["bodies"].items()
            },
            "dimension_count": len(self.doc["dimensions"]),
            "constraint_count": len(self.doc["constraints"]),
        }

    async def inspect_document(self) -> dict[str, Any]:
        """Normalized document summary (same shape as _op_inspect_model)."""
        result = await self.call_tool("inspect_model", {})
        if not result.get("success", False):
            raise BridgeError(str(result.get("error", "inspect failed")))
        return {k: v for k, v in result.items() if k != "success"}

    def _op_export_model(self, path: str, format: str = "stl", **_: Any) -> dict[str, Any]:  # noqa: A002
        return {"path": path, "format": format, "written": True}

    def _sketch(self, name: str) -> dict[str, Any]:
        sk = self.doc["sketches"].get(name)
        if sk is None:
            sk = self.doc["sketches"][name] = {"plane": "XY", "entities": [], "fully_constrained": False}
        return sk


# --------------------------------------------------------------------------
# Real MCP transports
# --------------------------------------------------------------------------

class StdioMCPClient:
    """Connects to a Fusion MCP server launched as a subprocess."""

    mode = "stdio"
    profile = None  # set to "faust" by create_bridge for faust modes

    def __init__(self, command: str) -> None:
        parts = command.split()
        if not parts:
            raise BridgeError("FUSION_MCP_COMMAND is empty")
        self._cmd, self._args = parts[0], parts[1:]
        self._session: Any = None
        self._ctx: Any = None
        self.config: Any = None  # attached by callers for limits access

    async def connect(self) -> None:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        params = StdioServerParameters(command=self._cmd, args=self._args)
        self._ctx = stdio_client(params)
        read, write = await self._ctx.__aenter__()
        self._session = ClientSession(read, write)
        await self._session.__aenter__()
        await self._session.initialize()

    async def disconnect(self) -> None:
        for obj in (self._session, self._ctx):
            try:
                if obj is not None:
                    await obj.__aexit__(None, None, None)
            except Exception:
                pass
        self._session = self._ctx = None

    async def list_tools(self) -> list[dict[str, Any]]:
        resp = await self._session.list_tools()
        out = []
        for t in resp.tools:
            schema = getattr(t, "inputSchema", None) or getattr(t, "input_schema", None) or {}
            out.append({"name": t.name, "description": t.description or "",
                        "inputSchema": schema})
        return out

    async def call_tool(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        resp = await self._session.call_tool(name, arguments=args)
        payload: dict[str, Any] = {"success": not resp.isError}
        texts: list[str] = []
        structured: Any = None
        for block in resp.content:
            text = getattr(block, "text", None)
            if text:
                texts.append(text)
        if getattr(resp, "structuredContent", None):
            structured = resp.structuredContent
        if isinstance(structured, dict):
            payload.update(structured)
        elif len(texts) == 1:
            payload["output"] = texts[0]
            payload.update(_try_parse_json(texts[0]))
            if not any(k != "output" and k != "success" for k in payload):
                payload.update(_parse_text_payload(texts[0]))
        elif texts:
            payload["output"] = "\n".join(texts)
            payload.update(_parse_text_payload("\n".join(texts)))
        return payload

    async def inspect_document(self) -> dict[str, Any]:
        """Normalized document summary via server-native tools.

        Prefers the faust-machines trio (get_scene_info + get_bounding_box),
        falls back to a generic inspect_model if present.
        """
        tools = {t["name"] for t in await self.list_tools()}

        if "inspect_model" in tools:
            result = await self.call_tool("inspect_model", {})
            if result.get("success"):
                return {k: v for k, v in result.items() if k != "success"}

        if "get_scene_info" not in tools:
            raise BridgeError("Server exposes neither inspect_model nor get_scene_info")

        scene = await self.call_tool("get_scene_info", {})
        if not scene.get("success", False):
            raise BridgeError(str(scene.get("error", "get_scene_info failed")))
        scene_data = {k: v for k, v in scene.items() if k != "success"}
        # unwrap nested payloads like {"scene_info": {...}} / {"output": {...}}
        for key in ("scene_info", "design", "data"):
            inner = scene_data.get(key)
            if isinstance(inner, dict):
                scene_data = {**scene_data, **inner}

        from app.fusion.faust_adapter import parse_scene_info

        bbox_tool = "get_bounding_box" if "get_bounding_box" in tools else None

        async def bbox_fetch(name: str) -> dict[str, Any]:
            if bbox_tool is None:
                raise BridgeError("no bounding box tool")
            raw = await self.call_tool(bbox_tool, {"name": name})
            if not raw.get("success", False):
                raise BridgeError(str(raw.get("error") or raw.get("output") or "bbox failed"))
            if isinstance(raw.get("bounding_box"), dict):
                return {**raw, **raw["bounding_box"]}
            return raw

        summary = await parse_scene_info(scene_data, bbox_fetch)

        # Enrich with volume via physical properties when available.
        if "get_physical_properties" in tools:
            for name, info in summary["bodies"].items():
                for arg_name in ("body_name", "entity_name"):
                    try:
                        props = await self.call_tool("get_physical_properties",
                                                     {arg_name: name})
                        if not props.get("success", True):
                            continue
                        vol_cm3 = (props.get("volume") or {}).get("cm3") \
                            if isinstance(props.get("volume"), dict) else props.get("volume")
                        if vol_cm3:
                            info["volume_mm3"] = round(float(vol_cm3) * 1000.0, 3)
                        break
                    except Exception:
                        continue
        return summary


class HttpMCPClient(StdioMCPClient):
    """Connects to a streamable-HTTP MCP endpoint."""

    mode = "http"

    def __init__(self, url: str) -> None:
        if not url:
            raise BridgeError("FUSION_MCP_URL is empty")
        self._url = url
        self._session = None
        self._ctx = None
        self.config: Any = None

    async def connect(self) -> None:
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        self._ctx = streamablehttp_client(self._url)
        read, write, _ = await self._ctx.__aenter__()
        self._session = ClientSession(read, write)
        await self._session.__aenter__()
        await self._session.initialize()


def _try_parse_json(text: str) -> dict[str, Any]:
    import json

    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _parse_text_payload(text: str) -> dict[str, Any]:
    """Parse the faust-machines text format:

        **get_scene_info** OK
          bodies: ['Body1']
          sketches: ['Sketch1']

    into ``{"bodies": [...], "sketches": [...]}``. Non-literal values are kept
    as strings; the header line is dropped.
    """
    import ast

    out: dict[str, Any] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or ":" not in line or line.startswith("**"):
            continue
        key, _, raw = line.partition(":")
        key = key.strip()
        raw = raw.strip()
        try:
            out[key] = ast.literal_eval(raw)
        except Exception:
            out[key] = raw
    return out


def create_bridge(config: Config) -> FusionBridge:
    mode = config.fusion_mode.lower()
    if mode == "mock":
        return MockFusionClient(simulate_failures=config.simulate_failures)
    if mode in ("faust", "faust-mock", "stdio"):
        if mode == "stdio" and not config.mcp_command:
            raise BridgeError("FUSION_AI_MODE=stdio requires FUSION_MCP_COMMAND")
        if mode == "faust":
            command = config.mcp_command or FAUST_COMMAND_SOCKET
        elif mode == "faust-mock":
            command = config.mcp_command or FAUST_COMMAND_MOCK
        else:
            command = config.mcp_command
        client = StdioMCPClient(command)
        client.profile = "faust"
        return client
    if mode == "http":
        return HttpMCPClient(config.mcp_url)
    raise BridgeError(f"Unknown fusion mode '{config.fusion_mode}' "
                      f"(expected mock|faust|faust-mock|stdio|http)")
