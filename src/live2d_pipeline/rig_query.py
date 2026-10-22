"""Inspect authoring geometry on a disposable copy of a verified saved rig."""

import asyncio
from pathlib import Path
import shutil
import tempfile
from typing import Literal
import uuid

from pydantic import Field, model_validator

from .review import built_directory
from .rig import open_rig
from .schema import Strict
from .store import digest, lock, write_json


class RigQuery(Strict):
    object_id: str = Field(min_length=1)
    kind: Literal["mesh", "warp", "rotation", "part", "glue", "parameter"]
    detail: Literal["object", "summary", "points"] = "object"
    coordinate: dict[str, float] = Field(default_factory=dict)
    space: Literal["local", "canvas"] = "local"
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=64, ge=1, le=256)
    secondary_id: str | None = None

    @model_validator(mode="after")
    def supported_query(self):
        if self.detail != "object" and self.kind not in {"mesh", "warp"}:
            raise ValueError("summary/points support mesh and warp; use object for other kinds")
        if self.detail == "object" and (self.coordinate or self.space != "local" or self.offset):
            raise ValueError(
                "object returns all keyforms; pose, canvas space and offset require summary/points"
            )
        if self.secondary_id is not None and self.kind != "glue":
            raise ValueError("secondary_id is only supported for glue")
        return self


async def query(session, request):
    """Use discovered server schemas; keep its exact field and point representations."""
    if request.kind == "parameter" or request.coordinate:
        parameters = (await session.call("project_list_parameters"))["parameters"]
        by_id = {parameter["id"]: parameter for parameter in parameters}
        if request.kind == "parameter":
            if request.object_id not in by_id:
                raise ValueError(f"Unknown parameter: {request.object_id}")
            return "project_list_parameters", {}, by_id[request.object_id]
        for parameter, value in request.coordinate.items():
            if parameter not in by_id:
                raise ValueError(f"Unknown parameter: {parameter}")
            if not by_id[parameter]["min"] <= value <= by_id[parameter]["max"]:
                raise ValueError(f"Parameter out of range: {parameter}")
    target = {"kind": request.kind, "id": request.object_id}
    if request.secondary_id is not None:
        target["secondary_id"] = request.secondary_id
    arguments = {"target": target}
    tool = "object_get"
    if request.detail != "object":
        tool = "rig_inspect"
        arguments.update(
            detail=request.detail,
            coordinate=request.coordinate,
            space=request.space,
            offset=request.offset,
            limit=request.limit,
        )
    return tool, arguments, await session.call(tool, arguments)


def inspect_rig(config, store, request: RigQuery, revision=None):
    # Both CLI and Python callers share the same exclusive runtime/project boundary.
    with lock(config.runtime / "task.lock"), lock(store.root / ".lock"):
        rev, directory = built_directory(store, revision)
        source = directory / "rig"
        hashes = {name: digest(source / name) for name in ("source.psd", "source.psd2live")}
        output = store.root / "previews" / rev.id / "rig-inspections" / uuid.uuid4().hex
        output.mkdir(parents=True)
        with tempfile.TemporaryDirectory(prefix="workspace-", dir=output) as temporary:
            work = Path(temporary)
            for name in hashes:
                shutil.copyfile(source / name, work / name)

            async def read():
                async with open_rig(
                    config,
                    work / "source.psd",
                    work / "source.psd2live",
                    work / "control",
                    output / "query.log",
                ) as (session, _):
                    return await query(session, request)

            try:
                tool, arguments, result = asyncio.run(read())
            except ExceptionGroup as exc:
                # MCP's task groups wrap even a single validation failure. Preserve
                # multiple failures, but expose one actionable error to CLI callers.
                cause = exc
                while isinstance(cause, ExceptionGroup) and len(cause.exceptions) == 1:
                    cause = cause.exceptions[0]
                if cause is exc:
                    raise
                raise cause from exc
        if any(digest(source / name) != expected for name, expected in hashes.items()):
            raise RuntimeError("Saved rig changed during inspection")
        report = {
            "revision": rev.id,
            "request": request.model_dump(),
            "tool": tool,
            "arguments": arguments,
            "result": result,
            "source_sha256": hashes,
            "receipt": str(output / "query.json"),
        }
        write_json(output / "query.json", report)
        return report
