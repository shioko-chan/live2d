from pathlib import Path
import shutil

from PIL import Image
import pytest
from pydantic import ValidationError

from live2d_pipeline import assets, refinement, review, vision
from live2d_pipeline.config import Config
from live2d_pipeline.rig import pose_samples
from live2d_pipeline.schema import Binding, Part, Parts, Plan, Refinement, Stage
from live2d_pipeline.store import Store, digest, read_json, write_json


def part():
    return Part(
        id="shirt", name="上衣", semantic="topwear", order=0, bounds=(2, 3, 20, 30), image="shirt.png"
    )


def edit(**changes):
    values = dict(
        action="split",
        source="shirt",
        name="领结",
        box=(4, 4, 8, 16),
        point=(8, 10),
        binding=Binding(motion="sway"),
        reason="领结独立摆动",
    )
    return Refinement(**(values | changes))


def test_refinement_rejects_invalid_selections_and_motion():
    for changes in ({"box": (-1, 0, 4, 4)}, {"point": (18, 20)}, {"action": "bind"}):
        with pytest.raises(ValidationError):
            edit(**changes)
    with pytest.raises(ValidationError):
        Binding(motion="sway", root=(0.5, 0.5), tip=(0.5, 0.5))
    with pytest.raises(ValidationError):
        Binding(degrees=float("nan"))
    with pytest.raises(ValidationError):
        Binding(root=(1.1, 0))
    manifest = Parts(width=40, height=40, parts=[part()])
    for edits in ([edit(source="invented")], [edit(box=(10, 4, 15, 16), point=(12, 8))], [edit(), edit()]):
        with pytest.raises(ValueError):
            refinement.validate_edits(manifest, edits)
    manifest.parts[0].semantic = "face"
    with pytest.raises(ValueError):
        refinement.validate_edits(manifest, [edit()])


def test_split_preserves_neutral_and_saves_provenance(tmp_path, monkeypatch, local_config):
    source = Image.new("RGBA", (20, 30), "navy")
    source.save(tmp_path / "shirt.png")
    assets.save_parts(tmp_path, Parts(width=40, height=40, parts=[part()]))
    assets.assemble(tmp_path, tmp_path / "composite.png")
    original = (tmp_path / "composite.png").read_bytes()

    def fake_run(command, log, **kwargs):
        work = log.parent
        if log.name == "segment.log":
            mask = Image.new("L", source.size)
            mask.paste(255, (4, 4, 12, 20))
            mask.save(work / "mask-0.png")
            write_json(work / "segmentation.json", [{"pixels": 128, "score": 0.95, "mask": "mask-0.png"}])
        if log.name == "inpaint.log":
            under = source.copy()
            child = Image.new("RGBA", source.size)
            for y in range(4, 20):
                for x in range(4, 12):
                    child.putpixel((x, y), source.getpixel((x, y)))
                    under.putpixel((x, y), (255, 0, 0, 255))
            under.save(work / "underpaint.png")
            child.save(work / "component.png")

    from live2d_pipeline import repair

    monkeypatch.setattr(repair, "run", fake_run)
    monkeypatch.setattr(
        vision,
        "ask",
        lambda *a: vision.ExtractionCheck(candidate="candidate-0", confidence=0.95, reason="isolated bow"),
    )
    refinement.apply(local_config, tmp_path, [edit()])
    manifest = assets.load_parts(tmp_path)
    added = manifest.parts[-1]
    assert added.bounds == (6, 7, 8, 16)
    assert added.derived_from == "shirt"
    assert added.binding.motion == "sway"
    assert (tmp_path / "composite.png").read_bytes() == original
    assert read_json(tmp_path / "refinement-checks.json")["neutral_pixels_identical"]


def test_pose_coverage_includes_face_and_component_controls():
    parameters = {
        k: {"min": -1, "max": 1, "default": 0}
        for k in ("ParamAngleX", "ParamAngleY", "ParamEyeBallX", "ParamBrowLY", "ParamComponent_test")
    }
    samples = pose_samples(parameters)
    assert {"ParamAngleY": -1} in samples
    assert {"ParamEyeBallX": 1} in samples
    assert {"ParamComponent_test": -1} in samples
    assert {"ParamAngleX": 1, "ParamEyeBallX": 1} in samples


