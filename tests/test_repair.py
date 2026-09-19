"""Pixel invariants, interruption/concurrency boundaries and actual HTTP repair contracts."""

from PIL import Image
import pytest
from starlette.testclient import TestClient
from live2d_pipeline import assets, repair, review
from live2d_pipeline.config import Config
from live2d_pipeline.repair_schema import DraftCreate, DraftUpdate, Mark, MaskCheck
from live2d_pipeline.schema import Binding, Part, Parts, Plan
from live2d_pipeline.store import Store, digest, read_json
from live2d_pipeline.web.app import create_app


@pytest.fixture
def setup(tmp_path):
    original = Config()
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        original.path.read_text().replace('runtime = ".runtime"', f'runtime = "{tmp_path}/runtime"')
    )
    config = Config(config_file)
    store = Store(tmp_path / "projects/test")
    store.init()
    rev = store.create(Plan(request="test", prompt="test"))
    rev.status = "built"
    store.save(rev)
    base = store.revision_dir(rev.id) / "materials"
    base.mkdir()
    image = Image.new("RGBA", (40, 50), "#555ba0")
    image.save(base / "clothes.png")
    assets.save_parts(
        base,
        Parts(
            width=64,
            height=64,
            parts=[
                Part(
                    id="clothes",
                    name="衣服",
                    semantic="topwear",
                    order=1,
                    bounds=(4, 7, 40, 50),
                    image="clothes.png",
                    binding=Binding(),
                )
            ],
        ),
    )
    assets.assemble(base, base / "composite.png")
    return config, store, rev


def split_draft(setup):
    config, store, rev = setup
    draft = repair.create(config, store, DraftCreate(revision=rev.id, part="clothes"))
    draft.edit.mask.marks = [
        Mark(kind="paint", points=[(8, 10), (8, 35)], radius=3),
        Mark(kind="paint", points=[(28, 10), (28, 35)], radius=3),
        Mark(kind="erase", points=[(8, 20)], radius=1),
        Mark(kind="keep", points=[(8, 10)]),
        Mark(kind="keep", points=[(28, 10)]),
        Mark(kind="exclude", points=[(18, 20)]),
    ]
    return repair.update(store, draft.id, DraftUpdate(version=draft.version, edit=draft.edit))


def fake_inpaint(config, engine, source, work, mode):
    image = Image.open(source).convert("RGBA")
    mask = Image.open(work / "mask.png").convert("L")
    under = image.copy()
    under.paste((220, 160, 100, 255), mask=mask)
    under.save(work / "underpaint.png")


def test_cleanup_scope_is_independent_and_visible_changes_require_opt_in(setup, monkeypatch):
    config, store, _ = setup
    draft = split_draft(setup)
    root = repair.directory(store, draft.id)
    extraction = repair.mask_image(root, draft).tobytes()
    draft.edit.cleanup.expansion = 3
    draft.edit.cleanup.mask.marks = [Mark(kind="paint", points=[(18, 20)], radius=2)]
    draft = repair.update(store, draft.id, DraftUpdate(version=draft.version, edit=draft.edit))
    assert repair.mask_image(root, draft).tobytes() == extraction
    assert repair.cleanup_mask(root, draft).getpixel((18, 20)) == 255
    monkeypatch.setattr(repair, "inference", fake_inpaint)
    candidate = repair.preview_materials(config, store, draft.id, draft.version)
    assert not candidate.result["preview"]["ready"]
    assert any(
        c["name"] == "默认姿态合成像素不变" and c["status"] == "fail"
        for c in candidate.result["preview"]["checks"]
    )
    draft.edit.cleanup.allow_visible = True
    draft = repair.update(store, draft.id, DraftUpdate(version=draft.version, edit=draft.edit))
    candidate = repair.preview_materials(config, store, draft.id, draft.version)
    assert candidate.result["preview"]["ready"]
    assert all(c["status"] == "pass" for c in candidate.result["preview"]["checks"])


