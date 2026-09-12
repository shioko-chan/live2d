"""Application operations shared by CLI and web workers; callers hold the task/project locks."""

import shutil
import tempfile
from pathlib import Path

from .pipeline import Pipeline
from .planner import make_plan
from .schema import Plan
from .store import digest, read_json, write_json
from .validation import validate_bundle


def plan_revision(config, store, request, source=None, reference=None, plan_file=None):
    state = read_json(store.root / "project.json")
    current = store.get() if state["head"] else None
    draft = current if current and current.status not in {"built", "accepted"} else None
    parent = draft.parent if draft else state["head"]
    base = store.get(parent) if parent else None
    if base and base.status not in {"built", "accepted"} and source is None:
        raise ValueError("请先选择一个已完成的基础版本，或导入新的源素材。")
    if draft:
        directory = store.revision_dir(draft.id)
        source = source or (directory / draft.source if draft.source else None)
        reference = reference or (directory / draft.reference if draft.reference else None)
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
    return store.create(result, source=source, reference=reference, parent=parent)


def export_revision(store, destination, revision=None):
    rev = store.get(revision)
    if rev.status not in {"built", "accepted"}:
        raise ValueError("Revision is not built")
    directory = store.revision_dir(rev.id)
    report = validate_bundle(directory / "rig/bundle")
    if destination.exists():
        raise ValueError("Destination must not exist")
    destination = destination.resolve()
    if destination.is_relative_to(store.root):
        raise ValueError("Export outside the project to keep revisions immutable")
    for stage in rev.stages.values():
        for path, sha in stage.artifacts.items():
            if digest(directory / path) != sha:
                raise ValueError(f"Artifact was modified: {path}")
    shutil.copytree(directory / "rig/bundle", destination / "model")
    shutil.copytree(directory / "materials", destination / "sources/layers")
    for filename in ("source.psd", "source.psd2live"):
        shutil.copyfile(directory / "rig" / filename, destination / "sources" / filename)
    shutil.copyfile(directory / "revision.json", destination / "revision.json")
    write_json(destination / "validation.json", report)
    return destination
