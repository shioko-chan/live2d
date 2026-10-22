"""Bounded pose repairs using the pinned PSD2Live authoring interfaces."""

import asyncio
import base64
import hashlib
import shutil
import time
import uuid
from pathlib import Path
from typing import Literal
from PIL import Image, ImageDraw
from pydantic import Field
from . import assets, repair, rig, vision
from .schema import Binding, Operation, Strict
from .repair_schema import RepairEdit, RigRepair
from .store import digest, fingerprint, read_json, write_json


def implementation_hash(config):
    return fingerprint(
        {
            "repair_rig": digest(Path(__file__)),
            "rig": digest(config.root / "src/live2d_pipeline/rig.py"),
            "canvas_mapping": digest(config.root / "integrations/psd2live/CanvasRepair.kt"),
            "preview_visibility": digest(config.root / "integrations/psd2live/preview-visibility.patch"),
        }
    )


def part_meshes(part, objects):
    token = str(int.from_bytes(hashlib.sha256(part.encode()).digest()[:4], "big") & 0x7FFFFFFF)
    return [
        o
        for o in objects
        if o["kind"] == "mesh"
        and (o.get("layerId") == "lyid:" + token or o.get("layerId", "").startswith("lyid:" + token + ":"))
    ]


def history_operations(store, parent):
    """Fold the effective rig history, honoring earlier material rebuild exclusions.

    A plan records intent, while rig-replay.json records historical operations
    omitted from the actual candidate. Once omitted, an operation must not come
    back just because a later material snapshot happens to be unchanged.
    """
    chain, seen = [], set()
    while parent:
        if parent.id in seen:
            raise ValueError("Revision ancestry contains a cycle")
        seen.add(parent.id)
        chain.append(parent)
        if parent.source or any(op.kind == "regenerate" for op in parent.plan.operations):
            break
        parent = store.get(parent.parent) if parent.parent else None
    effective = []
    for revision in reversed(chain):
        current = [op for op in revision.plan.operations if op.kind == "rig"]
        receipt = store.revision_dir(revision.id) / "rig-replay.json"
        if receipt.exists():
            record = read_json(receipt)
            rejected = record.get("needs_review") if isinstance(record, dict) else None
            if not isinstance(rejected, list):
                raise ValueError(f"Invalid rig replay receipt for revision {revision.id}")
            for row in rejected:
                if not isinstance(row, dict) or not isinstance(row.get("operation"), dict):
                    raise ValueError(f"Invalid rig replay operation for revision {revision.id}")
                operation = Operation.model_validate(row["operation"])
                if operation.kind != "rig":
                    raise ValueError(f"Invalid rig replay operation for revision {revision.id}")
                if operation in effective:
                    # Remove one occurrence per receipt row, retaining the order
                    # and multiplicity of authored transforms.
                    effective.remove(operation)
                elif operation in current:
                    # Older executors filtered ancestor and current operations
                    # together. Their receipts may also omit an own-plan operation.
                    current.remove(operation)
        # A freshly authored replacement, even with identical JSON, is allowed
        # after removal of an old occurrence from the inherited history.
        effective.extend(current)
    return effective


