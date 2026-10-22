"""Agent interfaces: paused materials, candidate evidence, revisions and immutable renders."""

import base64
from contextlib import asynccontextmanager
import io
import json
from types import SimpleNamespace

from PIL import Image
from psd_tools import PSDImage
import pytest
from typer.testing import CliRunner

from live2d_pipeline import assets, repair
from live2d_pipeline.cli import app
from live2d_pipeline.config import Config
from live2d_pipeline.schema import Part, Parts, Plan, Stage
from live2d_pipeline.store import Store, digest, read_json, write_json


@pytest.fixture
def scene(tmp_path, monkeypatch):
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        Config().path.read_text().replace('runtime = ".runtime"', f'runtime = "{tmp_path}/runtime"')
    )
    config = Config(config_file)
    store = Store(tmp_path / "character")
    store.init()
    rev = store.create(Plan(request="fixture", prompt="fixture"))
    material = store.revision_dir(rev.id) / "materials"
    material.mkdir()
    parts = []
    for index, semantic in enumerate(("face", "eyewhite", "irides", "eyelash", "eyebrow")):
        Image.new("RGBA", (16, 16), (100 + index, 80, 70, 255)).save(material / f"{semantic}.png")
        parts.append(
            Part(
                id=semantic,
                name=semantic,
                semantic=semantic,
                order=index,
                bounds=(index * 4, index * 4, 16, 16),
                image=f"{semantic}.png",
            )
        )
    assets.save_parts(material, Parts(width=64, height=96, parts=parts))
    assets.assemble(material, material / "composite.png")
    rev.status = "paused"
    rev.stages["materials"] = Stage(
        state="complete",
        fingerprint="fixture",
        artifacts={str(p.relative_to(material.parent)): digest(p) for p in material.iterdir()},
    )
    store.save(rev)

    # These commands must never secretly delegate decisions to a model.
    def forbidden(*args, **kwargs):
        pytest.fail("Unexpected model inference")

    monkeypatch.setattr("live2d_pipeline.planner.make_plan", forbidden)
    monkeypatch.setattr("live2d_pipeline.services.make_plan", forbidden)
    monkeypatch.setattr("live2d_pipeline.vision.ask", forbidden)
    monkeypatch.setattr("live2d_pipeline.comfy.image_job", forbidden)
    return config, store, rev


def invoke(*args, ok=True):
    result = CliRunner().invoke(app, list(map(str, args)))
    assert (result.exit_code == 0) == ok, (result.output, result.exception)
    return json.loads(result.stdout)


def add_preview(scene, tmp_path):
    config, store, _ = scene
    image = tmp_path / "mouth.png"
    Image.new("RGBA", (8, 8), "#cc3355").save(image)
    draft = invoke(
        "repair", "create", store.root, "--mode", "add", "--source", image, "--config", config.path
    )
    edit = draft["edit"]
    edit.update(name="mouth", semantic="mouth_open", placement=[20, 30, 8, 8])
    path = tmp_path / "edit.json"
    write_json(path, edit)
    draft = invoke("repair", "update", store.root, draft["id"], path, "--version", 1)
    assert draft["version"] == 2
    preview = invoke(
        "repair", "run", store.root, draft["id"], "preview", "--version", 2, "--config", config.path
    )
    assert preview["result"]["preview"]["ready"]
    return preview


def review_candidate(store, draft, tmp_path, status="pass"):
    evidence = invoke("repair", "evidence", store.root, draft["id"], "--version", draft["version"])
    assert all(Image.open(view["path"]).size for view in evidence["images"])
    report = tmp_path / "review.json"
    write_json(
        report,
        {
            "evidence_id": evidence["id"],
            "summary": "synthetic test evidence",
            "items": [{"name": "fixture", "status": status, "reason": "test verdict, not an art evaluation"}],
        },
    )
    return invoke("repair", "record-review", store.root, draft["id"], report, "--version", draft["version"])


