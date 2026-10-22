"""Versioned, reviewed multi-layer assembly, using local PNGs without inference."""

import shutil
import uuid
from pathlib import Path

from PIL import Image

from . import assets
from .agent_review import AgentReview
from .batch_schema import BatchDraft, BatchEdit
from .repair import copy_materials, material_files
from .schema import Part, Parts, Plan
from .store import digest, fingerprint, lock, read_json, write_json


def directory(store, identifier):
    if len(identifier) != 32 or any(c not in "0123456789abcdef" for c in identifier):
        raise ValueError("Invalid material batch ID")
    return store.root / "material-batches" / identifier


def load(store, identifier):
    return BatchDraft.model_validate(read_json(directory(store, identifier) / "draft.json"))


def save(store, draft):
    write_json(directory(store, draft.id) / "draft.json", draft.model_dump())


def describe(store, draft):
    root = directory(store, draft.id)
    return {
        **draft.model_dump(),
        "directory": str(root),
        "input_images": {key: str(root / value["path"]) for key, value in draft.inputs.items()},
    }


def list_drafts(store):
    return [read_json(path) for path in sorted((store.root / "material-batches").glob("*/draft.json"))]


def checked_path(root, relative):
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError("Batch artifact missing or outside its directory")
    return path


def capture_inputs(root, edit, version):
    """Publish inputs only after all PNGs have been captured and validated together."""
    staging = root / ("pending-" + uuid.uuid4().hex)
    staging.mkdir()
    inputs = {}
    try:
        for op in edit.operations:
            if op.source is None:
                continue
            source = Path(op.source).expanduser().resolve()
            if source.suffix.lower() != ".png":
                raise ValueError(f"{op.id}: source must be a PNG")
            dest = staging / f"{op.id}.png"
            shutil.copyfile(source, dest)
            with Image.open(dest) as image:
                if image.format != "PNG" or image.width * image.height > 24_000_000:
                    raise ValueError(f"{op.id}: expected PNG with at most 24 million pixels")
                if image.convert("RGBA").getchannel("A").getbbox() is None:
                    raise ValueError(f"{op.id}: empty alpha")
            inputs[op.id] = {
                "path": f"inputs/v{version}/{op.id}.png",
                "sha256": digest(dest),
                "source": str(source),
            }
        # Resolve metadata against staged inputs before changing the active draft.
        candidate_manifest(root / "base", edit, {key: staging / f"{key}.png" for key in inputs})
        target = root / "inputs" / f"v{version}"
        target.parent.mkdir(exist_ok=True)
        staging.rename(target)
        return inputs
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def candidate_manifest(base, edit, inputs):
    manifest = assets.load_parts(base)
    if edit.canvas and edit.canvas != (manifest.width, manifest.height):
        raise ValueError("Canvas cannot change in a material batch")
    parts = {part.id: part.model_copy(deep=True) for part in manifest.parts}
    if len(parts) != len(manifest.parts):
        raise ValueError("Base contains duplicate part IDs")
    for op in edit.operations:
        if op.semantic is not None and op.semantic not in assets.SEMANTICS:
            raise ValueError(f"{op.id}: unknown semantic {op.semantic}")
        if op.action == "add":
            if op.id in parts:
                raise ValueError(f"{op.id}: part already exists")
            parts[op.id] = Part(
                id=op.id,
                name=op.name or op.id,
                semantic=op.semantic,
                side=op.side or "none",
                order=op.order,
                bounds=op.placement,
                image=f"{op.id}.png",
                binding=op.binding,
            )
        elif op.id not in parts:
            raise ValueError(f"{op.id}: part does not exist")
        if op.action == "remove":
            del parts[op.id]
            continue
        part = parts[op.id]
        for field in ("name", "semantic", "side", "order", "binding"):
            value = getattr(op, field)
            if value is not None:
                setattr(part, field, value)
        if op.clear_binding:
            part.binding = None
        if op.placement is not None:
            part.bounds = op.placement
        if op.source is not None:
            part.image = f"{op.id}.png"
        path = inputs[op.id] if op.source else checked_path(base, part.image)
        with Image.open(path) as image:
            if image.size != part.bounds[2:]:
                raise ValueError(f"{op.id}: placement dimensions must match the PNG; no implicit resizing")
    if not parts:
        raise ValueError("A material batch must leave at least one layer")
    orders = [p.order for p in parts.values()]
    if len(orders) != len(set(orders)):
        raise ValueError("Layer orders must be unique; include all affected layers when reordering")
    images = [p.image for p in parts.values()]
    if any(Path(image).is_absolute() or ".." in Path(image).parts for image in images):
        raise ValueError("Material image paths must be relative without parent traversal")
    if len(images) != len(set(images)):
        raise ValueError("Parts cannot share a material image path")
    for part in parts.values():
        x, y, w, h = part.bounds
        if min(x, y) < 0 or min(w, h) < 1 or x + w > manifest.width or y + h > manifest.height:
            raise ValueError(f"{part.id}: placement exceeds canvas")
    manifest.parts = sorted(parts.values(), key=lambda p: p.order)
    return manifest


