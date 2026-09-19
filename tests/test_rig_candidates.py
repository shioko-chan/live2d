"""Finite rig searches preserve the baseline and never rank past failed rules."""

import asyncio
from contextlib import asynccontextmanager
import hashlib
import json
from PIL import Image
import pytest

from live2d_pipeline import assets, repair, repair_rig
from live2d_pipeline.config import Config
from live2d_pipeline.repair_schema import DraftCreate, DraftUpdate, Drag, RepairEdit, RigRepair
from live2d_pipeline.schema import Binding, Part, Parts, Plan
from live2d_pipeline.store import Store, read_json

inspect_current_preview = repair_rig.inspect_preview


@pytest.fixture
def search_setup(tmp_path, monkeypatch):
    config = Config()
    store = Store(tmp_path / "project")
    store.init()
    revision = store.create(Plan(request="test", prompt="test"))
    revision.status = "built"
    store.save(revision)
    materials = store.revision_dir(revision.id) / "materials"
    materials.mkdir()
    Image.new("RGBA", (20, 30), "#8877bb").save(materials / "bow.png")
    assets.save_parts(
        materials,
        Parts(
            width=64,
            height=64,
            parts=[
                Part(
                    id="bow",
                    name="领结",
                    semantic="objects",
                    order=1,
                    image="bow.png",
                    bounds=(20, 20, 20, 30),
                    binding=Binding(motion="sway"),
                )
            ],
        ),
    )
    draft = repair.create(config, store, DraftCreate(revision=revision.id, part="bow", mode="rig"))
    draft.edit.request = "减少飘带的拉裂"
    draft.edit.rig = RigRepair(
        drags=[
            Drag(start=(25, 23), end=(25, 23), fixed=True),
            Drag(start=(30, 40), end=(34, 40)),
        ]
    )
    draft = repair.update(store, draft.id, DraftUpdate(version=draft.version, edit=draft.edit))
    calls = []

    def preview(config, store, identifier, version):
        calls.append(identifier)
        current = repair.load(store, identifier)
        return repair.publish(
            store,
            current,
            preview={
                "version": version,
                "ready": True,
                "checks": [
                    {
                        "name": "no new flips",
                        "source": "rule",
                        "status": "pass",
                    }
                ],
            },
        )

    def inspect(config, store, identifier, version):
        current, root = repair.snapshot(store, identifier, version)
        Image.new("RGB", (32, 32), "white").save(root / "evidence.png")
        return repair.publish(
            store,
            current,
            visual={
                "version": version,
                "summary": "passed",
                "items": [{"status": "pass"}],
                "evidence": [{"path": "evidence.png", "label": "before/after"}],
            },
        )

    monkeypatch.setattr(repair, "preview_materials", preview)
    monkeypatch.setattr(repair_rig, "inspect_preview", inspect)
    monkeypatch.setattr(
        repair_rig.vision,
        "ask",
        lambda *a, **k: repair_rig.CandidateComparison(
            preferred=None,
            improved=False,
            reason="未发现明确改善，保留原版",
            uncertainties=[],
        ),
    )
    return config, store, revision, draft, calls


def test_candidates_are_bounded_and_preserve_targets_and_pins():
    edit = RepairEdit(
        mode="rig",
        visual_accepted=True,
        rig=RigRepair(
            target="actual-mesh",
            drags=[
                Drag(start=(10, 10), end=(10, 10), fixed=True),
                Drag(start=(20, 20), end=(28, 24)),
            ],
        ),
    )
    candidates = repair_rig.bounded_candidates(edit)
    assert len(candidates) == 3
    assert [candidate.rig.drags[1].end for _, candidate in candidates] == [(28, 24), (26, 23), (24, 22)]
    assert all(candidate.rig.drags[0] == edit.rig.drags[0] for _, candidate in candidates)
    assert all(
        candidate.rig.target == "actual-mesh" and not candidate.visual_accepted for _, candidate in candidates
    )
    assert edit.rig.drags[1].end == (28, 24)  # The source draft is never mutated.
    binding = RepairEdit(mode="bind", binding=Binding(motion="sway", degrees=12, root_pin=0.94))
    variants = repair_rig.bounded_candidates(binding)
    assert [e.binding.degrees for _, e in variants] == [12, 9, 6]
    assert variants[-1][1].binding.root_pin == 0.95
    assert all(e.binding.root == binding.binding.root for _, e in variants)
    assert len(repair_rig.bounded_candidates(RepairEdit(mode="rig", rig=RigRepair(action="opacity")))) == 1


