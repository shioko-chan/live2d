"""Application operations shared by CLI and web workers; callers hold the task/project locks."""

import shutil
import tempfile
from pathlib import Path

from .pipeline import Pipeline
from .planner import make_plan
from .schema import Plan
from .store import digest, read_json, write_json
from .validation import validate_bundle


def plan_revision(config, store, request, source=None, reference=None, plan_file=None, reuse_inputs=True):
    state = read_json(store.root / "project.json")
    current = store.get() if state["head"] else None
    draft = current if current and current.status not in {"built", "accepted"} else None
    parent = draft.parent if draft else state["head"]
    base = store.get(parent) if parent else None
    if base and base.status not in {"built", "accepted"} and source is None:
        raise ValueError("请先选择一个已完成的基础版本，或导入新的源素材。")
    if draft and reuse_inputs:
        directory = store.revision_dir(draft.id)
        source = source or (directory / draft.source if draft.source else None)
        reference = reference or (directory / draft.reference if draft.reference else None)
    if source is not None and reference is not None:
        raise ValueError("原稿直接用于制作模型，不能同时使用参考图。请只保留一种图片。")
    if plan_file:
        result = Plan.model_validate_json(Path(plan_file).read_text())
        result.request = request
    else:
        # Replace an unfinished proposal as a sibling. Operations must be planned against
        # the completed base, never against partial materials that cannot be inherited.
        context = Pipeline(config, store).context(base) if base else {}
        if source is not None:
            context.pop("rig", None)
            context.pop("parts", None)
        if source is not None and source.suffix.lower() == ".psd":
            from .assets import normalize

            with tempfile.TemporaryDirectory(prefix="l2d-plan-") as tmp:
                context["parts"] = normalize(source, Path(tmp)).model_dump()
        result = make_plan(config, request, context, store.root / "planner.log")
    if reference is not None and base and not any(o.kind == "regenerate" for o in result.operations):
        raise ValueError("参考图仅用于新角色生成或整图重新生成；普通局部修改不会使用参考图。")
    return store.create(result, source=source, reference=reference, parent=parent)


def export_revision(store, destination, revision=None):
    from . import performance

    rev = store.get(revision)
    if rev.status not in {"built", "accepted"}:
        raise ValueError("Revision is not built")
    directory = store.revision_dir(rev.id)
    validate_bundle(directory / "rig/bundle")
    if destination.exists():
        raise ValueError("Destination must not exist")
    destination = destination.resolve()
    if destination.is_relative_to(store.root):
        raise ValueError("Export outside the project to keep revisions immutable")
    for stage in rev.stages.values():
        for path, sha in stage.artifacts.items():
            if digest(directory / path) != sha:
                raise ValueError(f"Artifact was modified: {path}")
    library = performance.load_library(store, rev.id)
    if library["id"]:
        _, parameters = performance.parameter_map(store, rev.id)
        performance.validate_library(performance.contents(library), parameters)
    shutil.copytree(directory / "rig/bundle", destination / "model")
    performance.attach_bundle(store, rev.id, destination / "model", snapshot=library["id"])
    report = validate_bundle(destination / "model")
    report["performance"] = {"snapshot": library["id"], "version": library["version"]}
    write_json(destination / "performance.json", library)
    shutil.copytree(directory / "materials", destination / "sources/layers")
    for filename in ("source.psd", "source.psd2live"):
        shutil.copyfile(directory / "rig" / filename, destination / "sources" / filename)
    for filename in ("components.json", "pose-samples.json"):
        if (directory / "rig" / filename).is_file():
            shutil.copyfile(directory / "rig" / filename, destination / "sources" / filename)
    if (directory / "repair-evidence").exists():
        shutil.copytree(directory / "repair-evidence", destination / "repair")
        shutil.copyfile(directory / "repair.json", destination / "repair/draft.json")
    for filename in ("rig-replay.json", "material-batch.json"):
        if (directory / filename).exists():
            shutil.copyfile(directory / filename, destination / "sources" / filename)
    latest = store.root / "reviews" / rev.id / "latest.json"
    if latest.is_file():
        review = read_json(latest)
        evidence = (store.root / review["report_directory"]).resolve()
        if not evidence.is_relative_to((store.root / "reviews" / rev.id).resolve()):
            raise ValueError("Review evidence directory is outside the revision")
        (destination / "review").mkdir()
        shutil.copyfile(latest, destination / "review/report.json")
        for image in evidence.glob("sheet-*.png"):
            shutil.copyfile(image, destination / "review" / image.name)
    shutil.copyfile(directory / "revision.json", destination / "revision.json")
    write_json(destination / "validation.json", report)
    return destination