def test_cleanup_cannot_silently_omit_extracted_pixels(setup, monkeypatch):
    config, store, _ = setup
    draft = split_draft(setup)
    draft.edit.cleanup.mask.marks = [Mark(kind="erase", points=[(8, 10)], radius=3)]
    draft = repair.update(store, draft.id, DraftUpdate(version=draft.version, edit=draft.edit))
    monkeypatch.setattr(repair, "inference", lambda *a: pytest.fail("invalid mask ran model"))
    result = repair.preview_materials(config, store, draft.id, draft.version)
    assert not result.result["preview"]["ready"]
    assert result.result["preview"]["checks"][-1]["name"] == "清理范围覆盖提取范围"


def test_repaint_and_semantic_cleanup_supply_masks_to_generation(setup, monkeypatch):
    config, store, rev = setup
    calls = []

    def paint(config, prompt, negative, output, log, **kwargs):
        mask = Image.open(kwargs["mask"]).convert("L")
        source = Image.open(kwargs["image"]).convert("RGBA")
        calls.append((mask.tobytes(), kwargs["seed"]))
        source.paste((100, 120, 150, 255), mask=mask)
        source.save(output)

    monkeypatch.setattr(repair.comfy, "image_job", paint)
    split = split_draft(setup)
    split.edit.cleanup.backend = "comfy"
    split = repair.update(store, split.id, DraftUpdate(version=split.version, edit=split.edit))
    result = repair.preview_materials(config, store, split.id, split.version)
    assert result.result["preview"]["ready"]
    draft = repair.create(config, store, DraftCreate(revision=rev.id, part="clothes", mode="repaint"))
    draft.edit.mask.marks = [Mark(kind="paint", points=[(8, 10)], radius=3)]
    draft = repair.update(store, draft.id, DraftUpdate(version=draft.version, edit=draft.edit))
    result = repair.preview_materials(config, store, draft.id, draft.version)
    assert result.result["preview"]["ready"] and len(calls) == 2


def test_inspection_has_hidden_and_exposed_detail_evidence(setup, monkeypatch):
    config, store, _ = setup
    draft = split_draft(setup)
    monkeypatch.setattr(repair, "inference", fake_inpaint)
    draft = repair.preview_materials(config, store, draft.id, draft.version)
    root = repair.directory(store, draft.id)
    images = repair.material_review_images(root, draft, root / "inspection-test")
    assert any("不是绑定" in label for label, _ in images)
    assert any("局部放大" in label for label, _ in images)
    assert all(path.is_file() for _, path in images)


def test_failed_binding_candidate_cannot_bypass_geometry_by_rebuilding(setup, monkeypatch):
    config, store, _ = setup
    draft = split_draft(setup)
    monkeypatch.setattr(repair, "inference", fake_inpaint)
    draft = repair.preview_materials(config, store, draft.id, draft.version)
    draft = repair.publish(
        store,
        draft,
        binding_preview={
            "version": draft.version,
            "ready": False,
            "checks": [{"name": "geometry", "status": "fail", "source": "rule"}],
        },
    )
    with pytest.raises(ValueError, match="绑定候选"):
        repair.apply(config, store, draft.id, draft.version)


@pytest.mark.parametrize("implementation", [None, "obsolete"])
def test_rig_apply_requires_current_arrival_check_implementation(setup, monkeypatch, implementation):
    from live2d_pipeline import repair_rig

    config, store, rev = setup
    draft = repair.create(config, store, DraftCreate(revision=rev.id, part="clothes", mode="rig"))
    repair.publish(
        store,
        draft,
        preview={"version": draft.version, "ready": True, "implementation": implementation},
    )
    monkeypatch.setattr(repair_rig, "implementation_hash", lambda _: "current")
    with pytest.raises(ValueError, match="实际位置"):
        repair.apply(config, store, draft.id, draft.version)
    assert store.get().id == rev.id
    assert not repair.load(store, draft.id).applied_revision


