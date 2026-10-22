"""Versioned expressions, authored motions and deterministic CPU playback.

Cubism format references:
https://github.com/Live2D/CubismWebFramework/blob/develop/src/motion/cubismmotion.ts
https://docs.live2d.com/en/cubism-sdk-manual/expression/
This sampler is a parameter preview, not the official Cubism motion manager.
"""

from copy import deepcopy
import math
from pathlib import Path
import re

from .performance_schema import (
    ExpressionDocument, LibraryContents, MotionDocument, PlaybackRequest,
)
from .review import built_directory
from .store import fingerprint, read_json, write_json


def segments(curve):
    """Decode the flat Cubism stream into (kind, start, controls/end) tuples."""
    values = curve["Segments"]
    start = (values[0], values[1])
    offset = 2
    result = []
    while offset < len(values):
        kind = values[offset]
        if kind not in (0, 1, 2, 3):
            raise ValueError(f"Unsupported motion segment type: {kind}")
        count = 3 if kind == 1 else 1
        end = offset + 1 + count * 2
        if end > len(values):
            raise ValueError("Truncated motion segment")
        points = [tuple(values[i:i + 2]) for i in range(offset + 1, end, 2)]
        if points[-1][0] <= start[0]:
            raise ValueError("Motion segment times must increase")
        times = [start[0], *(p[0] for p in points)]
        if times != sorted(times):
            raise ValueError("Bezier control times must be monotonic inside the segment")
        result.append((int(kind), start, points))
        start, offset = points[-1], end
    return result


def cubic(values, u):
    return ((1 - u) ** 3 * values[0] + 3 * (1 - u) ** 2 * u * values[1]
            + 3 * (1 - u) * u * u * values[2] + u ** 3 * values[3])


def curve_extrema(kind, start, points):
    values = [start[1], *(p[1] for p in points)]
    if kind != 1:
        return values
    a, b, c, d = values
    # Derivative / 3 = A*u^2 + B*u + C.
    aa, bb, cc = -a + 3 * b - 3 * c + d, 2 * (a - 2 * b + c), b - a
    candidates = [0.0, 1.0]
    if abs(aa) < 1e-12:
        if abs(bb) > 1e-12:
            candidates.append(-cc / bb)
    elif bb * bb - 4 * aa * cc >= 0:
        root = math.sqrt(bb * bb - 4 * aa * cc)
        candidates += [(-bb + root) / (2 * aa), (-bb - root) / (2 * aa)]
    return [cubic(values, u) for u in candidates if 0 <= u <= 1]


def validate_expression(document, parameters):
    document = ExpressionDocument.model_validate(document).model_dump(exclude_none=True)
    for row in document["Parameters"]:
        if row["Id"] not in parameters:
            raise ValueError(f"Unknown expression parameter: {row['Id']}")
        parameter = parameters[row["Id"]]
        if row["Blend"] == "Overwrite" and not parameter["min"] <= row["Value"] <= parameter["max"]:
            raise ValueError(f"Expression value outside parameter range: {row['Id']}")
    return document


def validate_motion(document, parameters):
    document = MotionDocument.model_validate(document).model_dump()
    meta = document["Meta"]
    segment_count, point_count, seen = 0, 0, set()
    for curve in document["Curves"]:
        key = curve["Id"]
        if key not in parameters:
            raise ValueError(f"Unknown motion parameter: {key}")
        if key in seen:
            raise ValueError(f"Duplicate motion parameter curve: {key}")
        seen.add(key)
        parsed = segments(curve)
        if curve["Segments"][0] != 0 or parsed[-1][2][-1][0] > meta["Duration"]:
            raise ValueError("Motion curves must start at zero and end within Duration")
        parameter = parameters[key]
        for kind, start, points in parsed:
            if kind == 1 and meta["AreBeziersRestricted"]:
                span = points[-1][0] - start[0]
                expected = [start[0] + span / 3, start[0] + span * 2 / 3]
                if any(not math.isclose(p[0], t, abs_tol=1e-5) for p, t in zip(points[:2], expected)):
                    raise ValueError("Restricted Bezier controls must use one-third/two-thirds times")
            if any(not parameter["min"] - 1e-8 <= v <= parameter["max"] + 1e-8
                   for v in curve_extrema(kind, start, points)):
                raise ValueError(f"Motion curve exceeds parameter range: {key}")
        segment_count += len(parsed)
        point_count += 1 + sum(len(points) for _, _, points in parsed)
    expected = (len(document["Curves"]), segment_count, point_count)
    actual = (meta["CurveCount"], meta["TotalSegmentCount"], meta["TotalPointCount"])
    if actual != expected:
        raise ValueError(f"Motion metadata counts disagree: expected {expected}, got {actual}")
    return document


