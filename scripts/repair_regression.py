"""Record fixed repair cases without changing project revisions.

Default: verify the saved inputs and report historical evidence, with no models.
Use --execute to run independent cloned drafts sequentially under the task lock.
Example: nix develop --command uv run python scripts/repair_regression.py
"""

import argparse
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path
import shutil
import time
from typing import Literal
import uuid

from pydantic import Field

from live2d_pipeline import repair
from live2d_pipeline.config import ROOT, Config
from live2d_pipeline.repair_schema import DraftCreate, DraftUpdate
from live2d_pipeline.schema import Strict
from live2d_pipeline.store import Store, digest, fingerprint, lock, read_json, write_json


class Case(Strict):
    name: str = Field(min_length=1)
    project: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")
    draft: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    step: Literal["preview", "search", "inspect"]
    fixture: Literal["defect", "operation", "not_fixture"] = "operation"
    note: str = ""


def validate_source(store, identifier):
    """Unlike editable snapshots, a regression source may already have been applied."""
    draft = repair.load(store, identifier)
    root = repair.directory(store, identifier)
    store.get(draft.base_revision)
    base = store.revision_dir(draft.base_revision) / "materials"
    if digest(root / "source.png") != draft.source_hash:
        raise ValueError("保存的源图片哈希不一致")
    if any(
        fingerprint(repair.material_files(directory)) != draft.material_hash
        for directory in (root / "base", base)
    ):
        raise ValueError("保存的素材或基础版本素材已变化")
    if draft.rig_hash:
        workspace = store.revision_dir(draft.base_revision) / "rig/source.psd2live"
        if not workspace.is_file() or digest(workspace) != draft.rig_hash:
            raise ValueError("保存的基础绑定工程已变化")
    repair.validate_edit(root, draft.edit)
    return draft


def clone_source(config, store, source):
    """Reuse the normal create/edit path, including historical imported artwork."""
    original = repair.directory(store, source.id)
    upload = None
    if source.edit.mode in {"add", "replace"}:
        upload = config.runtime / "web/uploads" / (uuid.uuid4().hex + ".png")
        upload.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original / "source.png", upload)
    try:
        child = repair.create(
            config,
            store,
            DraftCreate(
                revision=source.base_revision,
                part=source.part,
                mode=source.edit.mode,
                upload=upload.name if upload else None,
            ),
        )
    finally:
        if upload:
            upload.unlink(missing_ok=True)
    if child.source_hash != source.source_hash or child.material_hash != source.material_hash:
        raise ValueError("克隆输入与保存的回归素材不一致")
    edit = source.edit.model_copy(deep=True)
    edit.visual_accepted = False
    child = repair.update(store, child.id, DraftUpdate(version=child.version, edit=edit))
    child.result["regression_source"] = {"draft": source.id, "version": source.version}
    segment = source.result.get("segmentation")
    if segment:
        path = Path(segment["path"])
        if (
            path.is_absolute()
            or ".." in path.parts
            or not (original / path).resolve().is_relative_to(original.resolve())
        ):
            raise ValueError("分割证据路径越界")
        shutil.copytree(original / path, repair.directory(store, child.id) / path)
        child.result["segmentation"] = segment
    repair.save(store, child)
    return child


def collect_evidence(draft):
    candidates = draft.result.get("search", {}).get("candidates", [])
    previews = [draft.result.get(key, {}) for key in ("preview", "binding_preview")]
    return {
        "program_checks": [check for p in previews for check in p.get("checks", [])]
        + [check for c in candidates for check in c.get("checks", [])],
        "vlm": ([{"draft": draft.id, **draft.result["visual"]}] if "visual" in draft.result else [])
        + [{"draft": c["draft"], **c["visual"]} for c in candidates if c.get("visual")],
        "candidate_count": len(candidates),
        "candidates": [
            {key: c.get(key) for key in ("draft", "version", "status", "reason", "elapsed", "visual")}
            for c in candidates
        ],
        "comparison": draft.result.get("search", {}).get("comparison"),
        "recommended": draft.result.get("search", {}).get("recommended"),
        "search_total_elapsed": draft.result.get("search", {}).get("total_elapsed"),
    }


