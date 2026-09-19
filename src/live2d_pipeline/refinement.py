"""Bounded component edits using existing SAM2, LaMa and PSD2Live capabilities."""

import hashlib
from PIL import Image, ImageChops
from . import assets
from .store import write_json

MOVABLE = {
    "front hair",
    "back hair",
    "headwear",
    "earwear",
    "neckwear",
    "topwear",
    "bottomwear",
    "handwear",
    "legwear",
    "footwear",
    "tail",
    "wings",
    "objects",
}


def validate_edits(manifest, edits):
    by_id = {p.id: p for p in manifest.parts}
    seen = set()
    for edit in edits:
        if edit.source not in by_id or edit.source in seen:
            raise ValueError("Refinement needs a unique existing source part per operation")
        seen.add(edit.source)
        part = by_id[edit.source]
        if part.semantic not in MOVABLE:
            raise ValueError("Component refinement is limited to hair, clothing and accessories")
        if edit.action == "split":
            x, y, w, h = edit.box
            if x + w > part.bounds[2] or y + h > part.bounds[3]:
                raise ValueError("Selection exceeds source layer")


def apply(config, directory, edits):
    """Explicit plan edits use the same candidate executor as interactive drafts."""
    import shutil
    from . import repair
    from .repair_schema import RepairDraft, RepairEdit, MaskConstraints, Mark
    from .store import digest, fingerprint

    if not edits:
        return
    manifest = assets.load_parts(directory)
    validate_edits(manifest, edits)
    before = Image.open(directory / "composite.png").convert("RGBA")
    for edit in edits:
        part = next(p for p in manifest.parts if p.id == edit.source)
        if edit.action == "bind":
            part.name, part.binding = edit.name, edit.binding
            continue
        token = hashlib.sha256(edit.model_dump_json().encode()).hexdigest()[:32]
        identifier = "component_" + token[:12]
        if any(p.id == identifier for p in manifest.parts):
            raise ValueError("Component already exists")
        work = directory / "refinement" / identifier
        work.mkdir(parents=True, exist_ok=True)
        assets.save_parts(directory, manifest)
        repair.copy_materials(directory, work / "base")
        shutil.copyfile(directory / part.image, work / "source.png")
        draft = RepairDraft(
            id=token, base_revision="explicitplan", part=part.id,
            source_hash=digest(work / "source.png"), material_hash=fingerprint(repair.material_files(work / "base")),
            edit=RepairEdit(mode="split", name=edit.name, request=edit.reason, semantic=part.semantic,
                placement=part.bounds, binding=edit.binding,
                mask=MaskConstraints(box=edit.box, marks=[Mark(kind="keep", points=[edit.point])])),
        )
        draft.result["segmentation"] = repair.generate_masks(config, draft, work)
        result = repair.material_candidate(config, draft, work)
        if not result["ready"]:
            raise ValueError("部件修正未通过规则检查，请打开修正工作区")
        repair.copy_materials(work / result["path"] / "materials", directory)
        manifest = assets.load_parts(directory)
    assets.save_parts(directory, manifest)
    assets.assemble(directory, directory / "composite.png")
    write_json(directory / "quality.json", assets.quality(directory))
    after = Image.open(directory / "composite.png").convert("RGBA")
    # Neutral artwork must be pixel-identical; a split is not a style change.
    if (
        ImageChops.difference(before, after).convert("RGB").getbbox()
        or ImageChops.difference(before.getchannel("A"), after.getchannel("A")).getbbox()
    ):
        raise ValueError("Refinement changed the neutral composite")
    write_json(
        directory / "refinement-checks.json",
        {
            "neutral_pixels_identical": True,
            "components": len(edits),
            "hidden_paint": "LaMa estimate; inspect moved poses",
        },
    )


