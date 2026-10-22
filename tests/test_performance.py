from base64 import b64encode
from contextlib import asynccontextmanager
from copy import deepcopy
import io
import json
from types import SimpleNamespace

from PIL import Image
import pytest
from typer.testing import CliRunner

from live2d_pipeline import performance, video
from live2d_pipeline.performance_cli import app
from live2d_pipeline.performance_schema import PlaybackRequest
from live2d_pipeline.physics import compose
from live2d_pipeline.schema import Plan
from live2d_pipeline.store import Store, read_json, write_json


PARAMETERS = {
    "Angle": {"id": "Angle", "min": -30.0, "max": 30.0, "default": 0.0},
    "Eye": {"id": "Eye", "min": 0.0, "max": 1.0, "default": 1.0},
}


def motion_document(flat=None, *, loop=False, fade=0):
    flat = flat or [0, 0, 0, 1, 20, 0, 2, 0]
    parsed = performance.segments({"Segments": flat})
    return {
        "Version": 3,
        "Meta": {
            "Duration": 2, "Fps": 24, "Loop": loop, "AreBeziersRestricted": False,
            "CurveCount": 1, "TotalSegmentCount": len(parsed),
            "TotalPointCount": 1 + sum(len(p) for _, _, p in parsed),
            "FadeInTime": fade, "FadeOutTime": fade,
        },
        "Curves": [{"Target": "Parameter", "Id": "Angle", "Segments": flat}],
    }


def expression_document(value=0.4, blend="Multiply", *, fade=0):
    return {"Type": "Live2D Expression", "FadeInTime": fade, "FadeOutTime": fade,
            "Parameters": [{"Id": "Eye", "Value": value, "Blend": blend}]}


@pytest.fixture
def built(tmp_path):
    store = Store(tmp_path / "project")
    store.init()
    rev = store.create(Plan(request="test", prompt="test"))
    rev.status = "built"
    store.save(rev)
    directory = store.revision_dir(rev.id)
    write_json(directory / "rig/rig-context.json", {"parameters": {"parameters": list(PARAMETERS.values())}})
    for name in ("source.psd", "source.psd2live"):
        (directory / "rig" / name).write_bytes(b"saved model")
    return store, rev


@pytest.mark.parametrize("flat,t,expected", [
    ([0, 0, 0, 2, 20], 0.5, 5),
    ([0, 0, 2, 1, 20, 0, 2, 0], 0.5, 0),
    ([0, 0, 2, 1, 20, 0, 2, 0], 1, 20),
    ([0, 0, 3, 2, 20], 0, 20),
    ([0, 0, 1, 0.1, 0, 0.3, 20, 2, 20], 0.4, 10),
])
def test_all_curve_types_and_nonuniform_bezier_time(flat, t, expected):
    sampler = performance.PlaybackSampler(PARAMETERS, motion_document(flat))
    assert sampler(t)["Angle"] == pytest.approx(expected, abs=1e-8)


def test_restricted_bezier_counts_and_range_validation():
    document = motion_document([0, 0, 1, 2 / 3, 0, 4 / 3, 20, 2, 20])
    document["Meta"]["AreBeziersRestricted"] = True
    assert performance.PlaybackSampler(PARAMETERS, document)(1)["Angle"] == pytest.approx(10)
    invalid = deepcopy(document)
    invalid["Curves"][0]["Segments"][3] = 0.1
    with pytest.raises(ValueError, match="one-third"):
        performance.validate_motion(invalid, PARAMETERS)
    invalid = deepcopy(document)
    invalid["Meta"]["TotalPointCount"] += 1
    with pytest.raises(ValueError, match="metadata counts"):
        performance.validate_motion(invalid, PARAMETERS)
    invalid = deepcopy(document)
    invalid["Curves"][0]["Segments"][4:7:2] = [100, 100]
    with pytest.raises(ValueError, match="exceeds parameter range"):
        performance.validate_motion(invalid, PARAMETERS)


