import io
import sys
import httpx
import pytest
from PIL import Image
from psd_tools import PSDImage
from typer.testing import CliRunner
from live2d_pipeline import assets
from live2d_pipeline.cli import app
from live2d_pipeline.comfy import ComfyClient, bind_workflow
from live2d_pipeline.config import Config, ROOT
from live2d_pipeline.pipeline import Pipeline
from live2d_pipeline.schema import Plan, Operation
from live2d_pipeline.store import Store, digest, read_json, write_json, lock
from live2d_pipeline.validation import validate_bundle


def source_psd(path):
    psd = PSDImage.new("RGBA", (64, 96))
    for index, name in enumerate(["face", "eyewhite", "irides", "eyelash", "eyebrow", "mouth_open"]):
        psd.create_pixel_layer(
            Image.new("RGBA", (16, 16), (120, 90, 60, 255)), name=name, left=index * 3, top=index * 4
        )
    psd.save(path)
    return path


def fixture_bundle(path):
    # Intentionally minimal, NOT a real model. Tests never label this runtime-compatible.
    path.mkdir(parents=True, exist_ok=True)
    (path / "test.moc3").write_bytes(b"MOC3" + b"\0" * 64)
    Image.new("RGBA", (16, 16)).save(path / "texture.png")
    write_json(
        path / "test.model3.json",
        {"Version": 3, "FileReferences": {"Moc": "test.moc3", "Textures": ["texture.png"]}},
    )


def test_offline_commands_do_not_import_torch(tmp_path):
    runner = CliRunner()
    assert runner.invoke(app, ["init", str(tmp_path / "project")]).exit_code == 0
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0, result.output
    assert '"gpu_accessed": false' in result.output
    assert "torch" not in sys.modules


def test_plan_validation():
    with pytest.raises(ValueError):
        Operation(kind="recolor", target="hair")
    with pytest.raises(ValueError):
        Operation(kind="rig", tool="shell", arguments={"command": "anything"})
    with pytest.raises(ValueError):
        Plan(
            request="x",
            prompt="x",
            operations=[
                Operation(kind="regenerate", instruction="x"),
                Operation(kind="remove", target="face"),
            ],
        )


def test_mouth_mask_allows_small_lip_overhang_but_rejects_face():
    import runpy

    valid = runpy.run_path(str(ROOT / "scripts/extract_mouth.py"))["valid_candidate"]
    box = [64, 89.6, 192, 166.4]
    assert valid([70, 85, 185, 165], 0.916, box)
    assert not valid([0, 0, 256, 240], 0.99, box)
    assert not valid([70, 65, 185, 165], 0.99, box)
    assert not valid([70, 85, 185, 165], 0.2, box)
    assert not valid([70, 85, 185, 165], float("nan"), box)
    assert not valid(None, 0.99, box)


def test_versions_keep_original(tmp_path):
    store = Store(tmp_path / "project")
    store.init()
    image = tmp_path / "input.png"
    Image.new("RGB", (8, 8), "red").save(image)
    rev1 = store.create(Plan(request="红色", prompt="red"), source=image)
    sha = rev1.source_hash
    Image.new("RGB", (8, 8), "blue").save(image)
    assert digest(store.revision_dir(rev1.id) / rev1.source) == sha
    rev2 = store.create(Plan(request="蓝色", prompt="blue"))
    assert rev2.parent == rev1.id
    store.checkout(rev1.id)
    rev3 = store.create(Plan(request="绿色", prompt="green"))
    assert rev3.parent == rev1.id
    assert len(store.history()) == 3
    with pytest.raises(ValueError):
        store.accept(rev2.id)
    with pytest.raises(ValueError):
        store.revision_dir("../outside")


def test_psd_roundtrip_and_alpha(tmp_path):
    original = source_psd(tmp_path / "source.psd")
    assets.normalize(original, tmp_path / "parts")
    assert not assets.quality(tmp_path / "parts")["missing_parts"]
    manifest = assets.load_parts(tmp_path / "parts")
    assert len({p.id for p in manifest.parts}) == 6
    assets.write_psd(tmp_path / "parts", tmp_path / "rebuilt.psd")
    rebuilt = PSDImage.open(tmp_path / "rebuilt.psd")
    assert len(rebuilt) == 6
    assert rebuilt.size == (64, 96)
    ids = {layer.name: layer.layer_id for layer in rebuilt}
    manifest.parts.reverse()
    manifest.parts.pop()
    write_json(tmp_path / "parts/parts.json", manifest.model_dump())
    assets.write_psd(tmp_path / "parts", tmp_path / "reordered.psd")
    reordered = PSDImage.open(tmp_path / "reordered.psd")
    assert all(ids[layer.name] == layer.layer_id for layer in reordered)


