"""Browser API and real worker checks; no model services or CUDA are started."""

import io
import signal
import subprocess
import sys
import time
import uuid
import zipfile

from PIL import Image
import pytest
from starlette.testclient import TestClient

from live2d_pipeline.config import Config
from live2d_pipeline.pipeline import Pipeline
from live2d_pipeline.schema import Plan
from live2d_pipeline.store import Store, digest, lock, read_json, write_json
from live2d_pipeline.web.app import create_app
from live2d_pipeline.web.jobs import JobRequest, Jobs, tail
from live2d_pipeline.web.worker import execute
from test_pipeline import fixture_bundle, source_psd

HEADERS = {"X-L2D-Request": "1", "Origin": "http://127.0.0.1"}


@pytest.fixture
def web_config(tmp_path):
    original = Config()
    path = tmp_path / "local.toml"
    text = original.path.read_text().replace('runtime = ".runtime"', f'runtime = "{tmp_path}/runtime"')
    path.write_text(text)
    config = Config(path)
    assert config.runtime == tmp_path / "runtime"
    return config


@pytest.fixture
def client(web_config, tmp_path):
    app = create_app(web_config, tmp_path / "projects")
    with TestClient(app, base_url="http://127.0.0.1", headers=HEADERS) as client:
        yield client


def create_store(tmp_path):
    store = Store(tmp_path / "projects/角色")
    store.init()
    return store


def job_file(config, store, **options):
    spec = JobRequest(expected_head=read_json(store.root / "project.json")["head"], **options)
    job_id = uuid.uuid4().hex
    directory = config.runtime / "web/jobs" / job_id
    write_json(
        directory / "job.json",
        {
            "id": job_id,
            "project": store.root.name,
            "spec": spec.model_dump(),
            "status": "queued",
            "created": time.time(),
            "phase": "等待执行",
            "revision": spec.revision,
            "error": None,
        },
    )
    return directory


def wait_job(client, job_id):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        job = next(j for j in client.get("/api/state").json()["jobs"] if j["id"] == job_id)
        if job["status"] not in {"queued", "running", "cancelling"}:
            return job
        time.sleep(0.02)
    pytest.fail("Worker did not finish")


def test_web_startup_projects_and_read_only_preview(client, tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Reading the GUI must not start any subprocess")

    monkeypatch.setattr(subprocess, "Popen", forbidden)
    assert client.get("/").status_code == 200
    page = client.get("/")
    assert "本地创作工作台" in page.text
    assert page.headers["cache-control"] == "no-cache"
    from live2d_pipeline.web.app import STATIC

    assert f"/static/app.js?v={digest(STATIC / 'app.js')[:12]}" in page.text
    assert client.get("/static/app.js").status_code == 200
    assert client.post("/api/projects", json={"name": "角色"}).status_code == 201
    assert client.post("/api/projects", json={"name": "角色"}).status_code == 400
    assert client.get("/api/projects/角色").json()["revision"] is None
    store = Store(tmp_path / "projects/角色")
    rev = store.create(Plan(request="银发", prompt="silver"))
    directory = store.revision_dir(rev.id) / "image"
    directory.mkdir()
    Image.new("RGB", (16, 16), "silver").save(directory / "input.png")
    detail = client.get("/api/projects/角色").json()
    assert detail["revision"]["plan"]["request"] == "银发"
    assert client.get(detail["previews"][0]["url"]).headers["content-type"] == "image/png"
    assert client.get("/api/state").json()["projects"][0]["thumbnail"]
    assert "torch" not in sys.modules


def test_local_origin_and_path_boundaries(client, tmp_path):
    assert client.post("/api/projects", json={"name": "../outside"}).status_code == 400
    assert (
        client.post(
            "/api/projects", json={"name": "valid"}, headers={"Origin": "https://other.example"}
        ).status_code
        == 403
    )
    assert (
        client.post("/api/projects", json={"name": "valid"}, headers={"X-L2D-Request": ""}).status_code == 403
    )
    assert client.get("/api/state", headers={"Host": "outside.example"}).status_code == 400
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "projects").mkdir(exist_ok=True)
    (tmp_path / "projects/escape").symlink_to(outside, target_is_directory=True)
    assert client.post("/api/projects", json={"name": "escape"}).status_code == 400
    store = create_store(tmp_path)
    rev = store.create(Plan(request="x", prompt="x"))
    image = outside / "secret.png"
    Image.new("RGB", (8, 8)).save(image)
    (store.revision_dir(rev.id) / "escape.png").symlink_to(image)
    base = f"/api/projects/角色/assets/{rev.id}"
    assert client.get(base + "/escape.png").status_code == 400
    assert client.get(base + "/revision.json").status_code == 404
    assert client.get(base + "/%2e%2e/%2e%2e/project.json").status_code == 400
    assert client.get("/api/projects/角色?revision=..").status_code == 400