async def bind(rig, manifest):
    """Independent child warps preserve the semantic rig's inherited head/body motion."""
    objects = await rig.objects()
    records = []
    for part in manifest.parts:
        binding = part.binding
        if binding is None:
            continue
        layer_id = str(int.from_bytes(hashlib.sha256(part.id.encode()).digest()[:4], "big") & 0x7FFFFFFF)
        meshes = [
            obj
            for obj in objects
            if obj["kind"] == "mesh"
            and (
                obj.get("layerId", "") == "lyid:" + layer_id
                or obj.get("layerId", "").startswith("lyid:" + layer_id + ":")
            )
        ]
        if not meshes:
            raise ValueError(f"No rig mesh for component {part.id}")
        token = hashlib.sha256(part.id.encode()).hexdigest()[:12]
        parameter = "ParamComponent_" + token

        async def mutate(tool, **args):
            state = await rig.call("project_get_state")
            return await rig.call(tool, {**args, "expected_history_head_node_id": state["historyHeadNodeId"]})

        record = {
            "part": part.id,
            "name": part.name,
            "motion": binding.motion,
            "parents": [],
            "parameter": None,
            "toggle": None,
            "diagnostics": [],
        }
        if binding.toggle:
            switch = "ParamVisible_" + token
            await mutate(
                "parameter_create", parameter_id=switch, name=part.name + " 可见", min=0, max=1, default=1
            )
            for mesh in meshes:
                for value in (0, 1):
                    await mutate(
                        "keyform_set",
                        target={"kind": "mesh", "id": mesh["id"]},
                        coordinate={switch: value},
                        channels={"opacity": value},
                    )
            record["toggle"] = switch
        if binding.motion == "sway":
            await mutate(
                "parameter_create", parameter_id=parameter, name=part.name + " 摆动", min=-1, max=1, default=0
            )
            groups = {}
            for mesh in meshes:
                info = await rig.call("rig_inspect", {"target": {"kind": "mesh", "id": mesh["id"]}})
                groups.setdefault(info["parentId"], []).append((mesh, info))
            for index, (parent, group) in enumerate(groups.items()):
                warp = "DeformComponent_" + token + "_" + str(index)
                await mutate(
                    "warp_create",
                    id=warp,
                    name=part.name,
                    parent_id=parent,
                    mesh_ids=[m["id"] for m, _ in group],
                    rows=32,
                    columns=32,
                )
                target = {"kind": "warp", "id": warp}
                info = await rig.call("rig_inspect", {"target": target})
                rows, cols = info["rows"], info["columns"]
                identity = [v for y in range(rows + 1) for x in range(cols + 1) for v in (x / cols, y / rows)]
                boxes = [i["localBounds"] for _, i in group]
                left, top = min(b[0] for b in boxes), min(b[1] for b in boxes)
                # rig_inspect.localBounds is [left, top, width, height].
                right, bottom = max(b[0] + b[2] for b in boxes), max(b[1] + b[3] for b in boxes)
                root = [left + (right - left) * binding.root[0], top + (bottom - top) * binding.root[1]]
                tip = [left + (right - left) * binding.tip[0], top + (bottom - top) * binding.tip[1]]
                for value in (0, -1, -0.5, 0.5, 1):
                    await mutate(
                        "keyform_set",
                        target=target,
                        coordinate={parameter: value},
                        geometry={"control_points": identity},
                    )
                    if value:
                        args = {
                            "target": target,
                            "coordinate": {parameter: value},
                            "operations": [
                                {
                                    "type": "sway",
                                    "root": root,
                                    "tip": tip,
                                    "degrees": value * binding.degrees,
                                    "softness": binding.softness,
                                    "root_pin": binding.root_pin,
                                }
                            ],
                        }
                        preview = await rig.call("rig_preview", args)
                        diagnostic = preview["diagnostics"]
                        if diagnostic["flippedTriangleCount"] or diagnostic["collapsedTriangleCount"]:
                            raise ValueError(f"Unsafe component deformation: {part.name}")
                        await mutate("rig_transform", **args)
                        record["diagnostics"].append({"value": value, **diagnostic})
                record["parents"].append(parent)
            await mutate(
                "physics_put",
                id="PhysicsComponent_" + token,
                name=part.name,
                input_parameter="ParamAngleX"
                if part.semantic in {"front hair", "back hair", "headwear", "earwear"}
                else "ParamBodyAngleX",
                output_parameter=parameter,
                length=10,
                mobility=0.8,
                delay=0.8,
                acceleration=1,
                output_scale=1,
            )
            record["parameter"] = parameter
        else:
            record["parents"] = sorted({obj.get("parentId", "") for obj in meshes})
        records.append(record)
    return records


def mask_iou(left, right):
    intersection = ImageChops.multiply(left, right).histogram()[255]
    union = ImageChops.lighter(left, right).histogram()[255]
    return intersection / union if union else 1.0