def test_edit_preserves_unselected_pixels():
    image = Image.new("RGBA", (8, 8), (10, 20, 30, 128))
    edited = Image.new("RGBA", (8, 8), (200, 0, 0, 255))
    mask = Image.new("L", (8, 8), 0)
    mask.paste(255, (2, 2, 4, 4))
    result = assets.preserve_outside(image, edited, mask)
    assert assets.unchanged_pixels(image, result, mask)
    assert result.getchannel("A").tobytes() == image.getchannel("A").tobytes()
    assert result.getpixel((2, 2))[:3] == (200, 0, 0)
    assert assets.recolor(image, "#ff0000").getchannel("A").tobytes() == image.getchannel("A").tobytes()


def test_stage_resume_and_tamper(tmp_path):
    store = Store(tmp_path / "p")
    store.init()
    rev = store.create(Plan(request="x", prompt="x"))
    pipeline = Pipeline(Config(), store)
    calls = []

    def action(path):
        calls.append(1)
        (path / "artifact").write_text("ok")

    output = pipeline.stage(rev, "test", "input", action)
    pipeline.stage(rev, "test", "input", action)
    assert len(calls) == 1
    (output / "artifact").write_text("tampered")
    pipeline.stage(rev, "test", "input", action)
    assert len(calls) == 2
    pipeline.stage(rev, "test", "new input", action)
    assert len(calls) == 3

    def fail(path):
        raise RuntimeError("interrupted")

    with pytest.raises(RuntimeError):
        pipeline.stage(rev, "failure", "x", fail)
    assert store.get(rev.id).stages["failure"].state == "failed"


def test_structural_checks_do_not_claim_runtime(tmp_path):
    fixture_bundle(tmp_path)
    report = validate_bundle(tmp_path)
    assert report["runtime_verified"] is False
    (tmp_path / "test.moc3").write_bytes(b"placeholder")
    with pytest.raises(ValueError, match="placeholder"):
        validate_bundle(tmp_path)
    fixture_bundle(tmp_path)
    data = read_json(tmp_path / "test.model3.json")
    data["FileReferences"]["Textures"] = ["../outside.png"]
    write_json(tmp_path / "test.model3.json", data)
    with pytest.raises(ValueError):
        validate_bundle(tmp_path)


def test_comfy_success_and_error(tmp_path):
    image = io.BytesIO()
    Image.new("RGB", (8, 8)).save(image, format="PNG")

    def transport(request):
        if request.url.path == "/object_info":
            return httpx.Response(200, json={"SaveImage": {}})
        if request.url.path == "/prompt":
            return httpx.Response(200, json={"prompt_id": "abc"})
        if request.url.path == "/history/abc":
            return httpx.Response(
                200, json={"abc": {"outputs": {"9": {"images": [{"filename": "out.png", "type": "output"}]}}}}
            )
        if request.url.path == "/view":
            return httpx.Response(200, content=image.getvalue())
        raise AssertionError(request.url)

    client = httpx.Client(base_url="http://test", transport=httpx.MockTransport(transport))
    service = ComfyClient("http://test", 1, client)
    service.execute({"9": {"class_type": "SaveImage", "inputs": {}}}, tmp_path / "out.png", "9")
    assert (tmp_path / "out.png").is_file()
    with pytest.raises(RuntimeError, match="Missing ComfyUI"):
        service.execute({"9": {"class_type": "Unknown", "inputs": {}}}, tmp_path / "x", "9")


