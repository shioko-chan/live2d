"""Isolated official Core geometry check, without a renderer or physics solver.

ABI reference: https://cubism.live2d.com/sdk-doc/reference/NativeCoreAPIReference_en_r15.pdf
Uses the parameter and drawable APIs available since Core 3.0. No library is bundled.
"""

import ctypes as C
import hashlib
import json
import math
import sys
from pathlib import Path

# Keep this worker runnable with the project's Python as well as an installed CLI.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from live2d_pipeline.runtime_check import (  # noqa: E402
    LIMITATIONS, TraceError, bundle_moc, failure, validate_trace,
)
from live2d_pipeline.store import digest, write_json  # noqa: E402


class Vector2(C.Structure):
    _fields_ = [("X", C.c_float), ("Y", C.c_float)]


def aligned(size):
    buffer = C.create_string_buffer(size + 63)
    return buffer, C.c_void_p((C.addressof(buffer) + 63) & ~63)


def bind(core):
    """ctypes signatures mirror the official Core C API, with opaque model pointers."""
    signatures = {
        "csmGetVersion": ([], C.c_uint),
        "csmReviveMocInPlace": ([C.c_void_p, C.c_uint], C.c_void_p),
        "csmGetSizeofModel": ([C.c_void_p], C.c_uint),
        "csmInitializeModelInPlace": ([C.c_void_p, C.c_void_p, C.c_uint], C.c_void_p),
        "csmUpdateModel": ([C.c_void_p], None),
        "csmResetDrawableDynamicFlags": ([C.c_void_p], None),
        "csmGetParameterCount": ([C.c_void_p], C.c_int),
        "csmGetParameterIds": ([C.c_void_p], C.POINTER(C.c_char_p)),
        "csmGetParameterMinimumValues": ([C.c_void_p], C.POINTER(C.c_float)),
        "csmGetParameterMaximumValues": ([C.c_void_p], C.POINTER(C.c_float)),
        "csmGetParameterDefaultValues": ([C.c_void_p], C.POINTER(C.c_float)),
        "csmGetParameterValues": ([C.c_void_p], C.POINTER(C.c_float)),
        "csmGetDrawableCount": ([C.c_void_p], C.c_int),
        "csmGetDrawableIds": ([C.c_void_p], C.POINTER(C.c_char_p)),
        "csmGetDrawableVertexCounts": ([C.c_void_p], C.POINTER(C.c_int)),
        "csmGetDrawableVertexPositions": ([C.c_void_p], C.POINTER(C.POINTER(Vector2))),
    }
    for name, (arguments, result) in signatures.items():
        function = getattr(core, name)
        function.argtypes, function.restype = arguments, result


def ids(pointer, count, label):
    if count and not pointer:
        raise TraceError(f"Core returned null {label} IDs", stage="model")
    result = []
    for index in range(count):
        value = pointer[index]
        if not value:
            raise TraceError(f"Core returned an empty {label} ID", stage="model", index=index)
        result.append(value.decode("utf-8"))
    if len(result) != len(set(result)):
        raise TraceError(f"Core returned duplicate {label} IDs", stage="model")
    return result