def test_review_rejects_changed_artifact(tmp_path):
    store = Store(tmp_path)
    store.init()
    rev = store.create(Plan(request="test", prompt="test"))
    path = store.revision_dir(rev.id) / "evidence.png"
    path.write_bytes(b"original")
    rev.status = "built"
    rev.stages = {
        "rig": Stage(fingerprint="test", state="complete", artifacts={"evidence.png": digest(path)})
    }
    store.save(rev)
    path.write_bytes(b"changed")
    with pytest.raises(ValueError, match="Artifact changed"):
        review.built_directory(store, rev.id)


def test_refine_previews_once_and_never_accepts(tmp_path, monkeypatch):
    store = Store(tmp_path)
    store.init()
    first = store.create(Plan(request="test", prompt="test"))
    first.status = "built"
    store.save(first)
    manifest = Parts(
        width=40, height=40, parts=[part().model_copy(update={"binding": Binding(motion="sway", degrees=4)})]
    )
    material = store.revision_dir(first.id) / "materials"
    material.mkdir()
    assets.save_parts(material, manifest)
    from live2d_pipeline import repair
    from live2d_pipeline.repair_schema import DraftCreate

    Image.new("RGBA", (20, 30), "navy").save(material / "shirt.png")
    assets.assemble(material, material / "composite.png")
    draft = repair.create(Config(), store, DraftCreate(revision=first.id, part="shirt", mode="bind"))
    calls = []
    monkeypatch.setattr(review, "propose", lambda *a, **k: ([draft], "调整摆幅"))
    monkeypatch.setattr(repair, "binding_preview", lambda *a: calls.append(a[2]))
    monkeypatch.setattr(repair, "inspect", lambda *a: calls.append("inspect"))

    def build(self, rev):
        calls.append(rev.id)
        if rev.id != first.id:
            shutil.copytree(material, self.store.revision_dir(rev.id) / "materials")
        rev.status = "built"
        self.store.save(rev)

    from live2d_pipeline.pipeline import Pipeline

    monkeypatch.setattr(Pipeline, "run", build)
    monkeypatch.setattr(
        review,
        "inspect",
        lambda *a, **k: {
            "revision": a[3],
            "findings": [{"part": "shirt", "repair": "reduce_sway", "confidence": 0.95, "severity": "error"}],
        },
    )
    report = review.refine(Config(), store, "摆动太大")
    assert calls == [draft.id, "inspect"]
    assert report["drafts"][0]["status"] == "ready_for_review"
    assert store.get().id == first.id
    assert store.get().status == "built"
    assert read_json(store.root / "project.json")["accepted"] is None


def test_review_rejects_hallucinated_view(tmp_path, monkeypatch):
    store = Store(tmp_path)
    store.init()
    rev = store.create(Plan(request="test", prompt="test"))
    rev.status = "built"
    store.save(rev)
    directory = store.revision_dir(rev.id)
    (directory / "materials").mkdir()
    (directory / "rig").mkdir()
    assets.save_parts(directory / "materials", Parts(width=40, height=40, parts=[part()]))
    write_json(directory / "rig/pose-samples.json", [{}])
    monkeypatch.setattr(assets, "quality", lambda p: {"errors": []})
    monkeypatch.setattr(review, "validate_bundle", lambda p: {"ok": True})

    def fake_sheets(directory, output):
        write_json(output / "views.json", {"pose-00": {"parameters": {}, "visible_parts": ["shirt"]}})
        return [(Path("sheet.png"), ["pose-00"])]

    monkeypatch.setattr(review, "sheets", fake_sheets)
    monkeypatch.setattr(
        vision,
        "ask",
        lambda *a: vision.Review(
            summary="x",
            uncertainties=[],
            findings=[
                vision.Finding(
                    part="shirt",
                    view="pose-99",
                    severity="error",
                    issue="x",
                    suggestion="y",
                    confidence=0.9,
                    repair="none",
                )
            ],
        ),
    )
    with pytest.raises(ValueError, match="unknown part or view"):
        review.inspect(Config(), store, revision=rev.id)