def test_workflow_links_and_bindings():
    config = Config()
    for name in ["generate", "edit"]:
        template = read_json(ROOT / "workflows" / f"{name}.json")
        values = {
            **config["limits"],
            **config["comfy"],
            "prompt": "test",
            "negative": "",
            "prefix": "test",
            "image": "test.png",
            "reference": "ref.png",
        }
        graph = bind_workflow(template, values)
        for node in graph.values():
            for value in node["inputs"].values():
                if isinstance(value, list):
                    assert value[0] in graph
        assert template["prompt"]["9"]["inputs"]["filename_prefix"] != "test"


def test_pipeline_with_protocol_backends(tmp_path, monkeypatch):
    store = Store(tmp_path / "project")
    store.init()
    source = source_psd(tmp_path / "source.psd")
    rev = store.create(Plan(request="已有 PSD", prompt="anime"), source=source)
    calls = []

    async def fake_build(config, psd, output, operations, parent_workspace=None):
        calls.append(operations)
        fixture_bundle(output / "bundle")
        (output / "source.psd2live").write_text("test fixture workspace")
        write_json(output / "rig-context.json", {})
        return output / "bundle"

    monkeypatch.setattr("live2d_pipeline.rig.build", fake_build)
    pipeline = Pipeline(Config(), store)
    pipeline.run(rev)
    assert store.get().status == "built"
    assert store.get().runtime_verified is False
    pid = assets.load_parts(store.revision_dir(rev.id) / "materials").parts[0].id
    child = store.create(
        Plan(
            request="脸改红色",
            prompt="anime",
            operations=[Operation(kind="recolor", target=pid, color="#ff0000")],
        )
    )
    pipeline.run(child)
    assert child.status == "built"
    assert len(calls) == 2
    pipeline.run(child)
    assert len(calls) == 2
    original = Image.open(store.revision_dir(rev.id) / "materials" / f"{pid}.png").getpixel((0, 0))
    changed = Image.open(store.revision_dir(child.id) / "materials" / f"{pid}.png").getpixel((0, 0))
    assert original != changed


def test_exclusive_task_lock(tmp_path):
    with lock(tmp_path / "task.lock"):
        with pytest.raises(RuntimeError):
            with lock(tmp_path / "task.lock"):
                pass


def test_comfy_execution_error_and_timeout(tmp_path):
    def handler(request):
        if request.url.path == "/object_info":
            return httpx.Response(200, json={"SaveImage": {}})
        if request.url.path == "/prompt":
            return httpx.Response(200, json={"prompt_id": "bad"})
        return httpx.Response(
            200,
            json={
                "bad": {
                    "status": {
                        "status_str": "error",
                        "messages": [["execution_error", {"exception_message": "OOM"}]],
                    }
                }
            },
        )

    client = httpx.Client(base_url="http://test", transport=httpx.MockTransport(handler))
    graph = {"9": {"class_type": "SaveImage", "inputs": {}}}
    with pytest.raises(RuntimeError, match="OOM"):
        ComfyClient("http://test", 1, client).execute(graph, tmp_path / "a.png", "9")
    with pytest.raises(TimeoutError):
        ComfyClient("http://test", 0, client).execute(graph, tmp_path / "a.png", "9")


def test_mcp_schema_and_uncertain_write():
    import asyncio
    from types import SimpleNamespace
    from live2d_pipeline.rig import RigSession

    class Session:
        def __init__(self):
            self.calls = []

        async def call_tool(self, name, args):
            self.calls.append((name, args))
            if name == "project_get_state":
                return SimpleNamespace(isError=False, structuredContent={"historyHeadNodeId": "head1"})
            raise TimeoutError("uncertain mutation")

    session = Session()
    adapter = RigSession(session)
    adapter.tools = {
        "project_get_state": {"type": "object"},
        "parameter_update": {"type": "object", "required": ["parameter_id", "expected_history_head_node_id"]},
    }
    operation = Operation(
        kind="rig", tool="parameter_update", arguments={"parameter_id": "ParamAngleX", "max": 10}
    )
    with pytest.raises(TimeoutError):
        asyncio.run(adapter.apply([operation]))
    assert len(session.calls) == 2  # No blind retry of the write.
    assert session.calls[1][1]["expected_history_head_node_id"] == "head1"


def test_owned_process_timeout_cleanup(tmp_path):
    from live2d_pipeline.process import run

    # Plain sleeping Python is a process-lifecycle test, not a model/service.
    with pytest.raises(RuntimeError, match="Timed out"):
        run([sys.executable, "-c", "import time; time.sleep(10)"], tmp_path / "log", timeout=0.05)


