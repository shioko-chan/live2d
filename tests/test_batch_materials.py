"""Atomic layer assembly with real image composition and no model backends."""

import json

from PIL import Image
import pytest
import typer
from typer.testing import CliRunner

from live2d_pipeline import assets, batch_cli, batch_materials as batch
from live2d_pipeline.agent_review import AgentReview
from live2d_pipeline.batch_schema import BatchEdit
from live2d_pipeline.config import Config
from live2d_pipeline.schema import Part, Parts, Plan
from live2d_pipeline.store import Store, digest, read_json, write_json


@pytest.fixture
def scene(tmp_path, monkeypatch):
    config = Config()
    store = Store(tmp_path / "project")
    store.init()
    revision = store.create(Plan(request="fixture", prompt="fixture"))
    material = store.revision_dir(revision.id) / "materials"
    material.mkdir()
    parts = []
    for index, semantic in enumerate(("face", "eyewhite", "irides", "eyelash", "eyebrow", "mouth_open")):
        Image.new("RGBA", (16, 12), (index * 20, 100, 30, 255)).save(material / f"{semantic}.png")
        parts.append(Part(id=semantic, name=semantic, semantic=semantic, order=index * 10,
                          bounds=(4, 4, 16, 12), image=f"{semantic}.png"))
    assets.save_parts(material, Parts(width=64, height=96, parts=parts))
    assets.assemble(material, material / "composite.png")
    revision.status = "paused"
    store.save(revision)

    def forbidden(*args, **kwargs):
        pytest.fail("Unexpected inference")

    for target in ("planner.make_plan", "vision.ask", "comfy.image_job"):
        monkeypatch.setattr("live2d_pipeline." + target, forbidden)
    return config, store, revision


def png(tmp_path, name, color="red", size=(16, 12)):
    path = tmp_path / (name + ".png")
    Image.new("RGBA", size, color).save(path)
    return str(path)


def edit(tmp_path):
    return BatchEdit.model_validate({
        "request": "replace face and eye, add a separately bound hair strand",
        "operations": [
            {"action": "replace", "id": "face", "source": png(tmp_path, "face-new")},
            {"action": "replace", "id": "irides", "source": png(tmp_path, "eye-new", "blue")},
            {"action": "add", "id": "strand", "source": png(tmp_path, "strand", "green", (8, 32)),
             "semantic": "front hair", "order": 25, "placement": [30, 10, 8, 32],
             "binding": {"motion": "sway"}},
        ],
    })


def reviewed(store, draft, status="pass"):
    batch.preview(store, draft.id, draft.version)
    evidence = batch.evidence(store, draft.id, draft.version)
    return batch.record_review(store, draft.id, draft.version, AgentReview.model_validate({
        "evidence_id": evidence["id"], "summary": "test fixture, not art assessment",
        "items": [{"name": "all changed layers", "status": status, "reason": "synthetic fixture"}],
    }))


def test_multi_layer_apply_preserves_resolution_snapshot_and_old_revision(scene, tmp_path):
    config, store, base = scene
    old_files = batch.material_files(store.revision_dir(base.id) / "materials")
    spec = edit(tmp_path)
    draft = batch.create(store, spec)
    # The author can overwrite or delete external images after capture without changing this batch.
    Image.new("RGBA", (2, 3), "black").save(spec.operations[0].source)
    draft = reviewed(store, draft)
    result = batch.apply(config, store, draft.id, draft.version)
    assert result.parent == base.id
    assert store.get().id == result.id
    assert len(store.history()) == 2
    material = store.revision_dir(result.id) / "materials"
    manifest = assets.load_parts(material)
    assert (manifest.width, manifest.height) == (64, 96)
    assert [p.id for p in manifest.parts] == ["face", "eyewhite", "irides", "strand", "eyelash", "eyebrow", "mouth_open"]
    assert next(p for p in manifest.parts if p.id == "strand").binding.motion == "sway"
    assert Image.open(material / "face.png").size == (16, 12)
    assert Image.open(material / "face.png").getpixel((0, 0)) == (255, 0, 0, 255)
    assert batch.material_files(store.revision_dir(base.id) / "materials") == old_files
    assert batch.load(store, draft.id).applied_revision == result.id
    assert read_json(store.root / "project.json")["accepted"] is None
    with pytest.raises(RuntimeError, match="already applied"):
        batch.apply(config, store, draft.id, draft.version)