def test_repair_cli_import_review_apply_preserves_base(scene, tmp_path):
    config, store, base = scene
    original = dict(base.stages["materials"].artifacts)
    draft = add_preview(scene, tmp_path)
    refused = invoke(
        "repair",
        "apply",
        store.root,
        draft["id"],
        "--version",
        2,
        "--until",
        "materials",
        "--config",
        config.path,
        ok=False,
    )
    assert "review" in refused["message"]
    assert store.get().id == base.id
    draft = review_candidate(store, draft, tmp_path)
    assert draft["result"]["visual"]["source"] == "agent"
    result = invoke(
        "repair",
        "apply",
        store.root,
        draft["id"],
        "--version",
        2,
        "--until",
        "materials",
        "--config",
        config.path,
    )
    assert result["revision"] != base.id
    assert store.get().parent == base.id
    assert result["user_acceptance"] == "pending"
    assert read_json(store.root / "project.json")["accepted"] is None
    report = invoke("inspect", store.root, "--stage", "materials")
    assert report["materials"]["quality"]["missing_parts"] == []
    assert all(digest(store.revision_dir(base.id) / p) == sha for p, sha in original.items())
    assert repair.load(store, draft["id"]).applied_revision == result["revision"]


@pytest.mark.parametrize("change", ["edit", "image", "candidate", "fail", "uncertain"])
def test_stale_or_failed_agent_evidence_cannot_apply(scene, tmp_path, change):
    config, store, base = scene
    draft = add_preview(scene, tmp_path)
    draft = review_candidate(store, draft, tmp_path, change if change in {"fail", "uncertain"} else "pass")
    root = repair.directory(store, draft["id"])
    if change == "edit":
        edit = draft["edit"]
        edit["placement"] = [21, 30, 8, 8]
        write_json(tmp_path / "changed.json", edit)
        invoke("repair", "update", store.root, draft["id"], tmp_path / "changed.json", "--version", 2)
    elif change == "image":
        Image.new("RGBA", (4, 4), "red").save(root / draft["result"]["visual"]["views"][-1]["path"])
    elif change == "candidate":
        Image.new("RGBA", (8, 8), "blue").save(root / draft["result"]["preview"]["path"] / "component.png")
    invoke(
        "repair",
        "apply",
        store.root,
        draft["id"],
        "--version",
        2,
        "--until",
        "materials",
        "--config",
        config.path,
        ok=False,
    )
    assert store.get().id == base.id
    assert repair.load(store, draft["id"]).applied_revision is None


def test_inspection_reports_tampered_artifacts_and_real_part_paths(scene):
    _, store, rev = scene
    result = invoke("inspect", store.root, "--part", "face")
    assert result["materials"]["parts"][0]["id"] == "face"
    assert result["stages"]["materials"]["integrity"] == "pass"
    assert result["visual_quality"] == "not_evaluated"
    Image.new("RGBA", (16, 16), "blue").save(store.revision_dir(rev.id) / "materials/face.png")
    result = invoke("inspect", store.root, "--stage", "materials")
    assert result["stages"]["materials"]["integrity"] == "fail"
    assert result["stages"]["materials"]["errors"][0]["error"] == "hash_mismatch"


def test_existing_human_acknowledgement_preserves_unchanged_agent_evidence(scene, tmp_path):
    config, store, _ = scene
    draft = review_candidate(store, add_preview(scene, tmp_path), tmp_path, status="uncertain")
    draft["edit"]["visual_accepted"] = True
    write_json(tmp_path / "acknowledgement.json", draft["edit"])
    updated = invoke(
        "repair", "update", store.root, draft["id"], tmp_path / "acknowledgement.json", "--version", 2
    )
    result = invoke(
        "repair",
        "apply",
        store.root,
        draft["id"],
        "--version",
        updated["version"],
        "--until",
        "materials",
        "--config",
        config.path,
    )
    assert result["status"] == "paused"
    assert result["user_acceptance"] == "pending"


