"""Public CLI, revision inheritance and normal export must share the same library."""

import json

from PIL import Image
import pytest
from typer.testing import CliRunner

from live2d_pipeline import performance
from live2d_pipeline.cli import app
from live2d_pipeline.inspection import inspect_project
from live2d_pipeline.schema import Plan, Stage
from live2d_pipeline.services import export_revision
from live2d_pipeline.store import Store, digest, read_json, write_json


@pytest.fixture
def built(tmp_path):
    store = Store(tmp_path / "project")
    store.init()
    rev = store.create(Plan(request="fixture", prompt="fixture"))
    root = store.revision_dir(rev.id)
    (root / "materials").mkdir()
    bundle = root / "rig/bundle"
    bundle.mkdir(parents=True)
    # Structurally valid test bytes, never presented as a real compatible MOC.
    (bundle / "fixture.moc3").write_bytes(b"MOC3" + b"\0" * 64)
    Image.new("RGBA", (8, 8), "red").save(bundle / "texture.png")
    write_json(bundle / "fixture.model3.json", {
        "Version": 3, "FileReferences": {"Moc": "fixture.moc3", "Textures": ["texture.png"]},
    })
    write_json(root / "rig/rig-context.json", {"parameters": {"parameters": [
        {"id": "Eye", "min": 0, "max": 1, "default": 1},
    ]}})
    for filename in ("source.psd", "source.psd2live"):
        (root / "rig" / filename).write_bytes(b"fixture source")
    rev.status = "built"
    rev.stages["rig"] = Stage(state="complete", fingerprint="fixture", artifacts={
        str(p.relative_to(root)): digest(p) for p in (root / "rig").rglob("*") if p.is_file()
    })
    store.save(rev)
    return store, rev


def expression(value):
    return {"Type": "Live2D Expression", "Parameters": [
        {"Id": "Eye", "Value": value, "Blend": "Overwrite"},
    ]}


def test_store_automatically_pins_library_before_parent_is_edited(built):
    store, parent = built
    snapshot = performance.put_expression(store, "half", expression(0.5))
    child = store.create(Plan(request="child", prompt="child"))
    performance.put_expression(store, "half", expression(0.2), revision=parent.id, version=1)
    assert performance.load_library(store, child.id)["id"] == snapshot["id"]
    store.checkout(parent.id)
    later = store.create(Plan(request="later", prompt="later"))
    assert performance.load_library(store, later.id)["version"] == 2


def test_normal_export_includes_managed_assets_and_preserves_build(built, tmp_path):
    store, rev = built
    before = dict(rev.stages["rig"].artifacts)
    library = performance.put_expression(store, "half", expression(0.5))
    write_json(store.revision_dir(rev.id) / "material-batch.json", {"id": "batch provenance"})
    target = export_revision(store, tmp_path / "delivery")
    model = read_json(target / "model/fixture.model3.json")
    entry = model["FileReferences"]["Expressions"][0]
    assert entry["Name"] == "half"
    assert read_json(target / "model" / entry["File"])["Parameters"][0]["Value"] == 0.5
    assert read_json(target / "performance.json")["id"] == library["id"]
    assert read_json(target / "sources/material-batch.json")["id"] == "batch provenance"
    report = read_json(target / "validation.json")
    assert report["performance"]["snapshot"] == library["id"]
    assert report["runtime_verified"] is False
    assert all(digest(store.revision_dir(rev.id) / p) == sha for p, sha in before.items())
    assert inspect_project(store, stage="rig")["performance"]["expressions"] == ["half"]


def test_export_rejects_library_parameter_removed_in_descendant(built, tmp_path):
    store, parent = built
    performance.put_expression(store, "half", expression(0.5))
    child = store.create(Plan(request="child", prompt="child"))
    import shutil

    shutil.copytree(store.revision_dir(parent.id) / "rig", store.revision_dir(child.id) / "rig")
    write_json(store.revision_dir(child.id) / "rig/rig-context.json", {"parameters": {"parameters": []}})
    child.status = "built"
    store.save(child)
    with pytest.raises(ValueError, match="Unknown expression parameter"):
        export_revision(store, tmp_path / "delivery")
    assert not (tmp_path / "delivery").exists()


@pytest.mark.parametrize("arguments,key", [
    (["materials-batch", "schema"], "operations"),
    (["performance", "schema", "playback"], "clips"),
    (["physics", "schema", "authoring"], "groups"),
])
def test_public_cli_registers_authoring_contracts(arguments, key):
    result = CliRunner().invoke(app, arguments)
    assert result.exit_code == 0, result.output
    assert key in json.loads(result.stdout)["properties"]