def test_full_material_set_from_empty_project(tmp_path):
    store = Store(tmp_path / "empty")
    store.init()
    source = png(tmp_path, "face")
    spec = BatchEdit.model_validate({"request": "start with an authored PNG", "canvas": [1024, 1536],
        "operations": [{"action": "add", "id": "face", "semantic": "face", "source": source,
                        "placement": [100, 200, 16, 12], "order": 0}]})
    draft = reviewed(store, batch.create(store, spec))
    assert draft.result["preview"]["ready"]
    assert not draft.result["preview"]["ready_for_rig"]
    with pytest.raises(ValueError, match="incomplete"):
        batch.apply(Config(), store, draft.id, 1, until="rig")
    assert store.history() == []
    result = batch.apply(Config(), store, draft.id, 1, until="materials")
    assert result.parent is None
    assert len(store.history()) == 1
    assert Image.open(store.revision_dir(result.id) / "materials/composite.png").size == (1024, 1536)


@pytest.mark.parametrize("fault", ["missing", "dimensions", "outside", "order", "semantic"])
def test_invalid_second_replacement_leaves_batch_and_project_unchanged(scene, tmp_path, fault):
    _, store, base = scene
    draft = reviewed(store, batch.create(store, edit(tmp_path)))
    before = digest(batch.directory(store, draft.id) / "draft.json")
    spec = edit(tmp_path)
    second = spec.operations[1]
    if fault == "missing":
        second.source = str(tmp_path / "missing.png")
    elif fault == "dimensions":
        second.source = png(tmp_path, "wrong-size", size=(32, 24))
    elif fault == "outside":
        second.placement = (60, 90, 16, 12)
    elif fault == "order":
        second.order = 0
    else:
        second.semantic = "unsupported-category"
    with pytest.raises((ValueError, OSError)):
        batch.update(store, draft.id, 1, spec)
    assert digest(batch.directory(store, draft.id) / "draft.json") == before
    assert not list(batch.directory(store, draft.id).glob("pending-*"))
    assert store.get().id == base.id
    assert len(store.history()) == 1


@pytest.mark.parametrize("change", ["input", "base-snapshot", "base-revision", "candidate", "receipt", "evidence", "version", "fail", "uncertain", "head"])
def test_stale_or_failed_review_cannot_change_head(scene, tmp_path, change):
    config, store, base = scene
    draft = reviewed(store, batch.create(store, edit(tmp_path)), status=change if change in {"fail", "uncertain"} else "pass")
    root = batch.directory(store, draft.id)
    expected = base.id
    if change == "input":
        Image.new("RGBA", (16, 12), "black").save(root / draft.inputs["face"]["path"])
    elif change == "base-snapshot":
        Image.new("RGBA", (16, 12), "black").save(root / "base/face.png")
    elif change == "base-revision":
        Image.new("RGBA", (16, 12), "black").save(store.revision_dir(base.id) / "materials/face.png")
    elif change == "candidate":
        Image.new("RGBA", (16, 12), "black").save(root / draft.result["preview"]["path"] / "materials/face.png")
    elif change == "receipt":
        write_json(root / draft.result["preview"]["path"] / "complete.json", {})
    elif change == "evidence":
        Image.new("RGBA", (64, 96), "black").save(root / "base/composite.png")
    elif change == "version":
        draft2 = batch.update(store, draft.id, 1, edit(tmp_path))
        assert draft2.version == 2 and draft2.result == {}
    elif change == "head":
        expected = store.create(Plan(request="different edit", prompt="fixture")).id
    with pytest.raises((ValueError, RuntimeError)):
        batch.apply(config, store, draft.id, 1)
    assert store.get().id == expected
    assert batch.load(store, draft.id).applied_revision is None