def test_binding_uses_xywh_bounds_and_preserves_parent_frame():
    import asyncio
    import hashlib

    component = part().model_copy(update={"binding": Binding(motion="sway", root=(0.5, 0), tip=(0.5, 1))})
    lid = int.from_bytes(hashlib.sha256(component.id.encode()).digest()[:4], "big") & 0x7FFFFFFF

    class Rig:
        def __init__(self):
            self.calls = []

        async def objects(self):
            return [{"kind": "mesh", "id": "MeshShirt", "layerId": f"lyid:{lid}"}]

        async def call(self, name, args=None):
            self.calls.append((name, args))
            if name == "project_get_state":
                return {"historyHeadNodeId": "head"}
            if name == "rig_inspect":
                if args["target"]["kind"] == "mesh":
                    return {"parentId": "Body", "localBounds": [0.3, 0.4, 0.2, 0.3]}
                return {"rows": 8, "columns": 8}
            if name == "rig_preview":
                return {"diagnostics": {"flippedTriangleCount": 0, "collapsedTriangleCount": 0}}
            return {}

    rig = Rig()
    records = asyncio.run(refinement.bind(rig, Parts(width=40, height=40, parts=[component])))
    assert records[0]["parents"] == ["Body"]
    transform = next(args for name, args in rig.calls if name == "rig_transform")
    assert transform["operations"][0]["root"] == pytest.approx([0.4, 0.4])
    assert transform["operations"][0]["tip"] == pytest.approx([0.4, 0.7])
    assert sum(name == "physics_put" for name, _ in rig.calls) == 1


def test_visual_proposal_schema_requires_selection_and_motion():
    import jsonschema

    schema = vision.output_schema(vision.Proposal)
    payload = {
        "summary": "split",
        "edits": [vision.Intent(**edit().model_dump(exclude={"box", "point"})).model_dump(mode="json")],
        "uncertainties": [],
    }
    jsonschema.validate(payload, schema)
    del payload["edits"][0]["binding"]["motion"]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(payload, schema)


def test_grounding_normalization_is_not_canvas_coordinates():
    grounded = vision.Grounding(description="bow", box=(350, 340, 650, 760), point=(500, 400))
    assert review.grounding_pixels(grounded, (400, 500)) == {"box": (140, 170, 120, 210), "point": (200, 200)}
    with pytest.raises(ValidationError):
        vision.Grounding(description="bad", box=(700, 340, 350, 760), point=(500, 400))


def test_face_view_does_not_offer_hidden_bow_as_evidence():
    face = Part(id="face", name="face", semantic="face", order=0, bounds=(40, 10, 20, 20), image="face.png")
    bow = Part(id="bow", name="bow", semantic="topwear", order=1, bounds=(40, 70, 20, 20), image="bow.png")
    manifest = Parts(width=100, height=100, parts=[face, bow])
    view = review.view_window(manifest, {"ParamEyeLOpen": 0}, [])
    assert "face" in view["visible_parts"]
    assert "bow" not in view["visible_parts"]
    assert "bow" in review.view_window(manifest, {}, [])["visible_parts"]


def test_near_duplicate_masks_do_not_hide_distinct_candidates():
    from PIL import ImageDraw

    one = Image.new("L", (100, 100))
    draw = ImageDraw.Draw(one)
    draw.rectangle((30, 30, 60, 60), fill=255)
    two = one.copy()
    two.putpixel((30, 30), 0)
    assert refinement.mask_iou(one, two) > 0.9
    three = Image.new("L", (100, 100))
    ImageDraw.Draw(three).rectangle((30, 50, 60, 60), fill=255)
    assert refinement.mask_iou(one, three) < 0.9
