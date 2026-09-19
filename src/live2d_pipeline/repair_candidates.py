"""Bounded, resumable material alternatives. Model votes never override pixel rules."""

import time
import uuid
from pathlib import Path
from pydantic import Field
from . import repair, vision
from .schema import Strict
from .store import digest, fingerprint, write_json


class Preference(Strict):
    preferred: str | None
    improved: bool
    reason: str
    evidence: list[str] = Field(min_length=1, max_length=8)


def variants(draft):
    edit = draft.edit.model_copy(deep=True)
    rows = [("当前设置", edit)]
    if edit.mode == "split":
        broader = edit.model_copy(deep=True)
        broader.inpaint_padding = min(32, max(12, broader.inpaint_padding + 8))
        if broader.cleanup.allow_visible:
            broader.cleanup.expansion = min(64, max(4, broader.cleanup.expansion + 4))
        rows.append(("扩大清理上下文", broader))
        redrawn = broader.model_copy(deep=True)
        redrawn.cleanup.backend = "comfy"
        rows.append(("带掩膜的语义补画", redrawn))
    elif edit.mode == "repaint":
        for index in (1, 2):
            alternative = edit.model_copy(deep=True)
            alternative.repaint_seed = (edit.repaint_seed + index * 104729) % 2**32
            rows.append((f"局部重绘候选 {index + 1}", alternative))
    else:
        raise ValueError("候选搜索用于拆分补全、局部重绘或绑定修正")
    unique, seen = [], set()
    for label, candidate in rows:
        identity = fingerprint(candidate.model_dump())
        if identity not in seen:
            seen.add(identity)
            unique.append((label, candidate, identity))
    return unique[: edit.candidate_limit]


def visual_status(visual):
    items = visual.get("items", [])
    if not items:
        return "uncertain"
    return (
        "fail"
        if any(i["status"] == "fail" for i in items)
        else ("pass" if all(i["status"] == "pass" for i in items) else "uncertain")
    )


def compare(config, store, parent, rows):
    for row in rows:
        try:
            repair.snapshot(store, row["draft"], row["version"])
        except (RuntimeError, ValueError, FileNotFoundError):
            row.update(status="stale", reason="候选已变化，旧检查不再有效")
    eligible = [row for row in rows if row["status"] == "pass"]
    result = {"recommended": None, "comparison": {"reason": "没有已完成且通过规则与视觉检查的候选"}}
    if not eligible:
        return result
    # Keep the original as a comparison even when it has known visual defects.
    evidence_rows = [row for row in rows if row["status"] != "stale" and row.get("preview")]
    images = []
    for row in evidence_rows:
        child = repair.load(store, row["draft"])
        root = repair.directory(store, child.id)
        path = root / row["preview"]["path"]
        focus = path / ("underpaint.png" if child.edit.mode == "split" else "component.png")
        if not focus.is_file() or not (path / "materials/composite.png").is_file():
            continue
        images += [
            (f"{row['draft']} {row['label']} 局部结果", focus),
            (f"{row['draft']} 合成结果", path / "materials/composite.png"),
        ]
    if not any(rows[0]["draft"] in label for label, _ in images):
        return {"recommended": None, "comparison": {"reason": "没有完整的基线图像，不能判断候选是否改善"}}
    root = repair.directory(store, parent.id)
    output = root / "comparisons" / (uuid.uuid4().hex + ".json")
    output.parent.mkdir(exist_ok=True)
    vote = vision.ask(
        config,
        Preference,
        "比较同一修正的少量候选。第一个是当前设置基线。检查残影、接缝、线条与画风。"
        "只在相对基线有可见改善且没有新增问题时improved=true，preferred必须是eligible里的ID；"
        "无法可靠比较或没有改善则preferred=null、improved=false。不以置信度或候选生成顺序判断。",
        {
            "request": parent.edit.request,
            "cleanup_request": parent.edit.cleanup.prompt,
            "eligible": [r["draft"] for r in eligible],
            "baseline": rows[0]["draft"],
            "checks": [
                {"draft": r["draft"], "items": r.get("visual", {}).get("items", [])} for r in evidence_rows
            ],
        },
        images,
        output,
    )
    for row in rows:
        try:
            repair.snapshot(store, row["draft"], row["version"])
        except (RuntimeError, ValueError, FileNotFoundError):
            row.update(status="stale", reason="比较期间候选已变化，旧检查失效")
    eligible = [row for row in rows if row["status"] == "pass"]
    baseline = rows[0]["draft"]
    if rows[0]["status"] == "stale":
        return {"recommended": None, "comparison": {"reason": "基线候选已编辑，旧比较失效，请查看各候选"}}
    if vote.improved and vote.preferred in {r["draft"] for r in eligible} and vote.preferred != baseline:
        result["recommended"] = vote.preferred
    result["comparison"] = vote.model_dump()
    return result


