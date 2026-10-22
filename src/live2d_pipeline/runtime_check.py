"""Run a supplied official Core library against a parameter trace in a child process."""

import json
import math
import subprocess
import sys
import tempfile
from pathlib import Path

from .store import digest


LIMITATIONS = {
    "runtime_verified": False,
    "physics_solver_parity": "not_evaluated",
    "rendered_pixels": "not_evaluated",
    "target_application": "not_evaluated",
    "scope": "Core sequential parameter updates and finite drawable geometry only",
}


class TraceError(ValueError):
    def __init__(self, message, **location):
        super().__init__(message)
        self.location = location


def finite_number(value):
    try:
        return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)
    except OverflowError:
        return False


def validate_trace(document):
    """Accept trace provenance, but require finite numeric values and strict time order."""
    if not isinstance(document, dict) or not isinstance(document.get("frames"), list) or not document["frames"]:
        raise TraceError("Trace requires a nonempty frames array", stage="trace")
    previous = -math.inf
    for index, frame in enumerate(document["frames"]):
        where = {"stage": "trace", "frame": index}
        if not isinstance(frame, dict):
            raise TraceError("Frame must be an object", **where)
        time = frame.get("time")
        if not finite_number(time) or time < 0:
            raise TraceError("Frame time must be finite and nonnegative", **where)
        if time <= previous:
            raise TraceError("Frame times must be strictly increasing", **where, time=time)
        previous = time
        parameters = frame.get("parameters")
        if not isinstance(parameters, dict):
            raise TraceError("Frame parameters must be an object", **where, time=time)
        for key, value in parameters.items():
            if not isinstance(key, str) or not key:
                raise TraceError("Parameter IDs must be nonempty strings", **where, time=time)
            if not finite_number(value):
                raise TraceError("Parameter values must be finite numbers", **where, time=time, parameter=key)
    return document["frames"]


def bundle_moc(bundle):
    bundle = Path(bundle).resolve()
    entries = list(bundle.glob("*.model3.json"))
    if len(entries) != 1:
        raise ValueError("Expected exactly one model3.json in the bundle")
    document = json.loads(entries[0].read_text())
    if not isinstance(document, dict) or not isinstance(document.get("FileReferences"), dict):
        raise ValueError("Expected a model3 object with FileReferences")
    reference = document["FileReferences"].get("Moc")
    if document.get("Version") != 3 or not isinstance(reference, str):
        raise ValueError("Expected model3 Version 3 with a Moc reference")
    moc = (bundle / reference).resolve()
    if not moc.is_relative_to(bundle) or not moc.is_file():
        raise ValueError("Moc reference is missing or outside the bundle")
    if moc.stat().st_size < 64:
        raise ValueError("MOC3 is truncated")
    with moc.open("rb") as handle:
        if handle.read(4) != b"MOC3":
            raise ValueError("Expected a MOC3 binary")
    return moc


def failure(message, *, hashes=None, **location):
    return {
        "status": "failed", "core_geometry_check": "failed", **LIMITATIONS,
        "hashes": hashes or {}, "error": {"message": message, **location},
    }


def verify_playback(bundle, trace_file, core, timeout=120):
    """Return a narrow verification receipt; never accept or modify a project revision."""
    bundle, trace_file, core = Path(bundle).resolve(), Path(trace_file).resolve(), Path(core).resolve()
    hashes = {}
    try:
        trace = json.loads(trace_file.read_text())
        frames = validate_trace(trace)
        moc = bundle_moc(bundle)
        if not core.is_file():
            raise ValueError("Supply an existing official Cubism Core shared library with --core")
        hashes = {"core_sha256": digest(core), "moc_sha256": digest(moc), "trace_sha256": digest(trace_file)}
    except TraceError as exc:
        return failure(str(exc), **exc.location)
    except (ValueError, OSError, OverflowError) as exc:
        return failure(str(exc), stage="inputs")
    if not math.isfinite(timeout) or timeout <= 0:
        return failure("Timeout must be finite and positive", hashes=hashes, stage="inputs")
    script = Path(__file__).resolve().parents[2] / "scripts/check_core_trace.py"
    with tempfile.TemporaryDirectory(prefix="live2d-core-trace-") as temporary:
        report = Path(temporary) / "report.json"
        command = [sys.executable, str(script), str(bundle), str(trace_file), str(core), str(report)]
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
        except subprocess.TimeoutExpired:
            location = {}
            if report.is_file():
                try:
                    pending = json.loads(report.read_text())
                    if pending.get("hashes") == hashes:
                        location = pending.get("error", {})
                except (ValueError, OSError, AttributeError):
                    pass
            return failure("Core trace worker timed out", hashes=hashes, stage="worker",
                           timeout=timeout, last_location=location)
        except OSError as exc:
            return failure(str(exc), hashes=hashes, stage="worker")
        if not report.is_file():
            return failure("Core worker exited without a report", hashes=hashes, stage="worker",
                           exit_code=result.returncode, stderr=result.stderr[-4000:])
        try:
            receipt = json.loads(report.read_text())
            if not isinstance(receipt, dict) or receipt.get("status") not in {"passed", "failed"}:
                raise ValueError("Invalid Core worker report")
            if receipt.get("status") == "passed" and result.returncode != 0:
                raise ValueError("Core worker reported success but exited unsuccessfully")
            if receipt.get("hashes") != hashes:
                raise ValueError("Core, MOC or trace identity changed before verification")
            current = {"core_sha256": digest(core), "moc_sha256": digest(moc), "trace_sha256": digest(trace_file)}
            if current != hashes:
                raise ValueError("Core, MOC or trace changed during verification")
            if receipt["status"] == "passed" and receipt.get("frames") != len(frames):
                raise ValueError("Core worker did not verify every trace frame")
            if receipt["status"] == "passed" and (
                receipt.get("core_geometry_check") != "passed"
                or not isinstance(receipt.get("vertices"), int)
                or receipt["vertices"] <= 0
                or receipt.get("vertex_checks") != receipt["vertices"] * len(frames)
            ):
                raise ValueError("Core worker returned an incomplete geometry receipt")
        except (ValueError, OSError) as exc:
            return failure(str(exc), hashes=hashes, stage="worker")
        return {**receipt, **LIMITATIONS, "frames_requested": len(frames), "worker_exit_code": result.returncode}
