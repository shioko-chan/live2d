"""Agent-facing commands use the same services and locks as the web workspace."""

from enum import Enum
from pathlib import Path

import typer

from . import repair
from .agent_review import AgentReview
from .cli_io import json_errors, show
from .config import Config
from .repair_schema import DraftUpdate, RepairEdit, RepairTarget
from .schema import Plan
from .store import Store, lock

repair_app = typer.Typer(no_args_is_help=True, help="Versioned repair drafts and review evidence.")


def describe(store, draft):
    root = repair.directory(store, draft.id)
    return {**draft.model_dump(), "directory": str(root), "source_image": str(root / "source.png")}


class InspectStage(str, Enum):
    all = "all"
    image = "image"
    materials = "materials"
    rig = "rig"


@json_errors
def inspect(
    project: Path,
    revision: str | None = None,
    stage: InspectStage = InspectStage.all,
    part: str | None = None,
):
    """Return saved images, parts, parameters and fresh integrity checks. No inference."""
    from .inspection import inspect_project

    store = Store(project)
    with lock(store.root / ".lock"):
        show(inspect_project(store, revision, stage.value, part))


@json_errors
def render(project: Path, pose_file: Path, revision: str | None = None, config: Path | None = None):
    """Render a JSON pose/region on a disposable workspace; CPU only."""
    from .render import RenderRequest, render_pose

    request = RenderRequest.model_validate_json(pose_file.read_text())
    c, store = Config(config), Store(project)
    with lock(c.runtime / "task.lock"), lock(store.root / ".lock"):
        show(render_pose(c, store, request, revision))


@json_errors
def preview_video(project: Path, revision: str | None = None, config: Path | None = None):
    """Generate the existing 8-second parameter animation; no continuous physics."""
    from .video import DURATION, FPS, generate

    c, store = Config(config), Store(project)
    with lock(c.runtime / "task.lock"), lock(store.root / ".lock"):
        rev = store.get(revision)
        path = generate(c, store, rev.id)
        show(
            {
                "revision": rev.id,
                "video": str(path),
                "fps": FPS,
                "seconds": DURATION,
                "runtime_verified": False,
                "physics_time_response": "not_evaluated",
            }
        )


class SchemaName(str, Enum):
    plan = "plan"
    repair_edit = "repair-edit"
    visual_review = "visual-review"
    render = "render"


def schema(name: SchemaName):
    """Print authoritative JSON input schemas without inference."""
    from .render import RenderRequest

    models = {"plan": Plan, "repair-edit": RepairEdit, "visual-review": AgentReview, "render": RenderRequest}
    show(models[name.value].model_json_schema())


class RepairMode(str, Enum):
    split = "split"
    mask = "mask"
    replace = "replace"
    add = "add"
    repaint = "repaint"
    bind = "bind"
    rig = "rig"


@repair_app.command("create")
@json_errors
def create(
    project: Path,
    mode: RepairMode = RepairMode.split,
    part: str | None = None,
    source: Path | None = None,
    revision: str | None = None,
    config: Path | None = None,
):
    """Copy current materials into a draft; add/replace accepts a local --source image."""
    c, store = Config(config), Store(project)
    with lock(c.runtime / "task.lock"), lock(store.root / ".lock"):
        spec = RepairTarget(revision=store.get(revision).id, part=part, mode=mode.value)
        show(describe(store, repair.create(c, store, spec, image=source)))


@repair_app.command("show")
@json_errors
def show_draft(project: Path, draft: str):
    """Read a draft including its current version, full edit and result paths."""
    store = Store(project)
    with lock(store.root / ".lock"):
        show(describe(store, repair.load(store, draft)))


@repair_app.command("list")
@json_errors
def list_drafts(project: Path, revision: str | None = None):
    store = Store(project)
    with lock(store.root / ".lock"):
        show(repair.list_drafts(store, revision))


@repair_app.command("update")
@json_errors
def update(project: Path, draft: str, edit_file: Path, version: int = typer.Option(..., min=1)):
    """Replace the full edit from JSON at the expected version; returns the new version."""
    edit = RepairEdit.model_validate_json(edit_file.read_text())
    store = Store(project)
    with lock(store.root / ".lock"):
        show(describe(store, repair.update(store, draft, DraftUpdate(version=version, edit=edit))))


class RepairStep(str, Enum):
    segment = "segment"
    preview = "preview"
    binding_preview = "binding-preview"
    rig_context = "rig-context"
    underpaint = "underpaint"
    inspect = "inspect"
    search = "search"


@repair_app.command("run")
@json_errors
def run_step(
    project: Path,
    draft: str,
    step: RepairStep,
    version: int = typer.Option(..., min=1),
    config: Path | None = None,
):
    """Execute one explicit repair step. inspect/search load the local VLM."""
    c, store = Config(config), Store(project)
    with lock(c.runtime / "task.lock"), lock(store.root / ".lock"):
        if step == RepairStep.rig_context:
            from .repair_rig import rig_context

            action = rig_context
        else:
            action = getattr(
                repair, "preview_materials" if step == RepairStep.preview else step.value.replace("-", "_")
            )
        show(describe(store, action(c, store, draft, version)))


@repair_app.command("evidence")
@json_errors
def evidence(project: Path, draft: str, version: int = typer.Option(..., min=1)):
    """Prepare hashed images for agent visual review; no inference."""
    from .agent_review import evidence as prepare

    store = Store(project)
    with lock(store.root / ".lock"):
        show(prepare(store, draft, version))


@repair_app.command("record-review")
@json_errors
def record_review(project: Path, draft: str, report_file: Path, version: int = typer.Option(..., min=1)):
    """Record an agent assessment of the exact evidence; never marks user acceptance."""
    from .agent_review import record

    report = AgentReview.model_validate_json(report_file.read_text())
    store = Store(project)
    with lock(store.root / ".lock"):
        show(describe(store, record(store, draft, version, report)))


class RepairUntil(str, Enum):
    materials = "materials"
    rig = "rig"


@repair_app.command("apply")
@json_errors
def apply(
    project: Path,
    draft: str,
    version: int = typer.Option(..., min=1),
    until: RepairUntil = RepairUntil.rig,
    config: Path | None = None,
):
    """Apply a reviewed candidate as a new revision. Rule failures remain blocking."""
    c, store = Config(config), Store(project)
    with lock(c.runtime / "task.lock"), lock(store.root / ".lock"):
        current, _ = repair.snapshot(store, draft, version)
        visual = current.result.get("visual", {})
        if visual.get("version") != version or not visual.get("items"):
            raise ValueError("Record a current visual review before applying from the CLI")
        rev = repair.apply(c, store, draft, version, until=until.value)
        show(
            {
                "revision": rev.id,
                "status": rev.status,
                "directory": str(store.revision_dir(rev.id)),
                "runtime_verified": rev.runtime_verified,
                "user_acceptance": "pending",
            }
        )


@repair_app.command("discard")
@json_errors
def discard(project: Path, draft: str, version: int = typer.Option(..., min=1)):
    store = Store(project)
    with lock(store.root / ".lock"):
        repair.discard(store, draft, version)
        show({"draft": draft, "discarded": True})


def register(app):
    from . import batch_cli, performance_cli, physics_cli, rig_query_cli, runtime_check_cli

    for command in (inspect, render, preview_video, schema):
        app.command()(command)
    app.add_typer(repair_app, name="repair")
    batch_cli.register(app)
    performance_cli.register(app)
    physics_cli.register(app)
    rig_query_cli.register(app)
    runtime_check_cli.register(app)