def search(config, store, identifier, version):
    parent, root = repair.snapshot(store, identifier, version)
    options = variants(parent)
    identity = fingerprint(
        {
            "edit": parent.edit.model_dump(),
            "source": parent.source_hash,
            "materials": parent.material_hash,
            "execution": repair.execution_identity(config),
            "search_code": digest(Path(__file__)),
            "review_code": digest(Path(vision.__file__)),
        }
    )
    report = parent.result.get("search", {})
    if report.get("version") != version or report.get("input") != identity:
        report = {
            "version": version,
            "input": identity,
            "candidates": [],
            "recommended": None,
            "elapsed": 0.0,
            "needs_user_review": True,
        }
    started = time.monotonic()
    total_before = report.get("total_elapsed", report.get("elapsed", 0.0))
    report.update(budget_reached=False, recommended=None)
    report.pop("comparison", None)
    report.pop("comparison_error", None)
    report["budget_policy"] = "每次搜索达到预算后不启动新候选；正在执行的步骤可能超出预算"

    def persist():
        report["elapsed"] = round(time.monotonic() - started, 3)
        report["total_elapsed"] = round(total_before + report["elapsed"], 3)
        write_json(root / "candidate-search.json", report)
        repair.publish(store, parent, search=report)

    for index, (label, edit, key) in enumerate(options):
        repair.snapshot(store, identifier, version)
        row = next((r for r in report["candidates"] if r["key"] == key), None)
        if row:
            try:
                repair.snapshot(store, row["draft"], row["version"])
            except (RuntimeError, ValueError, FileNotFoundError):
                row.update(status="stale", reason="候选已被编辑或应用，旧检查失效，保留人工修改")
                persist()
                continue
        if row and row["status"] in {"pass", "fail", "uncertain", "stale"}:
            continue
        if time.monotonic() - started >= parent.edit.candidate_timeout:
            report.update(stopped="budget", budget_reached=True)
            break
        if row is None:
            child = repair.fork_candidate(config, store, parent, label, edit)
            row = {
                "draft": child.id,
                "version": child.version,
                "key": key,
                "label": label,
                "status": "running",
                "elapsed": 0.0,
                "rule_status": "uncertain",
                "visual_status": "skipped",
            }
            report["candidates"].append(row)
            persist()
        child = repair.load(store, row["draft"])
        if child.version != row["version"] or child.applied_revision:
            row.update(status="stale", reason="候选已被编辑或应用，保留人工修改，不覆盖")
            persist()
            continue
        tick = time.monotonic()
        try:
            if child.result.get("preview", {}).get("version") != child.version:
                child = repair.preview_materials(config, store, child.id, child.version)
            preview = child.result["preview"]
            row.update(
                preview=preview, checks=preview["checks"], rule_status="pass" if preview["ready"] else "fail"
            )
            if not preview["ready"]:
                row.update(status="fail", reason="程序规则未通过，不进入视觉选优")
            else:
                if time.monotonic() - started >= parent.edit.candidate_timeout:
                    row.update(
                        status="paused", reason="预览已保存，预算到时未启动视觉检查；继续搜索可复用预览"
                    )
                    row["elapsed"] = round(row.get("elapsed", 0.0) + time.monotonic() - tick, 3)
                    report.update(budget_reached=True, stopped="budget")
                    persist()
                    break
                if child.result.get("visual", {}).get("version") != child.version:
                    child = repair.inspect(config, store, child.id, child.version)
                visual = child.result["visual"]
                state = visual_status(visual)
                row.update(status=state, visual_status=state, visual=visual, reason=visual.get("summary", ""))
        except Exception as exc:
            row.update(status="error", reason=str(exc))
        row["elapsed"] = round(row.get("elapsed", 0.0) + time.monotonic() - tick, 3)
        persist()
        # A clean baseline needs no extra generation. Manual search remains possible by
        # changing its settings; this is a bounded improvement attempt, not a batch factory.
        if index == 0 and row["status"] == "pass":
            report.update(stopped="baseline_pass", summary="当前设置通过检查，未继续生成；仍需人工验收")
            break
    if len(report["candidates"]) > 1 and not report.get("comparison"):
        if time.monotonic() - started < parent.edit.candidate_timeout:
            try:
                report.update(compare(config, store, parent, report["candidates"]))
                report["summary"] = report["comparison"].get("reason", "候选已保存")
            except Exception as exc:
                report.update(
                    recommended=None, comparison_error=str(exc), summary="候选已保留，视觉比较未完成"
                )
        else:
            report.update(
                recommended=None, budget_reached=True, summary="已到搜索预算，候选已保存，未再启动视觉比较"
            )
    report.setdefault("summary", "候选已保存；推荐仅为视觉模型意见，请查看证据后选择")
    persist()
    return repair.load(store, identifier)