def test_edge_landmarks_have_full_weight_and_actual_arrival_rejects_silent_noop():
    drags = [Drag(start=(10, 10), end=(14, 10)), Drag(start=(20, 20), end=(20, 20), fixed=True)]
    for radius in (0.01, 0.2, 1.0):
        for point in ((0, 0), (1, 1), (0.5, 0.5)):
            selection = repair_rig.landmark_selection([point, (0.5, 0.9)], drags, radius)
            left, top, right, bottom = selection["rect"]
            x, y = point
            # This is the upstream rectangle feather's full-weight condition.
            assert min(x - left, right - x, y - top, bottom - y) >= selection["feather"]
    goals = {0: [14, 10]}
    assert repair_rig.arrival_check(goals, {0: [10, 10]})["status"] == "fail"
    assert repair_rig.arrival_check(goals, {0: [13.9, 10.1]})["status"] == "pass"
    assert repair_rig.arrival_check(goals, {})["status"] == "fail"


def test_search_no_improvement_keeps_baseline_and_reuses_completed(search_setup):
    config, store, revision, draft, calls = search_setup
    result = repair_rig.search(config, store, draft.id, draft.version)
    candidates = result.result["search"]["candidates"]
    assert len(candidates) == len(calls) == 3
    assert result.result["search"]["recommended"] is None
    assert result.result["search"]["needs_user_review"]
    assert all(c["completed"] and c["status"] == "ready" for c in candidates)
    assert read_json(store.root / "project.json")["head"] == revision.id
    assert result.version == draft.version and not result.applied_revision
    repair_rig.search(config, store, draft.id, draft.version)
    assert len(calls) == 3


def test_failed_rules_skip_visual_and_cannot_be_recommended(search_setup, monkeypatch):
    config, store, _, draft, _ = search_setup
    inspections = []

    def bad_preview(config, store, identifier, version):
        current = repair.load(store, identifier)
        return repair.publish(
            store,
            current,
            preview={
                "version": version,
                "ready": True,
                "checks": [{"name": "root drift", "source": "rule", "status": "fail"}],
            },
        )

    monkeypatch.setattr(repair, "preview_materials", bad_preview)
    monkeypatch.setattr(repair_rig, "inspect_preview", lambda *a: inspections.append(a))
    result = repair_rig.search(config, store, draft.id, draft.version).result["search"]
    assert not inspections
    assert all(c["status"] == "rejected" for c in result["candidates"])
    comparison = repair_rig.CandidateComparison(
        preferred=result["candidates"][0]["draft"],
        improved=True,
        reason="looks good",
        uncertainties=[],
    )
    assert repair_rig.recommended_candidate(result["candidates"], comparison) is None
    assert not repair_rig.hard_checks_pass({"ready": True, "checks": []})


