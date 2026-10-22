"""Read saved production evidence without starting inference or a rig process."""

from . import assets
from .store import digest, read_json
from .validation import validate_bundle


def inspect_project(store, revision=None, stage="all", part=None):
    if stage not in {"all", "image", "materials", "rig"}:
        raise ValueError("Unknown inspection stage")
    if part and stage not in {"all", "materials"}:
        raise ValueError("Part inspection requires materials or all")
    rev = store.get(revision)
    root = store.revision_dir(rev.id)
    result = {
        "schema_version": 1,
        "project": str(store.root),
        "revision": rev.id,
        "status": rev.status,
        "directory": str(root),
        "runtime_verified": rev.runtime_verified,
        "visual_quality": "not_evaluated",
        "physics_time_response": "not_evaluated",
        "stages": {},
        "images": [],
    }
    from .performance import load_library

    library = load_library(store, rev.id)
    result["performance"] = {
        "snapshot": library["id"], "version": library["version"],
        "expressions": list(library["expressions"]),
        "motions": {group: list(motions) for group, motions in library["motions"].items()},
        "physics_groups": len(library["physics"]["PhysicsSettings"]) if library["physics"] else 0,
    }
    # Check all recorded stages, even when the caller requests one view.
    for name, record in rev.stages.items():
        errors = []
        for relative, expected in record.artifacts.items():
            path = root / relative
            if not path.resolve().is_relative_to(root):
                errors.append({"path": relative, "error": "outside_revision"})
            elif not path.is_file():
                errors.append({"path": relative, "error": "missing"})
            elif digest(path) != expected:
                errors.append({"path": relative, "error": "hash_mismatch"})
        result["stages"][name] = {
            "state": record.state,
            "artifact_count": len(record.artifacts),
            "integrity": "fail" if errors else "pass" if record.artifacts else "not_evaluated",
            "errors": errors,
            "stage_error": record.error,
        }

    def picture(path, label, **metadata):
        if path.is_file():
            result["images"].append({"path": str(path.resolve()), "label": label, **metadata})

    if stage in {"all", "image"}:
        inputs = list((root / "image").glob("input.*"))
        if not inputs and rev.source:
            inputs = [root / rev.source]
        result["input_files"] = [str(p.resolve()) for p in inputs if p.is_file()]
        for path in inputs:
            if path.suffix.lower() in {".png", ".jpg", ".jpeg"}:
                picture(path, "source artwork")
    if stage in {"all", "materials"} and (root / "materials/parts.json").is_file():
        material = root / "materials"
        manifest = assets.load_parts(material)
        selected = [p for p in manifest.parts if not part or p.id == part]
        if part and not selected:
            raise ValueError(f"Unknown part: {part}")
        try:
            quality = assets.quality(material)
        except (OSError, ValueError) as exc:
            quality = {"errors": [str(exc)], "visual_quality": "not_evaluated"}
        result["materials"] = {
            "manifest": str(material / "parts.json"),
            "width": manifest.width,
            "height": manifest.height,
            "quality": quality,
            "parts": [{**p.model_dump(), "image": str((material / p.image).resolve())} for p in selected],
        }
        picture(material / "composite.png", "neutral material composite")
        for p in selected:
            picture(material / p.image, p.name, part=p.id, bounds=p.bounds, coordinates="source_layer_pixels")
    elif part:
        raise ValueError("Materials are not available yet")
    if stage in {"all", "rig"} and (root / "rig").is_dir():
        rig = root / "rig"
        context = read_json(rig / "rig-context.json") if (rig / "rig-context.json").is_file() else {}
        result["rig"] = {
            "context": str(rig / "rig-context.json") if context else None,
            "parameters": context.get("parameters", {}).get("parameters", []),
            "object_count": len(context.get("objects", [])),
        }
        for key, filename in (("components", "components.json"), ("poses", "pose-samples.json")):
            if (rig / filename).is_file():
                result["rig"][key] = read_json(rig / filename)
        for index, pose in enumerate(result["rig"].get("poses", [])):
            picture(rig / f"pose-{index:02}.png", f"pose-{index:02}", parameters=pose)
        if (rig / "bundle").is_dir():
            try:
                result["rig"]["bundle"] = validate_bundle(rig / "bundle")
            except (ValueError, OSError, KeyError, TypeError) as exc:
                result["rig"]["bundle"] = {"error": str(exc), "runtime_verified": False}
    return result