def evaluate(config, projects, cases, output, *, execute=False):
    report = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "mode": "execute_clones" if execute else "check_saved_inputs",
        "applies_revisions": False,
        "metric_note": "source_saved_edit_versions = 源草稿版本号减一，包含程序保存；不是鼠标手势数或人工工作量。",
        "acceptance_note": "程序检查与VLM判断分别记录，完成运行不等于视觉验收。",
        "cases": [],
    }
    write_json(output, report)
    with lock(config.runtime / "task.lock") if execute else nullcontext():
        for case in cases:
            started = time.monotonic()
            row = {
                **case.model_dump(),
                "status": "pending",
                "human_acceptance": "not_evaluated",
                "evidence_origin": "executed_clone" if execute else "saved_history",
                "program_checks": [],
                "vlm": [],
                "candidate_count": 0,
            }
            clone = None
            store = Store(projects / case.project)
            try:
                if case.fixture == "not_fixture":
                    row["status"] = "not_fixture"
                elif case.draft is None:
                    raise ValueError("可执行回归案例必须指定源草稿")
                else:
                    with lock(store.root / ".lock") if execute else nullcontext():
                        state = read_json(store.root / "project.json")
                        source = validate_source(store, case.draft)
                        row.update(
                            base_revision=source.base_revision,
                            source_draft_version=source.version,
                            source_saved_edit_versions=source.version - 1,
                            input_checks="pass",
                        )
                        if not execute:
                            row.update(status="available", **collect_evidence(source))
                        else:
                            clone = clone_source(config, store, source)
                            row["clone"] = clone.id
                            args = (config, store, clone.id, clone.version)
                            if case.step == "search":
                                repair.search(*args)
                            else:
                                if clone.edit.mode == "bind":
                                    repair.binding_preview(*args)
                                else:
                                    repair.preview_materials(*args)
                                if case.step == "inspect":
                                    repair.inspect(*args)
                            clone = repair.load(store, clone.id)
                            row.update(
                                status="completed", human_acceptance="pending", **collect_evidence(clone)
                            )
                            if clone.applied_revision or read_json(store.root / "project.json") != state:
                                raise RuntimeError("回归执行意外改变正式版本，请检查保存的证据")
            except Exception as exc:
                row.update(
                    status="missing" if isinstance(exc, FileNotFoundError) else "failed", error=str(exc)
                )
                if clone:
                    try:
                        row.update(collect_evidence(repair.load(store, clone.id)))
                    except Exception as evidence_error:
                        row["evidence_error"] = str(evidence_error)
            row["elapsed_seconds"] = round(time.monotonic() - started, 3)
            report["cases"].append(row)
            write_json(output, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--manifest", type=Path, default=ROOT / "docs/repair-regression-cases.json")
    parser.add_argument("--projects", type=Path, default=ROOT / "projects")
    parser.add_argument("--output", type=Path, default=ROOT / ".runtime/repair-regression/report.json")
    parser.add_argument("--case", action="append", help="仅运行指定名称，可重复")
    parser.add_argument("--execute", action="store_true", help="运行模型；所有操作写入新草稿且不应用正式版本")
    args = parser.parse_args()
    cases = [Case.model_validate(c) for c in read_json(args.manifest)["cases"]]
    if args.case:
        if set(args.case) - {case.name for case in cases}:
            parser.error("--case 包含清单中不存在的名称")
        cases = [case for case in cases if case.name in args.case]
    report = evaluate(Config(args.config), args.projects, cases, args.output, execute=args.execute)
    for row in report["cases"]:
        print(f"{row['name']}: {row['status']}")
    print(f"Report: {args.output.resolve()}")
    return int(any(row["status"] in {"failed", "missing"} for row in report["cases"]))


if __name__ == "__main__":
    raise SystemExit(main())
