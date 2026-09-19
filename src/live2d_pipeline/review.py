"""Rules → visual review → bounded repair → user acceptance, with durable evidence."""

import uuid
from PIL import Image, ImageDraw
from . import assets, refinement, vision
from .schema import Refinement
from .store import digest, read_json, write_json
from .validation import validate_bundle


def built_directory(store, revision):
    rev = store.get(revision)
    if rev.status not in {"built", "accepted"}:
        raise ValueError("请先完成模型构建。")
    directory = store.revision_dir(rev.id)
    for stage in rev.stages.values():
        for name, sha in stage.artifacts.items():
            path = directory / name
            if not path.is_file() or digest(path) != sha:
                raise ValueError(f"Artifact changed: {name}")
    return rev, directory


def propose(config, store, request, revision=None):
    rev, directory = built_directory(store, revision)
    manifest = assets.load_parts(directory / "materials")
    output = store.root / "reviews" / rev.id / uuid.uuid4().hex
    output.mkdir(parents=True)
    candidates = [p for p in manifest.parts if p.semantic in refinement.MOVABLE]
    images = [("assembled neutral (for context)", directory / "materials/composite.png")]
    images += [
        (
            f"source layer {p.id}: ORIGINAL size {p.bounds[2]} x {p.bounds[3]} pixels",
            directory / "materials" / p.image,
        )
        for p in candidates
    ]
    result = vision.ask(
        config,
        vision.Proposal,
        "根据用户反馈，提出最多4个实际有帮助的部件细化操作。可拆头发、衣服中的领结/饰品等，"
        "不要增加原图没有的装饰。name用短中文部件名，例如领结，不要重复source的ID。一个 source 最多一个操作。split 是从原图层提取独立部件；"
        "先确定要细化的源部件和动作，不需要给出裁切坐标。bind 仅为已有部件设置运动/可见开关。"
        "binding.root/tip 是提取后部件内的0到1坐标；顶部连接的部件用root=[0.5,0.05],tip=[0.5,0.95]，不要把root放在部件中心；degrees建议2到5。"
        "发夹等硬装饰用fixed；用户要求摆动时必须sway，fixed代表无独立摆动。领结尾部、发梢适合sway。toggle只在用户需要隐藏配件时设置。"
        "不确定的拆分不要执行，写入uncertainties。已有binding的部件只能bind调整，不能再次split。"
        "新部件自动继承来源图层的头部/身体绑定；暂不支持把部件跨坐标系换父级。",
        {"request": request, "parts": [p.model_dump() for p in candidates]},
        images,
        output / "coarse-proposal.json",
    )
    if not result.edits:
        raise ValueError("视觉模型未找到可可靠执行的细化操作：" + result.summary)
    edits = []
    by_id = {part.id: part for part in candidates}
    for index, intent in enumerate(result.edits):
        if intent.source not in by_id:
            raise ValueError("Visual proposal cited an unknown source")
        values = intent.model_dump()
        if intent.action == "split":
            part = by_id[intent.source]
            location = vision.ask(
                config,
                vision.Grounding,
                "Locate the complete requested object in THIS SINGLE source-layer image, including its hanging ends. "
                "Describe what you see. Return box [x_min,y_min,x_max,y_max] and a point inside the object. "
                "Use normalized 0-1000 coordinates relative to THIS image, NOT pixel coordinates. "
                "The point should be in a solid central region of the requested object, not the gap between ribbons.",
                {"object": intent.name, "purpose": intent.reason, "user_request": request},
                [("source layer", directory / "materials" / part.image)],
                output / f"grounding-{index:02}.json",
            )
            values.update(grounding_pixels(location, part.bounds[2:]))
        edits.append(Refinement(**values))
    refinement.validate_edits(manifest, edits)
    write_json(output / "refinements.json", [edit.model_dump() for edit in edits])
    from . import repair
    from .repair_schema import DraftCreate, DraftUpdate, Mark

    drafts = []
    for edit in edits:
        draft = repair.create(config, store, DraftCreate(revision=rev.id, part=edit.source, mode=edit.action))
        draft.edit.name, draft.edit.request, draft.edit.binding = edit.name, edit.reason, edit.binding
        if edit.action == "split":
            draft.edit.mask.box = edit.box
            draft.edit.mask.marks = [Mark(kind="keep", points=[edit.point])]
        draft = repair.update(store, draft.id, DraftUpdate(version=draft.version, edit=draft.edit))
        drafts.append(draft)
    return drafts, result.summary


