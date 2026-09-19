"""Revision metadata used to explain available actions without running models."""

from ..store import read_json


def resume_targets(rev):
    if rev.status in {"built", "accepted"} or rev.plan.clarification or rev.plan.unsupported:
        return []
    inherited = rev.parent and not rev.source and not any(o.kind == "regenerate" for o in rev.plan.operations)
    candidates = (
        ["materials", "rig"]
        if inherited or (rev.source or "").endswith(".psd")
        else ["image", "materials", "rig"]
    )
    completed = [key for key, stage in rev.stages.items() if stage.state == "complete"]
    order = ["image", "decompose", "materials", "rig"]
    last = max((order.index(key) for key in completed if key in order), default=-1)
    return [key for key in candidates if order.index(key) > last]


def refinement_changes(store, rev):
    parent_parts = {}
    if rev.parent:
        path = store.revision_dir(rev.parent) / "materials/parts.json"
        if path.exists():
            parent_parts = {p["id"]: p for p in read_json(path)["parts"]}
    return [
        {
            **edit.model_dump(),
            "previous_binding": parent_parts.get(edit.source, {}).get("binding"),
            "completed": rev.status in {"built", "accepted"},
        }
        for edit in rev.plan.refinements
    ]


def part_names(store, rev, translations):
    names = {}
    for rid in (rev.parent, rev.id):
        if not rid:
            continue
        path = store.revision_dir(rid) / "materials/parts.json"
        if path.exists():
            for part in read_json(path)["parts"]:
                side = {"left": "左侧", "right": "右侧"}.get(part.get("side"), "")
                names[part["id"]] = (
                    part["name"]
                    if part.get("binding")
                    else side + translations.get(part["semantic"], part["name"])
                )
    return names