def test_sigterm_stops_owned_child_and_unwinds(tmp_path):
    import os
    import signal
    import subprocess
    import time

    ready = tmp_path / "child.pid"
    cleaned = tmp_path / "cleaned"
    script = f"""
import sys, time
from pathlib import Path
from live2d_pipeline.process import owned_process
try:
    with owned_process([sys.executable, '-c', 'import time; time.sleep(60)'], Path({str(tmp_path / "log")!r})) as child:
        Path({str(ready)!r}).write_text(str(child.pid))
        time.sleep(60)
finally:
    Path({str(cleaned)!r}).touch()
"""
    parent = subprocess.Popen([sys.executable, "-c", script])
    try:
        deadline = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.exists()
        os.kill(parent.pid, signal.SIGTERM)
        assert parent.wait(timeout=5) == 143
        assert cleaned.exists()
        with pytest.raises(ProcessLookupError):
            os.kill(int(ready.read_text()), 0)
    finally:
        if parent.poll() is None:
            parent.kill()
            parent.wait()


def test_source_support_rejects_invented_parts_but_preserves_real_ones(tmp_path):
    from PIL import ImageDraw
    from live2d_pipeline.schema import Part, Parts

    source = Image.new("RGB", (100, 100), "white")
    ImageDraw.Draw(source).rectangle((40, 10, 59, 89), fill="black")
    source.save(tmp_path / "source.png")
    for name in ["body", "wing"]:
        Image.new("RGBA", (20, 40), "gray").save(tmp_path / f"{name}.png")
    original_hash = digest(tmp_path / "body.png")
    manifest = Parts(
        width=100,
        height=100,
        parts=[
            Part(
                id="body", name="body", semantic="topwear", order=0, bounds=(40, 30, 20, 40), image="body.png"
            ),
            Part(
                id="wing", name="wing", semantic="wings", order=1, bounds=(10, 30, 20, 40), image="wing.png"
            ),
        ],
    )
    assets.save_parts(tmp_path, manifest)
    report = assets.filter_unsupported_parts(tmp_path, tmp_path / "source.png", tmp_path / "source.png")
    assert report["removed"] == ["wing"]
    assert digest(tmp_path / "body.png") == original_hash
    # A wing actually present in the source is retained with the same algorithm.
    ImageDraw.Draw(source).rectangle((10, 30, 29, 69), fill="gray")
    source.save(tmp_path / "source.png")
    Image.new("RGBA", (20, 40), "gray").save(tmp_path / "wing.png")
    assets.save_parts(tmp_path, manifest)
    report = assets.filter_unsupported_parts(tmp_path, tmp_path / "source.png", tmp_path / "source.png")
    assert report["removed"] == []
    # A scene background is not silently treated as white matting.
    Image.new("RGB", (100, 100), "blue").save(tmp_path / "scene.png")
    assert (
        assets.filter_unsupported_parts(tmp_path, tmp_path / "scene.png", tmp_path / "scene.png")["status"]
        == "not_applicable"
    )


def test_mouth_add_keeps_raw_generation_and_uses_segmented_layer(tmp_path, monkeypatch):
    from live2d_pipeline import comfy, segment

    source_psd(tmp_path / "source.psd")
    directory = tmp_path / "parts"
    assets.normalize(tmp_path / "source.psd", directory)

    def generate(config, prompt, negative, output, log, **kwargs):
        Image.new("RGB", (256, 256), "lime").save(output)

    def extract(config, source, output, size, log):
        assert source.name.endswith("-generated.png")
        assert Image.open(source).size == (256, 256)
        image = Image.new("RGBA", size)
        image.paste((100, 20, 30, 255), (2, 2, size[0] - 2, size[1] - 2))
        image.save(output)

    monkeypatch.setattr(comfy, "image_job", generate)
    monkeypatch.setattr(segment, "mouth", extract)
    Pipeline(Config(), None).modify(
        directory,
        [
            Operation(
                kind="add",
                semantic="mouth_close",
                instruction="closed mouth",
                bounds=(20, 30, 20, 10),
            )
        ],
    )
    assert Image.open(directory / "mouth_close_added_0-generated.png").size == (256, 256)
    part = next(p for p in assets.load_parts(directory).parts if p.semantic == "mouth_close")
    assert Image.open(directory / part.image).getchannel("A").getextrema() == (0, 255)


