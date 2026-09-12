from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Operation(Strict):
    kind: Literal["recolor", "repaint", "add", "remove", "rig", "regenerate"]
    target: str | None = None
    instruction: str = ""
    color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    # Bounds for new artwork in full-canvas pixels. Existing edits use stored placement.
    bounds: tuple[int, int, int, int] | None = None
    semantic: str | None = None
    # Rig tool inputs are checked against the pinned MCP server's actual schema.
    tool: Literal["parameter_update", "rig_transform", "keyform_set", "physics_put"] | None = None
    arguments: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def coherent(self):
        if self.kind in {"recolor", "repaint", "remove"} and not self.target:
            raise ValueError("Existing part ID required")
        if self.kind == "recolor" and self.color is None:
            raise ValueError("recolor requires a hex color")
        if self.kind in {"repaint", "add", "regenerate"} and not self.instruction.strip():
            raise ValueError("Image instruction required")
        if self.kind == "add" and (self.bounds is None or self.semantic is None):
            raise ValueError("New parts require explicit canvas bounds and semantic")
        if self.bounds and (min(self.bounds) < 0 or min(self.bounds[2:]) == 0):
            raise ValueError("Invalid placement")
        if self.kind == "rig" and not self.tool:
            raise ValueError("rig requires an allowed MCP tool")
        return self


class Plan(Strict):
    request: str
    prompt: str
    negative: str = "lowres, bad anatomy, cropped, text, watermark, blurry, background scenery"
    operations: list[Operation] = Field(default_factory=list, max_length=16)
    clarification: str | None = None
    unsupported: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def regeneration(self):
        if any(o.kind == "regenerate" for o in self.operations) and len(self.operations) != 1:
            raise ValueError("regenerate must be its own revision")
        return self


class Part(Strict):
    id: str
    name: str
    semantic: str
    side: Literal["left", "right", "none"] = "none"
    order: int
    bounds: tuple[int, int, int, int]
    image: str


class Parts(Strict):
    required_semantics: list[str] = Field(default_factory=list)
    width: int
    height: int
    parts: list[Part]


class Stage(Strict):
    fingerprint: str
    state: Literal["running", "complete", "failed"]
    artifacts: dict[str, str] = Field(default_factory=dict)
    error: str | None = None


class Revision(Strict):
    id: str
    parent: str | None
    plan: Plan
    source: str | None = None
    source_hash: str | None = None
    reference: str | None = None
    stages: dict[str, Stage] = Field(default_factory=dict)
    status: Literal["planned", "running", "paused", "failed", "built", "accepted"] = "planned"
    # Runtime verification and visual acceptance remain separate from build completion.
    runtime_verified: bool = False
