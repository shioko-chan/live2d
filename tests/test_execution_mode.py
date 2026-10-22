"""Hosted-agent defaults never silently start local model services."""

import pytest
from PIL import Image

from live2d_pipeline import comfy, planner, vision
from live2d_pipeline.config import Config
from live2d_pipeline.pipeline import Pipeline
from live2d_pipeline.schema import Plan
from live2d_pipeline.store import Store, digest


def test_default_and_omitted_mode_disable_local_models(tmp_path):
    assert Config().mode == "agent"
    legacy = tmp_path / "legacy.toml"
    legacy.write_text(Config().path.read_text().replace('mode = "agent"\n', ""))
    config = Config(legacy)
    assert config.mode == "agent"
    for engine in ("comfy", "see-through"):
        with pytest.raises(ValueError, match="全本地"):
            config.inference_command(engine, ["unused.py"])


@pytest.mark.parametrize("service", ["planner", "vision", "comfy"])
def test_agent_mode_rejects_model_services_before_process_or_port_access(tmp_path, monkeypatch, service):
    def forbidden(*args, **kwargs):
        pytest.fail("Agent mode accessed a local model service")

    for module in (planner, vision, comfy):
        monkeypatch.setattr(module, "free_port", forbidden)
        monkeypatch.setattr(module, "owned_process", forbidden)
    with pytest.raises(ValueError, match="agent 模式"):
        if service == "planner":
            planner.make_plan(Config(), "test", {}, tmp_path / "planner.log")
        else:
            context = vision.server if service == "vision" else comfy.session
            with context(Config(), tmp_path / "service.log"):
                pytest.fail("Local service started")


def test_explicit_local_mode_supports_existing_backends(local_config):
    assert local_config.mode == "local"
    assert local_config.inference_command("comfy", ["main.py"])[0] == "docker"
    local_config.data["execution"]["inference"] = "native"
    assert local_config.inference_command("see-through", ["run.py"])[0].endswith("envs/see-through/bin/python")


@pytest.mark.parametrize("source_png,until", [(False, "image"), (True, "materials")])
def test_model_work_is_rejected_without_changing_revision(tmp_path, source_png, until):
    store = Store(tmp_path / "project")
    store.init()
    source = tmp_path / "art.png" if source_png else None
    if source:
        Image.new("RGBA", (64, 64), "pink").save(source)
    rev = store.create(Plan(request="test", prompt="test"), source=source)
    revision_file = store.revision_dir(rev.id) / "revision.json"
    before = digest(revision_file)
    with pytest.raises(ValueError, match="agent 模式"):
        Pipeline(Config(), store).run(rev, until=until)
    assert digest(revision_file) == before
    assert store.get().status == "planned"
    assert not (store.revision_dir(rev.id) / "image").exists()


def test_unknown_execution_mode_is_rejected(tmp_path):
    path = tmp_path / "invalid.toml"
    path.write_text(Config().path.read_text().replace('mode = "agent"', 'mode = "automatic-fallback"'))
    with pytest.raises(ValueError, match="execution.mode"):
        Config(path)