def test_planner_omits_executor_owned_history_field():
    from live2d_pipeline.planner import compact_context

    schema = {
        "properties": {
            "parameter_id": {"type": "string"},
            "expected_history_head_node_id": {"type": "string"},
        },
        "required": ["parameter_id", "expected_history_head_node_id"],
    }
    context = {
        "rig": {
            "state": {},
            "layers": {"layers": []},
            "parameters": {"parameters": []},
            "objects": [],
            "physics": {},
            "tools": {"parameter_update": schema},
        }
    }
    tool = compact_context(context)["rig"]["tools"]["parameter_update"]
    assert tool["required"] == ["parameter_id"]
    assert "expected_history_head_node_id" not in tool["properties"]
    assert "expected_history_head_node_id" in schema["properties"]


def test_keyform_uses_render_object_id_and_rejects_layer_id():
    import asyncio
    from live2d_pipeline.rig import RigSession, blink_operations

    objects = [{"kind": "mesh", "id": "ArtMeshIridesL", "layerId": "lyid:123"}]
    operations = blink_operations([{"id": "lyid:123", "semanticTag": "irides", "side": "left"}], objects)

    class Session(RigSession):
        def __init__(self):
            super().__init__(None)
            self.writes = []

        async def call(self, name, args=None):
            if name == "project_get_state":
                return {"historyHeadNodeId": "head"}
            if name == "rig_list_objects":
                return {"objects": objects, "nextOffset": None}
            assert name == "keyform_set"
            self.writes.append(args)
            return {}

    session = Session()
    bad = operations[0].model_copy(deep=True)
    bad.arguments["target"]["id"] = "lyid:123"
    with pytest.raises(ValueError, match="actual rig object"):
        asyncio.run(session.apply([bad]))
    assert not session.writes
    asyncio.run(session.apply(operations))
    assert len(session.writes) == 3
    assert all(item["target"]["id"] == "ArtMeshIridesL" for item in session.writes)


def test_import_plan_drops_previous_source_rig(tmp_path, monkeypatch):
    from live2d_pipeline import cli, services

    config = Config()
    config.runtime = tmp_path / "runtime"
    monkeypatch.setattr(cli, "Config", lambda path=None: config)
    store = Store(tmp_path / "project")
    store.init()
    parent = store.create(Plan(request="old", prompt="old"))
    directory = store.revision_dir(parent.id)
    write_json(directory / "materials/parts.json", {"parts": ["old wing"]})
    write_json(directory / "rig/rig-context.json", {"objects": ["old wing rig"]})
    source = tmp_path / "new.png"
    Image.new("RGB", (64, 64), "white").save(source)

    def make_plan(config, request, context, log):
        assert "rig" not in context and "parts" not in context
        return Plan(request=request, prompt="new")

    monkeypatch.setattr(services, "make_plan", make_plan)
    cli.plan(store.root, "import new", source=source)
    assert store.get().source_hash == digest(source)


def test_download_manifest_has_visual_projector_and_pins():
    lockfile = read_json(ROOT / "models.lock.json")
    assert any("mmproj" in f["destination"] for f in lockfile["files"])
    assert all(len(f["revision"]) == 40 and f["bytes"] > 0 for f in lockfile["files"])
    assert any(f["destination"].endswith("tokenizer_config.json") for f in lockfile["files"])
    lockfile = read_json(ROOT / "upstreams.lock.json")
    assert all(len(value["revision"]) == 40 for value in lockfile.values())


def test_container_command_is_explicit_and_has_scoped_name(tmp_path):
    config = Config()
    command = config.inference_command("comfy", ["main.py"], [tmp_path])
    assert command[:2] == ["docker", "run"]
    assert command[command.index("--name") + 1].startswith("l2d-")
    assert "HF_HUB_OFFLINE=1" in command
    assert f"HF_HUB_CACHE={config.hf_cache}" in command
    assert f"{config.hf_cache}:{config.hf_cache}:ro" in command
    assert f"{tmp_path}:{tmp_path}" in command
    assert command[-2:] == ["live2d-comfy:pinned", "main.py"]