def safe_replay(store, parent, materials, operations):
    """Replay intact dependency groups only in unchanged material coordinate frames.

    Creation and structure edits must travel with their consumers. Filtering just
    by a keyform's target can otherwise replay geometry without its parameter or
    parent Warp, or leave a physics output with its authored shape removed.
    """
    original = store.revision_dir(parent.id) / "materials"
    before, after = assets.load_parts(original), assets.load_parts(materials)
    after_parts = {p.id: p for p in after.parts}
    # New layers have no old ancestry to inspect and may resize a generated
    # semantic parent frame. Do not guess which old coordinates remain valid.
    added_parts = set(after_parts) - {part.id for part in before.parts}
    context = store.revision_dir(parent.id) / "rig/rig-context.json"
    objects = read_json(context)["objects"] if context.exists() else []
    by_id = {o["id"]: o for o in objects}
    affected = {}
    for part in before.parts:
        other = after_parts.get(part.id)
        unchanged = bool(
            other
            and part.bounds == other.bounds
            and part.semantic == other.semantic
            and part.side == other.side
            and part.derived_from == other.derived_from
            and part.order == other.order
            and part.binding == other.binding
            and digest(original / part.image) == digest(materials / other.image)
        )
        for mesh in part_meshes(part.id, objects):
            node, seen = mesh, set()
            while node and node["id"] not in seen:
                seen.add(node["id"])
                affected.setdefault(node["id"], []).append(unchanged)
                node = by_id.get(node.get("parentId"))

    def lineage(target):
        result, node, seen = [], by_id.get(target), set()
        while node and node["id"] not in seen:
            seen.add(node["id"])
            result.append(node["id"])
            node = by_id.get(node.get("parentId"))
        return result

    def unchanged(target):
        ancestors = lineage(target)
        return (
            bool(ancestors)
            and not added_parts
            and all(all(affected.get(identifier, [False])) for identifier in ancestors)
            and (before.width, before.height) == (after.width, after.height)
        )

    refs, creators, structural, valid = [], {}, {}, []
    for index, operation in enumerate(operations):
        args, tool = operation.arguments, operation.tool
        objects = {args.get("target", {}).get("id")} - {None}
        parameters = set(args.get("coordinate", {}))
        if tool == "parameter_create":
            creators[("parameter", args.get("parameter_id"))] = index
        elif tool == "parameter_update":
            parameters.add(args.get("parameter_id"))
        elif tool == "warp_create":
            objects.update(args.get("mesh_ids", []))
            objects.update({args.get("id"), args.get("parent_id")} - {None})
            creators[("object", args.get("id"))] = index
            # Creating a warp also changes each attached mesh's coordinate parent.
            for identifier in objects:
                structural.setdefault(identifier, []).append(index)
        elif tool == "object_edit":
            for edit in args.get("edits", []):
                objects.update({edit.get("id"), edit.get("parent_id"), edit.get("before_id")} - {None})
                if edit.get("action") in {"move", "bind"}:
                    structural.setdefault(edit.get("id"), []).append(index)
        elif tool == "physics_put":
            parameters.update({args.get("input_parameter"), args.get("output_parameter")} - {None})
        expanded = {ancestor for identifier in objects for ancestor in lineage(identifier)} | objects
        refs.append(
            {
                *(("object", identifier) for identifier in expanded),
                *(("parameter", identifier) for identifier in parameters if identifier),
            }
        )
        valid.append(
            all(unchanged(identifier) for identifier in objects)
            if objects
            else tool in {"parameter_create", "parameter_update", "physics_put"}
        )

    # Union dependent operations without changing their original execution order.
    groups = list(range(len(operations)))

    def group(index):
        while groups[index] != index:
            groups[index] = groups[groups[index]]
            index = groups[index]
        return index

    for index, references in enumerate(refs):
        for reference in references:
            dependencies = []
            if reference in creators:
                dependencies.append(creators[reference])
            if reference[0] == "object":
                dependencies.extend(structural.get(reference[1], []))
            for dependency in dependencies:
                groups[group(index)] = group(dependency)
    rejected = {group(index) for index, is_valid in enumerate(valid) if not is_valid}
    safe, stale = [], []
    for index, operation in enumerate(operations):
        if group(index) not in rejected:
            safe.append(operation)
        else:
            stale.append(
                {
                    "operation": operation.model_dump(),
                    "reason": "素材、绑定或依赖操作的前提已变化，整组需要重新预览",
                    "dependency_group": [i for i in range(len(operations)) if group(i) == group(index)],
                }
            )
    return safe, stale


async def render(session, pose, output):
    state = await session.call("project_get_state")
    args = {
        "parameters": pose,
        "viewport": {
            "mode": "canvas_rect",
            "left": 0,
            "top": 0,
            "width": state["canvasWidth"],
            "height": state["canvasHeight"],
        },
        "background": "transparent",
        "target_long_edge": 1024,
    }
    result = await session.session.call_tool("view_render_model", args)
    metadata = rig.content(result)
    image = next(b for b in result.content if b.type == "image")
    output.write_bytes(base64.b64decode(image.data))
    return metadata


async def points(session, target, pose):
    result, offset = [], 0
    while True:
        page = await session.call(
            "rig_inspect",
            {
                "target": target,
                "coordinate": pose,
                "detail": "points",
                "space": "canvas",
                "offset": offset,
                "limit": 256,
            },
        )
        result.extend(page["points"])
        offset = page.get("nextOffset")
        if offset is None:
            return result


async def canvas_region(session, targets, pose):
    coordinates = []
    for target in targets:
        coordinates.extend(await points(session, {"kind": target["kind"], "id": target["id"]}, pose))
    if not coordinates:
        return None
    xs, ys = zip(*[p[1:] for p in coordinates])
    return [min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)]


def workspace(store, draft, root):
    base = store.revision_dir(draft.base_revision) / "rig"
    if not (base / "source.psd2live").is_file():
        raise ValueError("请先完成模型构建")
    work = root / "rig-attempts" / uuid.uuid4().hex
    work.mkdir(parents=True)
    shutil.copyfile(base / "source.psd2live", work / "source.psd2live")
    return base, work


async def context_async(config, store, draft, root):
    base, work = workspace(store, draft, root)
    async with rig.open_rig(
        config, base / "source.psd", work / "source.psd2live", work / "control", work / "rig.log"
    ) as (session, _):
        context = await session.context()
        choices = part_meshes(draft.part, context["objects"])
        identifiers = {o["id"] for o in choices}
        for obj in context["objects"]:
            if (
                obj["kind"] == "warp"
                and obj["id"].startswith("DeformComponent_")
                and any(m.get("parentId") == obj["id"] for m in choices)
            ):
                identifiers.add(obj["id"])
        choices = [o for o in context["objects"] if o["id"] in identifiers]
        if not choices:
            raise ValueError("没有找到这个部件的绑定对象")
        chosen = next((o for o in choices if o["id"] == draft.edit.rig.target), choices[0])
        target = {"kind": chosen["kind"], "id": chosen["id"]}
        pose = draft.edit.rig.pose
        metadata = await render(session, pose, work / "pose.png")
        info = await session.call("rig_inspect", {"target": target, "coordinate": pose})
        regions = {}
        for part in assets.load_parts(root / "base").parts:
            coordinates = []
            for mesh in part_meshes(part.id, context["objects"]):
                coordinates.extend(await points(session, {"kind": "mesh", "id": mesh["id"]}, pose))
            if coordinates:
                xs, ys = zip(*[p[1:] for p in coordinates])
                regions[part.id] = [min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)]
        result = {
            "version": draft.version,
            "target": target,
            "objects": choices,
            "parameters": context["parameters"]["parameters"],
            "geometry": info,
            "points": await points(session, target, pose),
            "part_regions": regions,
            "view": metadata,
            "pose": pose,
            "image": str((work / "pose.png").relative_to(root)),
        }
    return repair.publish(store, draft, rig_context=result)


