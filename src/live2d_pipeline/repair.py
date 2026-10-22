"""Durable, versioned part repairs shared by the GUI and automatic refinement."""

import asyncio
import hashlib
import shutil
import uuid
from pathlib import Path
from PIL import Image, ImageChops, ImageDraw, ImageFilter
from pydantic import Field
from typing import Literal
from . import assets, comfy, vision
from .process import run
from .schema import Binding, Part, Plan, Stage, Strict
from .repair_schema import DraftCreate, DraftUpdate, MaskCheck, RepairDraft, RepairEdit, RepairTarget
from .store import digest, fingerprint, lock, read_json, write_json


def directory(store, identifier):
    if len(identifier) != 32 or any(c not in "0123456789abcdef" for c in identifier):
        raise ValueError("Invalid repair draft ID")
    return store.root / "repairs" / identifier


def load(store, identifier):
    return RepairDraft.model_validate(read_json(directory(store, identifier) / "draft.json"))


def save(store, draft):
    write_json(directory(store, draft.id) / "draft.json", draft.model_dump())


def material_files(root):
    manifest = assets.load_parts(root)
    paths = [root / "parts.json", *[root / p.image for p in manifest.parts]]
    for path in paths:
        if not path.resolve().is_relative_to(root.resolve()) or not path.is_file():
            raise ValueError("素材不存在或路径越界")
    return {str(p.relative_to(root)): digest(p) for p in paths}


def copy_materials(source, target):
    target.mkdir(parents=True, exist_ok=True)
    for name in material_files(source):
        dest = target / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / name, dest)
    assets.assemble(target, target / "composite.png")


def list_drafts(store, revision=None):
    return [
        d.model_dump()
        for p in sorted((store.root / "repairs").glob("*/draft.json"))
        if (d := RepairDraft.model_validate(read_json(p))).base_revision == revision or revision is None
    ]


def create(config, store, spec: RepairTarget, *, image: Path | None = None):
    upload = spec.upload if isinstance(spec, DraftCreate) else None
    if image is not None and upload:
        raise ValueError("Specify either a local image or a web upload")
    image = config.runtime / "web/uploads" / upload if upload else image
    if spec.mode in {"add", "replace"}:
        if image is None or image.suffix.lower() not in {".png", ".jpg", ".jpeg"}:
            raise ValueError("Add/replace requires a PNG or JPEG image")
    elif image is not None:
        raise ValueError("Local images are only used by add/replace")
    rev = store.get(spec.revision)
    base = store.revision_dir(rev.id) / "materials"
    if not (base / "parts.json").is_file():
        raise ValueError("请先完成拆层；素材补全失败时已有图层仍可修正")
    manifest = assets.load_parts(base)
    part = next((p for p in manifest.parts if p.id == spec.part), None)
    if spec.part and part is None:
        raise ValueError("部件不存在")
    if spec.mode in {"bind", "rig"} and rev.status not in {"built", "accepted"}:
        raise ValueError("绑定修正需要已构建的模型")
    identifier = uuid.uuid4().hex
    root = directory(store, identifier)
    root.mkdir(parents=True)
    try:
        copy_materials(base, root / "base")
        source = image if image is not None else base / part.image
        with Image.open(source) as im:
            if im.width * im.height > 24_000_000:
                raise ValueError("单个部件图片不能超过 2400 万像素")
            image = im.convert("RGBA")
        image.save(root / "source.png")
        bounds = (
            part.bounds
            if part
            else (0, 0, min(image.width, manifest.width), min(image.height, manifest.height))
        )
        edit = RepairEdit(
            mode=spec.mode,
            name=part.name if part else "新部件",
            semantic=(part.semantic if part.semantic in assets.SEMANTICS else "objects")
            if part
            else "mouth_open",
            side=part.side if part else "none",
            placement=bounds,
            binding=part.binding if part else None,
        )
        draft = RepairDraft(
            id=identifier,
            base_revision=rev.id,
            part=spec.part,
            source_hash=digest(root / "source.png"),
            material_hash=fingerprint(material_files(base)),
            edit=edit,
        )
        workspace = store.revision_dir(rev.id) / "rig/source.psd2live"
        if workspace.is_file():
            draft.rig_hash = digest(workspace)
        save(store, draft)
        return draft
    except BaseException:
        shutil.rmtree(root)
        raise


def validate_edit(root, edit):
    size = Image.open(root / "source.png").size

    def box_check(box, dimensions):
        x, y, w, h = box
        if min(x, y) < 0 or min(w, h) < 1 or x + w > dimensions[0] or y + h > dimensions[1]:
            raise ValueError("选区超出图片范围")

    if edit.mask.box:
        box_check(edit.mask.box, size)
    for item in edit.mask.checks:
        box_check(item.box, size)
    for mark in [*edit.mask.marks, *edit.cleanup.mask.marks]:
        if any(not (0 <= x < size[0] and 0 <= y < size[1]) for x, y in mark.points):
            raise ValueError("标记超出图片范围")
    manifest = assets.load_parts(root / "base")
    if edit.placement:
        box_check(edit.placement, (manifest.width, manifest.height))
    if edit.semantic not in assets.SEMANTICS:
        raise ValueError("未知部件类别")