def test_disconnected_tails_holes_constraints_and_neutral_composite(setup, monkeypatch):
    config, store, rev = setup
    draft = split_draft(setup)
    root = repair.directory(store, draft.id)
    mask = repair.mask_image(root, draft)
    assert mask.getpixel((8, 10)) == mask.getpixel((28, 10)) == 255
    assert mask.getpixel((8, 20)) == mask.getpixel((18, 20)) == 0
    assert all(c["status"] == "pass" for c in repair.check_mask(root, draft, mask))
    old = digest(store.revision_dir(rev.id) / "materials/clothes.png")
    monkeypatch.setattr(repair, "inference", fake_inpaint)
    result = repair.preview_materials(config, store, draft.id, draft.version)
    assert result.result["preview"]["ready"]
    work = root / result.result["preview"]["path"]
    assert (
        Image.open(work / "materials/composite.png").tobytes()
        == Image.open(root / "base/composite.png").tobytes()
    )
    assert digest(store.revision_dir(rev.id) / "materials/clothes.png") == old
    assert read_json(store.root / "project.json")["head"] == rev.id


def test_bad_mask_cannot_apply_even_if_visual_confidence_high(setup):
    config, store, _ = setup
    draft = split_draft(setup)
    draft.edit.mask.marks.append(Mark(kind="exclude", points=[(8, 10)]))
    draft = repair.update(store, draft.id, DraftUpdate(version=draft.version, edit=draft.edit))
    result = repair.preview_materials(config, store, draft.id, draft.version)
    assert not result.result["preview"]["ready"]
    with pytest.raises(ValueError, match="预览"):
        repair.apply(config, store, draft.id, draft.version)


def test_old_task_cannot_publish_after_user_edits_or_discard(setup):
    config, store, _ = setup
    draft = split_draft(setup)
    newer = repair.update(store, draft.id, DraftUpdate(version=draft.version, edit=draft.edit))
    with pytest.raises(RuntimeError, match="过期"):
        repair.publish(store, draft, preview={"ready": True})
    repair.discard(store, draft.id, newer.version)
    with pytest.raises(FileNotFoundError):
        repair.publish(store, newer, preview={"ready": True})
    assert not repair.list_drafts(store)


def test_retry_only_reuses_successful_hidden_paint_step(setup, monkeypatch):
    config, store, _ = setup
    draft = split_draft(setup)
    calls = []

    def paint(*args):
        calls.append(1)
        fake_inpaint(*args)

    monkeypatch.setattr(repair, "inference", paint)
    repair.preview_materials(config, store, draft.id, draft.version)
    repair.preview_materials(config, store, draft.id, draft.version, retry=True)
    assert len(calls) == 1
    # Another draft's failed stage does not remove these artifacts.
    first = repair.load(store, draft.id)
    old_path = repair.directory(store, draft.id) / first.result["preview"]["path"]
    assert (old_path / "materials/composite.png").is_file()


def test_import_preserves_id_placement_and_binding_and_incomplete_can_resume(setup):
    config, store, rev = setup
    uploads = config.runtime / "web/uploads"
    uploads.mkdir(parents=True)
    token = "a" * 32 + ".png"
    Image.new("RGBA", (10, 20), "#ffe0a0").save(uploads / token)
    draft = repair.create(
        config, store, DraftCreate(revision=rev.id, part="clothes", mode="replace", upload=token)
    )
    draft.edit.placement = (3, 4, 16, 20)
    draft = repair.update(store, draft.id, DraftUpdate(version=draft.version, edit=draft.edit))
    result = repair.preview_materials(config, store, draft.id, draft.version)
    root = repair.directory(store, draft.id) / result.result["preview"]["path"] / "materials"
    part = assets.load_parts(root).parts[0]
    assert part.id == "clothes" and part.bounds == (3, 4, 16, 20) and part.binding == Binding()
    assert Image.open(root / part.image).size == (16, 20)
    applied = repair.apply(config, store, draft.id, draft.version, until="materials")
    assert applied.status == "paused" and applied.prepared_materials == "prepared-materials"
    assert (store.revision_dir(applied.id) / "materials/clothes.png").is_file()
    assert assets.load_parts(store.revision_dir(rev.id) / "materials").parts[0].bounds == (4, 7, 40, 50)