@pytest.mark.parametrize("mutation,match", [
    (lambda d: d["Curves"][0].update(Id="missing"), "Unknown motion"),
    (lambda d: d["Curves"][0].update(Target="PartOpacity"), "Parameter"),
    (lambda d: d["Curves"][0]["Segments"].append(0), "Truncated"),
    (lambda d: d["Curves"][0]["Segments"].__setitem__(4, float("nan")), "finite"),
    (lambda d: d["Curves"][0]["Segments"].__setitem__(3, 0), "must increase"),
    (lambda d: d["Meta"].update(UserDataCount=1), "0"),
])
def test_invalid_motion_documents_rejected(mutation, match):
    document = motion_document()
    mutation(document)
    with pytest.raises(ValueError, match=match):
        performance.validate_motion(document, PARAMETERS)


def test_expression_blends_and_fade_transition():
    events = [
        {"document": expression_document(-0.5, "Add"), "start": 0, "end": 3},
        {"document": expression_document(0.5, "Multiply"), "start": 0, "end": 3},
        {"document": expression_document(0.8, "Overwrite", fade=1), "start": 1, "end": 3},
    ]
    sampler = performance.PlaybackSampler(PARAMETERS, expression_events=events)
    assert sampler(0)["Eye"] == pytest.approx(0.25)
    assert sampler(1.5)["Eye"] == pytest.approx(0.525)
    assert sampler(2)["Eye"] == pytest.approx(0.8)
    assert sampler(2.5)["Eye"] == pytest.approx(0.525)
    assert sampler(4)["Eye"] == 1
    with pytest.raises(ValueError, match="Unknown expression"):
        performance.validate_expression({"Parameters": [{"Id": "bad", "Value": 0}]}, PARAMETERS)
    with pytest.raises(ValueError, match="outside parameter range"):
        performance.validate_expression(expression_document(2, "Overwrite"), PARAMETERS)
    versioned = {"Version": 3, **expression_document()}
    assert performance.validate_expression(versioned, PARAMETERS)["Version"] == 3
    assert "Version" not in performance.validate_expression(expression_document(), PARAMETERS)


def test_clip_loops_end_hold_and_per_parameter_fades():
    document = motion_document([0, 0, 0, 2, 20])
    library = {"expressions": {}, "motions": {"Idle": {"turn": document}}, "physics": None}
    loop = performance.PlaybackSampler(PARAMETERS, library=library,
                                       request={"motion": "turn", "loop": True})
    assert loop(0.25) == pytest.approx(loop(2.25))
    reset = performance.PlaybackSampler(PARAMETERS, library=library, request={"motion": "turn"})
    assert reset(2)["Angle"] == 20
    assert reset(2.01)["Angle"] == 0
    hold = performance.PlaybackSampler(PARAMETERS, library=library,
                                       request={"clips": [{"name": "turn", "hold": True}]})
    assert hold(3)["Angle"] == 20
    document["Curves"][0]["FadeInTime"] = 1
    document["Curves"][0]["FadeOutTime"] = 1
    faded = performance.PlaybackSampler(PARAMETERS, document)
    assert faded(0.5)["Angle"] == pytest.approx(2.5)
    assert faded(1.5)["Angle"] == pytest.approx(7.5)
    assert faded(2)["Angle"] == 0


def test_snapshot_versions_inherit_and_detect_modified_data(built):
    store, rev = built
    first = performance.put_expression(store, "smile", expression_document(), version=0)
    with pytest.raises(ValueError, match="Stale performance"):
        performance.put_expression(store, "smile", expression_document(0.3), version=0)
    child = store.create(Plan(request="child", prompt="child"))
    performance.inherit(store, child.id)
    second = performance.put_expression(store, "smile", expression_document(0.2), revision=rev.id, version=1)
    assert performance.load_library(store, child.id)["id"] == first["id"]
    assert performance.load_library(store, rev.id)["id"] == second["id"]
    assert [entry["version"] for entry in performance.history(store, rev.id)] == [2, 1]
    snapshot = store.root / "performance" / rev.id / f"{first['id']}.json"
    snapshot.write_text("{}")
    with pytest.raises(ValueError, match="snapshot was modified"):
        performance.load_library(store, child.id)