def rig_context(config, store, identifier, version):
    draft, root = repair.snapshot(store, identifier, version)
    return asyncio.run(context_async(config, store, draft, root))


async def map_drags(control, process, target, pose, drags):
    done = control / "map.done.json"
    done.unlink(missing_ok=True)
    write_json(
        control / "map.request.json",
        {"target": target, "pose": pose, "drags": [d.model_dump() for d in drags]},
    )
    deadline = time.monotonic() + 90
    while not done.exists():
        if process.poll() is not None or time.monotonic() > deadline:
            raise RuntimeError("姿态坐标转换失败或超时")
        await asyncio.sleep(0.1)
    result = read_json(done)
    if not result.get("ok"):
        raise ValueError(result.get("error", "局部坐标不可逆"))
    return result["result"]


def poses_for_repair(pose, params):
    result = [{}, pose]
    for key in pose:
        p = params[key]
        for value in (p["min"], (p["min"] + p["default"]) / 2, (p["max"] + p["default"]) / 2, p["max"]):
            result.append({**pose, key: value})
    return list({fingerprint(p): p for p in result}.values())


def landmark_selection(domain, drags, radius):
    """Keep every dragged landmark inside the fully weighted part of the selection.

    Upstream accepts finite normalized rectangles outside [0, 1]. Clamping the
    rectangle to a mesh edge puts an edge landmark at zero feather weight.
    """
    xs, ys = zip(*[p for p, drag in zip(domain, drags) if not drag.fixed])
    return {
        "rect": [min(xs) - radius, min(ys) - radius, max(xs) + radius, max(ys) + radius],
        "feather": min(0.15, radius / 2),
    }


def arrival_check(goals, actual):
    rows = []
    for index, goal in goals.items():
        position = actual.get(index)
        error = sum((a - b) ** 2 for a, b in zip(goal, position)) ** 0.5 if position is not None else None
        rows.append({"point": index, "expected": goal, "actual": position, "pixels": error})
    good = bool(rows) and all(row["pixels"] is not None and row["pixels"] <= 0.5 for row in rows)
    return {
        "name": "问题姿态移动点到达误差 ≤ 0.5 像素",
        "source": "rule",
        "status": "pass" if good else "fail",
        "pixels": max((r["pixels"] for r in rows if r["pixels"] is not None), default=None),
        "points": rows,
    }


def bounded_candidates(edit):
    """Vary only an existing finite edit; never invent a parent, point, or target."""
    original = edit.model_copy(deep=True)
    original.visual_accepted = False
    result = [("原修正方案", original)]
    for scale, label in ((0.75, "动作幅度 75%"), (0.5, "动作幅度 50%")):
        candidate = edit.model_copy(deep=True)
        if edit.mode == "bind" and edit.binding and edit.binding.motion == "sway":
            values = edit.binding.model_dump()
            values["degrees"] *= scale
            if scale == 0.5:
                values["root_pin"] = min(0.95, values["root_pin"] + 0.1)
                label += " · 扩大根部固定范围"
            candidate.binding = Binding.model_validate(values)
        elif edit.mode == "rig" and edit.rig.action == "landmarks":
            if not any(not d.fixed and d.start != d.end for d in edit.rig.drags):
                break
            for drag in candidate.rig.drags:
                if not drag.fixed:
                    drag.end = tuple(a + (b - a) * scale for a, b in zip(drag.start, drag.end))
        elif edit.mode == "rig" and edit.rig.action == "smooth":
            candidate.rig.strength *= scale
            label = f"平滑强度 {round(scale * 100)}%"
        else:
            break
        candidate.visual_accepted = False
        result.append((label, RepairEdit.model_validate(candidate.model_dump())))
    return result[:3]


def hard_checks_pass(preview):
    checks = [c for c in preview.get("checks", []) if c.get("source") == "rule"]
    return bool(preview.get("ready") and checks and all(c["status"] == "pass" for c in checks))


def geometry_targets(meshes, objects):
    """Inspect parent warps too: upstream mesh diagnostics deliberately exclude them."""
    by_id = {o["id"]: o for o in objects}
    selected = {}
    for mesh in meshes:
        node, seen = mesh, set()
        while node and node["id"] not in seen:
            seen.add(node["id"])
            if node["kind"] in {"mesh", "warp"}:
                selected[node["id"]] = node
            node = by_id.get(node.get("parentId"))
    return list(selected.values())


