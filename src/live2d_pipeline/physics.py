"""Validated physics3 chains and a deterministic CPU authoring preview.

The preview uses a constrained-particle solver; it is not the Cubism SDK solver.
Exported documents retain the standard physics3 contract for target runtimes.
"""

import math
from typing import Literal

from pydantic import Field, model_validator

from .schema import Strict


class Vector(Strict):
    X: float
    Y: float


class ParameterTarget(Strict):
    Target: Literal["Parameter"] = "Parameter"
    Id: str = Field(min_length=1)


class PhysicsInput(Strict):
    Source: ParameterTarget
    Weight: float = Field(ge=0, le=100)
    Type: Literal["X", "Y", "Angle"]
    Reflect: bool


class PhysicsOutput(Strict):
    Destination: ParameterTarget
    VertexIndex: int = Field(ge=1)
    Scale: float
    Weight: float = Field(ge=0, le=100)
    Type: Literal["X", "Y", "Angle"]
    Reflect: bool


class Particle(Strict):
    Position: Vector
    Mobility: float = Field(ge=0, le=1)
    Delay: float = Field(ge=0, le=10)
    Acceleration: float = Field(ge=0, le=100)
    Radius: float = Field(ge=0)


class NormalizationRange(Strict):
    Minimum: float
    Default: float
    Maximum: float

    @model_validator(mode="after")
    def ordered(self):
        if not self.Minimum <= self.Default <= self.Maximum or self.Minimum == self.Maximum:
            raise ValueError("Normalization requires Minimum <= Default <= Maximum and a nonzero range")
        return self


class Normalization(Strict):
    Position: NormalizationRange
    Angle: NormalizationRange


class PhysicsSetting(Strict):
    Id: str = Field(min_length=1)
    Input: list[PhysicsInput] = Field(min_length=1, max_length=128)
    Output: list[PhysicsOutput] = Field(min_length=1, max_length=128)
    Vertices: list[Particle] = Field(min_length=2, max_length=128)
    Normalization: Normalization

    @model_validator(mode="after")
    def chain(self):
        if self.Vertices[0].Radius != 0 or any(p.Radius <= 0 for p in self.Vertices[1:]):
            raise ValueError("Chain root radius must be zero; every child radius must be positive")
        if any(o.VertexIndex >= len(self.Vertices) for o in self.Output):
            raise ValueError("Physics output VertexIndex is outside its particle chain")
        return self


class Forces(Strict):
    Gravity: Vector
    Wind: Vector


class PhysicsName(Strict):
    Id: str
    Name: str


class PhysicsMeta(Strict):
    PhysicsSettingCount: int = Field(ge=0)
    TotalInputCount: int = Field(ge=0)
    TotalOutputCount: int = Field(ge=0)
    VertexCount: int = Field(ge=0)
    # Some existing assets include both fields; require agreement when present.
    TotalVertexCount: int | None = Field(default=None, ge=0)
    EffectiveForces: Forces
    Fps: float = Field(default=60, ge=0, le=240)
    PhysicsDictionary: list[PhysicsName]


class PhysicsDocument(Strict):
    Version: Literal[3] = 3
    Meta: PhysicsMeta
    PhysicsSettings: list[PhysicsSetting] = Field(max_length=256)

    @model_validator(mode="after")
    def counts(self):
        groups = self.PhysicsSettings
        expected = (len(groups), sum(len(g.Input) for g in groups), sum(len(g.Output) for g in groups),
                    sum(len(g.Vertices) for g in groups))
        actual = (self.Meta.PhysicsSettingCount, self.Meta.TotalInputCount,
                  self.Meta.TotalOutputCount, self.Meta.VertexCount)
        if actual != expected:
            raise ValueError(f"Physics metadata counts do not match actual data: expected {expected}")
        if self.Meta.TotalVertexCount is not None and self.Meta.TotalVertexCount != expected[-1]:
            raise ValueError("TotalVertexCount disagrees with VertexCount")
        ids = [g.Id for g in groups]
        names = [n.Id for n in self.Meta.PhysicsDictionary]
        if len(set(ids)) != len(ids) or len(set(names)) != len(names) or set(ids) != set(names):
            raise ValueError("Physics group IDs and dictionary IDs must be unique and match")
        return self