def parameter_map(store, revision=None):
    rev, directory = built_directory(store, revision)
    context = read_json(directory / "rig/rig-context.json")
    rows = context["parameters"]
    rows = rows.get("parameters", []) if isinstance(rows, dict) else rows
    return rev, {row["id"]: row for row in rows}


def validate_library(document, parameters):
    document = LibraryContents.model_validate(document).model_dump()
    document["expressions"] = {
        name: validate_expression(expression, parameters)
        for name, expression in document["expressions"].items()
    }
    document["motions"] = {
        group: {name: validate_motion(motion, parameters) for name, motion in motions.items()}
        for group, motions in document["motions"].items()
    }
    if document["physics"] is not None:
        from .physics import validate_document

        document["physics"] = validate_document(document["physics"], parameters)
    return document


def _directory(store, revision):
    store.revision_dir(revision)  # Validate IDs before constructing another path.
    return store.root / "performance" / revision


def _pointer(store, revision):
    rev = store.get(revision)
    while rev:
        path = _directory(store, rev.id) / "head.json"
        if path.exists():
            return read_json(path)
        rev = store.get(rev.parent) if rev.parent else None
    return None


def inherit(store, revision):
    """Pin the parent's library at model creation; no model build is required."""
    rev = store.get(revision)
    path = _directory(store, rev.id) / "head.json"
    if path.exists():
        return
    write_json(path, _pointer(store, rev.parent) if rev.parent else None)


def load_library(store, revision=None, snapshot=None):
    rev = store.get(revision)
    pointer = _pointer(store, rev.id)
    if snapshot is not None:
        if not re.fullmatch(r"[0-9a-f]{64}", snapshot):
            raise ValueError("Invalid performance snapshot ID")
        ancestor = rev
        pointer = None
        while ancestor:
            if (_directory(store, ancestor.id) / f"{snapshot}.json").is_file():
                pointer = {"revision": ancestor.id, "snapshot": snapshot}
                break
            ancestor = store.get(ancestor.parent) if ancestor.parent else None
        if pointer is None:
            raise ValueError("Performance snapshot is not in this model revision's ancestry")
    if pointer is None:
        return {"id": None, "version": 0, "model_revision": rev.id, "parent": None,
                **LibraryContents().model_dump()}
    ident = pointer["snapshot"]
    if not re.fullmatch(r"[0-9a-f]{64}", ident):
        raise ValueError("Invalid performance snapshot ID")
    document = read_json(_directory(store, pointer["revision"]) / f"{ident}.json")
    if fingerprint(document) != ident:
        raise ValueError("Performance snapshot was modified")
    return {"id": ident, **document}


def replace_library(store, contents, revision=None, version=0):
    """Callers hold the project lock. Each successful edit creates a new snapshot."""
    rev, parameters = parameter_map(store, revision)
    old = load_library(store, rev.id)
    if old["version"] != version:
        raise ValueError(f"Stale performance version: expected {old['version']}, got {version}")
    contents = validate_library(contents, parameters)
    parent = {"revision": old["model_revision"], "snapshot": old["id"]} if old["id"] else None
    document = {"version": version + 1, "model_revision": rev.id, "parent": parent, **contents}
    ident = fingerprint(document)
    directory = _directory(store, rev.id)
    write_json(directory / f"{ident}.json", document)
    write_json(directory / "head.json", {"revision": rev.id, "snapshot": ident})
    return {"id": ident, **document}


def contents(library):
    return {key: deepcopy(library[key]) for key in ("expressions", "motions", "physics")}


def put_expression(store, name, document, revision=None, version=0):
    library = contents(load_library(store, revision))
    library["expressions"][name] = document
    return replace_library(store, library, revision, version)


def put_motion(store, group, name, document, revision=None, version=0):
    library = contents(load_library(store, revision))
    library["motions"].setdefault(group, {})[name] = document
    return replace_library(store, library, revision, version)