async def binding_geometry(config, store, draft, root):
    """Compare the actual two rigs at matching poses, including new motion endpoints."""
    candidate = draft.result["binding_preview"]
    source = root / candidate["path"] / "rig"
    base = store.revision_dir(draft.base_revision) / "rig"
    work = root / "rig-attempts" / uuid.uuid4().hex
    work.mkdir(parents=True)
    records, views, mesh_ids = {}, [], {}
    # Candidate parameters define samples; the original may not have the new independent axis.
    for name, original in (("after", source), ("before", base)):
        copied = work / f"{name}.psd2live"
        shutil.copyfile(original / "source.psd2live", copied)
        async with rig.open_rig(
            config, original / "source.psd", copied, work / name, work / f"{name}.log"
        ) as (session, _):
            context = await session.context()
            params = {p["id"]: p for p in context["parameters"]["parameters"]}
            meshes = part_meshes(draft.part, context["objects"])
            if not meshes:
                raise ValueError("绑定检查找不到部件网格")
            mesh_ids[name] = {m["id"] for m in meshes}
            targets = geometry_targets(meshes, context["objects"])
            if name == "after":
                pose = dict(draft.edit.rig.pose)
                if any(
                    k not in params or not params[k]["min"] <= v <= params[k]["max"] for k, v in pose.items()
                ):
                    raise ValueError("问题姿态参数越界")
                if not pose and "ParamAngleX" in params:
                    pose = {"ParamAngleX": params["ParamAngleX"]["default"]}
                samples = poses_for_repair(pose, params)
                component = "ParamComponent_" + hashlib.sha256(draft.part.encode()).hexdigest()[:12]
                if component in params:
                    samples.extend(
                        poses_for_repair({**pose, component: params[component]["default"]}, params)
                    )
                samples = list({fingerprint(p): p for p in samples}.values())
            records[name] = []
            for index, pose in enumerate(samples):
                effective = {k: v for k, v in pose.items() if k in params}
                diagnostics = {}
                for obj in targets:
                    inspected = await session.call(
                        "rig_inspect",
                        {"target": {"kind": obj["kind"], "id": obj["id"]}, "coordinate": effective},
                    )
                    diagnostics[obj["id"]] = inspected["diagnostics"]
                records[name].append(diagnostics)
                path = work / f"{name}-{index:02}.png"
                await render(session, effective, path)
                views.append(
                    {
                        "path": str(path.relative_to(root)),
                        "label": f"{'修正前' if name == 'before' else '修正后'} · 姿态 {index}",
                        "pose": pose,
                        "effective_pose": effective,
                        "part_region": await canvas_region(session, meshes, effective),
                    }
                )
    checks = []
    for index, pose in enumerate(samples):
        before, after = records["before"][index], records["after"][index]
        added = after.keys() - before.keys()
        good = (
            mesh_ids["before"] == mesh_ids["after"]
            and all(key.startswith("DeformComponent_") for key in added)
            and all(
                after[key][metric] <= before.get(key, {}).get(metric, 0)
                for key in after
                for metric in ("flippedTriangleCount", "collapsedTriangleCount")
            )
        )
        checks.append(
            {
                "name": f"姿态 {index} 无新增翻折或塌缩",
                "source": "rule",
                "status": "pass" if good else "fail",
                "pose": pose,
                "before": before,
                "after": after,
            }
        )
    result = {
        **candidate,
        "checks": checks,
        "views": views,
        "ready": all(c["status"] == "pass" for c in checks),
    }
    write_json(work / "geometry.json", result)
    return repair.publish(store, draft, binding_preview=result)


