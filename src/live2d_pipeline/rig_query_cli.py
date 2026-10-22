"""CLI registration for read-only authoring-object inspection."""

from enum import Enum
from pathlib import Path

import typer

from .cli_io import json_errors, show
from .config import Config
from .rig_query import RigQuery, inspect_rig
from .store import Store, read_json


class ObjectKind(str, Enum):
    mesh = "mesh"
    warp = "warp"
    rotation = "rotation"
    part = "part"
    glue = "glue"
    parameter = "parameter"


class QueryDetail(str, Enum):
    object = "object"
    summary = "summary"
    points = "points"


class CoordinateSpace(str, Enum):
    local = "local"
    canvas = "canvas"


@json_errors
def rig_inspect(
    project: Path,
    object_id: str = typer.Option(..., "--object", help="Actual model object or parameter ID."),
    kind: ObjectKind = typer.Option(..., "--kind"),
    detail: QueryDetail = QueryDetail.object,
    pose_file: Path | None = typer.Option(None, help="JSON parameter/value dictionary for summary/points."),
    space: CoordinateSpace = CoordinateSpace.local,
    offset: int = typer.Option(0, min=0),
    limit: int = typer.Option(64, min=1, max=256),
    secondary_id: str | None = None,
    revision: str | None = None,
    config: Path | None = None,
):
    """Read hierarchy/keyforms or paged evaluated points from a temporary rig copy."""
    request = RigQuery(
        object_id=object_id,
        kind=kind.value,
        detail=detail.value,
        coordinate=read_json(pose_file) if pose_file else {},
        space=space.value,
        offset=offset,
        limit=limit,
        secondary_id=secondary_id,
    )
    show(inspect_rig(Config(config), Store(project), request, revision))


def register(app):
    app.command("rig-inspect")(rig_inspect)
