"""Process boundary keeps model signal handlers off the web server's event loop."""

import ctypes
import os
from pathlib import Path
import shutil
import signal
import sys
import time
import traceback

from ..config import Config
from ..pipeline import Pipeline
from ..process import terminate_invocation
from ..services import export_revision, plan_revision
from ..store import Store, lock, read_json, write_json
from .jobs import JobRequest


def execute(config, projects, directory):
    path = directory / "job.json"
    job = read_json(path)
    spec = JobRequest.model_validate(job["spec"])
    store = Store(projects / job["project"])

    def update(**fields):
        job.update(fields)
        write_json(path, job)
        print(job["phase"], flush=True)

    try:
        with lock(config.runtime / "task.lock"), lock(store.root / ".lock"):
            if (directory / "cancel.request").exists():
                raise SystemExit(143)
            state = read_json(store.root / "project.json")
            if state["head"] != spec.expected_head:
                raise ValueError("项目版本已经改变，请刷新后重新提交。")
            update(status="running", started=time.time(), phase="准备任务")
            if spec.action == "plan":
                if not spec.request.strip():
                    raise ValueError("请先描述角色或修改需求。")
                uploads = config.runtime / "web/uploads"
                update(phase="解析需求")
                rev = plan_revision(
                    config,
                    store,
                    spec.request,
                    source=uploads / spec.source if spec.source else None,
                    reference=uploads / spec.reference if spec.reference else None,
                )
                update(revision=rev.id, phase="方案已生成")
                if rev.plan.clarification or rev.plan.unsupported:
                    update(
                        status="needs_input", error=rev.plan.clarification or "；".join(rev.plan.unsupported)
                    )
                elif spec.until != "plan":
                    update(phase="执行流水线")
                    Pipeline(config, store).run(rev, until=spec.until)
            elif spec.action == "run":
                if spec.until == "plan":
                    raise ValueError("继续生成时请选择立绘、素材或完整模型。")
                rev = store.get(spec.revision)
                update(revision=rev.id, phase="执行流水线")
                Pipeline(config, store).run(rev, until=spec.until)
            elif spec.action in {"checkout", "accept"}:
                if not spec.revision:
                    raise ValueError("请选择版本。")
                store.checkout(spec.revision, accept=spec.action == "accept")
                update(revision=spec.revision, phase="版本已更新")
            elif spec.action == "export":
                rev = store.get(spec.revision)
                update(revision=rev.id, phase="校验并打包")
                target = export_revision(store, directory / "delivery", rev.id)
                previews = store.revision_dir(rev.id) / "rig"
                for image in previews.glob("pose-*.png"):
                    dest = target / "previews" / image.name
                    dest.parent.mkdir(exist_ok=True)
                    shutil.copyfile(image, dest)
                shutil.make_archive(str(directory / "model"), "zip", target)
                update(download=f"/api/jobs/{job['id']}/download", phase="模型包已就绪")
            if job["status"] != "needs_input":
                update(status="succeeded", phase="已完成")
    except BaseException as exc:
        cancelled = (
            isinstance(exc, (KeyboardInterrupt, SystemExit)) or (directory / "cancel.request").exists()
        )
        update(
            status="cancelled" if cancelled else "failed",
            phase="已停止" if cancelled else "执行失败",
            error="任务已停止，已完成的阶段会保留。" if cancelled else str(exc),
        )
        traceback.print_exc()
    finally:
        update(ended=time.time())


def main():
    signal.signal(signal.SIGTERM, terminate_invocation)
    # Linux is already required by the pipeline's flock, CUDA and container backend.
    # A crashed server must not leave an unowned GPU worker behind.
    if ctypes.CDLL(None, use_errno=True).prctl(1, signal.SIGTERM, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "Could not set worker parent-death signal")
    if os.getppid() != int(sys.argv[4]):
        return
    execute(Config(Path(sys.argv[1])), Path(sys.argv[2]), Path(sys.argv[3]))


if __name__ == "__main__":
    main()