async def preview(config, store, draft, root):
    edit = draft.edit.rig
    base, work = workspace(store, draft, root)
    control = work / "control"
    async with rig.open_rig(
        config, base / "source.psd", work / "source.psd2live", control, work / "rig.log"
    ) as (session, process):
        context = await session.context()
        meshes = part_meshes(draft.part, context["objects"])
        allowed = {o["id"]: o for o in meshes}
        for obj in context["objects"]:
            if (
                obj["kind"] == "warp"
                and obj["id"].startswith("DeformComponent_")
                and any(m.get("parentId") == obj["id"] for m in meshes)
            ):
                allowed[obj["id"]] = obj
        chosen = allowed.get(edit.target) if edit.target else next(iter(allowed.values()), None)
        if not chosen:
            raise ValueError("绑定对象不属于所选部件")
        target = {"kind": chosen["kind"], "id": chosen["id"]}
        params = {p["id"]: p for p in context["parameters"]["parameters"]}
        pose = dict(edit.pose)
        if not pose:
            pose = {"ParamAngleX": params["ParamAngleX"]["default"]}
        if any(k not in params or not params[k]["min"] <= v <= params[k]["max"] for k, v in pose.items()):
            raise ValueError("姿态参数越界")
        info = await session.call("rig_inspect", {"target": target, "coordinate": pose})
        for axis in info["axes"]:
            pose.setdefault(axis["id"], params[axis["id"]]["default"])
        pins, goals = [], {}
        if edit.action == "opacity":
            operation = Operation(
                kind="rig",
                tool="keyform_set",
                arguments={"target": target, "coordinate": pose, "channels": {"opacity": edit.opacity}},
            )
        else:
            selection = None
            if edit.action == "smooth":
                if target["kind"] != "warp":
                    raise ValueError("局部平滑请选择独立部件变形器")
                transform = {"type": "smooth", "strength": edit.strength}
                if edit.region is None:
                    raise ValueError("请先圈出需要平滑的区域")
                from .repair_schema import Drag

                x, y, width, height = edit.region
                corners = [Drag(start=p, end=p, fixed=True) for p in ((x, y), (x + width, y + height))]
                mapped = await map_drags(control, process, target, pose, corners)
                xs, ys = zip(*mapped["domain"])
                selection = {"rect": [min(xs), min(ys), max(xs), max(ys)], "feather": 0.15}
            else:
                if not edit.drags or all(d.fixed for d in edit.drags):
                    raise ValueError("请拖动至少一个移动点；固定点用于保持连接处")
                mapped = await map_drags(control, process, target, pose, edit.drags)
                transform = {"type": "landmarks", "from": mapped["from"], "to": mapped["to"]}
                pins = mapped["pins"]
                goals = {
                    i: [a + end - start for a, start, end in zip(position, drag.start, drag.end)]
                    for i, position, drag in zip(mapped["indices"], mapped["canvas"], edit.drags)
                    if not drag.fixed
                }
                selection = landmark_selection(mapped["domain"], edit.drags, edit.radius)
            args = {"target": target, "coordinate": pose, "operations": [transform]}
            if selection:
                args["selection"] = selection
            await session.call("rig_preview", args)
            operation = Operation(kind="rig", tool="rig_transform", arguments=args)
        samples = poses_for_repair(pose, params)
        views, baseline, pin_baselines = [], [], []
        for index, sample in enumerate(samples):
            before = work / f"before-{index:02}.png"
            await render(session, sample, before)
            before_info = await session.call("rig_inspect", {"target": target, "coordinate": sample})
            baseline.append(before_info["diagnostics"])
            pin_baselines.append({p[0]: p[1:] for p in await points(session, target, sample)} if pins else {})
            views.append(
                {
                    "path": str(before.relative_to(root)),
                    "label": f"修正前 · 姿态 {index}",
                    "pose": sample,
                    "part_region": await canvas_region(session, meshes, sample),
                }
            )
        await session.apply([operation])
        checks = []
        for index, sample in enumerate(samples):
            after = work / f"after-{index:02}.png"
            await render(session, sample, after)
            current = (await session.call("rig_inspect", {"target": target, "coordinate": sample}))[
                "diagnostics"
            ]
            good = all(
                current[k] <= baseline[index][k] for k in ("flippedTriangleCount", "collapsedTriangleCount")
            )
            checks.append(
                {
                    "name": f"姿态 {index} 无新增翻折或塌缩",
                    "source": "rule",
                    "status": "pass" if good else "fail",
                    "before": baseline[index],
                    "after": current,
                }
            )
            if pins or (goals and sample == pose):
                after_points = {p[0]: p[1:] for p in await points(session, target, sample)}
            if goals and sample == pose:
                checks.append(arrival_check(goals, after_points))
            if pins:
                drift = max(
                    sum((a - b) ** 2 for a, b in zip(pin_baselines[index][i], after_points[i])) ** 0.5
                    for i in pins
                )
                checks.append(
                    {
                        "name": f"姿态 {index} 固定点漂移 ≤ 0.5 像素",
                        "source": "rule",
                        "status": "pass" if drift <= 0.5 else "fail",
                        "pixels": drift,
                    }
                )
            views.append(
                {
                    "path": str(after.relative_to(root)),
                    "label": f"修正后 · 姿态 {index}",
                    "pose": sample,
                    "part_region": await canvas_region(session, meshes, sample),
                }
            )
        result = {
            "version": draft.version,
            "implementation": implementation_hash(config),
            "path": str(work.relative_to(root)),
            "operations": [operation.model_dump()],
            "views": views,
            "checks": checks,
            "ready": all(c["status"] == "pass" for c in checks),
            "quality": {},
            "geometry_evidence": info,
        }
        write_json(work / "preview.json", result)
    write_json(
        work / "complete.json",
        {str(p.relative_to(work)): digest(p) for p in work.glob("*.png")}
        | {"preview.json": digest(work / "preview.json")},
    )
    return repair.publish(store, draft, preview=result)


class Suggestion(Strict):
    summary: str
    candidates: list[str] = Field(max_length=4)
    action: Literal["binding", "landmarks", "opacity", "smooth", "materials", "uncertain"]
    binding: Binding | None
    rig: RigRepair | None
    reason: str