def test_empty_parent_library_is_pinned(built):
    store, rev = built
    child = store.create(Plan(request="child", prompt="child"))
    performance.inherit(store, child.id)
    performance.put_expression(store, "smile", expression_document(), revision=rev.id, version=0)
    assert performance.load_library(store, child.id)["version"] == 0


def test_derived_model_revalidates_inherited_parameter_ids(built):
    store, rev = built
    performance.put_expression(store, "smile", expression_document(), version=0)
    child = store.create(Plan(request="child", prompt="child"))
    performance.inherit(store, child.id)
    child.status = "built"
    store.save(child)
    write_json(store.revision_dir(child.id) / "rig/rig-context.json",
               {"parameters": {"parameters": [PARAMETERS["Angle"]]}})
    with pytest.raises(ValueError, match="Unknown expression parameter"):
        performance.make_sampler(store, {"expression": "smile"}, child.id)


def test_export_references_are_preserved_and_sources_immutable(built, tmp_path):
    store, rev = built
    first = performance.put_expression(store, "smile", expression_document(), version=0)
    performance.put_motion(store, "Custom", "turn", motion_document(), version=1)
    bundle = tmp_path / "export/model"
    original = {"Version": 3, "FileReferences": {"Moc": "a.moc3", "Textures": ["a.png"],
                "Physics": "a.physics3.json", "Motions": {"Idle": [{"File": "idle.motion3.json"}]}}}
    write_json(bundle / "a.model3.json", original)
    performance.attach_bundle(store, rev.id, bundle)
    model = read_json(bundle / "a.model3.json")
    refs = model["FileReferences"]
    assert refs["Moc"] == "a.moc3"
    assert refs["Motions"]["Idle"] == original["FileReferences"]["Motions"]["Idle"]
    assert read_json(bundle / refs["Expressions"][0]["File"])["Parameters"][0]["Value"] == 0.4
    assert (bundle / refs["Motions"]["Custom"][0]["File"]).is_file()
    assert performance.load_library(store, rev.id, first["id"])["version"] == 1
    with pytest.raises(ValueError, match="cannot modify"):
        performance.attach_bundle(store, rev.id, store.revision_dir(rev.id) / "rig/bundle")


def test_cli_roundtrip_import_sample_and_remove(built, tmp_path):
    store, _ = built
    runner = CliRunner()
    expression = tmp_path / "smile.exp3.json"
    write_json(expression, expression_document())
    result = runner.invoke(app, ["put-expression", str(store.root), "smile", str(expression), "--version", "0"])
    assert result.exit_code == 0, result.output
    library = json.loads(result.output)
    assert library["version"] == 1
    request = tmp_path / "playback.json"
    write_json(request, {"expression": "smile", "duration": 1, "fps": 2})
    sampled = runner.invoke(app, ["sample", str(store.root), str(request)])
    assert sampled.exit_code == 0, sampled.output
    frames = json.loads(sampled.output)["frames"]
    assert [frame["time"] for frame in frames] == [0, 0.5, 1]
    assert all(frame["parameters"]["Eye"] == 0.4 for frame in frames)
    exported = tmp_path / "library.json"
    assert runner.invoke(app, ["export", str(store.root), str(exported)]).exit_code == 0
    removed = runner.invoke(app, ["remove", str(store.root), "expression", "--name", "smile", "--version", "1"])
    assert removed.exit_code == 0, removed.output
    imported = runner.invoke(app, ["import", str(store.root), str(exported), "--version", "2"])
    assert imported.exit_code == 0, imported.output
    assert json.loads(imported.output)["version"] == 3


