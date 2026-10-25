"""Pydantic models for LLM output (PHASE3 §5.3) and the strict JSON schema derived from them."""
from __future__ import annotations

import copy
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Category = Literal["event", "change", "announcement", "resource", "question", "chitchat"]
Confidence = Literal["high", "medium", "low"]
EventType = Literal["quiz", "exam", "assignment", "project", "lab", "class", "office_hours", "presentation", "other"]
Action = Literal["create", "restate", "reschedule", "cancel", "uncancel", "venue", "details", "set_time", "set_date"]
Weekday = Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Relative(_Strict):
    anchor: Literal["posted", "weekday", "offset"]
    weekday: Weekday | None = None
    qualifier: Literal["this", "next"] | None = None
    days: int | None = None
    weeks: int | None = None


class When(_Strict):
    kind: Literal["absolute", "relative", "same_as_existing", "vague", "none"]
    date: str | None = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    time: str | None = Field(None, pattern=r"^\d{2}:\d{2}$")
    end_time: str | None = Field(None, pattern=r"^\d{2}:\d{2}$")
    relative: Relative | None = None
    text: str = ""

    @field_validator("time", "end_time")
    @classmethod
    def _valid_clock(cls, v):
        if v is not None and not (int(v[:2]) < 24 and int(v[3:]) < 60):
            raise ValueError("not a 24h HH:MM time")
        return v


class Item(_Strict):
    action: Action
    type: EventType
    title: str
    is_deadline: bool
    refers_to_existing: bool
    reference_hint: str | None
    when: When
    venue: str | None
    details: str | None
    confidence: Confidence


class MessageExtraction(_Strict):
    category: Category
    confidence: Confidence
    items: list[Item]


class ResolveChoice(_Strict):
    match: str  # an event id from the list, or "new"

    @field_validator("match", mode="before")
    @classmethod
    def _as_text(cls, v):
        return str(v) if isinstance(v, int) and not isinstance(v, bool) else v


def strict_json_schema(model: type[BaseModel]) -> dict:
    """OpenAI/Groq strict-mode friendly schema: $refs inlined, every property required, no extra properties.
    Optional fields become nullable (anyOf with null kept as a type list for simple types)."""
    raw = model.model_json_schema()
    defs = raw.pop("$defs", {})

    def conv(node):
        if isinstance(node, list):
            return [conv(x) for x in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            return conv(copy.deepcopy(defs[node["$ref"].split("/")[-1]]))
        # Drop pydantic's schema-level title/default/pattern, but never a FIELD that happens to be called "title".
        node = {k: ({n: conv(sub) for n, sub in v.items()} if k == "properties" else conv(v))
                for k, v in node.items() if k not in ("title", "default", "pattern")}
        if "anyOf" in node:
            non_null = [x for x in node["anyOf"] if x.get("type") != "null"]
            if len(non_null) == 1 and len(node["anyOf"]) == 2:
                inner = non_null[0]
                if "type" in inner and isinstance(inner["type"], str) and "$ref" not in inner:
                    merged = {**inner, "type": [inner["type"], "null"]}
                    if "enum" in merged and None not in merged["enum"]:
                        merged["enum"] = [*merged["enum"], None]
                    return merged
        if node.get("type") == "object":
            node["additionalProperties"] = False
            node["required"] = list(node.get("properties", {}))
        return node

    return conv(raw)