def test_upload_png_psd_invalid_and_reference_schema(client, web_config, tmp_path):
    image = io.BytesIO()
    Image.new("RGB", (16, 16), "red").save(image, format="PNG")
    response = client.post(
        "/api/uploads", files={"file": ("../../source.png", image.getvalue(), "image/png")}
    )
    assert response.status_code == 201, response.text
    token = response.json()["token"]
    assert "/" not in token
    assert (web_config.runtime / "web/uploads" / token).read_bytes() == image.getvalue()
    psd = source_psd(tmp_path / "source.psd")
    response = client.post("/api/uploads", files={"file": ("source.psd", psd.read_bytes())})
    assert response.status_code == 201, response.text
    assert client.post("/api/uploads", files={"file": ("bad.png", b"invalid")}).status_code == 400
    assert client.post("/api/uploads", files={"file": ("x.html", b"<html>")}).status_code == 400
    create_store(tmp_path)
    assert (
        client.post("/api/projects/角色/jobs", json={"action": "shell", "expected_head": None}).status_code
        == 400
    )
    assert (
        client.post(
            "/api/projects/角色/jobs", json={"action": "plan", "expected_head": None, "source": "/etc/passwd"}
        ).status_code
        == 400
    )
    assert (
        client.post(
            "/api/projects/角色/jobs", json={"action": "plan", "expected_head": None, "request": ""}
        ).status_code
        == 400
    )


def test_worker_uses_shared_pipeline_pauses_resumes_and_keeps_versions(web_config, tmp_path, monkeypatch):
    store = create_store(tmp_path)
    source = source_psd(tmp_path / "source.psd")
    token = uuid.uuid4().hex + ".psd"
    uploads = web_config.runtime / "web/uploads"
    uploads.mkdir(parents=True)
    (uploads / token).write_bytes(source.read_bytes())
    monkeypatch.setattr(
        "live2d_pipeline.services.make_plan", lambda c, r, context, log: Plan(request=r, prompt="anime")
    )

    async def build(config, psd, output, operations, parent_workspace=None):
        fixture_bundle(output / "bundle")
        (output / "source.psd2live").write_text("test workspace")
        write_json(output / "rig-context.json", {})

    monkeypatch.setattr("live2d_pipeline.rig.build", build)
    first = job_file(web_config, store, action="plan", request="已有 PSD", source=token, until="materials")
    execute(web_config, store.root.parent, first)
    rev = store.get()
    assert rev.status == "paused"
    assert read_json(first / "job.json")["status"] == "succeeded"
    assert rev.stages["materials"].state == "complete"
    second = job_file(web_config, store, action="run", revision=rev.id)
    execute(web_config, store.root.parent, second)
    assert store.get().status == "built"
    assert read_json(second / "job.json")["status"] == "succeeded"
    original = digest(store.revision_dir(rev.id) / "revision.json")
    edit = job_file(web_config, store, action="plan", request="新的修改", until="plan")
    execute(web_config, store.root.parent, edit)
    assert store.get().parent == rev.id
    assert digest(store.revision_dir(rev.id) / "revision.json") == original