def update(store, identifier, spec: DraftUpdate):
    root = directory(store, identifier)
    with lock(root / ".lock"):
        draft = load(store, identifier)
        if draft.version != spec.version or draft.applied_revision:
            raise RuntimeError("草稿已改变或已应用，请重新打开")
        if draft.edit.mode != spec.edit.mode:
            raise ValueError("改变修正类型请创建新草稿")
        validate_edit(root, spec.edit)
        acknowledgement_only = draft.edit.model_dump(exclude={"visual_accepted"}) == spec.edit.model_dump(
            exclude={"visual_accepted"}
        )
        draft.edit, draft.version = spec.edit, draft.version + 1
        if acknowledgement_only:
            for value in draft.result.values():
                if isinstance(value, dict) and value.get("version") == draft.version - 1:
                    value["version"] = draft.version
        else:
            draft.edit.visual_accepted = False
            draft.result = {
                k: v
                for k, v in draft.result.items()
                if k in {"segmentation", "advice", "rig_context", "origin"}
            }
        save(store, draft)
        return draft


def discard(store, identifier, version):
    root = directory(store, identifier)
    with lock(root / ".lock"):
        draft = load(store, identifier)
        if draft.version != version or draft.applied_revision:
            raise RuntimeError("草稿已改变或已应用")
        # Keep published files immutable; the draft is simply no longer editable/listed.
        (root / "draft.json").rename(root / "discarded.json")


def snapshot(store, identifier, version):
    draft = load(store, identifier)
    if draft.version != version or draft.applied_revision:
        raise RuntimeError("任务的草稿版本已过期")
    root = directory(store, identifier)
    if (
        digest(root / "source.png") != draft.source_hash
        or fingerprint(material_files(root / "base")) != draft.material_hash
    ):
        raise ValueError("草稿素材已被外部修改")
    validate_edit(root, draft.edit)
    if draft.rig_hash:
        workspace = store.revision_dir(draft.base_revision) / "rig/source.psd2live"
        if not workspace.is_file() or digest(workspace) != draft.rig_hash:
            raise ValueError("基础绑定工程已改变，请创建新草稿")
    return draft, root


def publish(store, draft, **results):
    root = directory(store, draft.id)
    with lock(root / ".lock"):
        current, _ = snapshot(store, draft.id, draft.version)
        current.result.update(results)
        save(store, current)
        return current


def execution_identity(config):
    code = [
        Path(__file__),
        Path(comfy.__file__),
        config.root / "scripts/split_component.py",
        *sorted((config.root / "workflows").glob("*.json")),
    ]
    return {
        "code": [digest(p) for p in code],
        "config": config.data,
        "models": digest(config.root / "models.lock.json"),
    }


def step(config, root, name, inputs, action):
    identity = {"name": name, "inputs": inputs, **execution_identity(config)}
    target = root / "steps" / fingerprint(identity)
    receipt = target / "complete.json"
    if receipt.exists():
        files = read_json(receipt)
        if all((target / p).is_file() and digest(target / p) == sha for p, sha in files.items()):
            return target
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    action(target)
    write_json(receipt, {str(p.relative_to(target)): digest(p) for p in target.rglob("*") if p.is_file()})
    return target


def segment(config, store, identifier, version):
    draft, root = snapshot(store, identifier, version)
    return publish(store, draft, segmentation=generate_masks(config, draft, root))


def generate_masks(config, draft, root):
    spec = {
        "box": draft.edit.mask.box,
        "points": [
            (*m.points[0], int(m.kind == "keep"))
            for m in draft.edit.mask.marks
            if m.kind in {"keep", "exclude"}
        ],
    }

    def execute(work):
        write_json(work / "selection.json", spec)
        run(
            config.inference_command(
                "comfy",
                [
                    config.root / "scripts/split_component.py",
                    "segment",
                    root / "source.png",
                    work,
                    config.models / "sam2",
                ],
                [root],
            ),
            work / "segment.log",
            timeout=300,
        )

    work = step(config, root, "segment", {"source": draft.source_hash, **spec}, execute)
    rows = read_json(work / "segmentation.json")
    return {"path": str(work.relative_to(root)), "candidates": rows}