def create(store, edit: BatchEdit, revision=None):
    state = read_json(store.root / "project.json")
    rid = revision or state["head"]
    identifier = uuid.uuid4().hex
    root = directory(store, identifier)
    root.mkdir(parents=True)
    try:
        if rid is None:
            if edit.canvas is None:
                raise ValueError("An empty project requires canvas: [width, height]")
            assets.save_parts(root / "base", Parts(width=edit.canvas[0], height=edit.canvas[1], parts=[]))
            assets.assemble(root / "base", root / "base/composite.png")
        else:
            store.get(rid)
            base = store.revision_dir(rid) / "materials"
            before = fingerprint(material_files(base))
            copy_materials(base, root / "base")
            if before != fingerprint(material_files(base)) or before != fingerprint(material_files(root / "base")):
                raise ValueError("Base materials changed while the batch was created")
        inputs = capture_inputs(root, edit, 1)
        draft = BatchDraft(
            id=identifier,
            base_revision=rid,
            material_hash=fingerprint(material_files(root / "base")),
            edit=edit,
            inputs=inputs,
        )
        save(store, draft)
        return draft
    except BaseException:
        shutil.rmtree(root)
        raise


def snapshot(store, identifier, version):
    draft = load(store, identifier)
    if draft.version != version or draft.applied_revision:
        raise RuntimeError("Batch version is stale or already applied")
    root = directory(store, identifier)
    if fingerprint(material_files(root / "base")) != draft.material_hash:
        raise ValueError("Batch base snapshot changed")
    for image in draft.inputs.values():
        if digest(checked_path(root, image["path"])) != image["sha256"]:
            raise ValueError("Batch input snapshot changed")
    return draft, root


def update(store, identifier, version, edit: BatchEdit):
    root = directory(store, identifier)
    with lock(root / ".lock"):
        draft, _ = snapshot(store, identifier, version)
        inputs = capture_inputs(root, edit, version + 1)
        draft.version += 1
        draft.edit, draft.inputs, draft.result = edit, inputs, {}
        try:
            save(store, draft)
        except BaseException:
            shutil.rmtree(root / "inputs" / f"v{draft.version}")
            raise
        return draft


def preview(store, identifier, version):
    root = directory(store, identifier)
    with lock(root / ".lock"):
        draft, _ = snapshot(store, identifier, version)
        manifest = candidate_manifest(
            root / "base", draft.edit, {key: checked_path(root, item["path"]) for key, item in draft.inputs.items()}
        )
        work = root / "previews" / uuid.uuid4().hex
        material = work / "materials"
        material.mkdir(parents=True)
        try:
            for part in manifest.parts:
                source = (
                    checked_path(root, draft.inputs[part.id]["path"])
                    if part.id in draft.inputs
                    else checked_path(root / "base", part.image)
                )
                dest = material / part.image
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, dest)
            assets.save_parts(material, manifest)
            assets.assemble(material, material / "composite.png")
            quality = assets.quality(material)
            write_json(work / "quality.json", quality)
            files = {str(p.relative_to(work)): digest(p) for p in work.rglob("*") if p.is_file()}
            write_json(work / "complete.json", files)
            draft.result = {
                "preview": {
                    "version": version,
                    "path": str(work.relative_to(root)),
                    "receipt_sha256": digest(work / "complete.json"),
                    "quality": quality,
                    "ready": not quality["errors"],
                    "ready_for_rig": not (quality["errors"] or quality["missing_parts"]),
                    "canvas": [manifest.width, manifest.height],
                    "parts": len(manifest.parts),
                }
            }
            save(store, draft)
            return draft
        except BaseException:
            shutil.rmtree(work)
            raise


def verify_candidate(draft, root):
    preview = draft.result.get("preview", {})
    if preview.get("version") != draft.version:
        raise ValueError("Generate a preview for the current batch version")
    receipt = checked_path(root, preview["path"] + "/complete.json")
    if digest(receipt) != preview["receipt_sha256"]:
        raise ValueError("Batch preview receipt changed")
    for path, sha in read_json(receipt).items():
        if digest(checked_path(receipt.parent, path)) != sha:
            raise ValueError("Batch preview artifact changed")
    return fingerprint({"version": draft.version, "edit": draft.edit.model_dump(), "preview": preview})