def test_worker_records_failure_clarification_and_checks_stale_head(web_config, tmp_path, monkeypatch):
    store = create_store(tmp_path)
    monkeypatch.setattr(
        "live2d_pipeline.services.make_plan",
        lambda c, r, context, log: Plan(request=r, prompt="", clarification="哪种发型？"),
    )
    directory = job_file(web_config, store, action="plan", request="改头发")
    execute(web_config, store.root.parent, directory)
    assert read_json(directory / "job.json")["status"] == "needs_input"
    assert store.get().status == "planned"
    clarified = store.get()
    old_hash = digest(store.revision_dir(clarified.id) / "revision.json")
    monkeypatch.setattr(
        "live2d_pipeline.services.make_plan", lambda c, r, context, log: Plan(request=r, prompt="blue hair")
    )
    replacement = job_file(web_config, store, action="plan", request="蓝色短发的成年角色", until="plan")
    execute(web_config, store.root.parent, replacement)
    assert store.get().parent is None
    assert store.get().id != clarified.id
    assert digest(store.revision_dir(clarified.id) / "revision.json") == old_hash
    old = job_file(web_config, store, action="run")
    store.create(Plan(request="new", prompt="new"))
    execute(web_config, store.root.parent, old)
    assert "版本已经改变" in read_json(old / "job.json")["error"]
    failed = job_file(web_config, store, action="run")

    def fail(*args, **kwargs):
        raise RuntimeError("expected backend failure")

    monkeypatch.setattr(Pipeline, "run", fail)
    execute(web_config, store.root.parent, failed)
    assert read_json(failed / "job.json")["error"] == "expected backend failure"
    assert read_json(failed / "job.json")["status"] == "failed"


def test_replan_draft_uses_completed_base_and_retains_import(web_config, tmp_path, monkeypatch):
    from live2d_pipeline.services import plan_revision

    store = create_store(tmp_path)
    original_source = source_psd(tmp_path / "source.psd")
    base = store.create(Plan(request="base", prompt="base"))
    base.status = "built"
    store.save(base)
    write_json(store.revision_dir(base.id) / "rig/rig-context.json", {"objects": ["completed"]})
    draft = store.create(Plan(request="draft", prompt="draft"), source=original_source)
    write_json(store.revision_dir(draft.id) / "rig/rig-context.json", {"objects": ["partial"]})
    sha = digest(store.revision_dir(draft.id) / "revision.json")

    def plan(c, request, context, log):
        assert "rig" not in context
        assert context["parts"]["parts"]
        return Plan(request=request, prompt="new")

    monkeypatch.setattr("live2d_pipeline.services.make_plan", plan)
    revised = plan_revision(web_config, store, "完整的新需求")
    assert revised.parent == base.id
    assert revised.source_hash == digest(original_source)
    assert digest(store.revision_dir(draft.id) / "revision.json") == sha
    assert len(store.history()) == 3


