"""Author full physics3 chains and store them with the performance library."""

from enum import Enum
from pathlib import Path

import typer
from pydantic import Field

from . import performance, physics
from .cli_io import json_errors, show
from .schema import Strict
from .store import Store, lock, read_json, write_json

app = typer.Typer(no_args_is_help=True, help="Author and version multi-particle physics3 chains.")


class PhysicsAuthoring(Strict):
    groups: list[physics.PhysicsSetting] = Field(max_length=256)
    names: dict[str, str] = Field(default_factory=dict)
    fps: float = Field(default=60, ge=0, le=240)
    gravity: physics.Vector = Field(default_factory=lambda: physics.Vector(X=0, Y=-1))
    wind: physics.Vector = Field(default_factory=lambda: physics.Vector(X=0, Y=0))


class SchemaName(str, Enum):
    document = "document"
    authoring = "authoring"


@app.command("schema")
def schema(name: SchemaName = typer.Argument(SchemaName.document)):
    show((physics.PhysicsDocument if name == SchemaName.document else PhysicsAuthoring).model_json_schema())


@app.command("compose")
@json_errors
def compose(authoring_file: Path, destination: Path):
    """Convert groups/names/forces into a standard physics3 file with correct counts."""
    if destination.exists():
        raise ValueError("Choose a new output path")
    spec = PhysicsAuthoring.model_validate(read_json(authoring_file))
    if set(spec.names) - {group.Id for group in spec.groups}:
        raise ValueError("Names contain an unknown physics group ID")
    result = physics.compose(
        spec.groups, spec.names, fps=spec.fps,
        gravity=spec.gravity.model_dump(), wind=spec.wind.model_dump(),
    )
    write_json(destination, result)
    show({"path": str(destination.resolve()), "groups": len(result["PhysicsSettings"])})


@app.command("put")
@json_errors
def put(
    project: Path, document_file: Path,
    version: int = typer.Option(..., min=0), revision: str | None = None,
):
    """Validate all parameter references and save a new immutable performance snapshot."""
    store = Store(project)
    with lock(store.root / ".lock"):
        show(performance.put_physics(store, read_json(document_file), revision, version))


@app.command("show")
@json_errors
def show_physics(project: Path, revision: str | None = None, snapshot: str | None = None):
    """Read authored physics or the current generated bundle's physics as a starting point."""
    store = Store(project)
    with lock(store.root / ".lock"):
        rev, parameters = performance.parameter_map(store, revision)
        library = performance.load_library(store, rev.id, snapshot)
        document = library["physics"]
        source = "performance"
        if document is None:
            source = "generated_bundle"
            bundle = store.revision_dir(rev.id) / "rig/bundle"
            models = list(bundle.glob("*.model3.json"))
            if len(models) != 1:
                raise ValueError("Expected exactly one model3.json")
            filename = read_json(models[0]).get("FileReferences", {}).get("Physics")
            if not filename:
                raise ValueError("This model has no physics document")
            path = (bundle / filename).resolve()
            if not path.is_relative_to(bundle.resolve()):
                raise ValueError("Physics reference is outside the bundle")
            document = read_json(path)
        show({"revision": rev.id, "snapshot": library["id"], "source": source,
              "document": physics.validate_document(document, parameters)})


def register(parent):
    parent.add_typer(app, name="physics")