def test_missing_materials_pause_without_hidden_planner(scene, tmp_path):
    config, store, _ = scene
    psd = PSDImage.new("RGBA", (64, 96))
    psd.create_pixel_layer(Image.new("RGBA", (16, 16), "pink"), name="face", left=8, top=8)
    psd.create_pixel_layer(Image.new("RGBA", (8, 8), "white"), name="eyewhite", left=12, top=12)
    source = tmp_path / "incomplete.psd"
    psd.save(source)
    plan = tmp_path / "plan.json"
    write_json(plan, {"request": "import", "prompt": "front view", "operations": []})
    result = invoke(
        "plan", store.root, "import", "--source", source, "--plan-file", plan, "--config", config.path
    )
    run = invoke("run", store.root, "--revision", result["revision"], "--config", config.path)
    assert run["status"] == "paused"
    report = invoke("inspect", store.root, "--stage", "materials")
    assert "mouth_open" in report["materials"]["quality"]["missing_parts"]
    assert len(report["materials"]["parts"]) == 2
    assert "rig" not in store.get().stages


def test_cli_schema_and_invalid_inputs(scene, tmp_path):
    config, store, _ = scene
    assert "properties" in invoke("schema", "repair-edit")
    assert "evidence_id" in invoke("schema", "visual-review")["required"]
    invoke("repair", "create", store.root, "--mode", "add", "--config", config.path, ok=False)
    draft = invoke(
        "repair", "create", store.root, "--part", "face", "--mode", "mask", "--config", config.path
    )
    write_json(tmp_path / "edit.json", draft["edit"])
    invoke("repair", "update", store.root, draft["id"], tmp_path / "edit.json", "--version", 99, ok=False)
    assert repair.load(store, draft["id"]).version == 1
    invoke("repair", "discard", store.root, draft["id"], "--version", 1)
    assert invoke("repair", "list", store.root) == []


@pytest.mark.parametrize(
    "parameters,region,ok",
    [
        ({"ParamAngleX": 15}, [5, 6, 20, 30], True),
        ({"does_not_exist": 1}, None, False),
        ({"ParamAngleX": 31}, None, False),
        ({"ParamAngleX": float("nan")}, None, False),
        ({}, [60, 0, 20, 20], False),
    ],
)
def test_pose_renderer_validates_requests_and_protects_workspace(
    scene, tmp_path, monkeypatch, parameters, region, ok
):
    config, store, rev = scene
    rigdir = store.revision_dir(rev.id) / "rig"
    rigdir.mkdir()
    (rigdir / "source.psd").write_bytes(b"mock input")
    (rigdir / "source.psd2live").write_bytes(b"immutable saved workspace")
    rev.status = "built"
    store.save(rev)
    sha = digest(rigdir / "source.psd2live")
    calls = []
    png = io.BytesIO()
    Image.new("RGBA", (16, 24), "pink").save(png, format="PNG")

    class FakeRig:
        tools = {"view_render_model": {"type": "object"}}

        async def call(self, name):
            if name == "project_get_state":
                return {"canvasWidth": 64, "canvasHeight": 96}
            return {
                "parameters": [
                    {"id": "ParamAngleX", "min": -30, "max": 30, "default": 0},
                    {"id": "ParamEyeLOpen", "min": 0, "max": 1, "default": 1},
                ]
            }

        async def call_tool(self, name, args):
            calls.append(args)
            return SimpleNamespace(
                isError=False, content=[SimpleNamespace(type="image", data=base64.b64encode(png.getvalue()))]
            )

    @asynccontextmanager
    async def open_rig(config, psd, workspace, control, log):
        assert workspace != rigdir / "source.psd2live"
        workspace.write_bytes(b"renderer touched disposable workspace")
        rig = FakeRig()
        rig.session = rig
        yield rig, None

    monkeypatch.setattr("live2d_pipeline.render.open_rig", open_rig)
    pose = tmp_path / "pose.json"
    write_json(pose, {"parameters": parameters, "region": region})
    result = invoke("render", store.root, pose, "--config", config.path, ok=ok)
    assert digest(rigdir / "source.psd2live") == sha
    if ok:
        assert calls[0]["parameters"] == {"ParamAngleX": 15, "ParamEyeLOpen": 1}
        assert calls[0]["viewport"]["left"] == 5
        assert Image.open(result["image"]).size == (16, 24)
        assert result["runtime_verified"] is False
    else:
        assert not calls