def test_real_worker_export_download_and_cli_task_lock(client, web_config, tmp_path):
    store = create_store(tmp_path)
    rev = store.create(Plan(request="test fixture", prompt="test"))
    directory = store.revision_dir(rev.id)
    fixture_bundle(directory / "rig/bundle")
    (directory / "rig/source.psd").write_bytes(b"test fixture source")
    (directory / "rig/source.psd2live").write_bytes(b"test fixture workspace")
    (directory / "materials").mkdir()
    (directory / "materials/test.txt").write_text("fixture layer")
    rev.status = "built"
    store.save(rev)
    original = digest(directory / "revision.json")
    response = client.post("/api/projects/角色/jobs", json={"action": "export", "expected_head": rev.id})
    assert response.status_code == 202
    job = wait_job(client, response.json()["id"])
    assert job["status"] == "succeeded", job
    download = client.get(job["download"])
    assert download.status_code == 200
    with zipfile.ZipFile(io.BytesIO(download.content)) as archive:
        assert "model/test.moc3" in archive.namelist()
        assert archive.testzip() is None
    assert digest(directory / "revision.json") == original
    # A CLI-owned GPU task must prevent web execution as well.
    with lock(web_config.runtime / "task.lock"):
        response = client.post("/api/projects/角色/jobs", json={"action": "export", "expected_head": rev.id})
        failed = wait_job(client, response.json()["id"])
        assert failed["status"] == "failed"
        assert "Task already running" in failed["error"]

    draft = store.create(Plan(request="another proposal", prompt="draft"))
    response = client.post(
        "/api/projects/角色/jobs",
        json={"action": "checkout", "expected_head": draft.id, "revision": rev.id},
    )
    assert wait_job(client, response.json()["id"])["status"] == "succeeded"
    assert store.get().id == rev.id
    response = client.post(
        "/api/projects/角色/jobs",
        json={"action": "accept", "expected_head": rev.id, "revision": rev.id},
    )
    assert wait_job(client, response.json()["id"])["status"] == "succeeded"
    assert store.get().status == "accepted"
    assert read_json(store.root / "project.json")["accepted"] == rev.id


def test_owned_job_cancellation_busy_and_restart(web_config, tmp_path, monkeypatch):
    jobs = Jobs(web_config, tmp_path / "projects")
    original_popen = subprocess.Popen
    marker = tmp_path / "ready"
    cleanup = tmp_path / "cleanup"
    code = (
        "import signal,time,pathlib\n"
        "def stop(sig, frame):\n"
        " raise SystemExit(143)\n"
        "signal.signal(signal.SIGTERM,stop)\n"
        f"pathlib.Path({str(marker)!r}).touch()\n"
        "try:\n time.sleep(10)\n"
        f"finally:\n pathlib.Path({str(cleanup)!r}).touch()\n"
    )
    monkeypatch.setattr(
        subprocess, "Popen", lambda command, **kw: original_popen([sys.executable, "-c", code], **kw)
    )
    job = jobs.submit("test", JobRequest(action="run", expected_head=None))
    process = jobs.process
    try:
        with pytest.raises(RuntimeError, match="已有任务"):
            jobs.submit("test", JobRequest(action="run", expected_head=None))
        deadline = time.monotonic() + 5
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert marker.exists()
        jobs.cancel(job["id"])
        process.wait(timeout=5)
        assert cleanup.exists()
        assert jobs.list()[0]["status"] == "cancelled"
    finally:
        if process.poll() is None:
            process.send_signal(signal.SIGTERM)
            process.wait(timeout=5)
    path = jobs.root / job["id"] / "job.json"
    data = read_json(path)
    data["status"] = "running"
    write_json(path, data)
    jobs.recover()
    assert jobs.list()[0]["status"] == "interrupted"
    path.with_name("output.log").write_bytes(b"x" * 100000 + "最新日志".encode())
    assert len(tail(path.with_name("output.log"))) < 16000
    assert tail(path.with_name("output.log")).endswith("最新日志")


def test_web_refinement_review_and_manual_acceptance(web_config, tmp_path, monkeypatch):
    store = create_store(tmp_path)
    base = store.create(Plan(request="original", prompt="anime"))
    base.status = "built"
    store.save(base)
    from live2d_pipeline import review

    def refine(config, store, request, revision):
        assert request == "领结有点僵硬"
        return {"revision": revision, "drafts": [{"draft": "a" * 32, "status": "ready_for_review"}],
                "findings": [], "user_acceptance": "pending"}

    monkeypatch.setattr(review, "refine", refine)
    job = job_file(web_config, store, action="refine", request="领结有点僵硬", revision=base.id)
    execute(web_config, store.root.parent, job)
    assert read_json(job / "job.json")["status"] == "succeeded"
    candidate = store.get()
    assert candidate.id == base.id
    assert read_json(job / "job.json")["drafts"][0]["status"] == "ready_for_review"
    assert read_json(store.root / "project.json")["accepted"] is None
    report = {"revision": candidate.id, "findings": [], "reviewed_views": ["pose-00"]}
    write_json(store.root / "reviews" / candidate.id / "latest.json", report)
    app = create_app(web_config, store.root.parent)
    with TestClient(app, base_url="http://127.0.0.1", headers=HEADERS) as client:
        assert client.get("/api/projects/角色").json()["review"] == report
        assert "细化部件与动作" in client.get("/").text
    store.accept(candidate.id)
    assert store.get().status == "accepted"