def test_ambiguous_plan_never_executes(tmp_path, monkeypatch):
    store = Store(tmp_path / "project")
    store.init()
    revision = store.create(Plan(request="改好看", prompt="", clarification="具体修改哪个部位？"))

    def forbidden(*args, **kwargs):
        raise AssertionError("No model call allowed")

    monkeypatch.setattr("live2d_pipeline.comfy.image_job", forbidden)
    with pytest.raises(ValueError, match="哪个部位"):
        Pipeline(Config(), store).run(revision)
    assert store.get().status == "planned"


def test_decomposition_selects_color_psd_not_depth(tmp_path, monkeypatch):
    from live2d_pipeline import decompose

    output = tmp_path / "decomposed"
    original = tmp_path / "portrait.png"

    def fake_inference(*args, **kwargs):
        (output / "portrait.psd").write_bytes(b"color fixture")
        (output / "portrait_depth.psd").write_bytes(b"depth fixture")

    monkeypatch.setattr(decompose, "run", fake_inference)
    result = decompose.decompose(Config(), original, output, tmp_path / "job.log")
    assert result == output / "portrait.psd"


def test_upstream_side_suffixes_survive_normalization(tmp_path):
    psd = PSDImage.new("RGBA", (32, 32))
    for name in ("eyelash-l", "eyelash-r"):
        psd.create_pixel_layer(Image.new("RGBA", (8, 8), "black"), name=name)
    source = tmp_path / "sides.psd"
    psd.save(source)
    parts = assets.normalize(source, tmp_path / "parts")
    assert [(p.semantic, p.side) for p in parts.parts] == [("eyelash", "left"), ("eyelash", "right")]


@pytest.fixture
def planner_material_context():
    return {
        "parts": {
            "width": 832,
            "height": 1216,
            "parts": [
                {
                    "id": "front_hair_none_1",
                    "name": "front hair front_hair_none_1",
                    "semantic": "front hair",
                    "side": "none",
                    "order": 19,
                    "bounds": [100, 100, 300, 400],
                    "image": "front_hair_none_1.png",
                }
            ],
        }
    }


def test_planner_schema_rejects_misplaced_image_instructions(planner_material_context):
    import jsonschema
    from live2d_pipeline.planner import generation_schema

    invalid = {
        "request": "重画",
        "prompt": "character",
        "operations": [{"kind": "regenerate", "arguments": {"prompt": "character"}}],
    }
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(invalid, generation_schema(planner_material_context))
    invalid["operations"] = [{"kind": "regenerate", "instruction": "character"}]
    jsonschema.validate(invalid, generation_schema(planner_material_context))


@pytest.mark.parametrize(
    "operation",
    [
        {"kind": "recolor", "color": "#8A2BE2"},
        {"kind": "repaint", "instruction": "Add fine hair highlights"},
        {"kind": "remove"},
    ],
)
def test_planner_schema_accepts_material_ids(planner_material_context, operation):
    import jsonschema
    from live2d_pipeline.planner import generation_schema

    plan = {
        "request": "修改前发",
        "prompt": "character",
        "operations": [{**operation, "target": "front_hair_none_1"}],
    }
    jsonschema.validate(plan, generation_schema(planner_material_context))


@pytest.mark.parametrize("target", ["ArtMeshFrontHairL", "lyid:882026013", "front hair front_hair_none_1"])
@pytest.mark.parametrize(
    "operation",
    [
        {"kind": "recolor", "color": "#8A2BE2"},
        {"kind": "repaint", "instruction": "Add fine hair highlights"},
        {"kind": "remove"},
    ],
)
def test_planner_schema_rejects_non_material_targets(planner_material_context, operation, target):
    import jsonschema
    from live2d_pipeline.planner import generation_schema

    plan = {
        "request": "修改前发",
        "prompt": "character",
        "operations": [{**operation, "target": target}],
    }
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(plan, generation_schema(planner_material_context))