def put_physics(store, document, revision=None, version=0):
    library = contents(load_library(store, revision))
    library["physics"] = document
    return replace_library(store, library, revision, version)


def remove(store, kind, name=None, group="Idle", revision=None, version=0):
    library = contents(load_library(store, revision))
    if kind == "physics":
        library["physics"] = None
    elif kind == "expression" and name in library["expressions"]:
        del library["expressions"][name]
    elif kind == "motion" and name in library["motions"].get(group, {}):
        del library["motions"][group][name]
        if not library["motions"][group]:
            del library["motions"][group]
    else:
        raise ValueError("Unknown performance entry")
    return replace_library(store, library, revision, version)


def history(store, revision=None):
    library = load_library(store, revision)
    entries = []
    while library["id"]:
        entries.append({key: library[key] for key in ("id", "version", "model_revision")})
        parent = library["parent"]
        if parent is None:
            break
        library = load_library(store, parent["revision"], parent["snapshot"])
    return entries


def attach_bundle(store, revision, bundle, snapshot=None):
    """Attach authored data to an export COPY, never the immutable build bundle."""
    bundle = Path(bundle).resolve()
    if bundle.is_relative_to((store.root / "revisions").resolve()):
        raise ValueError("Performance export cannot modify a saved model revision")
    library = load_library(store, revision, snapshot)
    if library["id"] is None:
        return library
    _, parameters = parameter_map(store, revision)
    validated = validate_library(contents(library), parameters)
    models = list(bundle.glob("*.model3.json"))
    if len(models) != 1:
        raise ValueError("Expected exactly one model3.json in copied bundle")
    model = read_json(models[0])
    refs = model["FileReferences"]
    directory = bundle / "performance"
    # Re-export to a fresh copied bundle is the supported path. Preserve generated
    # motion groups; authored groups replace the corresponding generated group.
    expressions = {entry["Name"]: entry for entry in refs.get("Expressions", [])}
    for name, expression in validated["expressions"].items():
        filename = f"performance/expressions/{name}.exp3.json"
        write_json(bundle / filename, expression)
        expressions[name] = {"Name": name, "File": filename}
    if expressions:
        refs["Expressions"] = list(expressions.values())
    for group, motions in validated["motions"].items():
        entries = []
        for name, motion in motions.items():
            filename = f"performance/motions/{group}/{name}.motion3.json"
            write_json(bundle / filename, motion)
            entries.append({"File": filename})
        refs.setdefault("Motions", {})[group] = entries
    if validated["physics"] is not None:
        write_json(directory / "authored.physics3.json", validated["physics"])
        refs["Physics"] = "performance/authored.physics3.json"
    write_json(directory / "library.json", library)
    write_json(models[0], model)
    return library


def evaluate_curve(parsed, t, restricted=False):
    if t < parsed[0][1][0]:
        return parsed[0][1][1]
    for kind, start, points in parsed:
        end = points[-1]
        if t >= end[0]:
            continue
        if kind == 2:
            return start[1]
        if kind == 3:
            return end[1]
        u = (t - start[0]) / (end[0] - start[0])
        if kind == 0:
            return start[1] + (end[1] - start[1]) * u
        if not restricted:
            times = [start[0], *(p[0] for p in points)]
            low, high = 0.0, 1.0
            for _ in range(45):
                u = (low + high) / 2
                if cubic(times, u) < t:
                    low = u
                else:
                    high = u
            u = (low + high) / 2
        return cubic([start[1], *(p[1] for p in points)], u)
    return parsed[-1][2][-1][1]


def easing(value):
    return 0.5 - 0.5 * math.cos(math.pi * max(0, min(1, value)))


def fade_weight(t, start, end, fade_in, fade_out):
    if t < start or (end is not None and t > end):
        return 0.0
    incoming = easing((t - start) / fade_in) if fade_in > 0 else 1.0
    outgoing = easing((end - t) / fade_out) if end is not None and fade_out > 0 else 1.0
    return incoming * outgoing