def test_video_worker_playback_download_and_revision_isolation(client, web_config, tmp_path, monkeypatch):
    store = create_store(tmp_path)
    rev = store.create(Plan(request="video", prompt="video"))
    rev.status = "built"
    store.save(rev)
    original = digest(store.revision_dir(rev.id) / "revision.json")

    def generate(config, current, rid, progress):
        assert rid == rev.id
        progress("渲染预览视频 192/192 帧")
        target = current.root / "previews" / rid / "preview.mp4"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"test-video-content")
        return target

    monkeypatch.setattr("live2d_pipeline.video.generate", generate)
    job = job_file(web_config, store, action="video", revision=rev.id)
    execute(web_config, store.root.parent, job)
    assert read_json(job / "job.json")["status"] == "succeeded"
    detail = client.get("/api/projects/角色").json()
    video = client.get(detail["video"])
    assert video.headers["content-type"] == "video/mp4"
    assert video.headers["content-disposition"].startswith("inline")
    assert client.get(detail["video"], headers={"Range": "bytes=0-3"}).status_code == 206
    assert client.get(detail["video"] + "&download=1").headers["content-disposition"].startswith("attachment")
    assert digest(store.revision_dir(rev.id) / "revision.json") == original
    store.create(Plan(request="new", prompt="new"))
    assert client.get("/api/projects/角色").json()["video"] is None
    target = store.root / "previews" / rev.id / "preview.mp4"
    target.unlink()
    target.symlink_to(tmp_path / "outside.mp4")
    assert client.get(detail["video"]).status_code == 400


def test_accept_history_does_not_switch_creation_base(web_config, tmp_path):
    store = create_store(tmp_path)
    old = store.create(Plan(request="old", prompt="old"))
    old.status = "built"
    store.save(old)
    current = store.create(Plan(request="current", prompt="current"))
    job = job_file(web_config, store, action="accept", revision=old.id)
    execute(web_config, store.root.parent, job)
    assert read_json(job / "job.json")["status"] == "succeeded"
    assert store.get().id == current.id
    assert store.get(old.id).status == "accepted"
    assert read_json(store.root / "project.json")["accepted"] == old.id
    store.checkout(old.id)
    assert store.get().id == old.id


def test_reference_conflicts_fail_before_creating_revision(web_config, tmp_path, monkeypatch):
    from live2d_pipeline.services import plan_revision
    from live2d_pipeline.schema import Operation

    store = create_store(tmp_path)
    image = tmp_path / "input.png"
    Image.new("RGB", (8, 8), "red").save(image)
    calls = []

    def plan(*args):
        calls.append(True)
        return Plan(request="edit", prompt="edit")

    monkeypatch.setattr("live2d_pipeline.services.make_plan", plan)
    with pytest.raises(ValueError, match="不能同时使用参考图"):
        plan_revision(web_config, store, "x", source=image, reference=image)
    assert not calls
    original = store.create(Plan(request="original", prompt="original"))
    original.status = "built"
    store.save(original)
    with pytest.raises(ValueError, match="普通局部修改"):
        plan_revision(web_config, store, "x", reference=image)
    assert store.get().id == original.id
    monkeypatch.setattr(
        "live2d_pipeline.services.make_plan",
        lambda *args: Plan(
            request="whole",
            prompt="whole",
            operations=[Operation(kind="regenerate", instruction="redraw the whole character")],
        ),
    )
    draft = plan_revision(web_config, store, "whole", reference=image)
    assert draft.reference
    # The user can explicitly discard the saved input when replacing a draft.
    monkeypatch.setattr("live2d_pipeline.services.make_plan", plan)
    clean = plan_revision(web_config, store, "edit", reuse_inputs=False)
    assert clean.reference is None and clean.parent == original.id