def mask_image(root, draft, *, annotations=True):
    source = Image.open(root / "source.png").convert("RGBA")
    selected = draft.edit.mask.candidate
    segmentation = draft.result.get("segmentation")
    if segmentation:
        rows = segmentation["candidates"]
        selected = selected or max(rows, key=lambda r: r["score"])["mask"]
        if selected not in {r["mask"] for r in rows}:
            raise ValueError("候选掩膜已过期，请重新选择")
        mask = Image.open(root / segmentation["path"] / selected).convert("L")
    else:
        mask = Image.new("L", source.size, 0 if draft.edit.mode in {"split", "repaint"} else 255)
    paint_marks(mask, draft.edit.mask.marks if annotations else [])
    return ImageChops.multiply(
        mask.point(lambda p: 255 if p >= 128 else 0), source.getchannel("A").point(lambda p: 255 if p else 0)
    )


def paint_marks(mask, marks):
    draw = ImageDraw.Draw(mask)
    for mark in marks:
        if mark.kind not in {"paint", "erase"}:
            continue
        fill = 255 if mark.kind == "paint" else 0
        points, r = mark.points, mark.radius
        if len(points) > 1:
            draw.line(points, fill=fill, width=max(1, round(r * 2)), joint="curve")
        for x, y in points:
            draw.ellipse((x - r, y - r, x + r, y + r), fill=fill)


def cleanup_mask(root, draft, *, annotations=True):
    mask = mask_image(root, draft)
    if draft.edit.cleanup.expansion:
        mask = mask.filter(ImageFilter.MaxFilter(2 * draft.edit.cleanup.expansion + 1))
    paint_marks(mask, draft.edit.cleanup.mask.marks if annotations else [])
    alpha = Image.open(root / "source.png").convert("RGBA").getchannel("A")
    return ImageChops.multiply(mask, alpha.point(lambda p: 255 if p else 0))


def fork_candidate(config, store, draft, label, edit):
    """Independent editable candidate, retaining the exact source and segmentation evidence."""
    snapshot(store, draft.id, draft.version)
    source = directory(store, draft.id)
    child = draft.model_copy(deep=True)
    child.id, child.version, child.applied_revision = uuid.uuid4().hex, 1, None
    child.edit = edit.model_copy(deep=True)
    child.edit.visual_accepted = False
    child.result = {"origin": {"draft": draft.id, "version": draft.version, "label": label}}
    target = directory(store, child.id)
    target.mkdir(parents=True)
    try:
        copy_materials(source / "base", target / "base")
        shutil.copyfile(source / "source.png", target / "source.png")
        segment = draft.result.get("segmentation")
        if segment:
            path = Path(segment["path"])
            if not (source / path).resolve().is_relative_to(source.resolve()):
                raise ValueError("分割证据路径越界")
            shutil.copytree(source / path, target / path)
            child.result["segmentation"] = segment
        validate_edit(target, child.edit)
        save(store, child)
        return child
    except BaseException:
        shutil.rmtree(target)
        raise


def search(config, store, identifier, version):
    draft, _ = snapshot(store, identifier, version)
    if draft.edit.mode in {"bind", "rig"}:
        from .repair_rig import search as search_rig

        return search_rig(config, store, identifier, version)
    from .repair_candidates import search as search_materials

    return search_materials(config, store, identifier, version)


def check_mask(root, draft, mask):
    checks = []

    def record(name, ok, **evidence):
        checks.append({"name": name, "status": "pass" if ok else "fail", "source": "rule", **evidence})

    record("掩膜包含可见像素", mask.getbbox() is not None)
    for index, mark in enumerate(draft.edit.mask.marks):
        if mark.kind in {"keep", "exclude"}:
            x, y = map(int, mark.points[0])
            record(
                f"{'保留' if mark.kind == 'keep' else '排除'}点 {index + 1}",
                bool(mask.getpixel((x, y))) == (mark.kind == "keep"),
            )
    for check in draft.edit.mask.checks:
        x, y, w, h = check.box
        area = mask.crop((x, y, x + w, y + h))
        coverage = sum(value * count for value, count in enumerate(area.histogram())) / (255 * w * h)
        record(
            check.name,
            coverage >= 0.98 if check.kind == "include" else coverage <= 0.01,
            coverage=coverage,
            box=check.box,
            confirmed=check.confirmed,
        )
        if not check.confirmed:
            checks[-1]["status"] = "uncertain"
            checks[-1]["reason"] = "请在图上核对模型建议的区域并确认"
    return checks


def inference(config, engine, source, work, mode):
    run(
        config.inference_command(
            engine, [config.root / "scripts/split_component.py", mode, source, work], [work]
        ),
        work / (mode + ".log"),
        timeout=300,
    )


