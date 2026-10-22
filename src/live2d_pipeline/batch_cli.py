"""Material batches are independent of the single-part repair CLI."""

from enum import Enum
from pathlib import Path

import typer

from . import batch_materials as batch
from .agent_review import AgentReview
from .batch_schema import BatchEdit
from .cli_io import json_errors, show
from .config import Config
from .store import Store, lock

batch_app = typer.Typer(no_args_is_help=True, help="Assemble and review multiple PNG layers atomically.")


@batch_app.command("schema")
def schema():
    """Full-edit JSON schema; source paths are resolved relative to the JSON file."""
    show(BatchEdit.model_json_schema())


def read_edit(path):
    edit = BatchEdit.model_validate_json(path.read_text())
    for op in edit.operations:
        if op.source:
            source = Path(op.source).expanduser()
            op.source = str(source if source.is_absolute() else (path.resolve().parent / source).resolve())
    return edit


@batch_app.command("create")
@json_errors
def create(project: Path, edit_file: Path, revision: str | None = None):
    """Capture a batch of PNGs; an initialized empty project requires a canvas size."""
    store = Store(project)
    with lock(store.root / ".lock"):
        show(batch.describe(store, batch.create(store, read_edit(edit_file), revision)))


@batch_app.command("show")
@json_errors
def show_draft(project: Path, draft: str):
    store = Store(project)
    with lock(store.root / ".lock"):
        show(batch.describe(store, batch.load(store, draft)))


@batch_app.command("list")
@json_errors
def list_drafts(project: Path):
    show(batch.list_drafts(Store(project)))


@batch_app.command("update")
@json_errors
def update(project: Path, draft: str, edit_file: Path, version: int = typer.Option(..., min=1)):
    """Replace the full batch and snapshot its inputs again; invalidate earlier review."""
    store = Store(project)
    show(batch.describe(store, batch.update(store, draft, version, read_edit(edit_file))))


@batch_app.command("preview")
@json_errors
def preview(project: Path, draft: str, version: int = typer.Option(..., min=1)):
    """Compose all layers together at original resolution; no models or GPU required."""
    store = Store(project)
    show(batch.describe(store, batch.preview(store, draft, version)))


@batch_app.command("evidence")
@json_errors
def evidence(project: Path, draft: str, version: int = typer.Option(..., min=1)):
    show(batch.evidence(Store(project), draft, version))


@batch_app.command("record-review")
@json_errors
def record_review(project: Path, draft: str, report_file: Path, version: int = typer.Option(..., min=1)):
    """Use the visual-review schema; all review items must pass before apply."""
    store = Store(project)
    report = AgentReview.model_validate_json(report_file.read_text())
    show(batch.describe(store, batch.record_review(store, draft, version, report)))


class BatchUntil(str, Enum):
    materials = "materials"
    rig = "rig"


@batch_app.command("apply")
@json_errors
def apply(
    project: Path,
    draft: str,
    version: int = typer.Option(..., min=1),
    until: BatchUntil = BatchUntil.materials,
    config: Path | None = None,
):
    """Build one revision and move head only after success. Default: material assembly."""
    c, store = Config(config), Store(project)
    with lock(c.runtime / "task.lock"):
        rev = batch.apply(c, store, draft, version, until.value)
        show({
            "revision": rev.id, "status": rev.status, "directory": str(store.revision_dir(rev.id)),
            "runtime_verified": rev.runtime_verified, "user_acceptance": "pending",
        })


@batch_app.command("discard")
@json_errors
def discard(project: Path, draft: str, version: int = typer.Option(..., min=1)):
    batch.discard(Store(project), draft, version)
    show({"draft": draft, "discarded": True})


def register(app):
    app.add_typer(batch_app, name="materials-batch")