def validate_document(document, parameters):
    """Return a canonical validated document without altering its declared counts."""
    parsed = PhysicsDocument.model_validate(document)
    for group in parsed.PhysicsSettings:
        for target in [i.Source for i in group.Input] + [o.Destination for o in group.Output]:
            if target.Id not in parameters:
                raise ValueError(f"Unknown physics parameter: {target.Id}")
        # Reusing an output in a later group's input is supported, in document order.
        for output in group.Output:
            p = parameters[output.Destination.Id]
            if p["min"] >= p["max"]:
                raise ValueError(f"Physics output needs a nonzero parameter range: {output.Destination.Id}")
    return parsed.model_dump(exclude_none=True)


def compose(groups, names=None, *, fps=60, gravity=None, wind=None):
    """Build standard metadata for authored chains; validation still runs on storage."""
    parsed = [PhysicsSetting.model_validate(g) for g in groups]
    names = names or {}
    result = {
        "Version": 3,
        "Meta": {
            "PhysicsSettingCount": len(parsed),
            "TotalInputCount": sum(len(g.Input) for g in parsed),
            "TotalOutputCount": sum(len(g.Output) for g in parsed),
            "VertexCount": sum(len(g.Vertices) for g in parsed),
            "Fps": fps,
            "EffectiveForces": {"Gravity": gravity or {"X": 0, "Y": -1},
                                "Wind": wind or {"X": 0, "Y": 0}},
            "PhysicsDictionary": [{"Id": g.Id, "Name": names.get(g.Id, g.Id)} for g in parsed],
        },
        "PhysicsSettings": [g.model_dump() for g in parsed],
    }
    return PhysicsDocument.model_validate(result).model_dump(exclude_none=True)


def _unit(x, y, default=(0.0, 1.0)):
    length = math.hypot(x, y)
    return (x / length, y / length) if length > 1e-12 else default


def _rotate(x, y, angle):
    cosine, sine = math.cos(angle), math.sin(angle)
    return cosine * x - sine * y, sine * x + cosine * y