def test_authored_video_uses_sampler_and_records_endpoint_without_mutating_rig(built, monkeypatch):
    store, rev = built
    rendered = []
    png = io.BytesIO()
    Image.new("RGBA", (16, 16)).save(png, format="PNG")

    class Session:
        async def call_tool(self, name, args):
            rendered.append(args["parameters"])
            return SimpleNamespace(isError=False, content=[SimpleNamespace(type="image", data=b64encode(png.getvalue()))])

    class Rig:
        session = Session()
        tools = {"view_render_model": {}}

        async def call(self, name):
            if name == "project_get_state":
                return {"canvasWidth": 16, "canvasHeight": 16}
            return {"parameters": list(PARAMETERS.values())}

    @asynccontextmanager
    async def fake_open(*args):
        yield Rig(), None

    def fake_encode(frames, output, log, fps):
        assert len(list(frames.glob("*.png"))) == 2
        assert fps == 2
        output.write_bytes(b"video")

    monkeypatch.setattr(video, "open_rig", fake_open)
    monkeypatch.setattr(video, "encode", fake_encode)
    monkeypatch.setattr(video.shutil, "which", lambda _: "/ffmpeg")
    sampler = performance.PlaybackSampler(PARAMETERS, motion_document())
    path = video.generate(None, store, rev.id, sampler=sampler, duration=1, fps=2)
    assert [pose["Angle"] for pose in rendered] == [0, 10]
    trace = read_json(path.parent / "parameters.json")["frames"]
    assert trace[-1] == {"time": 1, "parameters": {"Angle": 20, "Eye": 1}}
    assert read_json(path.parent / "playback.json")["runtime_verified"] is False
    assert (store.revision_dir(rev.id) / "rig/source.psd2live").read_bytes() == b"saved model"


def test_timeline_rejects_ambiguous_or_nonfinite_requests():
    with pytest.raises(ValueError, match="motion or clips"):
        PlaybackRequest(motion="turn", clips=[{"name": "turn"}])
    with pytest.raises(ValueError, match="finite"):
        PlaybackRequest(duration=float("nan"))


def test_library_physics_export_and_sample_composes_after_motion(built, tmp_path):
    store, rev = built
    chain = compose([{
        "Id": "eyelid", "Input": [{"Source": {"Id": "Angle"}, "Weight": 100,
                                    "Type": "Angle", "Reflect": True}],
        "Output": [{"Destination": {"Id": "Eye"}, "Weight": 100, "Type": "Angle",
                    "Reflect": True, "Scale": 1, "VertexIndex": 2}],
        "Vertices": [{"Position": {"X": 0, "Y": index * 10}, "Mobility": 0.8,
                      "Delay": 0.9, "Acceleration": 1, "Radius": 10 if index else 0}
                     for index in range(3)],
        "Normalization": {key: {"Minimum": -30, "Default": 0, "Maximum": 30}
                          for key in ("Position", "Angle")},
    }], fps=30)
    performance.put_motion(store, "Idle", "turn", motion_document(), version=0)
    stored = performance.put_physics(store, chain, version=1)
    assert stored["physics"]["Meta"]["VertexCount"] == 3
    _, library, sampler, simulation = performance.make_sampler(
        store, {"motion": "turn", "duration": 2, "physics": True}
    )
    assert library["version"] == 2
    rows = [simulation(sampler(index / 24), index / 24) for index in range(49)]
    assert rows[12]["Angle"] == 10
    assert simulation.report()["steps"] == 60
    assert simulation.report()["official_solver_parity"] is False
    bundle = tmp_path / "model"
    write_json(bundle / "a.model3.json", {"Version": 3, "FileReferences": {"Physics": "old.json"}})
    performance.attach_bundle(store, rev.id, bundle)
    reference = read_json(bundle / "a.model3.json")["FileReferences"]["Physics"]
    assert read_json(bundle / reference)["Meta"]["VertexCount"] == 3


def test_missing_or_invalid_clip_does_not_silently_preview_defaults(built):
    store, _ = built
    with pytest.raises(ValueError, match="Unknown motion"):
        performance.make_sampler(store, {"motion": "missing"})
    with pytest.raises(ValueError, match="Unknown expression"):
        performance.make_sampler(store, {"expression": "missing"})


def test_physics_option_can_use_immutable_built_bundle_document(built):
    store, rev = built
    bundle = store.revision_dir(rev.id) / "rig/bundle"
    write_json(bundle / "model.model3.json", {"Version": 3, "FileReferences": {"Physics": "model.physics3.json"}})
    write_json(bundle / "model.physics3.json", compose([]))
    _, _, sampler, physics = performance.make_sampler(store, {"physics": True})
    assert physics(sampler(0), 0) == {"Angle": 0, "Eye": 1}