@pytest.mark.parametrize("failure", ["exception", "paused", "input-changed-during-build"])
def test_build_failure_never_checks_out_or_leaves_partial_revision(scene, tmp_path, monkeypatch, failure):
    config, store, base = scene
    draft = reviewed(store, batch.create(store, edit(tmp_path)))
    performance_before = set((store.root / "performance").iterdir())

    def run(pipeline, rev, until):
        assert read_json(store.root / "project.json")["head"] == base.id
        if failure == "exception":
            (store.revision_dir(rev.id) / "partial-output").write_text("failed build")
            raise RuntimeError("renderer failed")
        if failure == "input-changed-during-build":
            from live2d_pipeline.schema import Stage
            rev.stages["rig"] = Stage(fingerprint="fixture", state="complete")
            rev.status = "built"
            Image.new("RGBA", (1, 1), "black").save(batch.directory(store, draft.id) / draft.inputs["face"]["path"])
        else:
            rev.status = "paused"

    monkeypatch.setattr("live2d_pipeline.pipeline.Pipeline.run", run)
    with pytest.raises((RuntimeError, ValueError)):
        batch.apply(config, store, draft.id, 1, until="rig")
    assert store.get().id == base.id
    assert len(store.history()) == 1
    assert batch.load(store, draft.id).applied_revision is None
    assert set((store.root / "performance").iterdir()) == performance_before


def test_reorder_remove_and_clear_binding_in_one_batch(scene, tmp_path):
    config, store, base = scene
    first = reviewed(store, batch.create(store, edit(tmp_path)))
    batch.apply(config, store, first.id, 1)
    spec = BatchEdit.model_validate({"request": "reorder eye and face, remove hair, bind eyebrow",
        "operations": [
            {"action": "update", "id": "face", "order": 20},
            {"action": "update", "id": "irides", "order": 0},
            {"action": "remove", "id": "strand"},
            {"action": "update", "id": "eyebrow", "binding": {"motion": "sway"}},
        ]})
    second = reviewed(store, batch.create(store, spec))
    result = batch.apply(config, store, second.id, 1)
    material = store.revision_dir(result.id) / "materials"
    manifest = assets.load_parts(material)
    assert manifest.parts[0].id == "irides"
    assert "strand" not in {p.id for p in manifest.parts}
    assert not (material / "strand.png").exists()
    third = batch.create(store, BatchEdit.model_validate({"request": "remove independent binding",
        "operations": [{"action": "update", "id": "eyebrow", "clear_binding": True}]}))
    third = batch.preview(store, third.id, 1)
    material = batch.directory(store, third.id) / third.result["preview"]["path"] / "materials"
    assert next(p for p in assets.load_parts(material).parts if p.id == "eyebrow").binding is None


def test_cli_relative_inputs_schema_and_discard(scene, tmp_path):
    _, store, _ = scene
    app = typer.Typer()
    batch_cli.register(app)
    runner = CliRunner()
    assert runner.invoke(app, ["materials-batch", "schema"]).exit_code == 0
    spec = edit(tmp_path).model_dump()
    for op in spec["operations"]:
        op["source"] = op["source"].rsplit("/", 1)[-1]
    write_json(tmp_path / "edit.json", spec)
    result = runner.invoke(app, ["materials-batch", "create", str(store.root), str(tmp_path / "edit.json")])
    assert result.exit_code == 0, result.output
    draft = json.loads(result.stdout)
    result = runner.invoke(app, ["materials-batch", "preview", str(store.root), draft["id"], "--version", "1"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["result"]["preview"]["ready"]
    result = runner.invoke(app, ["materials-batch", "discard", str(store.root), draft["id"], "--version", "1"])
    assert result.exit_code == 0, result.output
    assert batch.list_drafts(store) == []


@pytest.mark.parametrize("operations", [
    [{"action": "remove", "id": "face", "binding": {"motion": "sway"}}],
    [{"action": "update", "id": "face"}],
    [{"action": "add", "id": "face", "source": "face.png"}],
    [{"action": "remove", "id": "face"}, {"action": "remove", "id": "face"}],
])
def test_batch_schema_rejects_ambiguous_operations(operations):
    with pytest.raises(ValueError):
        BatchEdit.model_validate({"request": "invalid", "operations": operations})


def test_noncanonical_material_paths_are_rejected_before_assembly(scene, tmp_path):
    _, store, base = scene
    materials = store.revision_dir(base.id) / "materials"
    manifest = assets.load_parts(materials)
    (materials / "nested").mkdir()
    manifest.parts[1].image = "nested/../eyewhite.png"
    assets.save_parts(materials, manifest)
    original = digest(materials / "eyewhite.png")
    with pytest.raises(ValueError, match="parent traversal"):
        batch.create(store, edit(tmp_path))
    assert digest(materials / "eyewhite.png") == original
    assert batch.list_drafts(store) == []