def isolated_selection(source, mask):
    """Whole disconnected alpha islands need separation, not hallucinated underpainting."""
    alpha = source.getchannel("A").point(lambda p: 255 if p else 0)
    remaining = mask.copy()
    while remaining.getbbox():
        x0, y0, x1, _ = remaining.getbbox()
        seed = next((x, y0) for x in range(x0, x1) if remaining.getpixel((x, y0)))
        marked = alpha.copy()
        ImageDraw.floodfill(marked, seed, 128)
        island = marked.point(lambda p: 255 if p == 128 else 0)
        total = island.histogram()[255]
        included = ImageChops.multiply(island, mask).histogram()[255]
        if not total or included / total < 0.98:
            return False
        remaining = ImageChops.subtract(remaining, island)
    return True


def preview_materials(config, store, identifier, version, *, retry=False, retry_hidden=False):
    draft, root = snapshot(store, identifier, version)
    if draft.edit.mode == "rig":
        from .repair_rig import preview

        return asyncio.run(preview(config, store, draft, root))
    return publish(
        store, draft, preview=material_candidate(config, draft, root, retry=retry, retry_hidden=retry_hidden)
    )


def underpaint(config, store, identifier, version):
    draft, _ = snapshot(store, identifier, version)
    if draft.edit.mode != "split":
        raise ValueError("遮挡补全用于提取独立部件")
    return preview_materials(config, store, identifier, version, retry_hidden=True)