def evidence(store, identifier, version):
    root = directory(store, identifier)
    with lock(root / ".lock"):
        draft, _ = snapshot(store, identifier, version)
        candidate = verify_candidate(draft, root)
        material = root / draft.result["preview"]["path"] / "materials"
        before, after = assets.load_parts(root / "base"), assets.load_parts(material)
        images = [("Before: full material composite", root / "base/composite.png")]
        images.append(("After: full material composite", material / "composite.png"))
        for label, manifest, folder in (("Before", before, root / "base"), ("After", after, material)):
            changed = {op.id for op in draft.edit.operations}
            images.extend((f"{label}: {p.id} (order {p.order}, bounds {p.bounds})", folder / p.image)
                          for p in manifest.parts if p.id in changed)
        result = {
            "id": uuid.uuid4().hex,
            "version": version,
            "candidate": candidate,
            "views": [{"label": label, "path": str(path.relative_to(root)), "sha256": digest(path)}
                      for label, path in images],
            "deformation_quality": "not_evaluated",
            "physics_time_response": "not_evaluated",
        }
        write_json(root / "reviews" / result["id"] / "evidence.json", result)
        draft.result["review_inputs"] = result
        draft.result.pop("visual", None)
        save(store, draft)
        return {**result, "images": [{**view, "path": str(root / view["path"])} for view in result["views"]]}


def verify_evidence(draft, root, evidence_id):
    inputs = draft.result.get("review_inputs", {})
    if inputs.get("id") != evidence_id or inputs.get("version") != draft.version:
        raise ValueError("Batch review evidence is stale")
    if inputs.get("candidate") != verify_candidate(draft, root):
        raise ValueError("Batch candidate changed after evidence")
    for view in inputs["views"]:
        if digest(checked_path(root, view["path"])) != view["sha256"]:
            raise ValueError("Batch review image changed")
    return inputs


def record_review(store, identifier, version, report: AgentReview):
    root = directory(store, identifier)
    with lock(root / ".lock"):
        draft, _ = snapshot(store, identifier, version)
        verify_evidence(draft, root, report.evidence_id)
        visual = {
            **report.model_dump(), "source": "agent", "version": version,
            "user_acceptance": "pending", "deformation_quality": "not_evaluated",
            "physics_time_response": "not_evaluated",
        }
        write_json(root / "reviews" / report.evidence_id / "agent-review.json", visual)
        draft.result["visual"] = visual
        save(store, draft)
        return draft


def verify_base(store, draft):
    if read_json(store.root / "project.json")["head"] != draft.base_revision:
        raise RuntimeError("Project head changed; return to the batch base before applying")
    if draft.base_revision is not None:
        base = store.revision_dir(draft.base_revision) / "materials"
        if fingerprint(material_files(base)) != draft.material_hash:
            raise ValueError("Base revision materials changed")


def apply(config, store, identifier, version, until="materials"):
    """Commit one revision only when its requested stage succeeds; failure keeps the old head."""
    from .pipeline import Pipeline

    if until not in {"materials", "rig"}:
        raise ValueError("until must be materials or rig")
    root = directory(store, identifier)
    with lock(store.root / ".lock"), lock(root / ".lock"):
        draft, _ = snapshot(store, identifier, version)
        visual = draft.result.get("visual", {})
        if visual.get("version") != version or not visual.get("items"):
            raise ValueError("Record a current visual review before applying")
        verify_evidence(draft, root, visual.get("evidence_id"))
        if any(item["status"] != "pass" for item in visual["items"]):
            raise ValueError("Batch visual review has failed or uncertain items")
        preview = draft.result["preview"]
        if not preview["ready"] or (until == "rig" and not preview["ready_for_rig"]):
            raise ValueError("Batch materials are incomplete for the requested stage")
        verify_base(store, draft)
        parent = store.get(draft.base_revision) if draft.base_revision else None
        rev = store.create(
            Plan(request=draft.edit.request, prompt=parent.plan.prompt if parent else draft.edit.request),
            parent=draft.base_revision, checkout=False,
        )
        destination = store.revision_dir(rev.id)
        try:
            copy_materials(root / preview["path"] / "materials", destination / "prepared-materials")
            rev.prepared_materials = "prepared-materials"
            store.save(rev)
            write_json(destination / "material-batch.json", draft.model_dump())
            Pipeline(config, store).run(rev, until=until)
            if rev.stages.get(until) is None or rev.stages[until].state != "complete":
                raise RuntimeError("Requested material batch stage did not complete")
            if until == "rig" and rev.status != "built":
                raise RuntimeError("Material batch rig build did not complete")
            # Recheck inputs and evidence after the long build, before committing anything.
            snapshot(store, identifier, version)
            verify_evidence(draft, root, visual["evidence_id"])
            verify_base(store, draft)
            draft.applied_revision = rev.id
            save(store, draft)
            try:
                store.checkout(rev.id)
            except BaseException:
                draft.applied_revision = None
                save(store, draft)
                raise
        except BaseException:
            # Only this detached, newly allocated revision is removed; old revisions stay intact.
            shutil.rmtree(destination, ignore_errors=True)
            shutil.rmtree(store.root / "performance" / rev.id, ignore_errors=True)
            raise
        return rev


def discard(store, identifier, version):
    root = directory(store, identifier)
    with lock(root / ".lock"):
        snapshot(store, identifier, version)
        (root / "draft.json").rename(root / "discarded.json")