def view_window(manifest, pose, components):
    part_by_id = {p.id: p for p in manifest.parts}
    component = next((part_by_id[c["part"]] for c in components if c.get("parameter") in pose), None)
    face = next((p for p in manifest.parts if p.semantic == "face"), None)
    if component:
        x, y, w, h = component.bounds
        margin = max(w, h) * 0.4
        window = (x - margin, y - margin, x + w + margin, y + h + margin)
    elif (
        face
        and pose
        and not any(k.startswith(("ParamComponent_", "ParamVisible_", "ParamBody")) for k in pose)
    ):
        x, y, w, h = face.bounds
        window = (x - w * 0.6, y - h * 0.4, x + w * 1.6, y + h * 1.15)
    else:
        window = (0, 0, manifest.width, manifest.height)
    left, top, right, bottom = window
    window = (max(0, left), max(0, top), min(manifest.width, right), min(manifest.height, bottom))
    visible = []
    for part in manifest.parts:
        x, y, w, h = part.bounds
        area = max(0, min(x + w, window[2]) - max(x, window[0])) * max(
            0, min(y + h, window[3]) - max(y, window[1])
        )
        if area >= w * h * 0.05:
            visible.append(part.id)
    return {"parameters": pose, "crop": window, "visible_parts": visible}


def sheets(directory, output):
    poses = read_json(directory / "rig/pose-samples.json")
    manifest = assets.load_parts(directory / "materials")
    component_file = directory / "rig/components.json"
    components = read_json(component_file) if component_file.exists() else []
    frames, views = [], {}
    for start in range(0, len(poses), 9):
        sheet = Image.new("RGB", (900, 990), "#e7e9ef")
        draw = ImageDraw.Draw(sheet)
        for slot, index in enumerate(range(start, min(start + 9, len(poses)))):
            raw = Image.open(directory / "rig" / f"pose-{index:02}.png").convert("RGBA")
            view = view_window(manifest, poses[index], components)
            views[f"pose-{index:02}"] = view
            scale = raw.width / manifest.width
            raw = raw.crop(tuple(round(v * scale) for v in view["crop"]))
            raw.thumbnail((290, 300))
            x, y = slot % 3 * 300, slot // 3 * 330
            sheet.paste(raw, (x + (300 - raw.width) // 2, y + 25), raw)
            draw.text((x + 8, y + 5), f"pose-{index:02}", fill="black")
        path = output / f"sheet-{start // 9:02}.png"
        sheet.save(path)
        frames.append((path, [f"pose-{i:02}" for i in range(start, min(start + 9, len(poses)))]))
    write_json(output / "views.json", views)
    return frames


def inspect(config, store, request="检查穿帮、连接处和新增动作", revision=None):
    rev, directory = built_directory(store, revision)
    output = store.root / "reviews" / rev.id / uuid.uuid4().hex
    output.mkdir(parents=True)
    manifest = assets.load_parts(directory / "materials")
    rules = {
        "materials": assets.quality(directory / "materials"),
        "bundle": validate_bundle(directory / "rig/bundle"),
    }
    components = (
        read_json(directory / "rig/components.json") if (directory / "rig/components.json").exists() else []
    )
    rules["component_geometry"] = components
    write_json(output / "rules.json", rules)
    frames = sheets(directory, output)
    views = read_json(output / "views.json")
    findings, uncertainties, summaries = [], [], []
    # Small batches preserve visual resolution without overflowing the local context.
    for index, (sheet, view_ids) in enumerate(frames):
        review = vision.ask(
            config,
            vision.Review,
            "比较原始合成和带编号的姿态图，定位明显穿帮、拉裂、粘连和不合适的动作。"
            "尽量结合用户的普通语言反馈。只报告有画面证据的问题，正常部分不要逐项报告。静态图不能证明动作流畅。部分图只显示脸，不要在这些图中评判衣服或领结。元数据不是画面证据。"
            "findings.part 必须使用给定素材ID；view 必须使用本批pose编号。"
            "repair=reduce_sway 仅用于有binding且motion=sway的部件，且画面显示独立摆动过大；"
            "其他问题repair=none，并给用户可理解的具体修正建议。不要要求用户填写技术参数。",
            {
                "request": request,
                "parts": manifest.model_dump(),
                "components": components,
                "views": {v: views[v] for v in view_ids},
            },
            [("neutral source composite", directory / "materials/composite.png"), ("pose sheet", sheet)],
            output / f"visual-{index:02}.json",
        )
        by_id = {p.id: p for p in manifest.parts}
        ignored = False
        for finding in review.findings:
            if finding.part not in by_id or finding.view not in view_ids:
                raise ValueError("Visual report cited an unknown part or view")
            if finding.part not in views[finding.view]["visible_parts"]:
                ignored = True
                uncertainties.append(f"忽略模型对 {finding.view} 中不可见部件 {finding.part} 的判断")
                continue
            if finding.repair == "reduce_sway":
                binding = by_id[finding.part].binding
                if not binding or binding.motion != "sway":
                    raise ValueError("Visual report proposed an inapplicable repair")
                parameter = next((c["parameter"] for c in components if c["part"] == finding.part), None)
                if not views[finding.view]["parameters"].get(parameter):
                    finding.repair = "none"
                    uncertainties.append("未在独立摆动样例中观察到该问题，不自动调整：" + finding.part)
            if finding.severity != "info":
                findings.append(finding.model_dump())
        uncertainties += review.uncertainties
        summaries.append(
            "本组有不可见部件的判断，已过滤；保留意见仍需人工复核。" if ignored else review.summary
        )
    report = {
        "revision": rev.id,
        "request": request,
        "rules": rules,
        "summaries": summaries,
        "findings": findings,
        "uncertainties": uncertainties,
        "reviewed_views": [v for _, vs in frames for v in vs],
        "user_acceptance": "pending",
        "runtime_verified": rev.runtime_verified,
        "physics_time_response": "not_evaluated",
        "report_directory": str(output.relative_to(store.root)),
    }
    write_json(output / "report.json", report)
    write_json(store.root / "reviews" / rev.id / "latest.json", report)
    return report


def refine(config, store, request, revision=None):
    """Generate reviewable drafts; the same executor serves automatic and manual repair."""
    from . import repair

    drafts, summary = propose(config, store, request, revision)
    records = []
    for draft in drafts:
        try:
            if draft.edit.mode == "split":
                repair.segment(config, store, draft.id, draft.version)
            if draft.edit.mode == "bind":
                repair.binding_preview(config, store, draft.id, draft.version)
            else:
                repair.preview_materials(config, store, draft.id, draft.version)
            repair.inspect(config, store, draft.id, draft.version)
            records.append({"draft": draft.id, "status": "ready_for_review"})
        except Exception as exc:
            records.append({"draft": draft.id, "status": "needs_input", "error": str(exc)})
    return {"revision": drafts[0].base_revision, "summary": summary, "drafts": records}


def grounding_pixels(location, size):
    width, height = size
    x0, y0, x1, y1 = location.box
    left, top = round(x0 * width / 1000), round(y0 * height / 1000)
    right, bottom = min(width, round(x1 * width / 1000)), min(height, round(y1 * height / 1000))
    return {
        "box": (left, top, right - left, bottom - top),
        "point": (
            min(width - 1, round(location.point[0] * width / 1000)),
            min(height - 1, round(location.point[1] * height / 1000)),
        ),
    }
