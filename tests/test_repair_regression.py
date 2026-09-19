"""Saved regression records are read-only by default and execute only cloned drafts."""

import importlib.util

from PIL import Image
import pytest

from live2d_pipeline import assets, repair
from live2d_pipeline.config import ROOT, Config
from live2d_pipeline.repair_schema import DraftCreate
from live2d_pipeline.schema import Part, Parts, Plan
from live2d_pipeline.store import Store, read_json

spec = importlib.util.spec_from_file_location("repair_regression", ROOT / "scripts/repair_regression.py")
regression = importlib.util.module_from_spec(spec)
spec.loader.exec_module(regression)


@pytest.fixture
def saved_case(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        Config().path.read_text().replace('runtime = ".runtime"', f'runtime = "{tmp_path}/runtime"')
    )
    config = Config(path)
    projects = tmp_path / "projects"
    store = Store(projects / "example")
    store.init()
    revision = store.create(Plan(request="test", prompt="test"))
    directory = store.revision_dir(revision.id) / "materials"
    directory.mkdir()
    Image.new("RGBA", (32, 32), "red").save(directory / "bow.png")
    assets.save_parts(
        directory,
        Parts(
            width=32,
            height=32,
            parts=[
                Part(
                    id="bow",
                    name="bow",
                    semantic="neckwear",
                    order=1,
                    image="bow.png",
                    bounds=(0, 0, 32, 32),
                )
            ],
        ),
    )
    source = repair.create(config, store, DraftCreate(revision=revision.id, part="bow", mode="mask"))
    source.applied_revision = revision.id
    repair.save(store, source)
    case = regression.Case(name="saved", project="example", draft=source.id, step="preview")
    return config, projects, store, source, case, tmp_path / "report.json"


def test_default_only_checks_saved_inputs_without_models_or_new_drafts(saved_case, monkeypatch):
    config, projects, store, source, case, output = saved_case
    before = {p.name for p in (store.root / "repairs").iterdir()}
    for name in ("create", "preview_materials", "search", "inspect", "apply"):
        monkeypatch.setattr(repair, name, lambda *a, **k: pytest.fail("default mode executed repair"))
    report = regression.evaluate(config, projects, [case], output)
    assert report["cases"][0]["status"] == "available"
    assert report["cases"][0]["source_saved_edit_versions"] == source.version - 1
    assert report["cases"][0]["evidence_origin"] == "saved_history"
    assert "不是鼠标手势数" in report["metric_note"]
    assert {p.name for p in (store.root / "repairs").iterdir()} == before


def test_execution_clones_applied_source_without_applying_or_changing_original(saved_case, monkeypatch):
    config, projects, store, source, case, output = saved_case
    original = (repair.directory(store, source.id) / "draft.json").read_bytes()
    state = (store.root / "project.json").read_bytes()
    calls = []

    def preview(config, store, identifier, version):
        assert identifier != source.id
        draft = repair.load(store, identifier)
        assert draft.base_revision == source.base_revision and draft.applied_revision is None
        calls.append(identifier)
        return repair.publish(
            store,
            draft,
            preview={
                "version": version,
                "checks": [
                    {
                        "name": "nonempty",
                        "source": "rule",
                        "status": "pass",
                    }
                ],
            },
        )

    monkeypatch.setattr(repair, "preview_materials", preview)
    monkeypatch.setattr(repair, "apply", lambda *a, **k: pytest.fail("regression applied revision"))
    report = regression.evaluate(config, projects, [case], output, execute=True)
    row = report["cases"][0]
    assert row["status"] == "completed" and row["human_acceptance"] == "pending"
    assert row["clone"] == calls[0] and row["program_checks"][0]["status"] == "pass"
    assert (repair.directory(store, source.id) / "draft.json").read_bytes() == original
    assert (store.root / "project.json").read_bytes() == state


def test_failed_case_is_saved_and_does_not_prevent_later_case(saved_case, monkeypatch):
    config, projects, store, source, case, output = saved_case
    second = case.model_copy(update={"name": "later"})
    calls = []

    def preview(config, store, identifier, version):
        calls.append(identifier)
        if len(calls) == 1:
            raise RuntimeError("deliberate model failure")
        assert read_json(output)["cases"][0]["status"] == "failed"
        return repair.load(store, identifier)

    monkeypatch.setattr(repair, "preview_materials", preview)
    report = regression.evaluate(config, projects, [case, second], output, execute=True)
    assert [row["status"] for row in report["cases"]] == ["failed", "completed"]
    assert len(calls) == 2 and calls[0] != calls[1]
    assert read_json(output) == report


def test_not_fixture_is_not_counted_as_a_success_or_executed(saved_case, monkeypatch):
    config, projects, _, _, case, output = saved_case
    case = case.model_copy(update={"fixture": "not_fixture", "draft": None})
    monkeypatch.setattr(regression, "clone_source", lambda *a: pytest.fail("missing fixture cloned"))
    report = regression.evaluate(config, projects, [case], output, execute=True)
    assert report["cases"][0]["status"] == "not_fixture"
    assert report["cases"][0]["human_acceptance"] == "not_evaluated"
