"""Image evidence and attributed agent reviews tied to an exact repair candidate."""

import uuid
from pydantic import Field

from . import repair
from .schema import Strict
from .store import digest, fingerprint, read_json, write_json


class AgentReview(Strict):
    evidence_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    summary: str = Field(min_length=1)
    items: list[repair.VisualItem] = Field(min_length=1, max_length=16)


def candidate_identity(draft, root):
    candidates = {}
    for key in ("preview", "binding_preview"):
        candidate = draft.result.get(key)
        if not candidate:
            continue
        if candidate.get("version") != draft.version:
            raise ValueError("Preview version is stale")
        work = (root / candidate["path"]).resolve()
        if not work.is_relative_to(root):
            raise ValueError("Preview path is outside the repair")
        receipt = work / "complete.json"
        if not receipt.is_file():
            raise ValueError("Preview receipt is missing")
        for name, sha in read_json(receipt).items():
            path = (work / name).resolve()
            if not path.is_relative_to(work) or not path.is_file() or digest(path) != sha:
                raise ValueError(f"Preview artifact changed: {name}")
        # A human acknowledgement may bump the draft version without changing pixels.
        # Substantive edit updates discard review_inputs; candidate identity describes content.
        candidates[key] = {
            "result": {k: v for k, v in candidate.items() if k != "version"},
            "receipt_sha256": digest(receipt),
        }
    if "preview" not in candidates:
        raise ValueError("Generate the current repair preview first")
    return fingerprint(candidates)


def evidence(store, identifier, version):
    draft, root = repair.snapshot(store, identifier, version)
    identity = candidate_identity(draft, root)
    evidence_id = uuid.uuid4().hex
    output = root / "reviews" / evidence_id
    output.mkdir(parents=True)
    if draft.edit.mode in {"rig", "bind"}:
        from .repair_rig import comparison_images

        preview = draft.result.get("binding_preview") or draft.result["preview"]
        if not preview.get("views"):
            raise ValueError("Generate a binding preview with pose comparisons first")
        images = comparison_images(draft, root, preview, output)
        if not images:
            raise ValueError("No comparable before/after poses are available")
    else:
        images = repair.material_review_images(root, draft, output)
    images.insert(0, ("before: neutral material composite", root / "base/composite.png"))
    # Include every saved binding pose, including new parts without a before counterpart.
    for view in draft.result.get("binding_preview", {}).get("views", []):
        images.append((view["label"], root / view["path"]))
    views = [
        {"label": label, "path": str(path.relative_to(root)), "sha256": digest(path)}
        for label, path in images
    ]
    result = {
        "id": evidence_id,
        "version": version,
        "candidate": identity,
        "views": views,
        "physics_time_response": "not_evaluated",
    }
    write_json(output / "evidence.json", result)
    repair.publish(store, draft, review_inputs=result)
    return {**result, "directory": str(root), "images": [{**v, "path": str(root / v["path"])} for v in views]}


def verify_inputs(draft, root, evidence_id):
    inputs = draft.result.get("review_inputs", {})
    if inputs.get("id") != evidence_id or inputs.get("version") != draft.version:
        raise ValueError("Review evidence is stale; generate evidence for this draft version")
    if candidate_identity(draft, root) != inputs.get("candidate"):
        raise ValueError("Candidate changed after evidence was generated")
    for view in inputs["views"]:
        path = (root / view["path"]).resolve()
        if not path.is_relative_to(root) or not path.is_file() or digest(path) != view["sha256"]:
            raise ValueError("Review image changed")
    return inputs


def record(store, identifier, version, report: AgentReview):
    draft, root = repair.snapshot(store, identifier, version)
    inputs = verify_inputs(draft, root, report.evidence_id)
    visual = {
        **report.model_dump(),
        "source": "agent",
        "version": version,
        "regions": [],
        "views": inputs["views"],
        "user_acceptance": "pending",
        "physics_time_response": "not_evaluated",
    }
    write_json(root / "reviews" / inputs["id"] / "agent-review.json", visual)
    return repair.publish(store, draft, visual=visual)


def verify_review(draft, root):
    visual = draft.result["visual"]
    if visual.get("version") != draft.version:
        raise ValueError("Agent review version is stale")
    verify_inputs(draft, root, visual.get("evidence_id"))
