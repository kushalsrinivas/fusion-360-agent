"""Structured CAD requirements extracted from natural language."""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class Dimension(BaseModel):
    name: str                      # e.g. "width", "slot_width", "wall_thickness"
    value_mm: float | None = None
    value_deg: float | None = None  # for angular dimensions
    source: Literal["user", "inferred", "default"] = "default"


class CADRequirements(BaseModel):
    object: str = "part"
    description: str = ""
    dimensions: list[Dimension] = Field(default_factory=list)
    features: list[str] = Field(default_factory=list)   # e.g. ["rounded_edges", "screw_holes"]
    mounting: bool = False
    material: Optional[str] = None
    units: Literal["mm"] = "mm"

    # Follow-up bookkeeping
    revision: int = 0
    open_questions: list[str] = Field(default_factory=list)

    def dim(self, name: str) -> Dimension | None:
        for d in self.dimensions:
            if d.name == name:
                return d
        return None

    def set_dim(self, name: str, value_mm: float | None = None, value_deg: float | None = None,
                source: str = "user") -> None:
        existing = self.dim(name)
        if existing:
            if value_mm is not None:
                existing.value_mm = value_mm
            if value_deg is not None:
                existing.value_deg = value_deg
            existing.source = source  # type: ignore[arg-type]
        else:
            self.dimensions.append(
                Dimension(name=name, value_mm=value_mm, value_deg=value_deg, source=source)  # type: ignore[arg-type]
            )

    def has_feature(self, name: str) -> bool:
        return any(name in f or f in name for f in self.features)

    def merge(self, other: "CADRequirements") -> "CADRequirements":
        """Merge follow-up requirements into this one (follow-up values win)."""
        merged = self.model_copy(deep=True)
        merged.revision += 1
        if other.object and other.object != "part":
            merged.object = other.object
        if other.description:
            merged.description = (merged.description + "\n" + other.description).strip()
        for d in other.dimensions:
            merged.set_dim(d.name, d.value_mm, d.value_deg, d.source)
        for f in other.features:
            if not merged.has_feature(f):
                merged.features.append(f)
        merged.mounting = merged.mounting or other.mounting
        if other.material:
            merged.material = other.material
        merged.open_questions = other.open_questions
        return merged

    def to_prompt(self) -> str:
        dims = ", ".join(
            f"{d.name}={d.value_mm}mm" if d.value_mm is not None else f"{d.name}={d.value_deg}deg"
            for d in self.dimensions
        )
        parts = [f"object: {self.object}"]
        if dims:
            parts.append(f"dims: {dims}")
        if self.features:
            parts.append(f"features: {', '.join(self.features)}")
        if self.mounting:
            parts.append("mounting: wall-mounted")
        if self.material:
            parts.append(f"material: {self.material}")
        return "; ".join(parts)


REQUIREMENTS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "object": {"type": "string"},
        "description": {"type": "string"},
        "dimensions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "value_mm": {"type": "number"},
                    "value_deg": {"type": "number"},
                    "source": {"type": "string", "enum": ["user", "inferred", "default"]},
                },
                "required": ["name"],
            },
        },
        "features": {"type": "array", "items": {"type": "string"}},
        "mounting": {"type": "boolean"},
        "material": {"type": ["string", "null"]},
        "open_questions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["object"],
}