def test_region_names_do_not_replace_pixel_evidence(setup):
    config, store, _ = setup
    draft = split_draft(setup)
    draft.edit.mask.checks = [
        MaskCheck(name="两条飘带都在", kind="include", box=(16, 20, 3, 3), confirmed=True)
    ]
    root = repair.directory(store, draft.id)
    report = repair.check_mask(root, draft, repair.mask_image(root, draft))
    assert report[-1]["status"] == "fail" and report[-1]["coverage"] == 0


def test_http_drafts_persist_and_validate_paths_without_starting_models(setup, monkeypatch):
    import subprocess

    config, store, rev = setup
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("read/edit started a model"))
    headers = {"X-L2D-Request": "1", "Origin": "http://127.0.0.1"}
    with TestClient(
        create_app(config, store.root.parent), base_url="http://127.0.0.1", headers=headers
    ) as client:
        payload = {"revision": rev.id, "part": "clothes", "mode": "split"}
        response = client.post("/api/projects/test/repairs", json=payload)
        assert response.status_code == 201
        draft = response.json()
        identifier = draft["id"]
        endpoint = "/api/projects/test/repairs/" + identifier
        assert client.get(draft["source"]).status_code == 200
        assert client.get(draft["mask"]).headers["content-type"] == "image/png"
        draft["edit"]["mask"]["marks"] = [{"kind": "paint", "points": [[5, 5]], "radius": 3}]
        assert client.put(endpoint, json={"version": 1, "edit": draft["edit"]}).status_code == 200
        assert client.put(endpoint, json={"version": 1, "edit": draft["edit"]}).status_code == 409
        assert client.get(endpoint).json()["version"] == 2
        import io

        actual = Image.open(io.BytesIO(client.get(draft["mask"]).content))
        base = Image.open(io.BytesIO(client.get(draft["mask"] + "&annotations=0").content))
        assert actual.getpixel((5, 5)) == 255 and base.getpixel((5, 5)) == 0
        assert client.get(endpoint + "/assets/draft.json").status_code == 404
        assert client.get("/api/projects/test").json()["repairs"][0]["id"] == identifier
        draft["edit"]["mask"]["marks"][0]["points"] = [[1000, 5]]
        assert client.put(endpoint, json={"version": 2, "edit": draft["edit"]}).status_code == 400


def test_refine_produces_drafts_and_leaves_head_unchanged(setup, monkeypatch):
    config, store, rev = setup
    draft = split_draft(setup)
    monkeypatch.setattr(review, "propose", lambda *a: ([draft], "提取领结"))
    monkeypatch.setattr(repair, "segment", lambda *a: None)
    monkeypatch.setattr(repair, "inference", fake_inpaint)
    monkeypatch.setattr(repair, "inspect", lambda *a: None)
    report = review.refine(config, store, "分出领结", rev.id)
    assert report["drafts"][0]["status"] == "ready_for_review"
    assert read_json(store.root / "project.json") == {"head": rev.id, "accepted": None}