def test_generated_face_tracks_closed_mouth_and_stacking(tmp_path):
    psd = PSDImage.new("RGBA", (32, 32))
    for name, color in (("irides-l", "blue"), ("mouth", "red"), ("face", "white")):
        psd.create_pixel_layer(Image.new("RGBA", (8, 8), color), name=name)
    source = tmp_path / "face.psd"
    psd.save(source)
    directory = tmp_path / "parts"
    assets.normalize(source, directory)
    assets.prepare_generated_face(directory, "1girl, closed mouth, smile")
    parts = assets.load_parts(directory)
    by_tag = {p.semantic: p for p in parts.parts}
    assert "mouth_close" in by_tag and "mouth" not in by_tag
    assert by_tag["face"].order < by_tag["irides"].order
    assert "mouth_open" in assets.quality(directory)["missing_parts"]
    assets.write_psd(directory, tmp_path / "export.psd")
    names = [layer.name for layer in PSDImage.open(tmp_path / "export.psd")]
    assert any(name.startswith("irides") and name.endswith("-l") for name in names)


def test_pause_after_image_does_not_decompose(tmp_path, monkeypatch):
    from live2d_pipeline import comfy, decompose

    def generated(config, prompt, negative, output, *args, **kwargs):
        Image.new("RGB", (64, 64), "white").save(output)

    monkeypatch.setattr(comfy, "image_job", generated)
    monkeypatch.setattr(decompose, "decompose", lambda *args: pytest.fail("must pause before decomposition"))
    store = Store(tmp_path / "p")
    store.init()
    revision = store.create(Plan(request="test", prompt="test"))
    pipeline = Pipeline(Config(), store)
    pipeline.run(revision, until="image")
    assert store.get().status == "paused"
    assert store.get().stages["image"].state == "complete"
    assert "decompose" not in store.get().stages


def test_recolor_honors_brightness_and_preserves_alpha():
    image = Image.new("RGBA", (2, 1))
    image.putdata([(80, 80, 80, 255), (160, 160, 160, 128)])
    black = assets.recolor(image, "#000000")
    assert [p[:3] for p in black.get_flattened_data()] == [(0, 0, 0), (0, 0, 0)]
    blue = assets.recolor(image, "#80c0ff")
    assert blue.getpixel((1, 0))[2] > blue.getpixel((0, 0))[2]
    assert blue.getchannel("A").tobytes() == image.getchannel("A").tobytes()


def test_generated_image_rejects_silent_black_output(tmp_path):
    from live2d_pipeline.comfy import validate_generated_image

    path = tmp_path / "failed.png"
    Image.new("RGB", (64, 64), "black").save(path)
    with pytest.raises(RuntimeError, match="Degenerate generated image"):
        validate_generated_image(path)
    image = Image.new("RGB", (64, 64), "white")
    image.paste("blue", (16, 16, 48, 48))
    image.save(path)
    validate_generated_image(path)


def test_download_reuses_main_cache_and_rebuilds_entry(tmp_path, monkeypatch):
    import hashlib
    import huggingface_hub
    from live2d_pipeline.setup import download_models

    config = Config()
    config.root = tmp_path
    config.models = tmp_path / "models"
    config.hf_cache = tmp_path / "shared-hub"
    config.hf_cache.mkdir()
    blob = config.hf_cache / "weight"
    blob.write_bytes(b"cached weight")
    manifest = {
        "files": [
            {
                "repo": "test/model",
                "file": "weight",
                "revision": "a" * 40,
                "destination": "planner/weight",
                "bytes": blob.stat().st_size,
                "sha256": hashlib.sha256(blob.read_bytes()).hexdigest(),
            }
        ]
    }
    (tmp_path / "models.lock.json").write_text(__import__("json").dumps(manifest))

    def cached(*args, **kwargs):
        assert kwargs["cache_dir"] == config.hf_cache
        return str(blob)

    monkeypatch.setattr(huggingface_hub, "try_to_load_from_cache", cached)
    monkeypatch.setattr(huggingface_hub, "hf_hub_download", cached)
    # No extra disk space is required when every weight is already cached.
    monkeypatch.setattr(
        "live2d_pipeline.setup.shutil.disk_usage", lambda p: (_ for _ in ()).throw(AssertionError())
    )
    download_models(config)
    entry = config.models / "planner/weight"
    assert entry.is_symlink() and entry.resolve() == blob
    download_models(config)
    assert entry.read_bytes() == b"cached weight"
    assert not (config.models / ".hf").exists()
