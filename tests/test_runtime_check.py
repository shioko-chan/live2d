"""Core trace contracts use a fake ABI; these tests do not validate official Core."""

import ctypes as C
import importlib.util
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest
import typer
from typer.testing import CliRunner

from live2d_pipeline import runtime_check as check, runtime_check_cli
from live2d_pipeline.store import digest, read_json, write_json


@pytest.fixture
def worker():
    path = Path(__file__).resolve().parents[1] / "scripts/check_core_trace.py"
    spec = importlib.util.spec_from_file_location("check_core_trace_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Function:
    def __init__(self, implementation):
        self.implementation = implementation

    def __call__(self, *args):
        return self.implementation(*args)


class FakeCore:
    """Python-owned ctypes arrays simulate the ABI layout, not native Core behavior."""

    def __init__(self, worker, fail_frame=None):
        self.values = (C.c_float * 2)(99, 99)
        self.minimums = (C.c_float * 2)(-30, 0)
        self.maximums = (C.c_float * 2)(30, 1)
        self.defaults = (C.c_float * 2)(0, 1)
        self.ids = (C.c_char_p * 2)(b"ParamAngleX", b"ParamEyeLOpen")
        self.drawables = (C.c_char_p * 2)(b"ArtMesh1", b"EmptyMesh")
        self.counts = (C.c_int * 2)(2, 0)
        self.points = (worker.Vector2 * 2)(worker.Vector2(0, 0), worker.Vector2(1, 1))
        self.positions = (C.POINTER(worker.Vector2) * 2)(self.points, C.POINTER(worker.Vector2)())
        self.frames, self.resets = [], 0

        def update(model):
            self.frames.append(list(self.values))
            if fail_frame == len(self.frames) - 1:
                self.points[1].X = float("nan")

        def reset(model):
            self.resets += 1

        functions = {
            "csmGetVersion": lambda: 0x05000000,
            "csmReviveMocInPlace": lambda pointer, size: pointer,
            "csmGetSizeofModel": lambda model: 128,
            "csmInitializeModelInPlace": lambda moc, pointer, size: pointer,
            "csmGetParameterCount": lambda model: 2,
            "csmGetDrawableCount": lambda model: 2,
            "csmGetParameterIds": lambda model: self.ids,
            "csmGetParameterMinimumValues": lambda model: self.minimums,
            "csmGetParameterMaximumValues": lambda model: self.maximums,
            "csmGetParameterDefaultValues": lambda model: self.defaults,
            "csmGetParameterValues": lambda model: self.values,
            "csmGetDrawableIds": lambda model: self.drawables,
            "csmGetDrawableVertexCounts": lambda model: self.counts,
            "csmGetDrawableVertexPositions": lambda model: self.positions,
            "csmUpdateModel": update,
            "csmResetDrawableDynamicFlags": reset,
        }
        for name, function in functions.items():
            setattr(self, name, Function(function))


@pytest.fixture
def scene(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "test.moc3").write_bytes(b"MOC3" + bytes(100))
    write_json(bundle / "test.model3.json", {"Version": 3, "FileReferences": {"Moc": "test.moc3"}})
    trace = tmp_path / "trace.json"
    write_json(trace, {"provenance": {"test": True}, "frames": [
        {"time": 0, "parameters": {"ParamAngleX": 30, "ParamEyeLOpen": 0}},
        {"time": 0.25, "parameters": {}},
        {"time": 0.5, "parameters": {"ParamAngleX": -30}},
    ]})
    core = tmp_path / "fake-core.so"
    core.write_bytes(b"This is deliberately not an official or native library.")
    return bundle, trace, core


def test_mock_core_checks_all_frames_defaults_and_finite_geometry(worker, scene):
    _, trace, _ = scene
    core = FakeCore(worker)
    frames = check.validate_trace(read_json(trace))
    result = worker.evaluate(core, b"mock MOC", frames)
    assert result["frames"] == 3
    assert result["vertices"] == 2
    assert result["vertex_checks"] == 6
    assert core.frames == [[30, 0], [0, 1], [-30, 1]]
    assert core.resets == 3
    assert core.csmGetDrawableVertexPositions.restype == C.POINTER(C.POINTER(worker.Vector2))
    assert core.csmGetParameterValues.restype == C.POINTER(C.c_float)


@pytest.mark.parametrize("frame,expected", [
    ({"time": float("nan"), "parameters": {}}, "time"),
    ({"time": -1, "parameters": {}}, "time"),
    ({"time": True, "parameters": {}}, "time"),
    ({"time": 0, "parameters": {"ParamAngleX": float("inf")}}, "finite"),
    ({"time": 0, "parameters": {"ParamAngleX": True}}, "finite"),
    ({"time": 0, "parameters": {"ParamAngleX": 1 << 10000}}, "finite"),
    ({"time": 0, "parameters": {"": 1}}, "IDs"),
    ({"time": 0, "parameters": []}, "object"),
])
def test_trace_rejects_invalid_numbers_and_structure(frame, expected):
    with pytest.raises(check.TraceError, match=expected) as error:
        check.validate_trace({"frames": [frame]})
    assert error.value.location["frame"] == 0


@pytest.mark.parametrize("time", [0, -0.1])
def test_trace_times_are_strictly_increasing(time):
    with pytest.raises(check.TraceError):
        check.validate_trace({"frames": [{"time": 0, "parameters": {}}, {"time": time, "parameters": {}}]})


@pytest.mark.parametrize("document", [{}, {"frames": []}, [], {"frames": [None]}])
def test_empty_or_invalid_trace_rejected(document):
    with pytest.raises(check.TraceError):
        check.validate_trace(document)


@pytest.mark.parametrize("parameters,message", [
    ({"Unknown": 0}, "Unknown"),
    ({"ParamAngleX": 31}, "range"),
    ({"ParamEyeLOpen": -0.01}, "range"),
])
def test_authoritative_core_ids_and_ranges_reject_before_first_update(worker, parameters, message):
    core = FakeCore(worker)
    with pytest.raises(check.TraceError, match=message) as error:
        worker.evaluate(core, b"mock MOC", [{"time": 0, "parameters": parameters}])
    assert error.value.location["frame"] == 0
    assert error.value.location["parameter"] == next(iter(parameters))
    assert core.frames == []


def test_nonfinite_geometry_reports_frame_drawable_and_vertex(worker, scene):
    _, trace, _ = scene
    core = FakeCore(worker, fail_frame=1)
    with pytest.raises(check.TraceError, match="nonfinite") as error:
        worker.evaluate(core, b"mock MOC", check.validate_trace(read_json(trace)))
    assert error.value.location == {"stage": "geometry", "frame": 1, "time": 0.25,
                                    "drawable": "ArtMesh1", "vertex": 1}


def test_worker_receipt_hashes_and_limits_with_mock_abi(worker, scene, tmp_path, monkeypatch):
    bundle, trace, library = scene
    monkeypatch.setattr(worker.C, "CDLL", lambda path: FakeCore(worker))
    report = tmp_path / "receipt.json"
    assert worker.run(bundle, trace, library, report) == 0
    receipt = read_json(report)
    assert receipt["status"] == "passed"
    assert receipt["runtime_verified"] is False
    assert receipt["rendered_pixels"] == "not_evaluated"
    assert receipt["hashes"] == {"core_sha256": digest(library), "trace_sha256": digest(trace),
                                 "moc_sha256": digest(bundle / "test.moc3")}


def test_real_subprocess_rejects_non_library_without_loading_in_parent(scene):
    result = check.verify_playback(*scene)
    assert result["status"] == "failed"
    assert result["error"]["stage"] == "load-core"
    assert result["worker_exit_code"] == 1
    assert result["runtime_verified"] is False


@pytest.mark.parametrize("problem", ["timeout", "crash", "short-trace", "hash", "mutation", "success-exit-mismatch"])
def test_parent_detects_worker_and_provenance_failures(scene, monkeypatch, problem):
    bundle, trace, core = scene

    def run(command, **kwargs):
        assert kwargs["capture_output"] is True
        assert "shell" not in kwargs
        assert command[1].endswith("scripts/check_core_trace.py")
        if problem == "timeout":
            raise subprocess.TimeoutExpired(command, 120)
        if problem == "crash":
            return SimpleNamespace(returncode=-11, stdout="", stderr="mock native crash")
        report = {"status": "passed", "frames": 2 if problem == "short-trace" else 3,
                  "hashes": {"core_sha256": digest(core), "trace_sha256": digest(trace),
                             "moc_sha256": digest(bundle / "test.moc3")}}
        if problem == "hash":
            report["hashes"]["trace_sha256"] = "incorrect"
        write_json(Path(command[-1]), report)
        if problem == "mutation":
            trace.write_text("{}")
        return SimpleNamespace(returncode=1 if problem == "success-exit-mismatch" else 0, stdout="", stderr="")

    monkeypatch.setattr(check.subprocess, "run", run)
    result = check.verify_playback(*scene)
    assert result["status"] == "failed"
    assert result["error"]["stage"] == "worker"
    assert result["runtime_verified"] is False


def test_native_crash_retains_last_frame_receipt(scene, monkeypatch):
    bundle, trace, core = scene

    def run(command, **kwargs):
        hashes = {"core_sha256": digest(core), "trace_sha256": digest(trace),
                  "moc_sha256": digest(bundle / "test.moc3")}
        write_json(Path(command[-1]), check.failure("frame did not finish", hashes=hashes,
                                                   stage="geometry", frame=2, time=0.5))
        return SimpleNamespace(returncode=-11, stdout="", stderr="")

    monkeypatch.setattr(check.subprocess, "run", run)
    result = check.verify_playback(*scene)
    assert result["error"]["frame"] == 2
    assert result["worker_exit_code"] == -11


def test_complete_worker_receipt_preserves_narrow_scope(scene, monkeypatch):
    bundle, trace, core = scene

    def run(command, **kwargs):
        hashes = {"core_sha256": digest(core), "trace_sha256": digest(trace),
                  "moc_sha256": digest(bundle / "test.moc3")}
        write_json(Path(command[-1]), {"status": "passed", "hashes": hashes,
            "core_geometry_check": "passed", "frames": 3, "vertices": 2, "vertex_checks": 6,
            "runtime_verified": True})
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(check.subprocess, "run", run)
    receipt = check.verify_playback(*scene)
    assert receipt["status"] == "passed"
    assert receipt["runtime_verified"] is False
    assert receipt["frames_requested"] == 3
    assert receipt["physics_solver_parity"] == "not_evaluated"


def test_bundle_reference_and_missing_library_fail_before_worker(scene, monkeypatch):
    bundle, trace, core = scene

    def forbidden(*args, **kwargs):
        pytest.fail("Worker should not run for invalid inputs")

    monkeypatch.setattr(check.subprocess, "run", forbidden)
    core.unlink()
    assert check.verify_playback(*scene)["status"] == "failed"
    write_json(bundle / "test.model3.json", {"Version": 3, "FileReferences": {"Moc": "../other.moc3"}})
    assert "outside" in check.verify_playback(*scene)["error"]["message"]


def test_cli_failure_is_json_and_nonzero(scene):
    app = typer.Typer()
    runtime_check_cli.register(app)
    # Typer flattens single-command applications; include another command like the real CLI.
    app.command()(lambda: None)
    bundle, trace, core = scene
    result = CliRunner().invoke(app, ["verify-playback", str(bundle), str(trace), "--core", str(core)])
    assert result.exit_code == 1, result.output
    assert json.loads(result.stdout)["status"] == "failed"


@pytest.mark.parametrize("explicit", [False, True])
def test_cli_core_environment_and_explicit_precedence(tmp_path, monkeypatch, explicit):
    app = typer.Typer()
    runtime_check_cli.register(app)
    app.command()(lambda: None)
    configured = tmp_path / "configured core.so"
    override = tmp_path / "override core.so"
    monkeypatch.setenv("CUBISM_CORE_LIBRARY", str(configured))
    calls = []

    def verify(bundle, trace, core, timeout):
        calls.append(core)
        return {"status": "passed", "runtime_verified": False}

    monkeypatch.setattr(runtime_check_cli, "verify", verify)
    args = ["verify-playback", str(tmp_path / "model"), str(tmp_path / "trace.json")]
    if explicit:
        args.extend(["--core", str(override)])
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 0, result.output
    assert calls == [override if explicit else configured]
    assert json.loads(result.stdout)["runtime_verified"] is False


def test_cli_requires_core_when_not_configured(tmp_path, monkeypatch):
    monkeypatch.delenv("CUBISM_CORE_LIBRARY", raising=False)
    app = typer.Typer()
    runtime_check_cli.register(app)
    app.command()(lambda: None)
    result = CliRunner().invoke(app, ["verify-playback", str(tmp_path), str(tmp_path / "trace.json")])
    assert result.exit_code == 2
    assert "--core" in result.output