def test_split_whole_ear_island_needs_no_inpaint(setup, monkeypatch):
    from PIL import ImageDraw

    config, store, rev = setup
    base = store.revision_dir(rev.id) / "materials"
    image = Image.new("RGBA", (40, 50))
    draw = ImageDraw.Draw(image)
    draw.rectangle((3, 3, 8, 38), fill="gold")
    draw.rectangle((30, 3, 35, 38), fill="gold")
    image.save(base / "clothes.png")
    manifest = assets.load_parts(base)
    manifest.parts[0].semantic = "earwear"
    assets.save_parts(base, manifest)
    assets.assemble(base, base / "composite.png")
    draft = repair.create(config, store, DraftCreate(revision=rev.id, part="clothes"))
    draft.edit.mask.marks = [Mark(kind="paint", points=[(5, 3), (5, 38)], radius=5)]
    draft = repair.update(store, draft.id, DraftUpdate(version=1, edit=draft.edit))
    monkeypatch.setattr(repair, "inference", lambda *a: pytest.fail("isolated island inpainted"))
    result = repair.preview_materials(config, store, draft.id, draft.version)
    assert result.result["preview"]["ready"]
    work = repair.directory(store, draft.id) / result.result["preview"]["path"] / "materials"
    parts = assets.load_parts(work).parts
    assert {p.side for p in parts} == {"left", "right"}
    assert [p.bounds[2] for p in parts] == [6, 6]
    assert Image.open(work / "composite.png").tobytes() == Image.open(base / "composite.png").tobytes()

    extra = repair.create(config, store, DraftCreate(revision=rev.id, part="clothes"))
    extra.edit.mask = draft.edit.mask
    extra.edit.cleanup.allow_visible = True
    extra.edit.cleanup.mask.marks = [Mark(kind="paint", points=[(32, 10)], radius=3)]
    extra = repair.update(store, extra.id, DraftUpdate(version=extra.version, edit=extra.edit))
    result = repair.preview_materials(config, store, extra.id, extra.version)
    assert not result.result["preview"]["ready"]
    assert result.result["preview"]["checks"][-1]["name"] == "透明岛拆分与额外重绘分开执行"


def test_mask_keeps_identity_and_failed_repaint_does_not_erase_other_results(setup, monkeypatch):
    config, store, rev = setup
    draft = repair.create(config, store, DraftCreate(revision=rev.id, part="clothes", mode="mask"))
    draft.edit.mask.marks = [Mark(kind="erase", points=[(10, 10)], radius=3)]
    draft = repair.update(store, draft.id, DraftUpdate(version=1, edit=draft.edit))
    result = repair.preview_materials(config, store, draft.id, draft.version)
    work = repair.directory(store, draft.id) / result.result["preview"]["path"]
    before = digest(work / "materials/clothes.png")
    assert Image.open(work / "materials/clothes.png").getpixel((10, 10))[3] == 0
    assert assets.load_parts(work / "materials").parts[0].id == "clothes"
    other = repair.create(config, store, DraftCreate(revision=rev.id, part="clothes", mode="repaint"))
    other.edit.mask.marks = [Mark(kind="paint", points=[(10, 10)], radius=3)]
    other = repair.update(store, other.id, DraftUpdate(version=other.version, edit=other.edit))
    monkeypatch.setattr(
        repair.comfy, "image_job", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("interrupted"))
    )
    with pytest.raises(RuntimeError, match="interrupted"):
        repair.preview_materials(config, store, other.id, other.version)
    assert digest(work / "materials/clothes.png") == before
    assert repair.load(store, other.id).version == 2


def test_apply_rejects_changed_head_and_tampered_candidate_before_checkout(setup, monkeypatch):
    config, store, rev = setup
    draft = split_draft(setup)
    monkeypatch.setattr(repair, "inference", fake_inpaint)
    result = repair.preview_materials(config, store, draft.id, draft.version)
    newer = store.create(Plan(request="another", prompt="another"), parent=rev.id)
    with pytest.raises(RuntimeError, match="起点"):
        repair.apply(config, store, draft.id, draft.version)
    assert store.get().id == newer.id
    store.checkout(rev.id)
    result.result["binding_preview"] = {"path": "missing-candidate", "version": draft.version, "ready": True}
    repair.save(store, result)
    with pytest.raises(ValueError, match="绑定候选"):
        repair.apply(config, store, draft.id, draft.version)
    assert store.get().id == rev.id and not repair.load(store, draft.id).applied_revision


def test_sampled_poses_include_problem_and_neighbor_endpoints():
    from live2d_pipeline.repair_rig import poses_for_repair

    poses = poses_for_repair(
        {"angle": 12, "eye": 0},
        {"angle": {"min": -30, "max": 30, "default": 0}, "eye": {"min": 0, "max": 1, "default": 1}},
    )
    assert {} in poses and {"angle": 12, "eye": 0} in poses
    assert {"angle": 15, "eye": 0} in poses and {"angle": 12, "eye": 0.5} in poses


