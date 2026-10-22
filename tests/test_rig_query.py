"""Read-only authoring inspection preserves saved workspaces and real coordinates."""

import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from live2d_pipeline import rig_query, rig_query_cli
from live2d_pipeline.config import Config
from live2d_pipeline.rig_query import RigQuery
from live2d_pipeline.schema import Plan, Stage
from live2d_pipeline.store import Store, digest, lock, read_json


@pytest.fixture
def scene(tmp_path, monkeypatch):
    config = Config()
    config.runtime = tmp_path / "runtime"
    store = Store(tmp_path / "character")
    store.init()
    rev = store.create(Plan(request="test", prompt="test"))
    directory = store.revision_dir(rev.id) / "rig"
    directory.mkdir()
    for name in ("source.psd", "source.psd2live"):
        (directory / name).write_bytes(name.encode())
    rev.status = "built"
    rev.stages["rig"] = Stage(
        fingerprint="fixture",
        state="complete",
        artifacts={f"rig/{name}": digest(directory / name) for name in ("source.psd", "source.psd2live")},
    )
    store.save(rev)
    calls, workspaces = [], []

    class Session:
        async def call(self, name, args=None):
            calls.append((name, args))
            if name == "project_list_parameters":
                return {"parameters": [{"id": "ParamSmile", "min": 0, "max": 1, "default": 0}]}
            if name == "object_get":
                assert args["target"] == {"kind": "warp", "id": "Strand"}
                return {
                    "name": "Strand",
                    "parentId": "Head",
                    "geometry": {"axes": [{"parameterId": "ParamSmile", "keys": [0, 1]}]},
                }
            assert name == "rig_inspect"
            return {
                "points": [[args["offset"], 0.25, 0.75]],
                "nextOffset": args["offset"] + args["limit"],
                "coordinateSpace": "canvas",
            }

    @asynccontextmanager
    async def open_rig(c, psd, workspace, control, log):
        assert c is config
        assert psd != directory / "source.psd"
        assert workspace != directory / "source.psd2live"
        assert psd.read_bytes() == b"source.psd"
        assert workspace.read_bytes() == b"source.psd2live"
        for path in (config.runtime / "task.lock", store.root / ".lock"):
            with pytest.raises(RuntimeError, match="already running"):
                with lock(path):
                    pass
        workspaces.append(workspace)
        workspace.write_bytes(b"temporary loader bookkeeping")
        yield Session(), None

    monkeypatch.setattr(rig_query, "open_rig", open_rig)
    monkeypatch.setattr(rig_query_cli, "Config", lambda path=None: config)
    app = typer.Typer()

    @app.callback()
    def root():
        pass

    rig_query_cli.register(app)
    return config, store, rev, calls, workspaces, app


def test_object_cli_preserves_source_and_saves_exact_authoring_geometry(scene):
    _, store, rev, calls, workspaces, app = scene
    before = (store.revision_dir(rev.id) / "revision.json").read_bytes()
    result = CliRunner().invoke(app, ["rig-inspect", str(store.root), "--object", "Strand", "--kind", "warp"])
    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    assert calls == [("object_get", {"target": {"kind": "warp", "id": "Strand"}})]
    assert report["result"]["geometry"]["axes"][0]["parameterId"] == "ParamSmile"
    assert read_json(Path(report["receipt"])) == report
    assert not workspaces[0].exists()
    assert (store.revision_dir(rev.id) / "rig/source.psd2live").read_bytes() == b"source.psd2live"
    assert (store.revision_dir(rev.id) / "revision.json").read_bytes() == before
    assert store.get().id == rev.id


def test_points_preserve_pose_space_and_pagination(scene):
    config, store, _, calls, _, _ = scene
    request = RigQuery(
        object_id="Strand",
        kind="warp",
        detail="points",
        coordinate={"ParamSmile": 0.5},
        space="canvas",
        offset=64,
        limit=128,
    )
    result = rig_query.inspect_rig(config, store, request)
    assert calls[-1] == (
        "rig_inspect",
        {
            "target": {"kind": "warp", "id": "Strand"},
            "detail": "points",
            "coordinate": {"ParamSmile": 0.5},
            "space": "canvas",
            "offset": 64,
            "limit": 128,
        },
    )
    assert result["result"]["nextOffset"] == 192
    assert result["result"]["points"] == [[64, 0.25, 0.75]]


def test_parameter_inspection_returns_actual_range_without_object_tool(scene):
    config, store, _, calls, _, _ = scene
    result = rig_query.inspect_rig(config, store, RigQuery(object_id="ParamSmile", kind="parameter"))
    assert calls == [("project_list_parameters", None)]
    assert result["result"] == {"id": "ParamSmile", "min": 0, "max": 1, "default": 0}


@pytest.mark.parametrize("coordinate", [{"Unknown": 0}, {"ParamSmile": 1.1}])
def test_invalid_pose_fails_before_geometry_and_cleans_temporary_copy(scene, coordinate):
    config, store, _, calls, workspaces, _ = scene
    with pytest.raises(ValueError, match="parameter|range"):
        rig_query.inspect_rig(
            config, store, RigQuery(object_id="Strand", kind="warp", detail="points", coordinate=coordinate)
        )
    assert calls == [("project_list_parameters", None)]
    assert not workspaces[0].exists()


@pytest.mark.parametrize(
    "values",
    [
        {"kind": "rotation", "detail": "points"},
        {"kind": "parameter", "detail": "summary"},
        {"kind": "warp", "coordinate": {"ParamSmile": 0.5}},
        {"kind": "warp", "space": "canvas"},
        {"kind": "warp", "secondary_id": "not-a-glue"},
    ],
)
def test_unsupported_query_combinations_fail_before_startup(values):
    with pytest.raises(ValueError):
        RigQuery(object_id="object", **values)


def test_tampered_stage_prevents_query(scene):
    config, store, rev, calls, workspaces, _ = scene
    (store.revision_dir(rev.id) / "rig/source.psd2live").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="Artifact changed"):
        rig_query.inspect_rig(config, store, RigQuery(object_id="Strand", kind="warp"))
    assert calls == workspaces == []


def test_single_mcp_error_group_is_actionable_json(scene, monkeypatch):
    _, store, _, _, _, app = scene

    @asynccontextmanager
    async def failing_rig(*args):
        raise ExceptionGroup("MCP", [ExceptionGroup("session", [RuntimeError("Object not found")])])
        yield

    monkeypatch.setattr(rig_query, "open_rig", failing_rig)
    result = CliRunner().invoke(app, ["rig-inspect", str(store.root), "--object", "Absent", "--kind", "warp"])
    assert result.exit_code == 1
    assert json.loads(result.stdout) == {"error": "RuntimeError", "message": "Object not found"}


def test_glue_secondary_id_uses_actual_object_get_target_schema():
    calls = []

    class Session:
        async def call(self, name, args):
            calls.append((name, args))
            return {"geometry": {}}

    asyncio.run(rig_query.query(Session(), RigQuery(object_id="Glue", kind="glue", secondary_id="Binding")))
    assert calls == [("object_get", {"target": {"kind": "glue", "id": "Glue", "secondary_id": "Binding"}})]
