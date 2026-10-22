"""Supported Cubism expression/motion documents and reproducible playback requests.

Parameter curves support all four Cubism segment types. Model effect curves and
part-opacity curves are rejected until the renderer can apply those targets.
"""

from typing import Annotated, Literal

from pydantic import Field, model_validator

from .schema import Strict

Name = Annotated[str, Field(min_length=1, max_length=80, pattern=r"^[\w-]+$")]


class ExpressionParameter(Strict):
    Id: str = Field(min_length=1)
    Value: float
    Blend: Literal["Add", "Multiply", "Overwrite"] = "Add"


class ExpressionDocument(Strict):
    Version: Literal[3] | None = None
    Type: Literal["Live2D Expression"] = "Live2D Expression"
    FadeInTime: float = Field(default=1, ge=0)
    FadeOutTime: float = Field(default=1, ge=0)
    Parameters: list[ExpressionParameter] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_parameters(self):
        if len({p.Id for p in self.Parameters}) != len(self.Parameters):
            raise ValueError("Duplicate expression parameter")
        return self


class MotionCurve(Strict):
    Target: Literal["Parameter"] = "Parameter"
    Id: str = Field(min_length=1)
    FadeInTime: float = Field(default=-1, ge=-1)
    FadeOutTime: float = Field(default=-1, ge=-1)
    Segments: list[float] = Field(min_length=5)


class MotionMeta(Strict):
    Duration: float = Field(gt=0)
    Fps: float = Field(gt=0, le=240)
    Loop: bool = False
    AreBeziersRestricted: bool = False
    CurveCount: int = Field(ge=1)
    TotalSegmentCount: int = Field(ge=1)
    TotalPointCount: int = Field(ge=2)
    UserDataCount: Literal[0] = 0
    TotalUserDataSize: Literal[0] = 0
    FadeInTime: float = Field(default=1, ge=0)
    FadeOutTime: float = Field(default=1, ge=0)


class MotionDocument(Strict):
    Version: Literal[3] = 3
    Meta: MotionMeta
    Curves: list[MotionCurve] = Field(min_length=1)
    # Event dispatch/audio are not part of this CPU parameter preview.
    UserData: list = Field(default_factory=list, max_length=0)


class LibraryContents(Strict):
    expressions: dict[Name, ExpressionDocument] = Field(default_factory=dict)
    motions: dict[Name, dict[Name, MotionDocument]] = Field(default_factory=dict)
    physics: dict | None = None


class ExpressionEvent(Strict):
    name: Name
    start: float = Field(default=0, ge=0)
    end: float | None = Field(default=None, gt=0)
    weight: float = Field(default=1, ge=0, le=1)

    @model_validator(mode="after")
    def times(self):
        if self.end is not None and self.end <= self.start:
            raise ValueError("Expression end must follow start")
        return self


class MotionClip(Strict):
    name: Name
    group: Name = "Idle"
    start: float = Field(default=0, ge=0)
    end: float | None = Field(default=None, gt=0)
    loop: bool | None = None
    hold: bool = False

    @model_validator(mode="after")
    def times(self):
        if self.end is not None and self.end <= self.start:
            raise ValueError("Motion end must follow start")
        if self.loop and self.hold:
            raise ValueError("Loop and hold are mutually exclusive")
        return self


class PlaybackRequest(Strict):
    motion: Name | None = None
    group: Name = "Idle"
    expression: Name | None = None
    clips: list[MotionClip] = Field(default_factory=list, max_length=64)
    expressions: list[ExpressionEvent] = Field(default_factory=list, max_length=64)
    duration: float = Field(default=8, gt=0, le=120)
    fps: int = Field(default=24, ge=1, le=60)
    loop: bool | None = None
    physics: bool = False
    long_edge: int = Field(default=512, ge=64, le=2048)

    @model_validator(mode="after")
    def unambiguous(self):
        if self.motion and self.clips:
            raise ValueError("Choose motion or clips")
        if self.loop is not None and not self.motion:
            raise ValueError("Top-level loop requires motion")
        return self