def test_jobs_spawn_on_long_lived_event_loop_thread(setup, monkeypatch):
    import asyncio
    from live2d_pipeline.web.jobs import Jobs

    config, store, rev = setup

    def submit(self, project, spec):
        assert asyncio.get_running_loop().is_running()
        return {"id": "test", "status": "queued"}

    monkeypatch.setattr(Jobs, "submit", submit)
    with TestClient(
        create_app(config, store.root.parent), base_url="http://127.0.0.1", headers={"X-L2D-Request": "1"}
    ) as client:
        response = client.post("/api/projects/test/jobs", json={"action": "run", "expected_head": rev.id})
        assert response.status_code == 202


def test_suggested_check_requires_user_confirmation(setup):
    _, store, _ = setup
    draft = split_draft(setup)
    draft.edit.mask.checks = [MaskCheck(name="模型建议保留", kind="include", box=(7, 10, 2, 2))]
    root = repair.directory(store, draft.id)
    checks = repair.check_mask(root, draft, repair.mask_image(root, draft))
    assert checks[-1]["status"] == "uncertain"


def test_replay_rejects_changed_parent_geometry(setup):
    from live2d_pipeline.repair_rig import safe_replay
    from live2d_pipeline.schema import Operation
    from live2d_pipeline.store import write_json
    import hashlib

    config, store, rev = setup
    base = store.revision_dir(rev.id)
    token = str(int.from_bytes(hashlib.sha256(b"clothes").digest()[:4], "big") & 0x7FFFFFFF)
    write_json(
        base / "rig/rig-context.json",
        {
            "objects": [
                {"id": "mesh", "kind": "mesh", "parentId": "warp", "layerId": "lyid:" + token},
                {"id": "warp", "kind": "warp"},
            ]
        },
    )
    material = store.root / "candidate"
    repair.copy_materials(base / "materials", material)
    op = Operation(
        kind="rig",
        tool="keyform_set",
        arguments={
            "target": {"kind": "mesh", "id": "mesh"},
            "coordinate": {"ParamAngleX": 30},
            "channels": {"opacity": 0},
        },
    )
    assert safe_replay(store, rev, material, [op])[0] == [op]
    parts = assets.load_parts(material)
    parts.parts[0].side = "left"
    assets.save_parts(material, parts)
    safe, stale = safe_replay(store, rev, material, [op])
    assert not safe and len(stale) == 1


def test_underpaint_review_and_user_acknowledgement_gate_apply(setup, monkeypatch):
    config, store, rev = setup
    draft = split_draft(setup)
    calls = []

    def paint(*args):
        calls.append(1)
        fake_inpaint(*args)

    monkeypatch.setattr(repair, "inference", paint)
    repair.preview_materials(config, store, draft.id, draft.version)
    repair.underpaint(config, store, draft.id, draft.version)
    assert len(calls) == 2

    def inspect(*args):
        assert any("underpaint" in label for label, _ in args[4])
        assert any("exposure" in label for label, _ in args[4])
        return repair.MaterialReview(
            summary="底层待核对",
            items=[repair.VisualItem(name="底层", status="uncertain", reason="轮廓残影")],
            regions=[],
        )

    monkeypatch.setattr(repair.vision, "ask", inspect)
    reviewed = repair.inspect(config, store, draft.id, draft.version)
    with pytest.raises(ValueError, match="视觉检查"):
        repair.apply(config, store, draft.id, draft.version, until="materials")
    assert store.get().id == rev.id
    reviewed.edit.visual_accepted = True
    confirmed = repair.update(store, draft.id, DraftUpdate(version=reviewed.version, edit=reviewed.edit))
    assert confirmed.result["preview"]["version"] == confirmed.version
    assert confirmed.result["visual"]["version"] == confirmed.version
    assert repair.apply(config, store, draft.id, confirmed.version, until="materials").status == "paused"
