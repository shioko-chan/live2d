"""Explicit, inference-free contracts for assembling several image layers together."""

from typing import Literal

from pydantic import Field, model_validator

from .schema import Binding, Strict


class LayerOperation(Strict):
    action: Literal["add", "replace", "update", "remove"]
    id: str = Field(pattern=r"^[A-Za-z0-9_][A-Za-z0-9_-]{0,127}$")
    source: str | None = None
    name: str | None = Field(default=None, min_length=1, max_length=160)
    semantic: str | None = None
    side: Literal["left", "right", "none"] | None = None
    order: int | None = Field(default=None, ge=0)
    # No implicit resampling: width and height must equal the source PNG size.
    placement: tuple[int, int, int, int] | None = None
    binding: Binding | None = None
    clear_binding: bool = False

    @model_validator(mode="after")
    def coherent(self):
        if (self.action in {"add", "replace"}) != bool(self.source):
            raise ValueError("Only add/replace require a source PNG")
        if self.action == "add" and any(v is None for v in (self.semantic, self.order, self.placement)):
            raise ValueError("Add requires semantic, order and placement")
        if self.action == "remove" and any(
            v is not None for v in (self.name, self.semantic, self.side, self.order, self.placement, self.binding)
        ):
            raise ValueError("Remove accepts only action and id")
        if self.clear_binding and (self.binding is not None or self.action in {"add", "remove"}):
            raise ValueError("clear_binding is only used by replace/update without binding")
        if self.placement:
            x, y, w, h = self.placement
            if min(x, y) < 0 or min(w, h) < 1:
                raise ValueError("Placement requires nonnegative origin and positive dimensions")
        if self.action == "update" and not self.clear_binding and all(
            v is None for v in (self.name, self.semantic, self.side, self.order, self.placement, self.binding)
        ):
            raise ValueError("Update requires metadata, placement or binding changes")
        return self


class BatchEdit(Strict):
    request: str = Field(min_length=1)
    # Required for an empty project; existing material canvas cannot be resized here.
    canvas: tuple[int, int] | None = None
    operations: list[LayerOperation] = Field(min_length=1, max_length=512)

    @model_validator(mode="after")
    def coherent(self):
        if self.canvas and (min(self.canvas) < 1 or self.canvas[0] * self.canvas[1] > 24_000_000):
            raise ValueError("Canvas must be positive and at most 24 million pixels")
        ids = [op.id for op in self.operations]
        if len(ids) != len(set(ids)):
            raise ValueError("Each part may occur only once per batch; combine changes in one operation")
        return self


class BatchDraft(Strict):
    id: str = Field(pattern=r"^[a-f0-9]{32}$")
    version: int = Field(default=1, ge=1)
    base_revision: str | None
    material_hash: str
    edit: BatchEdit
    inputs: dict[str, dict[str, str]] = Field(default_factory=dict)
    result: dict = Field(default_factory=dict)
    applied_revision: str | None = None