def evaluate(core, moc, frames, progress=None):
    """Native calls live here; production invokes this only inside the worker process."""
    bind(core)
    if len(moc) > 0xFFFFFFFF:
        raise TraceError("MOC exceeds Core's unsigned size limit", stage="model")
    moc_buffer, moc_pointer = aligned(len(moc))
    C.memmove(moc_pointer, moc, len(moc))
    revived = core.csmReviveMocInPlace(moc_pointer, len(moc))
    if not revived:
        raise TraceError("Core rejected MOC3", stage="model")
    size = core.csmGetSizeofModel(revived)
    if not 0 < size <= 2_000_000_000:
        raise TraceError("Core returned an invalid model allocation size", stage="model")
    model_buffer, model_pointer = aligned(size)
    model = core.csmInitializeModelInPlace(revived, model_pointer, size)
    if not model:
        raise TraceError("Core model initialization failed", stage="model")
    # Both backing buffers remain referenced until the last native call has completed.
    buffers = (moc_buffer, model_buffer)
    parameter_count = core.csmGetParameterCount(model)
    drawable_count = core.csmGetDrawableCount(model)
    if not 0 <= parameter_count <= 1_000_000 or not 0 < drawable_count <= 1_000_000:
        raise TraceError("Core returned invalid parameter/drawable counts", stage="model")
    parameter_ids = ids(core.csmGetParameterIds(model), parameter_count, "parameter")
    drawable_ids = ids(core.csmGetDrawableIds(model), drawable_count, "drawable")
    values = core.csmGetParameterValues(model)
    minimums = core.csmGetParameterMinimumValues(model)
    maximums = core.csmGetParameterMaximumValues(model)
    defaults = core.csmGetParameterDefaultValues(model)
    if parameter_count and not all((values, minimums, maximums, defaults)):
        raise TraceError("Core returned null parameter arrays", stage="model")
    indices = {key: index for index, key in enumerate(parameter_ids)}
    limits = []
    for index, key in enumerate(parameter_ids):
        low, high, default = minimums[index], maximums[index], defaults[index]
        if not all(math.isfinite(v) for v in (low, high, default)) or not low <= default <= high:
            raise TraceError("Core parameter range/default is invalid", stage="model", parameter=key)
        limits.append((low, high, default))
    # Validate the entire trace against authoritative Core ranges before updating any frame.
    for index, frame in enumerate(frames):
        for key, value in frame["parameters"].items():
            where = {"stage": "parameters", "frame": index, "time": frame["time"], "parameter": key}
            if key not in indices:
                raise TraceError("Unknown Core parameter ID", **where)
            low, high, _ = limits[indices[key]]
            if not low <= value <= high:
                raise TraceError("Trace parameter is outside the Core range", **where, minimum=low, maximum=high)
    vertex_counts = core.csmGetDrawableVertexCounts(model)
    if not vertex_counts:
        raise TraceError("Core returned null vertex counts", stage="model")
    counts = [vertex_counts[index] for index in range(drawable_count)]
    if any(count < 0 for count in counts) or not 0 < sum(counts) <= 100_000_000:
        raise TraceError("Core returned invalid vertex counts", stage="model")
    vertices = sum(counts)
    for frame_index, frame in enumerate(frames):
        where = {"stage": "geometry", "frame": frame_index, "time": frame["time"]}
        if progress:
            progress(where)
        for index, (_, _, default) in enumerate(limits):
            values[index] = default
        for key, value in frame["parameters"].items():
            values[indices[key]] = value
        core.csmResetDrawableDynamicFlags(model)
        core.csmUpdateModel(model)
        positions = core.csmGetDrawableVertexPositions(model)
        if not positions:
            raise TraceError("Core returned null vertex positions", **where)
        for drawable, count in enumerate(counts):
            points = positions[drawable]
            if count and not points:
                raise TraceError("Core returned null drawable vertices", **where, drawable=drawable_ids[drawable])
            for vertex in range(count):
                if not (math.isfinite(points[vertex].X) and math.isfinite(points[vertex].Y)):
                    raise TraceError("Core produced nonfinite drawable vertices", **where,
                                     drawable=drawable_ids[drawable], vertex=vertex)
    version = core.csmGetVersion()
    del buffers
    return {
        "status": "passed", "core_geometry_check": "passed", "frames": len(frames),
        "vertices": vertices, "vertex_checks": vertices * len(frames), "drawables": drawable_count,
        "parameters": parameter_count, "core_version": version,
        "duration": frames[-1]["time"] - frames[0]["time"],
    }


def run(bundle, trace_file, library, report_path):
    hashes = {}
    where = {"stage": "inputs"}
    try:
        moc_path = bundle_moc(bundle)
        moc = moc_path.read_bytes()
        trace_bytes = trace_file.read_bytes()
        hashes = {"core_sha256": digest(library), "moc_sha256": hashlib.sha256(moc).hexdigest(),
                  "trace_sha256": hashlib.sha256(trace_bytes).hexdigest()}
        frames = validate_trace(json.loads(trace_bytes))
        # Persist the last pending location before calls that might crash native code.
        def progress(location):
            write_json(report_path, failure("Core worker did not finish this frame", hashes=hashes, **location))

        where = {"stage": "load-core"}
        progress(where)
        core = C.CDLL(str(library))
        where = {"stage": "model"}
        progress(where)
        report = evaluate(core, moc, frames, progress)
        report.update(hashes=hashes, **LIMITATIONS)
        if digest(library) != hashes["core_sha256"]:
            raise TraceError("Core library changed during verification", stage="inputs")
    except TraceError as exc:
        report = failure(str(exc), hashes=hashes, **exc.location)
    except (ValueError, OSError, AttributeError, OverflowError) as exc:
        report = failure(str(exc), hashes=hashes, **where)
    write_json(report_path, report)
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    if len(sys.argv) != 5:
        raise SystemExit("usage: check_core_trace.py BUNDLE TRACE_JSON CORE_LIBRARY REPORT_JSON")
    raise SystemExit(run(*map(Path, sys.argv[1:])))