def suggest(config, store, identifier, version):
    draft, root = repair.snapshot(store, identifier, version)
    if draft.edit.mode not in {"rig", "bind"}:
        return repair.inspect(config, store, identifier, version)
    context_async_result = asyncio.run(context_async(config, store, draft, root))
    context = context_async_result.result["rig_context"]
    output = root / "reviews" / uuid.uuid4().hex
    output.mkdir(parents=True)
    manifest = assets.load_parts(root / "base")
    images = [("current pose: 整体", root / context["image"])]
    region = draft.edit.rig.region or context.get("part_regions", {}).get(draft.part)
    if region:
        with Image.open(root / context["image"]) as source:
            sx, sy = source.width / manifest.width, source.height / manifest.height
            x, y, width, height = region
            margin = max(12, min(width, height) * 0.25)
            box = (
                max(0, int((x - margin) * sx)),
                max(0, int((y - margin) * sy)),
                min(source.width, int((x + width + margin) * sx)),
                min(source.height, int((y + height + margin) * sy)),
            )
            if box[2] > box[0] and box[3] > box[1]:
                source.crop(box).save(output / "pose-detail.png")
                images.append((f"current pose: 局部，原画布范围 {region}", output / "pose-detail.png"))
    result = vision.ask(
        config,
        Suggestion,
        "根据反馈定位问题并提出一个有限修正。候选部件只能引用给定ID。当前图像为指定姿态。"
        "连接点/摆幅问题用binding；闭眼残留用opacity；局部拉裂用landmarks，start/end使用完整画布坐标，"
        "fixed点的start=end。目标对象只能选择当前部件的objects。需要补画用materials，不能可靠判断用uncertain。"
        "不要跨父级重绑。不得修改用户没有要求的外观。",
        {
            "request": draft.edit.request,
            "part": draft.part,
            "parts": [p.model_dump() for p in manifest.parts],
            "context": {k: v for k, v in context.items() if k not in {"points", "view"}},
            "canvas": context["view"].get("canvasRect"),
            "user_marks": draft.edit.rig.model_dump(),
        },
        images,
        output / "suggestion.json",
    )
    if not set(result.candidates) <= {p.id for p in manifest.parts}:
        raise ValueError("视觉模型引用了不存在的部件，请重试或手动选择")
    advice = {"version": version, **result.model_dump()}
    repair.publish(store, draft, advice=advice)
    if result.candidates != [draft.part] or result.action not in {
        "binding",
        "landmarks",
        "opacity",
        "smooth",
    }:
        return repair.load(store, identifier)
    mode = "bind" if result.action == "binding" else "rig"
    if (mode == "bind" and result.binding is None) or (mode == "rig" and result.rig is None):
        return repair.load(store, identifier)
    edit = draft.edit.model_copy(deep=True)
    edit.mode = mode
    if mode == "bind":
        edit.binding = result.binding
    else:
        edit.rig = result.rig.model_copy(update={"action": result.action, "pose": draft.edit.rig.pose})
        if draft.edit.rig.drags:
            edit.rig.drags = draft.edit.rig.drags
    return search(config, store, identifier, version, edit=edit)