def material_candidate(config, draft, root, *, retry=False, retry_hidden=False):
    version = draft.version
    mask = mask_image(root, draft)
    constraints = check_mask(root, draft, mask) if draft.edit.mode != "bind" else []
    mask_hash = hashlib.sha256(mask.tobytes()).hexdigest()
    inputs = {
        "source": draft.source_hash,
        "material": draft.material_hash,
        "edit": draft.edit.model_dump(),
        "mask": mask_hash,
    }
    if retry or retry_hidden:
        inputs["retry"] = uuid.uuid4().hex

    def produce(work):
        target = work / "materials"
        copy_materials(root / "base", target)
        manifest = assets.load_parts(target)
        part = next((p for p in manifest.parts if p.id == draft.part), None)
        mask.save(work / "mask.png")
        source = Image.open(root / "source.png").convert("RGBA")
        selected = source.copy()
        selected.putalpha(ImageChops.multiply(source.getchannel("A"), mask))
        selected.save(work / "component.png")
        mode = draft.edit.mode
        if mode == "split":
            if any(c["status"] == "fail" for c in constraints):
                write_json(work / "checks.json", constraints)
                return

            cleaning = cleanup_mask(root, draft)
            cleaning.save(work / "cleanup-mask.png")
            isolated = isolated_selection(source, mask)
            if isolated and ImageChops.subtract(cleaning, mask).getbbox():
                constraints.append(
                    {
                        "name": "透明岛拆分与额外重绘分开执行",
                        "status": "fail",
                        "source": "rule",
                        "reason": "独立透明部件无需补背景；请先拆分，再用局部重绘处理其他可见像素",
                    }
                )
                write_json(work / "checks.json", constraints)
                return
            if ImageChops.subtract(mask, cleaning).getbbox():
                constraints.append({"name": "清理范围覆盖提取范围", "status": "fail", "source": "rule"})
                write_json(work / "checks.json", constraints)
                return

            def paint(hidden):
                cleaning.save(hidden / "mask.png")
                write_json(hidden / "inpaint.json", {"padding": draft.edit.inpaint_padding})
                if isolated:
                    under = source.copy()
                    under.putalpha(ImageChops.multiply(source.getchannel("A"), ImageChops.invert(mask)))
                    under.save(hidden / "underpaint.png")
                elif draft.edit.cleanup.backend == "lama":
                    inference(config, "see-through", root / "source.png", hidden, "inpaint")
                else:
                    prompt = draft.edit.cleanup.prompt.strip() or (
                        f"Remove the extracted {draft.edit.name} completely, including its outline and shadow. "
                        "Reconstruct the underlying surface continuously from the surrounding context. "
                        "Preserve the original illustration style. Do not redraw the removed object. "
                        + draft.edit.request
                    )
                    comfy.image_job(
                        config,
                        prompt,
                        "",
                        hidden / "underpaint.png",
                        hidden / "inpaint.log",
                        image=root / "source.png",
                        mask=hidden / "mask.png",
                        seed=draft.edit.repaint_seed,
                    )
                if not isolated:
                    # The extracted layer already owns the original translucent edge.
                    # Leaving it in the parent would composite that alpha twice. Only
                    # extraction pixels are transferred; expanded cleanup keeps alpha.
                    alpha = source.getchannel("A")
                    transferred = ImageChops.multiply(mask, alpha.point(lambda a: 255 if a < 255 else 0))
                    under = Image.open(hidden / "underpaint.png").convert("RGBA")
                    under.putalpha(ImageChops.multiply(alpha, ImageChops.invert(transferred)))
                    under.save(hidden / "underpaint.png")

            hidden_inputs = {
                "source": draft.source_hash,
                "mask": hashlib.sha256(cleaning.tobytes()).hexdigest(),
                "extraction": mask_hash,
                "padding": draft.edit.inpaint_padding,
                "cleanup": draft.edit.cleanup.model_dump(),
                "seed": draft.edit.repaint_seed,
                "request": draft.edit.request,
                "name": draft.edit.name,
                "isolated": isolated,
            }
            if retry_hidden:
                hidden_inputs["retry"] = inputs["retry"]
            hidden = step(config, root, "underpaint", hidden_inputs, paint)
            shutil.copyfile(hidden / "underpaint.png", target / part.image)
            shutil.copyfile(hidden / "underpaint.png", work / "underpaint.png")
            if not isolated:
                under = Image.open(work / "underpaint.png").convert("RGBA")
                constraints.append(
                    {
                        "name": "底层清理范围之外不变",
                        "source": "rule",
                        "status": "pass" if assets.unchanged_pixels(source, under, cleaning) else "fail",
                    }
                )
            bbox = selected.getchannel("A").getbbox()
            pid = "component_" + draft.id[:12]
            selected.crop(bbox).save(target / (pid + ".png"))
            for other in manifest.parts:
                if other.order > part.order:
                    other.order += 1
            manifest.parts.append(
                Part(
                    id=pid,
                    name=draft.edit.name,
                    semantic=draft.edit.semantic,
                    side=draft.edit.side,
                    order=part.order + 1,
                    bounds=(
                        part.bounds[0] + bbox[0],
                        part.bounds[1] + bbox[1],
                        bbox[2] - bbox[0],
                        bbox[3] - bbox[1],
                    ),
                    image=pid + ".png",
                    derived_from=part.id,
                    binding=draft.edit.binding or Binding(),
                )
            )
            if isolated:
                under = Image.open(target / part.image).convert("RGBA")
                parent_box = under.getchannel("A").getbbox()
                if not parent_box:
                    raise ValueError("该部件已经完整独立，请直接调整绑定或使用掩膜修正")
                under.crop(parent_box).save(target / part.image)
                part.bounds = (
                    part.bounds[0] + parent_box[0],
                    part.bounds[1] + parent_box[1],
                    parent_box[2] - parent_box[0],
                    parent_box[3] - parent_box[1],
                )
            if part.semantic == "earwear":
                for ear in (part, manifest.parts[-1]):
                    if ear.side == "none":
                        ear.side = (
                            "right" if ear.bounds[0] + ear.bounds[2] / 2 < manifest.width / 2 else "left"
                        )
        elif mode == "mask":
            selected.save(target / part.image)
        elif mode in {"replace", "add"}:
            bounds = draft.edit.placement
            if bounds is None:
                raise ValueError("请设置素材的位置和尺寸")
            if mode == "add":
                pid = "part_" + draft.id[:12]
                part = Part(
                    id=pid,
                    name=draft.edit.name,
                    semantic=draft.edit.semantic,
                    bounds=bounds,
                    order=max(p.order for p in manifest.parts) + 1,
                    image=pid + ".png",
                )
                manifest.parts.append(part)
            selected.resize(bounds[2:], Image.Resampling.LANCZOS).save(target / part.image)
            part.bounds = bounds
            part.semantic = draft.edit.semantic
            part.side = draft.edit.side
            part.name, part.binding = draft.edit.name, draft.edit.binding
        elif mode == "repaint":
            if not mask.getbbox():
                raise ValueError("请标记需要重绘的区域")
            comfy.image_job(
                config,
                draft.edit.request + " Preserve composition and silhouette.",
                "",
                work / "painted.png",
                work / "repaint.log",
                image=root / "source.png",
                mask=work / "mask.png",
                seed=draft.edit.repaint_seed,
            )
            with Image.open(work / "painted.png") as painted:
                changed = painted.convert("RGBA")
            changed.save(target / part.image)
            changed.save(work / "component.png")
            constraints.append(
                {
                    "name": "重绘区域之外不变",
                    "source": "rule",
                    "status": "pass" if assets.unchanged_pixels(source, changed, mask) else "fail",
                }
            )
        elif mode == "bind":
            part.binding, part.name = draft.edit.binding or Binding(), draft.edit.name
        assets.save_parts(target, manifest)
        assets.assemble(target, target / "composite.png")
        if mode in {"split", "bind"}:
            before = Image.open(root / "base/composite.png").convert("RGBA")
            after = Image.open(target / "composite.png").convert("RGBA")
            identical = all(c.getbbox() is None for c in ImageChops.difference(before, after).split())
            if mode == "split" and draft.edit.cleanup.allow_visible:
                global_mask = Image.new("L", before.size)
                original = next(p for p in assets.load_parts(root / "base").parts if p.id == draft.part)
                global_mask.paste(cleaning, original.bounds[:2])
                constraints.append(
                    {
                        "name": "可见重绘范围之外合成像素不变",
                        "source": "rule",
                        "status": "pass" if assets.unchanged_pixels(before, after, global_mask) else "fail",
                    }
                )
            else:
                constraints.append(
                    {
                        "name": "默认姿态合成像素不变",
                        "source": "rule",
                        "status": "pass" if identical else "fail",
                        "reason": "扩大清理范围可能改变可见原画；如有需要，请明确启用允许重绘可见区域"
                        if not identical
                        else "",
                    }
                )
        write_json(work / "checks.json", constraints)
        write_json(work / "quality.json", assets.quality(target))

    work = step(config, root, "materials", inputs, produce)
    quality = (
        read_json(work / "quality.json")
        if (work / "quality.json").exists()
        else {"errors": ["请修正掩膜约束"]}
    )
    result = {
        "version": version,
        "path": str(work.relative_to(root)),
        "checks": read_json(work / "checks.json"),
        "quality": quality,
        "ready": not quality.get("errors")
        and all(c["status"] == "pass" for c in read_json(work / "checks.json")),
    }
    return result