class PlaybackSampler:
    """Sample authored clips, then expression layers, starting from model defaults.

Clips use exact Duration loops without an extra SDK-specific correction frame.
Nonloop clips reset to defaults after their end, or hold when explicitly selected.
Expression events overlap in their listed order; end includes fade-out. The output
is clamped to parameter ranges. Optional physics runs after this sampler.
"""

    def __init__(self, parameters, motion_document=None, expression_events=None, *, library=None, request=None):
        self.parameters = parameters
        self.clips, self.expressions = [], []
        if request is not None:
            request = PlaybackRequest.model_validate(request)
            library = validate_library(contents(library), parameters)
            clips = request.clips
            if request.motion:
                from .performance_schema import MotionClip

                clips = [MotionClip(name=request.motion, group=request.group, loop=request.loop)]
            for clip in clips:
                try:
                    document = library["motions"][clip.group][clip.name]
                except KeyError:
                    raise ValueError(f"Unknown motion: {clip.group}/{clip.name}") from None
                self._clip(document, clip.model_dump())
            events = [event.model_dump() for event in request.expressions]
            if request.expression:
                events.insert(0, {"name": request.expression, "start": 0, "end": None, "weight": 1})
            for event in events:
                if event["name"] not in library["expressions"]:
                    raise ValueError(f"Unknown expression: {event['name']}")
                self.expressions.append((library["expressions"][event["name"]], event))
        else:
            if motion_document is not None:
                self._clip(validate_motion(motion_document, parameters), {})
            for event in expression_events or []:
                self.expressions.append((validate_expression(event["document"], parameters), event))

    def _clip(self, document, options):
        start = options.get("start", 0)
        loop = options.get("loop")
        loop = document["Meta"]["Loop"] if loop is None else loop
        if loop and options.get("hold"):
            raise ValueError("Loop and hold are mutually exclusive")
        end = options.get("end")
        if end is None and not loop and not options.get("hold"):
            end = start + document["Meta"]["Duration"]
        self.clips.append((document, start, end, loop,
                           [(curve, segments(curve)) for curve in document["Curves"]]))

    def __call__(self, t):
        if not math.isfinite(t) or t < 0:
            raise ValueError("Playback time must be finite and nonnegative")
        values = {key: parameter["default"] for key, parameter in self.parameters.items()}
        for document, start, end, loop, curves in self.clips:
            if t < start or (end is not None and t > end):
                continue
            meta = document["Meta"]
            elapsed = t - start
            local = elapsed % meta["Duration"] if loop else min(elapsed, meta["Duration"])
            for curve, parsed in curves:
                fade_in = curve["FadeInTime"] if curve["FadeInTime"] >= 0 else meta["FadeInTime"]
                fade_out = curve["FadeOutTime"] if curve["FadeOutTime"] >= 0 else meta["FadeOutTime"]
                weight = fade_weight(t, start, end, fade_in, fade_out)
                target = evaluate_curve(parsed, local, meta["AreBeziersRestricted"])
                key = curve["Id"]
                values[key] += (target - values[key]) * weight
        for expression, event in self.expressions:
            weight = event.get("weight", 1) * fade_weight(
                t, event.get("start", 0), event.get("end"),
                expression["FadeInTime"], expression["FadeOutTime"],
            )
            for row in expression["Parameters"]:
                key, value = row["Id"], row["Value"]
                if row["Blend"] == "Add":
                    values[key] += value * weight
                elif row["Blend"] == "Multiply":
                    values[key] *= 1 + (value - 1) * weight
                else:
                    values[key] += (value - values[key]) * weight
        for key, value in values.items():
            if not math.isfinite(value):
                raise ValueError(f"Nonfinite playback parameter: {key}")
            values[key] = max(self.parameters[key]["min"], min(self.parameters[key]["max"], value))
        return values


def make_sampler(store, request, revision=None, snapshot=None):
    request = PlaybackRequest.model_validate(request)
    rev, parameters = parameter_map(store, revision)
    library = load_library(store, rev.id, snapshot)
    sampler = PlaybackSampler(parameters, library=library, request=request)
    physics = None
    if request.physics:
        from .physics import PhysicsSampler

        document = library["physics"]
        if document is None:
            bundle = store.revision_dir(rev.id) / "rig/bundle"
            models = list(bundle.glob("*.model3.json"))
            if len(models) != 1:
                raise ValueError("Expected exactly one model3.json")
            reference = read_json(models[0])["FileReferences"].get("Physics")
            if not reference:
                raise ValueError("Model has no physics document")
            path = (bundle / reference).resolve()
            if not path.is_relative_to(bundle.resolve()):
                raise ValueError("Invalid physics reference")
            document = read_json(path)
        physics = PhysicsSampler(parameters, document)
    return rev, library, sampler, physics
