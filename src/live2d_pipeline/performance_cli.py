"""Commands for reusable expression/motion libraries and their actual playback."""

from enum import Enum
import math
from pathlib import Path

import typer

from . import performance
from .cli_io import json_errors, show
from .config import Config
from .performance_schema import ExpressionDocument, LibraryContents, MotionDocument, PlaybackRequest
from .store import Store, lock, read_json, write_json

app = typer.Typer(no_args_is_help=True, help="Version expressions/motions and preview authored performances.")


@app.command("show")
@json_errors
def show_library(project: Path, revision: str | None = None, snapshot: str | None = None):
    store = Store(project)
    with lock(store.root / ".lock"):
        show(performance.load_library(store, revision, snapshot))


@app.command("history")
@json_errors
def history(project: Path, revision: str | None = None):
    store = Store(project)
    with lock(store.root / ".lock"):
        show(performance.history(store, revision))


@app.command("put-expression")
@json_errors
def put_expression(
    project: Path, name: str, expression_file: Path,
    version: int = typer.Option(..., min=0), revision: str | None = None,
):
    """Create/replace an expression from an exp3.json document at an expected library version."""
    document = read_json(expression_file)
    store = Store(project)
    with lock(store.root / ".lock"):
        show(performance.put_expression(store, name, document, revision, version))


@app.command("put-motion")
@json_errors
def put_motion(
    project: Path, name: str, motion_file: Path,
    version: int = typer.Option(..., min=0), group: str = "Idle", revision: str | None = None,
):
    """Create/replace a motion from validated motion3.json; metadata counts must agree."""
    document = read_json(motion_file)
    store = Store(project)
    with lock(store.root / ".lock"):
        show(performance.put_motion(store, group, name, document, revision, version))


@app.command("import")
@json_errors
def import_library(
    project: Path, library_file: Path, version: int = typer.Option(..., min=0), revision: str | None = None,
):
    """Replace the complete library contents from JSON; also supports exported snapshots."""
    document = read_json(library_file)
    if "model_revision" in document:
        document = performance.contents(document)
    store = Store(project)
    with lock(store.root / ".lock"):
        show(performance.replace_library(store, document, revision, version))


class EntryKind(str, Enum):
    expression = "expression"
    motion = "motion"
    physics = "physics"


@app.command("remove")
@json_errors
def remove(
    project: Path, kind: EntryKind, name: str | None = None, group: str = "Idle",
    version: int = typer.Option(..., min=0), revision: str | None = None,
):
    store = Store(project)
    with lock(store.root / ".lock"):
        show(performance.remove(store, kind.value, name, group, revision, version))


@app.command("export")
@json_errors
def export_library(
    project: Path, destination: Path, revision: str | None = None, snapshot: str | None = None,
):
    """Write a portable library snapshot; normal l2d export also attaches its assets to the model."""
    store = Store(project)
    destination = destination.resolve()
    if destination.exists() or destination.is_relative_to(store.root):
        raise ValueError("Choose a new destination outside the project")
    with lock(store.root / ".lock"):
        library = performance.load_library(store, revision, snapshot)
        _, parameters = performance.parameter_map(store, revision)
        performance.validate_library(performance.contents(library), parameters)
        write_json(destination, library)
        show({"snapshot": library["id"], "path": str(destination)})


def _request(path, physics):
    request = PlaybackRequest.model_validate(read_json(path))
    if physics:
        request.physics = True
    return request


@app.command("sample")
@json_errors
def sample(
    project: Path, playback_file: Path, revision: str | None = None,
    snapshot: str | None = None, physics: bool = False,
):
    """Sample a timeline to JSON, including the exact endpoint; does not render or start a model."""
    request = _request(playback_file, physics)
    store = Store(project)
    with lock(store.root / ".lock"):
        rev, library, sampler, simulation = performance.make_sampler(store, request, revision, snapshot)
        samples = []
        count = math.ceil(request.duration * request.fps)
        for index in range(count + 1):
            t = min(index / request.fps, request.duration)
            values = sampler(t)
            if simulation:
                values = simulation(values, t)
            samples.append({"time": t, "parameters": values})
        show({
            "revision": rev.id, "snapshot": library["id"], "request": request.model_dump(),
            "runtime_verified": False, "frames": samples,
            "physics": simulation.report() if simulation else {"status": "not_evaluated"},
        })


@app.command("preview")
@json_errors
def preview(
    project: Path, playback_file: Path, revision: str | None = None,
    snapshot: str | None = None, physics: bool = False, config: Path | None = None,
):
    """Render the requested motion/expression timeline to MP4 on a temporary CPU workspace."""
    from .video import generate

    request = _request(playback_file, physics)
    c, store = Config(config), Store(project)
    with lock(c.runtime / "task.lock"), lock(store.root / ".lock"):
        rev, library, sampler, simulation = performance.make_sampler(store, request, revision, snapshot)
        path = generate(
            c, store, rev.id, sampler=sampler, physics=simulation,
            duration=request.duration, fps=request.fps, long_edge=request.long_edge,
            provenance={"snapshot": library["id"], "request": request.model_dump()},
        )
        show({
            "video": str(path), "receipt": str(path.parent / "playback.json"),
            "parameters": str(path.parent / "parameters.json"), "runtime_verified": False,
        })


class SchemaName(str, Enum):
    expression = "expression"
    motion = "motion"
    library = "library"
    playback = "playback"


@app.command("schema")
def schema(name: SchemaName):
    models = {
        "expression": ExpressionDocument, "motion": MotionDocument,
        "library": LibraryContents, "playback": PlaybackRequest,
    }
    show(models[name.value].model_json_schema())


def register(parent):
    parent.add_typer(app, name="performance")