def test_resume_targets_are_available_stages_and_worker_honors_choice(web_config, tmp_path, monkeypatch):
    from live2d_pipeline.schema import Stage
    from live2d_pipeline.web.presentation import resume_targets

    store = create_store(tmp_path)
    rev = store.create(Plan(request="new", prompt="new"))
    assert resume_targets(rev) == ["image", "materials", "rig"]
    calls = []

    def run(self, revision, until):
        calls.append(until)
        revision.stages[until] = Stage(fingerprint="x", state="complete")
        revision.status = "paused"
        self.store.save(revision)

    monkeypatch.setattr(Pipeline, "run", run)
    job = job_file(web_config, store, action="run", revision=rev.id, until="image", request="must not replan")
    execute(web_config, store.root.parent, job)
    assert calls == ["image"]
    assert resume_targets(store.get()) == ["materials", "rig"]
    stale = job_file(web_config, store, action="run", revision=rev.id, until="image")
    execute(web_config, store.root.parent, stale)
    assert read_json(stale / "job.json")["status"] == "failed"
    assert calls == ["image"]
    rev.source = "source.psd"
    rev.stages = {}
    assert resume_targets(rev) == ["materials", "rig"]
    rev.source = None
    rev.parent = "parent"
    assert resume_targets(rev) == ["materials", "rig"]
    rev.stages["materials"] = Stage(fingerprint="x", state="complete")
    assert resume_targets(rev) == ["rig"]


def test_detail_links_review_poses_and_explains_refinement_changes(client, tmp_path):
    from live2d_pipeline.schema import Binding, Refinement

    store = create_store(tmp_path)
    base = store.create(Plan(request="base", prompt="base"))
    base.status = "built"
    store.save(base)
    write_json(
        store.revision_dir(base.id) / "materials/parts.json",
        {
            "parts": [
                {
                    "id": "bow",
                    "name": "领结",
                    "semantic": "neckwear",
                    "binding": {"motion": "sway", "degrees": 4},
                }
            ]
        },
    )
    rev = store.create(
        Plan(
            request="smaller",
            prompt="base",
            refinements=[
                Refinement(
                    action="bind",
                    source="bow",
                    name="领结",
                    binding=Binding(motion="sway", degrees=2),
                    reason="减小摆幅",
                )
            ],
        )
    )
    rev.status = "built"
    store.save(rev)
    directory = store.revision_dir(rev.id) / "rig"
    directory.mkdir()
    Image.new("RGB", (16, 16)).save(directory / "pose-01.png")
    write_json(directory / "pose-samples.json", [{}, {"ParamAngleX": 30}])
    detail = client.get("/api/projects/角色").json()
    assert detail["previews"][0]["view"] == "pose-01"
    assert detail["previews"][0]["label"] == "向右转头"
    change = detail["refinement_changes"][0]
    assert change["completed"] and change["binding"]["degrees"] == 2
    assert change["previous_binding"]["degrees"] == 4
    assert detail["part_names"]["bow"] == "领结"
    assert detail["revision"]["resume_targets"] == []


def test_uploads_only_apply_to_planning(client, tmp_path):
    create_store(tmp_path)
    for body in [
        {"action": "plan", "source": "a" * 32 + ".png", "reference": "b" * 32 + ".png", "request": "x"},
        {"action": "review", "source": "a" * 32 + ".png"},
    ]:
        assert client.post("/api/projects/角色/jobs", json={"expected_head": None, **body}).status_code == 400
