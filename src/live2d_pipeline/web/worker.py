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
            if spec.action == "repair":
                from .. import repair

                update(
                    draft=spec.draft,
                    phase={
                        "segment": "生成分割候选",
                        "preview": "生成修正预览",
                        "inspect": "核对部件完整性",
                        "suggest": "定位问题并提出修正",
                        "apply": "应用修正并构建",
                        "rig_context": "读取姿态与绑定对象",
                        "binding_preview": "预览绑定与相关姿态",
                        "underpaint": "重做遮挡补全",
                        "search": "生成并比较有限修正候选",
                    }[spec.repair_step],
                )
                args = (config, store, spec.draft, spec.draft_version)
                if spec.repair_step == "apply":
                    rev = repair.apply(*args, until=spec.until)
                    update(revision=rev.id)
                    if rev.status != "built" and spec.until == "rig":
                        update(status="needs_input", error="素材仍有缺失，请在修正工作区补齐后继续绑定")
                elif spec.repair_step == "preview":
                    repair.preview_materials(*args, retry=spec.retry)
                elif spec.repair_step in {"rig_context", "suggest"}:
                    from .. import repair_rig

                    getattr(repair_rig, spec.repair_step)(*args)
                else:
                    getattr(repair, spec.repair_step)(*args)
                update(phase="修正结果已保存，请查看工作区")
            elif spec.action == "plan":
                if not spec.request.strip():
                    raise ValueError("请先描述角色或修改需求。")
                uploads = config.runtime / "web/uploads"
                update(phase="解析需求")
                rev = plan_revision(
                    config,
                    store,
                    spec.request,
                    reuse_inputs=spec.reuse_inputs,
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
                from .presentation import resume_targets

                if spec.until not in resume_targets(rev):
                    raise ValueError("所选阶段已完成或不适用于该方案，请刷新后重新选择。")
                Pipeline(config, store).run(rev, until=spec.until)
            elif spec.action in {"refine", "review"}:
                from ..review import refine, inspect

                update(phase="细化部件并复查" if spec.action == "refine" else "规则与视觉检查")
                if spec.action == "refine":
                    report = refine(config, store, spec.request or "完善头发、服装和饰品", spec.revision)
                    update(revision=report["revision"], drafts=report["drafts"])
                else:
                    report = inspect(config, store, spec.request or "检查细节和动作", spec.revision)
                    update(revision=report["revision"])
                update(review=report, phase="检查完成，等待用户验收")
            elif spec.action in {"checkout", "accept"}:
                if not spec.revision:
                    raise ValueError("请选择版本。")
                if spec.action == "accept":
                    store.accept(spec.revision)
                else:
                    store.checkout(spec.revision)
                update(revision=spec.revision, phase="版本已更新")
            elif spec.action == "video":
                from ..video import generate

                rev = store.get(spec.revision)
                update(revision=rev.id)
                generate(config, store, rev.id, progress=lambda message: update(phase=message))
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
            if spec.action in {"run", "plan"} and spec.until == "rig" and job.get("revision"):
                current = store.get(job["revision"])
                if current.status == "paused":
                    update(status="needs_input", error="素材尚未齐全，可在部件修正工作区补齐后继续")
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
