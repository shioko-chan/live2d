"""CPU-only candidate lifecycle tests: user edits and resumes cannot reuse stale votes."""

import pytest
from PIL import Image

from live2d_pipeline import repair, repair_candidates
from live2d_pipeline.repair_schema import DraftCreate, DraftUpdate, RepairEdit
from test_repair import setup as setup


@pytest.fixture
def candidate_search(setup, monkeypatch):
    config, store, revision = setup
    parent = repair.create(config, store, DraftCreate(revision=revision.id, part="clothes"))
    parent.edit.candidate_limit = 2
    parent = repair.update(store, parent.id, DraftUpdate(version=parent.version, edit=parent.edit))
    calls = []

    def preview(_config, _store, identifier, version):
        child = repair.load(store, identifier)
        calls.append(identifier)
        path = repair.directory(store, identifier) / "preview"
        (path / "materials").mkdir(parents=True, exist_ok=True)
        for name in ("underpaint.png", "materials/composite.png"):
            Image.new("RGBA", (8, 8), "white").save(path / name)
        return repair.publish(
            store,
            child,
            preview={
                "version": version,
                "path": "preview",
                "ready": True,
                "checks": [{"name": "像素边界", "status": "pass", "source": "rule"}],
            },
        )

    def inspect(_config, _store, identifier, version):
        child = repair.load(store, identifier)
        status = "uncertain" if child.result["origin"]["label"] == "当前设置" else "pass"
        return repair.publish(
            store,
            child,
            visual={
                "version": version,
                "summary": "fixture",
                "items": [
                    {"name": "残影", "status": status, "reason": "fixture"},
                ],
            },
        )

    def vote(_config, _schema, _prompt, context, _images, _output):
        return repair_candidates.Preference(
            preferred=context["eligible"][-1],
            improved=True,
            reason="fixture",
            evidence=["fixture"],
        )

    monkeypatch.setattr(repair, "preview_materials", preview)
    monkeypatch.setattr(repair, "inspect", inspect)
    monkeypatch.setattr(repair_candidates.vision, "ask", vote)
    return config, store, parent, calls


def test_resume_invalidates_vote_for_manually_edited_candidate(candidate_search):
    config, store, parent, calls = candidate_search
    report = repair_candidates.search(config, store, parent.id, parent.version).result["search"]
    identifier = report["recommended"]
    assert identifier is not None
    child = repair.load(store, identifier)
    child.edit.request = "用户正在手动修正，不得用旧检查推荐"
    changed = repair.update(store, child.id, DraftUpdate(version=child.version, edit=child.edit))
    completed = len(calls)

    report = repair_candidates.search(config, store, parent.id, parent.version).result["search"]

    assert report["recommended"] is None
    assert next(r for r in report["candidates"] if r["draft"] == identifier)["status"] == "stale"
    assert repair.load(store, identifier).edit == changed.edit
    assert len(calls) == completed, "resume must not overwrite a user's candidate edits"


def test_budget_resume_continues_without_repeating_completed_candidate(candidate_search, monkeypatch):
    config, store, parent, calls = candidate_search
    now = [0.0]
    monkeypatch.setattr(repair_candidates.time, "monotonic", lambda: now[0])
    preview = repair.preview_materials

    def slow_preview(*args):
        result = preview(*args)
        now[0] += 601.0
        return result

    monkeypatch.setattr(repair, "preview_materials", slow_preview)
    report = repair_candidates.search(config, store, parent.id, parent.version).result["search"]
    assert len(report["candidates"]) == 1
    first = report["candidates"][0]["draft"]

    monkeypatch.setattr(repair, "preview_materials", preview)
    report = repair_candidates.search(config, store, parent.id, parent.version).result["search"]

    assert len(report["candidates"]) == 2
    assert calls.count(first) == 1


def test_candidate_origin_survives_manual_edits(candidate_search):
    config, store, parent, _ = candidate_search
    child = repair.fork_candidate(config, store, parent, "候选", parent.edit)
    child.edit.request = "进一步清理残影"
    child = repair.update(store, child.id, DraftUpdate(version=child.version, edit=child.edit))
    assert child.result["origin"]["draft"] == parent.id


def test_changed_implementation_does_not_reuse_old_candidate_votes(candidate_search, monkeypatch):
    config, store, parent, calls = candidate_search
    previous = repair_candidates.search(config, store, parent.id, parent.version).result["search"]
    old_children = {row["draft"] for row in previous["candidates"]}
    monkeypatch.setattr(repair, "execution_identity", lambda _: {"code": "updated"})

    current = repair_candidates.search(config, store, parent.id, parent.version).result["search"]

    assert current["input"] != previous["input"]
    assert old_children.isdisjoint(row["draft"] for row in current["candidates"])
    assert len(calls) == len(old_children) + len(current["candidates"])
    assert all(repair.load(store, identifier) for identifier in old_children)


def test_repaint_variants_stay_inside_valid_seed_range(candidate_search):
    _, _, parent, _ = candidate_search
    parent.edit.mode = "repaint"
    parent.edit.repaint_seed = 2**31 - 1
    for _, candidate, _ in repair_candidates.variants(parent):
        RepairEdit.model_validate(candidate.model_dump())


def test_retry_failed_step_preserves_successful_alternative(candidate_search, monkeypatch):
    config, store, parent, calls = candidate_search
    original = repair.preview_materials
    failures = []

    def fail_once(*args):
        child = repair.load(store, args[2])
        if child.result["origin"]["label"] == "当前设置":
            failures.append(child.id)
            if len(failures) == 1:
                raise RuntimeError("temporary model failure")
        return original(*args)

    monkeypatch.setattr(repair, "preview_materials", fail_once)
    report = repair_candidates.search(config, store, parent.id, parent.version).result["search"]
    assert report["candidates"][0]["status"] == "error"
    completed = report["candidates"][1]["draft"]
    report = repair_candidates.search(config, store, parent.id, parent.version).result["search"]
    assert len(failures) == 2
    assert calls.count(completed) == 1
    assert report["candidates"][0]["status"] != "error"


def test_comparison_cannot_claim_improvement_without_baseline_image(candidate_search, monkeypatch):
    config, store, parent, _ = candidate_search
    preview = repair.preview_materials

    def fail_baseline_rules(*args):
        child = preview(*args)
        if child.result["origin"]["label"] == "当前设置":
            value = child.result["preview"]
            value["ready"] = False
            value["checks"][0]["status"] = "fail"
            child = repair.publish(store, child, preview=value)
        return child

    compared = []

    def compare(_config, _schema, _prompt, context, images, _output):
        compared.append(context["baseline"] in " ".join(label for label, _ in images))
        return repair_candidates.Preference(
            preferred=context["eligible"][-1],
            improved=True,
            reason="fixture",
            evidence=["fixture"],
        )

    monkeypatch.setattr(repair, "preview_materials", fail_baseline_rules)
    monkeypatch.setattr(repair_candidates.vision, "ask", compare)
    report = repair_candidates.search(config, store, parent.id, parent.version).result["search"]
    assert report["recommended"] is None or compared == [True]