class VisualItem(Strict):
    name: str
    status: Literal["pass", "fail", "uncertain"]
    reason: str


class MaterialReview(Strict):
    summary: str
    items: list[VisualItem] = Field(min_length=1, max_length=16)
    regions: list[MaskCheck] = Field(max_length=16)


def material_review_images(root, draft, work):
    """Context and enlarged exposed surfaces; translations are explicitly not rig renders."""
    work.mkdir(parents=True, exist_ok=True)
    source = Image.open(root / "source.png").convert("RGBA")
    mask = mask_image(root, draft)
    selected = source.copy()
    selected.putalpha(ImageChops.multiply(source.getchannel("A"), mask))
    selected.save(work / "selected.png")
    images = [("source: 原始部件图层", root / "source.png"), ("selected: 提取范围", work / "selected.png")]
    preview = draft.result.get("preview", {})
    if preview.get("version") != draft.version:
        return images
    path = root / preview["path"]
    manifest_path = path / "materials/parts.json"
    if not manifest_path.is_file():
        return images
    images.append(("character: 修正后整体角色", path / "materials/composite.png"))
    if draft.edit.mode == "repaint":
        edited = Image.open(path / "component.png").convert("RGBA")
        box = mask.getbbox()
        if box:
            box = (
                max(0, box[0] - 24),
                max(0, box[1] - 24),
                min(source.width, box[2] + 24),
                min(source.height, box[3] + 24),
            )
            board = Image.new("RGBA", ((box[2] - box[0]) * 2, box[3] - box[1]), "#e7e9ef")
            board.alpha_composite(source.crop(box), (0, 0))
            board.alpha_composite(edited.crop(box), (box[2] - box[0], 0))
            board.save(work / "detail.png")
            images.append(("detail: 左原图、右局部重绘；检查线条、画风和接缝", work / "detail.png"))
    hidden = path / "underpaint.png"
    if hidden.is_file():
        images.append(("underpaint: 底层不应残留被提取部件", hidden))
        box = cleanup_mask(root, draft).getbbox()
        if box:
            box = (
                max(0, box[0] - 24),
                max(0, box[1] - 24),
                min(source.width, box[2] + 24),
                min(source.height, box[3] + 24),
            )
            Image.open(hidden).crop(box).save(work / "underpaint-detail.png")
            images.append(
                ("underpaint detail: 底层局部放大；检查残影及重复线条", work / "underpaint-detail.png")
            )
        manifest = assets.load_parts(path / "materials")
        child_id = "component_" + draft.id[:12]
        child = next((p for p in manifest.parts if p.id == child_id), None)
        if child:
            # Recompose at the real layer order, first hidden then shifted. These are
            # material exposure probes, not a claim about deformation/physics quality.
            x, y, w, h = child.bounds
            crop = (
                max(0, x - 32),
                max(0, y - 32),
                min(manifest.width, x + w + 32),
                min(manifest.height, y + h + 32),
            )
            panels = []
            for shift in (None, -max(4, min(24, w // 8)), max(4, min(24, w // 8))):
                canvas = Image.new("RGBA", (manifest.width, manifest.height))
                tags = {p.semantic for p in manifest.parts}
                for part in sorted(manifest.parts, key=lambda p: p.order):
                    if part.semantic == "mouth_open" and "mouth_close" in tags:
                        continue
                    if part.id == child_id and shift is None:
                        continue
                    im = Image.open(path / "materials" / part.image).convert("RGBA")
                    canvas.alpha_composite(
                        im,
                        (
                            part.bounds[0] + (shift or 0) if part.id == child_id else part.bounds[0],
                            part.bounds[1],
                        ),
                    )
                panels.append(canvas.crop(crop))
            board = Image.new("RGBA", (panels[0].width * 3, panels[0].height), "#e7e9ef")
            for index, panel in enumerate(panels):
                board.alpha_composite(panel, (index * panel.width, 0))
            board.save(work / "exposure.png")
            images.append(
                (
                    "exposure: 左隐藏部件、中向左平移、右向右平移；仅检查素材露出，不是绑定或物理验收",
                    work / "exposure.png",
                )
            )
    return images


def inspect(config, store, identifier, version):
    draft, root = snapshot(store, identifier, version)
    if draft.edit.mode in {"rig", "bind"}:
        from .repair_rig import inspect_preview

        return inspect_preview(config, store, identifier, version)
    work = root / "reviews" / uuid.uuid4().hex
    images = material_review_images(root, draft, work)
    result = vision.ask(
        config,
        MaterialReview,
        "逐项核对request及cleanup_request是否完成，不用置信度代替证据。mode=split时检查提取部件完整、"
        "是否混入邻物，例如两条飘带分别检查。mode=repaint时比较原图与重绘结果，核对要求的变化、"
        "画风、接缝和线条；不能把没有修改或原问题仍在判为通过。"
        "regions 只提供原始source图像像素坐标的小型确定应保留/排除区域，box=[x,y,width,height]。"
        "不要用整个轮廓框作为必须100%填满的区域。confirmed必须false；看不清返回uncertain。"
        "如提供underpaint，必须另列检查项检查是否有被提取部件的残影、重复轮廓或不自然纹理；不要只检查selected。",
        {
            "request": draft.edit.request,
            "name": draft.edit.name,
            "source_size": Image.open(root / "source.png").size,
            "mode": draft.edit.mode,
            "cleanup_request": draft.edit.cleanup.prompt,
            "checks": [c.model_dump() for c in draft.edit.mask.checks],
        },
        images,
        work / "visual.json",
    )
    return publish(
        store,
        draft,
        visual={
            "version": version,
            **result.model_dump(),
            "views": [{"label": label, "path": str(path.relative_to(root))} for label, path in images],
        },
    )


def stage_record(path, name, key):
    return Stage(
        state="complete",
        fingerprint=key,
        artifacts={str(p.relative_to(path)): digest(p) for p in (path / name).rglob("*") if p.is_file()},
    )


def apply(config, store, identifier, version, until="rig"):
    from .pipeline import Pipeline

    draft, root = snapshot(store, identifier, version)
    if draft.result.get("visual", {}).get("source") == "agent":
        from .agent_review import verify_review

        verify_review(draft, root)
    preview = draft.result.get("preview", {})
    if preview.get("version") != version or not preview.get("ready"):
        raise ValueError("请先生成并检查当前草稿的预览")
    if draft.edit.mode == "rig":
        from .repair_rig import implementation_hash

        if preview.get("implementation") != implementation_hash(config):
            raise ValueError("绑定修正实现已更新，请重新预览并检查移动点的实际位置")
    visual = draft.result.get("visual", {})
    if (
        visual.get("version") == version
        and any(i["status"] != "pass" for i in visual.get("items", []))
        and not draft.edit.visual_accepted
    ):
        raise ValueError("视觉检查仍有失败或待确认项，请修正后复查，或人工查看证据并确认候选")
    if read_json(store.root / "project.json")["head"] != draft.base_revision:
        raise RuntimeError("创作起点已改变，请切换回草稿基础版本后应用")
    base = store.revision_dir(draft.base_revision) / "materials"
    if fingerprint(material_files(base)) != draft.material_hash:
        raise ValueError("基础版本素材已变化")
    work = root / preview["path"]
    receipt = work / "complete.json"
    if not receipt.is_file() or any(
        not (work / p).is_file() or digest(work / p) != sha for p, sha in read_json(receipt).items()
    ):
        raise ValueError("修正预览文件已变化，请重新预览")
    candidate = draft.result.get("binding_preview")
    if (
        candidate
        and candidate.get("version") == version
        and (
            not candidate.get("ready") or any(c.get("status") == "fail" for c in candidate.get("checks", []))
        )
    ):
        raise ValueError("绑定候选的程序检查未通过，请修正后重新预览")
    built = None
    if until == "rig" and candidate and candidate["version"] == version and candidate["ready"]:
        built = root / candidate["path"]
        receipt = built / "complete.json"
        if not receipt.is_file() or any(
            not (built / p).is_file() or digest(built / p) != sha for p, sha in read_json(receipt).items()
        ):
            raise ValueError("绑定候选文件已变化，请重新预览")
    from .schema import Operation

    operations = [Operation.model_validate(o) for o in preview.get("operations", [])]
    rev = store.create(
        Plan(
            request=draft.edit.request or f"修正 {draft.edit.name}",
            prompt=store.get(draft.base_revision).plan.prompt,
            operations=operations,
        ),
        parent=draft.base_revision,
        checkout=False,
    )
    destination = store.revision_dir(rev.id)
    if draft.edit.mode != "rig":
        copy_materials(work / "materials", destination / "prepared-materials")
        rev.prepared_materials = "prepared-materials"
        store.save(rev)
    write_json(destination / "repair.json", draft.model_dump())
    evidence = destination / "repair-evidence"
    evidence.mkdir()
    for path in work.glob("*.png"):
        shutil.copyfile(path, evidence / path.name)
    write_json(
        evidence / "checks.json", {"rules": preview.get("checks", []), "visual": draft.result.get("visual")}
    )
    # Record the applied revision before a long build so cancellation never loses its identity.
    with lock(root / ".lock"):
        current = load(store, draft.id)
        if current.version != version or current.applied_revision:
            raise RuntimeError("草稿已改变")
        if read_json(store.root / "project.json")["head"] != draft.base_revision:
            raise RuntimeError("创作起点已改变")
        current.applied_revision = rev.id
        save(store, current)
        store.checkout(rev.id)
    if built:
        copy_materials(built / "materials", destination / "materials")
        shutil.copytree(built / "rig", destination / "rig")
        shutil.copyfile(built / "rig-replay.json", destination / "rig-replay.json")
        for name in ("materials", "rig"):
            rev.stages[name] = stage_record(destination, name, fingerprint(candidate))
        rev.status = "built"
        store.save(rev)
    else:
        Pipeline(config, store).run(rev, until=until)
    return rev


def binding_preview(config, store, identifier, version):
    """Build a disposable candidate, then promote exactly these checked artifacts on apply."""
    from . import rig
    from .repair_rig import history_operations, implementation_hash, safe_replay
    from .validation import validate_bundle

    draft, root = snapshot(store, identifier, version)
    if draft.edit.mode == "rig":
        return preview_materials(config, store, identifier, version)
    preview = draft.result.get("preview")
    if not preview or preview.get("version") != version:
        draft = preview_materials(config, store, identifier, version)
        preview = draft.result["preview"]
    if not preview["ready"] or preview["quality"].get("missing_parts"):
        raise ValueError("请先修正掩膜或补齐必要素材，再预览绑定")
    material = root / preview["path"] / "materials"
    parent = store.get(draft.base_revision)
    old_ops = history_operations(store, parent)
    operations, stale = safe_replay(store, parent, material, old_ops)

    def build(work):
        copy_materials(material, work / "materials")
        output = work / "rig"
        output.mkdir()
        assets.write_psd(work / "materials", output / "source.psd")
        asyncio.run(rig.build(config, output / "source.psd", output, operations))
        write_json(output / "validation.json", validate_bundle(output / "bundle"))
        write_json(work / "rig-replay.json", {"needs_review": stale})
        previous = store.revision_dir(parent.id) / "rig"
        if (previous / "pose-samples.json").is_file():
            prior_poses = read_json(previous / "pose-samples.json")
            for index, pose in enumerate(read_json(output / "pose-samples.json")):
                if pose in prior_poses:
                    shutil.copyfile(
                        previous / f"pose-{prior_poses.index(pose):02}.png", work / f"before-{index:02}.png"
                    )
                elif any(k.startswith("ParamComponent_") for k in pose):
                    shutil.copyfile(previous / "pose-00.png", work / f"before-{index:02}.png")

    work = step(
        config,
        root,
        "binding",
        {
            "materials": material_files(material),
            "operations": [o.model_dump() for o in operations],
            "rig_code": [
                digest(config.root / "src/live2d_pipeline" / p)
                for p in ("rig.py", "refinement.py", "repair_rig.py")
            ],
            "rig_repair_implementation": implementation_hash(config),
        },
        build,
    )
    poses = read_json(work / "rig/pose-samples.json")
    views = []
    for index, pose in enumerate(poses):
        if (
            index == 0
            or any(k.startswith("ParamComponent_") for k in pose)
            or pose
            in ({"ParamAngleX": -45.0}, {"ParamAngleX": 45.0}, {"ParamAngleY": -30.0}, {"ParamAngleY": 30.0})
        ):
            image = work / "rig" / f"pose-{index:02}.png"
            before = work / f"before-{index:02}.png"
            if before.exists():
                views.append(
                    {
                        "path": str(before.relative_to(root)),
                        "label": "绑定前 · " + str(pose or "默认姿态"),
                        "pose": pose,
                    }
                )
            views.append(
                {
                    "path": str(image.relative_to(root)),
                    "label": "绑定预览 · " + str(pose or "默认姿态"),
                    "pose": pose,
                }
            )
    return publish(
        store,
        draft,
        binding_preview={
            "version": version,
            "path": str(work.relative_to(root)),
            "views": views,
            "ready": True,
            "needs_review": stale,
        },
    )