def comparison_images(draft, root, preview, output):
    """Keep the whole silhouette and magnify the actual marked part for each pose pair."""
    manifest = assets.load_parts(root / "base")
    part = next(p for p in manifest.parts if p.id == draft.part)
    grouped = {}
    for row in preview["views"]:
        grouped.setdefault(fingerprint(row["pose"]), []).append(row)
    groups = list(grouped.values())
    posed = next((g for g in groups if g[0]["pose"] == draft.edit.rig.pose and g[0]["pose"]), None)
    neutral = next((g for g in groups if not g[0]["pose"]), None)
    component = "ParamComponent_" + hashlib.sha256(draft.part.encode()).hexdigest()[:12]
    moved = [g for g in groups if component in g[0]["pose"] and g[0]["pose"][component] != 0]
    selected = []
    for group in [posed, *moved[-1:], neutral, *groups[-1:], *groups]:
        if group and group not in selected and len(group) >= 2:
            selected.append(group)
        if len(selected) == 3:
            break
    images = []
    for index, group in enumerate(selected):
        regions = [row["part_region"] for row in group if row.get("part_region")]
        if draft.edit.rig.region:
            x, y, width, height = draft.edit.rig.region
        elif regions:
            x, y = min(r[0] for r in regions), min(r[1] for r in regions)
            width = max(r[0] + r[2] for r in regions) - x
            height = max(r[1] + r[3] for r in regions) - y
        else:
            x, y, width, height = part.bounds
        margin = max(12, min(width, height) * 0.25)
        group = sorted(group, key=lambda row: not ("前" in row["label"] or "/before-" in row["path"]))
        board = Image.new("RGB", (1024, 928), "#e7e9ef")
        draw = ImageDraw.Draw(board)
        draw.text((12, 6), "BEFORE: whole / detail", fill="black")
        draw.text((524, 6), "AFTER: whole / detail", fill="black")
        for column, row in enumerate((group[0], group[-1])):
            with Image.open(root / row["path"]) as raw:
                image = raw.convert("RGBA")
            whole = image.copy()
            whole.thumbnail((500, 500))
            board.paste(whole, (column * 512 + (512 - whole.width) // 2, 28), whole)
            sx, sy = image.width / manifest.width, image.height / manifest.height
            box = (
                max(0, int((x - margin) * sx)),
                max(0, int((y - margin) * sy)),
                min(image.width, int((x + width + margin) * sx)),
                min(image.height, int((y + height + margin) * sy)),
            )
            if box[2] > box[0] and box[3] > box[1]:
                detail = image.crop(box)
                factor = min(500 / detail.width, 384 / detail.height)
                detail = detail.resize(
                    (max(1, round(detail.width * factor)), max(1, round(detail.height * factor)))
                )
                board.paste(detail, (column * 512 + (512 - detail.width) // 2, 536), detail)
        path = output / f"comparison-{index:02}.png"
        board.save(path)
        images.append((f"整体和局部 · 姿态 {group[0]['pose']}", path))
    return images


def rule_context(checks):
    """Keep full diagnostics on disk; the VLM only needs the finite rule outcomes."""
    return [{key: check[key] for key in ("name", "status", "pixels") if key in check} for check in checks]


def candidate_context(candidate):
    visual = candidate.get("visual", {})
    return {
        "draft": candidate["draft"],
        "label": candidate["label"],
        "rules": rule_context(candidate.get("checks", [])),
        "visual": {
            "summary": visual.get("summary", ""),
            "items": [
                {key: item[key] for key in ("name", "status", "reason") if key in item}
                for item in visual.get("items", [])
            ],
        },
    }


def inspect_preview(config, store, identifier, version):
    draft, root = repair.snapshot(store, identifier, version)
    preview = draft.result.get("binding_preview") or draft.result.get("preview", {})
    if preview.get("version") != version or not preview.get("views"):
        raise ValueError("请先生成当前草稿的绑定预览")
    output = root / "reviews" / uuid.uuid4().hex
    output.mkdir(parents=True)
    images = comparison_images(draft, root, preview, output)
    if not images:
        raise ValueError("缺少可配对的修正前后姿态证据")
    result = vision.ask(
        config,
        repair.MaterialReview,
        "比较修正前后姿态，逐项判断用户反馈是否改善，以及连接、穿帮、遮挡是否新增问题。"
        "程序几何检查不代表视觉通过。看不清返回uncertain；只核对提供的静态图片，不推断连续物理。regions返回空数组。",
        {"request": draft.edit.request, "part": draft.part, "rules": rule_context(preview.get("checks", []))},
        images,
        output / "visual.json",
    )
    return repair.publish(
        store,
        draft,
        visual={
            "version": version,
            **result.model_dump(),
            "evidence": [{"label": label, "path": str(path.relative_to(root))} for label, path in images],
            "views": [{"label": label, "path": str(path.relative_to(root))} for label, path in images],
        },
    )


class CandidateComparison(Strict):
    preferred: str | None
    improved: bool
    reason: str
    uncertainties: list[str] = Field(max_length=8)


def recommended_candidate(candidates, comparison):
    """A visual vote cannot rescue failed rules or replace an uncertain baseline."""
    if not comparison.improved or comparison.uncertainties:
        return None
    for candidate in candidates:
        if (
            candidate["draft"] == comparison.preferred
            and candidate["status"] == "ready"
            and hard_checks_pass({"ready": True, "checks": candidate.get("checks", [])})
            and candidate.get("visual", {}).get("items")
            and all(c["status"] == "pass" for c in candidate["visual"]["items"])
        ):
            return candidate["draft"]
    return None


def refresh_candidate_versions(store, candidates):
    """A saved visual vote only applies to the exact child version that produced it."""
    for item in candidates:
        if item.get("status") == "stale":
            continue
        try:
            repair.snapshot(store, item["draft"], item["version"])
        except (FileNotFoundError, RuntimeError, ValueError):
            item.update(status="stale", reason="候选已被编辑、应用或丢弃；保留旧证据，不再参与推荐")


def search(config, store, identifier, version, *, edit=None):
    """Bounded child drafts are previewed and compared; no project revision is applied."""
    draft, root = repair.snapshot(store, identifier, version)
    edit = edit or draft.edit
    if edit.mode not in {"rig", "bind"}:
        raise ValueError("绑定候选搜索只适用于绑定或姿态修正")
    variants = bounded_candidates(edit)[: edit.candidate_limit]
    identity = fingerprint(
        {
            "edit": edit.model_dump(),
            "source": draft.source_hash,
            "rig": draft.rig_hash,
            "implementation": implementation_hash(config),
        }
    )
    previous = draft.result.get("search", {})
    result = (
        previous
        if previous.get("input") == identity and previous.get("version") == version
        else {
            "version": version,
            "input": identity,
            "candidates": [],
            "recommended": None,
            "summary": "逐个预览候选；任何修正均需最终人工验收",
            "needs_user_review": True,
        }
    )
    result["budget_seconds"] = edit.candidate_timeout
    result["budget_policy"] = "预算到时不再启动新候选；已开始的模型步骤使用现有任务超时"
    result["recommended"] = None
    for key in ("comparison", "comparison_error", "budget_reached"):
        result.pop(key, None)
    started = time.monotonic()
    previous_elapsed = result.get("total_elapsed", 0)
    records = result["candidates"]
    for index, (label, candidate_edit) in enumerate(variants):
        repair.snapshot(store, identifier, version)
        if time.monotonic() - started >= edit.candidate_timeout:
            result["budget_reached"] = True
            break
        existing = records[index] if index < len(records) else None
        candidate = None
        if existing:
            try:
                child, _ = repair.snapshot(store, existing["draft"], existing["version"])
                if child.edit != candidate_edit:
                    existing.update(status="stale", reason="候选标注已变化，不覆盖用户修改")
                    continue
                if existing.get("status") == "stale" or (
                    existing.get("completed") and existing.get("status") != "error"
                ):
                    continue
                candidate = child
            except (FileNotFoundError, RuntimeError, ValueError):
                existing.update(status="stale", reason="候选已被编辑、应用或丢弃；保留旧证据，不再参与推荐")
                continue
        candidate = candidate or repair.fork_candidate(config, store, draft, label, candidate_edit)
        item = {
            "draft": candidate.id,
            "version": candidate.version,
            "label": label,
            "status": "running",
            "reason": "正在预览",
            "elapsed": 0,
        }
        if index < len(records):
            records[index] = item
        else:
            records.append(item)
        repair.publish(store, draft, search=result)
        attempt = time.monotonic()
        try:
            if candidate.edit.mode == "bind":
                binding = candidate.result.get("binding_preview", {})
                if binding.get("version") != candidate.version:
                    candidate = repair.binding_preview(config, store, candidate.id, candidate.version)
                    binding = candidate.result["binding_preview"]
                if not binding.get("checks"):
                    candidate = asyncio.run(
                        binding_geometry(config, store, candidate, repair.directory(store, candidate.id))
                    )
            else:
                preview = candidate.result.get("preview", {})
                if preview.get("version") != candidate.version or not preview.get("checks"):
                    candidate = repair.preview_materials(config, store, candidate.id, candidate.version)
            preview = candidate.result.get("binding_preview") or candidate.result.get("preview", {})
            item["checks"] = preview.get("checks", [])
            if not hard_checks_pass(preview):
                item.update(status="rejected", reason="程序检查未通过，候选已淘汰")
            elif time.monotonic() - started >= edit.candidate_timeout:
                item.update(status="paused", reason="预算已用完；继续搜索可复用预览并完成视觉检查")
                result["budget_reached"] = True
            else:
                if candidate.result.get("visual", {}).get("version") != candidate.version:
                    candidate = inspect_preview(config, store, candidate.id, candidate.version)
                visual = candidate.result["visual"]
                item["visual"] = visual
                if any(c["status"] == "fail" for c in visual["items"]):
                    item.update(status="rejected", reason="视觉检查发现失败项，保留证据供人工核对")
                elif not visual["items"] or any(c["status"] == "uncertain" for c in visual["items"]):
                    item.update(status="uncertain", reason="视觉判断不确定，需要人工比较")
                else:
                    item.update(status="ready", reason="规则检查通过，等待与原始状态和其他候选比较")
        except Exception as exc:
            item.update(status="error", reason=str(exc))
        item.update(elapsed=round(time.monotonic() - attempt, 2), completed=item["status"] != "paused")
        repair.publish(store, draft, search=result)
    result["recommended"] = None
    refresh_candidate_versions(store, records)
    eligible = [c for c in records if c["status"] == "ready"]
    if time.monotonic() - started >= edit.candidate_timeout:
        result["budget_reached"] = True
        result["summary"] = "本轮预算已用完，未启动视觉比较；已有候选和预览已保留，原版本不变"
    elif eligible:
        output = root / "reviews" / uuid.uuid4().hex
        output.mkdir(parents=True)
        images = []
        for candidate in eligible:
            candidate_root = repair.directory(store, candidate["draft"])
            for view in candidate["visual"].get("evidence", [])[:1]:
                images.append(
                    (
                        f"候选 {candidate['draft']} / {candidate['label']} · 左为原始右为修正",
                        candidate_root / view["path"],
                    )
                )
        if images:
            try:
                comparison = vision.ask(
                    config,
                    CandidateComparison,
                    "比较每个候选的左侧原始状态与右侧修正。必须确实改善用户反馈且未新增问题才选择候选ID。"
                    "没有改善、差异看不清或均有问题时preferred=null、improved=false。不要强行排名；"
                    "若不确定，写入uncertainties。程序通过不等于外观更好。这些静态图片不能证明连续物理表现。",
                    {"request": draft.edit.request, "candidates": [candidate_context(c) for c in eligible]},
                    images,
                    output / "comparison.json",
                )
                result["comparison"] = comparison.model_dump()
                refresh_candidate_versions(store, records)
                result["recommended"] = recommended_candidate(records, comparison)
                result["summary"] = comparison.reason
            except Exception as exc:
                result["comparison_error"] = str(exc)
                result["summary"] = "候选已保留，但视觉比较失败；维持原版本并等待人工核对"
        else:
            result["summary"] = "候选缺少可比较的图像证据；维持原版本并等待人工核对"
    else:
        result["summary"] = "没有可确认改善的候选；维持原版本，保留失败和待确认的证据"
    result["elapsed"] = round(time.monotonic() - started, 2)
    result["total_elapsed"] = round(previous_elapsed + result["elapsed"], 2)
    return repair.publish(store, draft, search=result)