class PhysicsSampler:
    """Stateful fixed-step chain preview. Call with increasing absolute times.

    Frames are sampled at a fixed physics frequency with interpolated input, so
    output video FPS does not change the simulation step. Reports label the solver
    explicitly and never set official runtime verification.
    """

    engine = "cpu-constrained-particles-v1"
    official_parity = False

    def __init__(self, parameters, document, fps=None):
        self.parameters = parameters
        self.document = validate_document(document, parameters)
        self.rig = PhysicsDocument.model_validate(self.document)
        frequency = fps if fps is not None else self.rig.Meta.Fps or 60
        if not math.isfinite(frequency) or not 1 <= frequency <= 240:
            raise ValueError("Physics frequency must be between 1 and 240")
        self.dt = 1 / frequency
        self.reset()

    def reset(self):
        self.time = None
        self.step_index = 0
        self.previous_inputs = None
        self.positions = []
        self.velocities = []
        self.last_output = {}
        self.clamped_outputs = 0
        for group in self.rig.PhysicsSettings:
            positions = [(0.0, 0.0)]
            for vertex in group.Vertices[1:]:
                positions.append((0.0, positions[-1][1] + vertex.Radius))
            self.positions.append(positions)
            self.velocities.append([(0.0, 0.0)] * len(positions))

    def _input(self, item, group, values):
        p = self.parameters[item.Source.Id]
        norm = group.Normalization.Angle if item.Type == "Angle" else group.Normalization.Position
        # physics3 normalization is centered on the parameter interval midpoint.
        midpoint = (p["min"] + p["max"]) / 2
        value = min(p["max"], max(p["min"], values[item.Source.Id]))
        span = p["max"] - midpoint if value >= midpoint else midpoint - p["min"]
        normalized = norm.Default
        if span:
            extent = norm.Maximum - norm.Default if value >= midpoint else norm.Default - norm.Minimum
            normalized += (value - midpoint) / span * extent
        return normalized * (1 if item.Reflect else -1) * item.Weight / 100

    def _advance(self, values, initialize=False):
        values = dict(values)
        forces = self.rig.Meta.EffectiveForces
        down = _unit(-forces.Gravity.X, -forces.Gravity.Y)
        for index, group in enumerate(self.rig.PhysicsSettings):
            drive = {"X": 0.0, "Y": 0.0, "Angle": 0.0}
            for item in group.Input:
                drive[item.Type] += self._input(item, group, values)
            angle = math.radians(drive["Angle"])
            gravity = _rotate(*down, angle)
            root = _rotate(drive["X"], drive["Y"], -angle)
            positions, velocities = self.positions[index], self.velocities[index]
            positions[0] = root
            for j, particle in enumerate(group.Vertices[1:], start=1):
                parent = positions[j - 1]
                force = (gravity[0] * particle.Acceleration + forces.Wind.X,
                         gravity[1] * particle.Acceleration + forces.Wind.Y)
                if initialize:
                    direction = _unit(*force, default=gravity)
                    positions[j] = (parent[0] + direction[0] * particle.Radius,
                                    parent[1] + direction[1] * particle.Radius)
                    velocities[j] = (0.0, 0.0)
                    continue
                scale = self.dt * 30 * particle.Delay
                old = positions[j]
                vx, vy = velocities[j]
                proposed = (old[0] + vx * scale + force[0] * scale * scale,
                            old[1] + vy * scale + force[1] * scale * scale)
                direction = _unit(proposed[0] - parent[0], proposed[1] - parent[1], gravity)
                positions[j] = (parent[0] + direction[0] * particle.Radius,
                                parent[1] + direction[1] * particle.Radius)
                velocities[j] = tuple((new - prior) / scale * particle.Mobility for new, prior in
                                      zip(positions[j], old)) if scale else (0.0, 0.0)
            for output in group.Output:
                j = output.VertexIndex
                delta = (positions[j][0] - positions[j - 1][0], positions[j][1] - positions[j - 1][1])
                if output.Type == "Angle":
                    parent_direction = down if j == 1 else (
                        positions[j - 1][0] - positions[j - 2][0],
                        positions[j - 1][1] - positions[j - 2][1])
                    raw = math.atan2(parent_direction[0] * delta[1] - parent_direction[1] * delta[0],
                                     parent_direction[0] * delta[0] + parent_direction[1] * delta[1])
                else:
                    raw = delta[0 if output.Type == "X" else 1]
                raw *= output.Scale * (-1 if output.Reflect else 1)
                p = self.parameters[output.Destination.Id]
                bounded = min(p["max"], max(p["min"], raw))
                self.clamped_outputs += bounded != raw
                weight = output.Weight / 100
                key = output.Destination.Id
                values[key] = values[key] * (1 - weight) + bounded * weight
                if not math.isfinite(values[key]):
                    raise ValueError(f"Physics produced a nonfinite value: {key}")
        self.last_output = {o.Destination.Id: values[o.Destination.Id]
                            for g in self.rig.PhysicsSettings for o in g.Output}
        return values

    def __call__(self, values, t):
        if not math.isfinite(t) or t < 0 or (self.time is not None and t < self.time):
            raise ValueError("Physics samples require finite nonnegative increasing time; reset to seek")
        unknown = set(values) - set(self.parameters)
        if unknown:
            raise ValueError(f"Unknown physics input parameters: {sorted(unknown)}")
        inputs = {key: values.get(key, p["default"]) for key, p in self.parameters.items()}
        if any(not math.isfinite(v) or not self.parameters[k]["min"] <= v <= self.parameters[k]["max"]
               for k, v in inputs.items()):
            raise ValueError("Physics input is nonfinite or outside the parameter range")
        if self.time is None:
            if t != 0:
                raise ValueError("Start physics playback at time zero")
            result = self._advance(inputs, initialize=True)
        else:
            if t - self.time > 5:
                raise ValueError("Physics sample gap exceeds five seconds; sample the intervening timeline")
            target_step = math.floor(t / self.dt + 1e-8)
            while self.step_index < target_step:
                self.step_index += 1
                stamp = self.step_index * self.dt
                fraction = (stamp - self.time) / (t - self.time) if t != self.time else 1
                sampled = {k: self.previous_inputs[k] + (v - self.previous_inputs[k]) * fraction
                           for k, v in inputs.items()}
                self._advance(sampled)
            result = {**inputs, **self.last_output}
        self.previous_inputs, self.time = inputs, t
        return result

    def report(self):
        return {"engine": self.engine, "physics_time_response": "simulated",
                "official_runtime_verified": False, "official_solver_parity": False,
                "groups": len(self.rig.PhysicsSettings),
                "particles": self.rig.Meta.VertexCount, "steps": self.step_index,
                "clamped_outputs": self.clamped_outputs}
