"""Only finite, image-grounded repair inputs cross the browser/model boundary."""

from typing import Literal
from pydantic import Field, model_validator
from .schema import Binding, Strict


class Mark(Strict):
    kind: Literal["keep", "exclude", "paint", "erase"]
    points: list[tuple[float, float]] = Field(min_length=1, max_length=20000)
    radius: float = Field(default=4, ge=0.5, le=256)


class MaskCheck(Strict):
    name: str = Field(min_length=1, max_length=100)
    kind: Literal["include", "exclude"]
    box: tuple[int, int, int, int]
    confirmed: bool = False


class MaskConstraints(Strict):
    box: tuple[int, int, int, int] | None = None
    marks: list[Mark] = Field(default_factory=list, max_length=500)
    checks: list[MaskCheck] = Field(default_factory=list, max_length=32)
    candidate: str | None = Field(default=None, pattern=r"^mask-[0-9]+\.png$")


class Drag(Strict):
    start: tuple[float, float]
    end: tuple[float, float]
    fixed: bool = False


class RigRepair(Strict):
    action: Literal["landmarks", "smooth", "opacity"] = "landmarks"
    target: str | None = None
    kind: Literal["mesh", "warp"] = "mesh"
    pose: dict[str, float] = Field(default_factory=dict)
    drags: list[Drag] = Field(default_factory=list, max_length=32)
    radius: float = Field(default=0.2, gt=0, le=1)
    strength: float = Field(default=0.15, gt=0, le=0.5)
    opacity: float = Field(default=0, ge=0, le=1)
    region: tuple[float, float, float, float] | None = None


class Cleanup(Strict):
    mask: MaskConstraints = Field(default_factory=MaskConstraints)
    expansion: int = Field(default=0, ge=0, le=64)
    backend: Literal["lama", "comfy"] = "lama"
    allow_visible: bool = False
    prompt: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def brush_only(self):
        if (
            self.mask.box
            or self.mask.candidate
            or self.mask.checks
            or any(mark.kind not in {"paint", "erase"} for mark in self.mask.marks)
        ):
            raise ValueError("底层清理范围使用画笔、橡皮擦和扩展距离")
        return self


class RepairEdit(Strict):
    mode: Literal["split", "mask", "replace", "add", "repaint", "bind", "rig"] = "split"
    name: str = Field(default="新部件", min_length=1, max_length=80)
    request: str = Field(default="", max_length=6000)
    semantic: str | None = None
    side: Literal["left", "right", "none"] = "none"
    placement: tuple[int, int, int, int] | None = None
    mask: MaskConstraints = Field(default_factory=MaskConstraints)
    binding: Binding | None = None
    rig: RigRepair = Field(default_factory=RigRepair)
    visual_accepted: bool = False
    inpaint_padding: int = Field(default=4, ge=0, le=32)
    cleanup: Cleanup = Field(default_factory=Cleanup)
    repaint_seed: int = Field(default=42, ge=0, le=2**32 - 1)
    candidate_limit: int = Field(default=3, ge=1, le=3)
    candidate_timeout: int = Field(default=600, ge=60, le=1800)


class RepairDraft(Strict):
    id: str = Field(pattern=r"^[a-f0-9]{32}$")
    base_revision: str = Field(pattern=r"^[a-zA-Z0-9]+$")
    part: str | None
    source_hash: str
    material_hash: str
    rig_hash: str | None = None
    version: int = Field(default=1, ge=1)
    edit: RepairEdit
    result: dict = Field(default_factory=dict)
    applied_revision: str | None = None


class RepairTarget(Strict):
    revision: str
    part: str | None = None
    mode: Literal["split", "mask", "replace", "add", "repaint", "bind", "rig"] = "split"

    @model_validator(mode="after")
    def required_part(self):
        if self.mode != "add" and not self.part:
            raise ValueError("请选择要修正的部件")
        return self


class DraftCreate(RepairTarget):
    # Web upload identifiers remain confined to the web upload directory.
    upload: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}\.(png|jpg|jpeg)$")

    @model_validator(mode="after")
    def required_upload(self):
        if self.mode in {"replace", "add"} and not self.upload:
            raise ValueError("请先上传替换或新增的图片")
        return self


class DraftUpdate(Strict):
    version: int = Field(ge=1)
    edit: RepairEdit