def test_limit_and_budget_stop_starting_new_candidates(search_setup, monkeypatch):
    config, store, _, draft, calls = search_setup
    draft.edit.candidate_limit = 2
    draft.edit.candidate_timeout = 60
    draft = repair.update(store, draft.id, DraftUpdate(version=draft.version, edit=draft.edit))
    clock = [0.0]
    preview = repair.preview_materials

    def slow_preview(*args):
        result = preview(*args)
        clock[0] += 61
        return result

    monkeypatch.setattr(repair_rig.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(repair, "preview_materials", slow_preview)
    result = repair_rig.search(config, store, draft.id, draft.version).result["search"]
    assert len(calls) == len(result["candidates"]) == 1
    assert result["budget_reached"]
    assert "不再启动" in result["budget_policy"]


def test_edited_candidate_stays_visible_but_is_never_overwritten_or_recommended(search_setup):
    config, store, _, draft, calls = search_setup
    first = repair_rig.search(config, store, draft.id, draft.version).result["search"]
    identifier = first["candidates"][0]["draft"]
    child = repair.load(store, identifier)
    child.edit.request = "用户手工调整后的说明"
    child = repair.update(store, child.id, DraftUpdate(version=child.version, edit=child.edit))
    second = repair_rig.search(config, store, draft.id, draft.version).result["search"]
    assert second["candidates"][0]["draft"] == identifier
    assert second["candidates"][0]["status"] == "stale"
    assert len(calls) == 3
    assert repair.load(store, identifier).edit.request == "用户手工调整后的说明"


def test_candidate_changed_during_comparison_cannot_be_recommended(search_setup, monkeypatch):
    config, store, _, draft, _ = search_setup

    def compare(config, schema, instruction, context, images, path):
        identifier = context["candidates"][0]["draft"]
        child = repair.load(store, identifier)
        child.edit.request = "在模型比较期间修改"
        repair.update(store, identifier, DraftUpdate(version=child.version, edit=child.edit))
        return repair_rig.CandidateComparison(
            preferred=identifier,
            improved=True,
            reason="seems improved",
            uncertainties=[],
        )

    monkeypatch.setattr(repair_rig.vision, "ask", compare)
    result = repair_rig.search(config, store, draft.id, draft.version).result["search"]
    assert result["candidates"][0]["status"] == "stale"
    assert result["recommended"] is None


def test_budget_resume_reuses_same_child_and_existing_preview(search_setup, monkeypatch):
    config, store, _, draft, calls = search_setup
    draft.edit.candidate_limit = 1
    draft.edit.candidate_timeout = 60
    draft = repair.update(store, draft.id, DraftUpdate(version=draft.version, edit=draft.edit))
    clock = [0.0]
    preview = repair.preview_materials

    def slow_preview(*args):
        result = preview(*args)
        clock[0] += 61
        return result

    monkeypatch.setattr(repair_rig.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(repair, "preview_materials", slow_preview)
    first = repair_rig.search(config, store, draft.id, draft.version).result["search"]
    child = first["candidates"][0]
    assert child["status"] == "paused" and not child["completed"]
    assert first["total_elapsed"] == first["elapsed"] == 61
    assert "comparison" not in first
    second = repair_rig.search(config, store, draft.id, draft.version).result["search"]
    assert second["candidates"][0]["draft"] == child["draft"]
    assert second["candidates"][0]["status"] == "ready"
    assert len(calls) == 1
    assert second["total_elapsed"] == 61 and second["elapsed"] == 0


def test_uncertainty_and_unknown_visual_choice_preserve_baseline():
    candidate = {
        "draft": "known",
        "status": "ready",
        "checks": [{"source": "rule", "status": "pass"}],
        "visual": {"items": [{"status": "pass"}]},
    }
    for preferred, improved, uncertainties in (
        ("wrong", True, []),
        ("known", False, []),
        ("known", True, ["blurred"]),
    ):
        comparison = repair_rig.CandidateComparison(
            preferred=preferred,
            improved=improved,
            reason="test",
            uncertainties=uncertainties,
        )
        assert repair_rig.recommended_candidate([candidate], comparison) is None


def test_evidence_pairs_whole_and_local_images(search_setup, tmp_path):
    _, store, _, draft, _ = search_setup
    root = repair.directory(store, draft.id)
    views = []
    for pose_index, pose in enumerate(({}, {"ParamAngleX": 30})):
        for side, color in (("before", "#110000"), ("after", "#001100")):
            name = f"{side}-{pose_index:02}.png"
            Image.new("RGBA", (64, 64), color).save(root / name)
            views.append({"path": name, "label": "修正前" if side == "before" else "修正后", "pose": pose})
    evidence = repair_rig.comparison_images(draft, root, {"views": list(reversed(views))}, tmp_path)
    assert len(evidence) == 2
    with Image.open(evidence[0][1]) as board:
        assert board.size == (1024, 928)
        assert board.getpixel((250, 560)) == (17, 0, 0)
        assert board.getpixel((762, 560)) == (0, 17, 0)


def test_binding_geometry_catches_parent_warp_fold_at_real_endpoint(search_setup, monkeypatch):
    config, store, revision, draft, _ = search_setup
    root = repair.directory(store, draft.id)
    base = store.revision_dir(revision.id) / "rig"
    candidate = root / "candidate/rig"
    for directory in (base, candidate):
        directory.mkdir(parents=True)
        (directory / "source.psd2live").write_bytes(b"test")
    draft.result["binding_preview"] = {"path": "candidate", "version": draft.version, "ready": True}
    repair.save(store, draft)
    parameter = "ParamComponent_" + hashlib.sha256(b"bow").hexdigest()[:12]
    layer_id = "lyid:" + str(int.from_bytes(hashlib.sha256(b"bow").digest()[:4], "big") & 0x7FFFFFFF)
    calls = []

    class Session:
        def __init__(self, after):
            self.after = after

        async def context(self):
            params = [{"id": "ParamAngleX", "min": -30, "max": 30, "default": 0}]
            objects = [
                {"id": "mesh", "kind": "mesh", "layerId": layer_id, "parentId": "DeformBody"},
                {"id": "DeformBody", "kind": "warp", "parentId": None},
            ]
            if self.after:
                params.append({"id": parameter, "min": -1, "max": 1, "default": 0})
                objects[0]["parentId"] = "DeformComponent_bow"
                objects.append({"id": "DeformComponent_bow", "kind": "warp", "parentId": "DeformBody"})
            return {"parameters": {"parameters": params}, "objects": objects}

        async def call(self, tool, arguments):
            assert tool == "rig_inspect"
            assert self.after or parameter not in arguments["coordinate"]
            calls.append((self.after, arguments))
            if arguments.get("detail") == "points":
                return {"points": [[0, 20, 20], [1, 40, 50]], "nextOffset": None}
            fold = (
                self.after
                and arguments["target"]["id"] == "DeformComponent_bow"
                and arguments["coordinate"].get(parameter) == 1
            )
            return {"diagnostics": {"flippedTriangleCount": int(fold), "collapsedTriangleCount": 0}}

    @asynccontextmanager
    async def open_rig(config, psd, workspace, control, log):
        yield Session("candidate" in str(psd)), None

    async def render(session, pose, path):
        Image.new("RGBA", (64, 64)).save(path)

    monkeypatch.setattr(repair_rig.rig, "open_rig", open_rig)
    monkeypatch.setattr(repair_rig, "render", render)
    result = asyncio.run(repair_rig.binding_geometry(config, store, draft, root))
    preview = result.result["binding_preview"]
    assert not preview["ready"]
    assert any(check["status"] == "fail" and check["pose"].get(parameter) == 1 for check in preview["checks"])
    assert {call[1]["coordinate"].get(parameter) for call in calls if call[0]} >= {-1, -0.5, 0, 0.5, 1}
    assert {view["pose"].get("ParamAngleX") for view in preview["views"]} >= {-30, -15, 0, 15, 30}


def test_vlm_boundaries_omit_large_diagnostics_but_keep_saved_evidence(search_setup, monkeypatch):
    config, store, _, draft, _ = search_setup
    preview = repair.preview_materials
    requests = []
    diagnostic = {"RAW_DIAGNOSTICS_SENTINEL": list(range(10000))}

    def detailed_preview(config, store, identifier, version):
        child = preview(config, store, identifier, version)
        root = repair.directory(store, identifier)
        result = child.result["preview"]
        result["checks"][0].update(before=diagnostic, after=diagnostic, pixels=0.125)
        result["views"] = []
        for side, label in (("before", "修正前"), ("after", "修正后")):
            Image.new("RGBA", (64, 64), "#9988aa").save(root / f"{side}.png")
            result["views"].append({"path": f"{side}.png", "label": label, "pose": {}})
        return repair.publish(store, child, preview=result)

    def ask(config, schema, instruction, context, images, path):
        requests.append(context)
        serialized = json.dumps(context)
        assert "RAW_DIAGNOSTICS_SENTINEL" not in serialized
        assert '"path"' not in serialized and len(serialized) < 12000
        if schema is repair.MaterialReview:
            assert context["rules"][0] == {"name": "no new flips", "status": "pass", "pixels": 0.125}
            return repair.MaterialReview(
                summary="清楚",
                items=[
                    repair.VisualItem(
                        name="轮廓",
                        status="pass",
                        reason="未见新问题",
                    )
                ],
                regions=[],
            )
        assert set(context["candidates"][0]) == {"draft", "label", "rules", "visual"}
        return repair_rig.CandidateComparison(
            preferred=None, improved=False, reason="无明确改善", uncertainties=[]
        )

    monkeypatch.setattr(repair, "preview_materials", detailed_preview)
    monkeypatch.setattr(repair_rig, "inspect_preview", inspect_current_preview)
    monkeypatch.setattr(repair_rig.vision, "ask", ask)
    result = repair_rig.search(config, store, draft.id, draft.version)
    assert len(requests) == 4  # Three independent reviews and one final comparison.
    for candidate in result.result["search"]["candidates"]:
        saved = repair.load(store, candidate["draft"])
        assert saved.result["preview"]["checks"][0]["before"] == diagnostic
